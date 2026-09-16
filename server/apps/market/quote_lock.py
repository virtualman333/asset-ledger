# -*- coding: utf-8 -*-
"""跨进程互斥：这一台机器上只许有一个进程真的去抓行情。

纯标准库（`os` + `fcntl`/`msvcrt`），零 Django、零网络、零第三方依赖。

## 为什么需要它

`quote_schedule.should_autostart` 只看 `sys.argv`，它能挡住 `runserver` 的
autoreload 双进程，也能挡住 `migrate` 这种一次性命令，但挡不住**生产部署最
常见的那一种**：`gunicorn -w 4` / `uvicorn --workers 4`。每个 worker 都会
import 一次 WSGI 应用，于是 `AppConfig.ready()` 在每个 worker 里各跑一遍，
`should_autostart` 对每个 worker 都回答「是」——argv 里确实没有 `manage.py`。

实测（`_e2e_al_sched.py`，fork 4 个真进程各走一遍 ready()）：**4 / 4 都起了调度器**。
后果不是「多抓几遍」这么轻：README 的已知约束里写着「轮询太快会被源限流」，
免费源本来就是这个系统的软肋，按 worker 数放大等于自己把自己撞上限流。

`build_scheduler` 里的 `max_instances=1` 只在**单个调度器内部**生效，跨进程无效，
所以修法只能是进程间互斥。

## 为什么是文件锁，而不是数据库锁 / 环境变量

- **数据库锁**：`AppConfig.ready()` 跑的时候数据库不一定连得上（`migrate` 之前、
  数据库还没起来），拿不到锁就等于不起任务 —— 一个会因为基础设施状态而静默
  失效的判据，正是这个仓库反复栽跟头的那种。
- **环境变量**（比如「只在 1 号 worker 里起」）：把「谁是 1 号」这件事推给部署方，
  而 gunicorn 并没有稳定的 worker 序号；写错了同样静默。
- **文件锁**：只需要一个本地文件。进程无论怎么死（kill -9、OOM、被 supervisord
  掐掉），操作系统都会在 fd 关闭时自动放锁 —— 不存在「锁留在那里、谁也拿不到」
  的经典问题，也就不需要超时/心跳/清理。

## 不变式

1. 拿不到锁就返回 `None`，**不抛异常**、不等待（服务启动不能被锁卡住）。
2. 持锁的进程只要还活着就一直是它；死了立刻让位。
3. 同一个进程里第二次抢**同一把**锁也会失败 —— 进程内重复启动由
   `scheduler.autostart` 的 `already_started` 挡，这里不重复兜。
"""
from __future__ import annotations

import errno
import logging
import os

logger = logging.getLogger(__name__)


def _lock_fd(fd: int) -> None:
    """把 fd 的第 1 个字节锁上；已被别人锁住就抛 OSError。"""
    if os.name == "nt":
        import msvcrt

        msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
    else:
        import fcntl

        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)


def _unlock_fd(fd: int) -> None:
    try:
        if os.name == "nt":
            import msvcrt

            os.lseek(fd, 0, os.SEEK_SET)
            msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            fcntl.flock(fd, fcntl.LOCK_UN)
    except OSError:  # 已经没了就别挡着关 fd
        pass


class QuoteLock:
    """一把已被持有的锁。`release()` 之后不可再用；进程退出会自动放。"""

    def __init__(self, path: str, fd: int):
        self.path = path
        self._fd = fd

    @property
    def pid(self) -> int:
        return os.getpid()

    def release(self) -> None:
        if self._fd < 0:
            return
        _unlock_fd(self._fd)
        try:
            os.close(self._fd)
        except OSError:
            pass
        self._fd = -1

    def __enter__(self) -> "QuoteLock":
        return self

    def __exit__(self, *exc) -> None:
        self.release()

    def __repr__(self) -> str:  # pragma: no cover - 只为排障时好看
        return f"<QuoteLock pid={self.pid} path={self.path!r}>"


def try_acquire(path: str) -> QuoteLock | None:
    """试着独占 `path`（第 1 个字节）。拿到返回锁对象，拿不到返回 None。

    目录不存在会先建出来。`path` 是文件锁，所以它必须是**本机**路径：
    多机部署时每台机器各一个调度器是正确的，不要把它放到网络盘上当全局锁。
    """
    parent = os.path.dirname(os.path.abspath(path))
    try:
        os.makedirs(parent, exist_ok=True)
    except OSError as exc:
        logger.warning("行情锁的目录建不出来（%s）：%s", parent, exc)
        return None

    try:
        fd = os.open(path, os.O_CREAT | os.O_RDWR)
    except OSError as exc:
        logger.warning("行情锁文件打不开（%s）：%s", path, exc)
        return None

    try:
        # 锁文件**刻意留空**：Windows 的字节区间锁会连读都挡住，持有期间
        # 谁也别想读它 —— 往里面写 pid 只会制造一个「排障时读不到」的字段。
        # 「上一个持锁的是谁」要看日志，不看这个文件。
        _lock_fd(fd)
    except OSError as exc:
        os.close(fd)
        if exc.errno not in (errno.EACCES, errno.EAGAIN, errno.EDEADLK, errno.EWOULDBLOCK):
            logger.warning("行情锁抢失败（既不是「已被占用」也不是预期内的错）：%s", exc)
        return None

    return QuoteLock(path, fd)
