# -*- coding: utf-8 -*-
"""跨进程锁（`apps/market/quote_lock.py`）—— 不需要数据库、Django、APScheduler。

这条判据唯一会错的方式是「以为排他了，其实没有」，所以这里的断言刻意都落在
**真实的 OS 锁状态**上：抢两次、放一次再抢、真 fork 进程抢。注入一个假锁
只能测出「有没有调用」，测不出排他性。
"""
import json
import os
import pathlib
import subprocess
import sys
import tempfile
import textwrap
import time
import unittest

from apps.market import quote_lock

SERVER_DIR = pathlib.Path(__file__).resolve().parents[3]  # server/

HOLD_CHILD = textwrap.dedent(
    """
    import json, os, sys, time
    sys.path.insert(0, os.environ["SRV"])
    from apps.market.quote_lock import try_acquire
    lock = try_acquire(sys.argv[1])
    print(json.dumps({"pid": os.getpid(), "got": lock is not None}), flush=True)
    time.sleep(float(sys.argv[2]))
    if lock is not None:
        lock.release()
    """
)


class TempLock:
    """给每个用例一把干净、互不干扰的锁文件。"""

    def __init__(self):
        self.root = tempfile.mkdtemp(prefix="al-quote-lock-")

    @property
    def path(self) -> str:
        return os.path.join(self.root, "quote_refresh.lock")

    @property
    def nested(self) -> str:
        return os.path.join(self.root, "var", "deep", "quote_refresh.lock")


class TryAcquireTest(unittest.TestCase):
    def setUp(self):
        self.tmp = TempLock()

    def test_第一次能拿到(self):
        lock = quote_lock.try_acquire(self.tmp.path)
        self.assertIsNotNone(lock)
        self.assertEqual(lock.pid, os.getpid())
        lock.release()

    def test_同一个进程里第二次抢同一把锁会失败(self):
        # 进程内重复启动由 scheduler.autostart 的 already_started 挡；
        # 这里要的是「真的排他」——两个独立的 fd 也不能同时持有。
        first = quote_lock.try_acquire(self.tmp.path)
        self.assertIsNotNone(first)
        try:
            self.assertIsNone(quote_lock.try_acquire(self.tmp.path))
        finally:
            first.release()

    def test_放锁之后别人能抢到(self):
        first = quote_lock.try_acquire(self.tmp.path)
        first.release()
        second = quote_lock.try_acquire(self.tmp.path)
        self.assertIsNotNone(second, "放锁之后必须立刻可抢，否则就是「一次失败永久失效」")
        second.release()

    def test_重复释放不会炸(self):
        lock = quote_lock.try_acquire(self.tmp.path)
        lock.release()
        lock.release()  # 幂等
        again = quote_lock.try_acquire(self.tmp.path)
        self.assertIsNotNone(again)
        again.release()

    def test_with_语句也会放锁(self):
        with quote_lock.try_acquire(self.tmp.path) as lock:
            self.assertIsNotNone(lock)
        self.assertIsNotNone(quote_lock.try_acquire(self.tmp.path))

    def test_目录不存在会自动建(self):
        lock = quote_lock.try_acquire(self.tmp.nested)
        self.assertIsNotNone(lock, "锁文件所在目录不存在就放弃 = 部署时静默不起任务")
        self.assertTrue(os.path.exists(self.tmp.nested))
        lock.release()

    def test_锁文件是空的(self):
        # 刻意留空：Windows 的字节区间锁持有期间连读都挡（实测 PermissionError），
        # 往里面写 pid 只会造出一个排障时读不到的字段。
        lock = quote_lock.try_acquire(self.tmp.path)
        try:
            self.assertEqual(os.path.getsize(self.tmp.path), 0)
        finally:
            lock.release()

    def test_路径不可写时返回None而不是抛异常(self):
        # 服务启动不能被一把锁卡住：拿不到就当成「不起任务」
        bad = os.path.join(self.tmp.root, "nope")
        pathlib.Path(bad).write_text("我是一个文件，不是目录", encoding="utf-8")
        self.assertIsNone(quote_lock.try_acquire(os.path.join(bad, "x.lock")))


class CrossProcessTest(unittest.TestCase):
    """真开进程验证排他 —— 多 worker 部署踩的就是这条"""

    def setUp(self):
        self.tmp = TempLock()

    def _spawn(self, hold="3.0"):
        # hold 给得宽一点：三个子进程是**同时**抢的，如果第一个在第三个还没启动
        # 就放锁了，这条测试会变成「三个先后各拿一次」而假绿。
        return subprocess.Popen(
            [sys.executable, "-c", HOLD_CHILD, self.tmp.path, hold],
            env={**os.environ, "SRV": str(SERVER_DIR), "PYTHONIOENCODING": "utf-8"},
            cwd=str(SERVER_DIR),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
        )

    def _got(self, proc):
        out, err = proc.communicate(timeout=60)
        lines = [ln for ln in (out or "").splitlines() if ln.strip().startswith("{")]
        self.assertTrue(lines, f"子进程没有结论，stderr：{(err or '')[:400]}")
        return json.loads(lines[-1])["got"]

    def test_三个进程同时抢只有一个拿到(self):
        procs = [self._spawn() for _ in range(3)]
        winners = sum(1 for proc in procs if self._got(proc))
        self.assertEqual(winners, 1, f"应该是 1 个赢家，实际 {winners} 个 —— 多 worker 会各抓一遍")

    def test_前一个进程退出后下一个立刻能拿到(self):
        # 进程被 kill -9 / OOM 掉之后锁必须自动放（fd 关闭），不能留在那儿挡人
        first = self._spawn(hold="2.0")
        self.assertTrue(self._got_fast(first), "第一个进程应该拿到锁")
        first.kill()
        first.wait(timeout=30)

        second = self._spawn(hold="0.2")
        self.assertTrue(self._got(second), "上一个进程死了之后锁应该自动让出来")

    def _got_fast(self, proc):
        """只读第一行（子进程还握着锁，来不及等它退出）。"""
        line = proc.stdout.readline()
        while line and not line.strip().startswith("{"):
            line = proc.stdout.readline()
        self.assertTrue(line, "子进程没有输出结论")
        return json.loads(line.strip())["got"]


class PurityTest(unittest.TestCase):
    """判据模块不许碰 OS：那样它就能在没数据库、没 Django 的机器上跑"""

    def test_quote_schedule不引入任何OS锁(self):
        source = (SERVER_DIR / "apps" / "market" / "quote_schedule.py").read_text(encoding="utf-8")
        for token in ("fcntl", "msvcrt", "os.open(", "tempfile"):
            with self.subTest(token=token):
                self.assertNotIn(token, source)

    def test_抢锁的调用点只有一处(self):
        # 「这一台机器上谁去抓」只能有一份实现，两份就一定会漂。
        # 找的是**调用**（`quote_lock.try_acquire`），定义本身不算。
        hits = sorted(
            str(path.relative_to(SERVER_DIR)).replace("\\", "/")
            for path in (SERVER_DIR / "apps").rglob("*.py")
            if "tests" not in path.relative_to(SERVER_DIR / "apps").parts
            and "quote_lock.try_acquire(" in path.read_text(encoding="utf-8")
        )
        self.assertEqual(hits, ["apps/market/scheduler.py"])


if __name__ == "__main__":
    unittest.main()
