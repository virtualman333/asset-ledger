# -*- coding: utf-8 -*-
"""进程自证：把「我跑的是哪份代码」变成**可核对的事实**。

要解决的问题
------------
冒烟脚本的 `--base` 模式原先只能**声明**自己无法自证 —— 它把「验的是这份代码」这句承诺
转包给「那个地址上跑的恰好是这份代码」这个约定，而它对这个约定一无所知。实测的代价：
一个两天前起的旧进程让 26 条检查里 5 条 FAIL，**全是假缺陷**，随后还在缺字段上崩掉、
连结论都没打印出来（见 `scripts/_smoke_lib.py` 的说明）。

声明有用，但声明拦不住误诊。要拦得住，服务端就得**报出自己是谁**，于是有了这个模块：

- `fingerprint`：**服务端自己那份源码**的内容指纹（相对路径 + 内容一起入哈希）。
  调用方对本地同一目录算一遍，相等就是同一份代码 —— 与时钟无关，与哪台机器无关。
- `newest_mtime` / `PROCESS_STARTED_AT`：进程起来之后源码又被改过吗。这条**只能服务端
  自己判**（两边各拿自己的时间戳比会撞上时钟偏差），而且它是指纹的必要补充：
  先改文件、再发请求，指纹算的是**磁盘上的新内容**、进程跑的却是**旧代码**，
  只看指纹会得出「一致」这个错误结论。

两个刻意的口径
--------------
1. **行尾先归一再入哈希**：同一份代码在 Windows（CRLF）与 Linux（LF）上必须算出同一个
   指纹，否则跨机核对会把「同一份代码」判成不同。代价是「只改了行尾」不算改动。
2. **跳过依赖目录**：`__pycache__`（字节码）、`.venv` / `venv`（那里可能躺着**另一份**
   被装进去的代码）等等都不算 —— 它们既不属于「这份代码」，也会随环境漂移。

只用标准库
----------
`apps/core/tests/test_source_stamp.py` 会 import 本模块，而按 README 的承诺，那套单测
**不需要 Django、也不需要第三方依赖**。所以这里不 import django：扫描根目录由调用方
传进来（视图传 `settings.BASE_DIR`）。
"""
from __future__ import annotations

import hashlib
import os
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

#: 进程启动时刻 —— **取的是本模块被 import 的那一刻**。
#:
#: `apps/core/apps.py` 的 `ready()` 会在 `django.setup()` 里 import 本模块，于是对
#: runserver / gunicorn / wsgi 三种起法它都等于「进程刚开始跑」。放到视图里 import 就晚了：
#: 那会变成「第一个请求」，而「启动之后源码被改过」这件事就再也判不出来了。
PROCESS_STARTED_AT = time.time()

#: 只把 Python 源文件算进来
SOURCE_SUFFIX = ".py"

#: 不进指纹的目录：字节码缓存、别人装依赖的地方、版本库内部
EXCLUDE_DIRS = frozenset(
    {
        "__pycache__",
        ".venv",
        "venv",
        ".git",
        "node_modules",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
    }
)


def iso(ts: float) -> str:
    """epoch 秒 → UTC 的 ISO8601（`Z` 结尾）。

    一律 UTC：服务端可能在另一台机器、另一个时区，读的人不该先做一次时区换算。
    """
    return (
        datetime.fromtimestamp(float(ts), tz=timezone.utc)
        .isoformat(timespec="seconds")
        .replace("+00:00", "Z")
    )


def iter_source_files(root) -> list:
    """`root` 下所有该进指纹的 `.py`，按路径排好序（顺序稳定，指纹才稳定）。"""
    root = Path(root)
    if not root.is_dir():
        return []  # 目录不存在 = 没有源码，不抛
    found: list[Path] = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if d not in EXCLUDE_DIRS)
        found += [Path(dirpath) / name for name in filenames if name.endswith(SOURCE_SUFFIX)]
    return sorted(found)


def _relpath(path: Path, root: Path) -> str:
    """相对路径入哈希 —— 文件挪了位置就该换一个指纹，所以路径本身也是内容的一部分。"""
    try:
        return path.relative_to(root).as_posix()
    except ValueError:  # 不在 root 下（测试会注入这种路径），原样用，同样稳定
        return path.as_posix()


@dataclass(frozen=True)
class Snapshot:
    """一次扫描的结果。**纯数据**，不碰文件系统，所以断言可以直接构造它。"""

    entries: tuple = ()  # ((相对路径, mtime), ...)，按相对路径排序
    fingerprint: str = ""
    unreadable: tuple = ()  # 读不出来的（相对路径）—— 不能静默，否则指纹会悄悄少算几个文件

    @property
    def files(self) -> int:
        return len(self.entries)

    @property
    def newest_mtime(self) -> float:
        return max((m for _, m in self.entries), default=0.0)

    @property
    def newest_file(self) -> str:
        if not self.entries:
            return ""
        return max(self.entries, key=lambda e: e[1])[0]

    def changed_after(self, ts: float, limit: int | None = None) -> list:
        """比 `ts` 更新（即「进程起来之后又被动过」）的文件，最近改的排在最前。"""
        hits = sorted((e for e in self.entries if e[1] > ts), key=lambda e: e[1], reverse=True)
        names = [rel for rel, _ in hits]
        return names[:limit] if limit else names


def scan(root, paths=None) -> Snapshot:
    """扫一遍 `root`，算出内容指纹 + 每个文件的 mtime。

    `paths` 是给测试留的注入点（可以塞进读不到的路径，验证「读不出来」不会被静默吞掉）；
    正常调用不传，走 `iter_source_files()`。
    """
    root = Path(root)
    candidates = iter_source_files(root) if paths is None else [Path(p) for p in paths]

    digest = hashlib.sha256()
    entries: list[tuple[str, float]] = []
    unreadable: list[str] = []
    for path in candidates:
        rel = _relpath(path, root)
        try:
            data = path.read_bytes()
            mtime = path.stat().st_mtime
        except OSError:  # 目录、被删掉、没权限……都算「读不到」，不许抛也不许静默
            unreadable.append(rel)
            continue
        # 行尾归一：同一份代码在 CRLF 与 LF 上必须同指纹（见模块说明的口径 1）
        data = data.replace(b"\r\n", b"\n")
        # 每段都带长度前缀：否则「路径 + 内容」直接拼会粘连 ——
        # `a.py` 里写 `xb.py`（一个文件）与 `a.py` 写 `x` + 空的 `b.py`（两个文件）
        # 会拼出同一串字节，两份不同的源码算出一个指纹。
        digest.update(rel.encode("utf-8"))
        digest.update(b"\0")
        digest.update(str(len(data)).encode("ascii"))
        digest.update(b"\0")
        digest.update(data)
        digest.update(b"\0")
        entries.append((rel, mtime))

    entries.sort()
    return Snapshot(tuple(entries), digest.hexdigest(), tuple(unreadable))
