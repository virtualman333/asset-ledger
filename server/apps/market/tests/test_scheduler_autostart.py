# -*- coding: utf-8 -*-
"""`scheduler.autostart` 的接线行为 —— 不出网、不起线程、不需要数据库。

`test_quote_schedule.py` 里那几条是**读源码**的结构锁，只能证明「那些字还在」。
这里换成真的把 `autostart()` 调起来，把那把跨进程锁换成假的，然后看行为：

  - 判据不过（`manage.py migrate`）时**连锁都不许碰** —— 这条同时就是「顺序不能反」
    的行为版：反过来的话看门狗的父进程会先抢走锁再放弃起任务，真正干活的子进程
    永远抢不到，从「每轮抓两遍」变成「一遍都不抓」。
  - 抢不到锁就必须直接放弃，不能照常把调度器起起来（多 worker 部署就靠这个）。
  - 调度器起不来、以及 `stop()` 时都必须放锁，否则这个进程死了别人也拿不到。

只用到 `django.conf.settings.configure`：不碰 app registry、不连库。
本机没装 Django 时**整条跳过**，不报 ERROR —— README 明确承诺过
`python -m unittest discover -s apps -t .` 这套测试不需要 Django。
"""
import sys
import unittest
from unittest import mock

try:
    from django.conf import settings as dj_settings
except ImportError:  # 没装 Django 就跳过这一条，别让整轮测试变成 FAILED
    raise unittest.SkipTest(
        "本机没装 Django：这一条测的是 scheduler.autostart 的接线行为，"
        "它要 django.conf.settings.configure。装上 requirements 之后会真的跑；"
        "其余模块都不需要 Django。"
    ) from None

if not dj_settings.configured:
    dj_settings.configure(QUOTE_REFRESH_MINUTES=15, QUOTE_REFRESH_LOCK="/nonexistent/x.lock")

from apps.market import quote_lock, scheduler  # noqa: E402

SERVER_ARGV = ["gunicorn", "config.wsgi:application"]
MANAGE_ARGV = ["manage.py", "migrate"]


class FakeScheduler:
    def __init__(self):
        self.started = False
        self.jobs = []

    def add_job(self, job, trigger, **kwargs):
        self.jobs.append({"job": job, "kwargs": kwargs})

    def start(self):
        self.started = True

    def shutdown(self, wait=True):
        self.started = False


class BrokenScheduler(FakeScheduler):
    def start(self):
        raise RuntimeError("调度器起不来")


class FakeLock:
    path = "/nonexistent/x.lock"

    def __init__(self):
        self.released = False

    @property
    def pid(self):
        return 4321

    def release(self):
        self.released = True


class AutostartTest(unittest.TestCase):
    def setUp(self):
        self._argv = list(sys.argv)
        sys.argv = list(SERVER_ARGV)
        scheduler._scheduler = None
        scheduler._lock = None
        self.addCleanup(self._cleanup)

    def _cleanup(self):
        sys.argv = self._argv
        scheduler._scheduler = None
        scheduler._lock = None

    def test_判据不过时连锁都不许碰(self):
        """顺序：先判据、后抢锁。反了就会让看门狗把锁抢走、干活的进程起不来。"""
        sys.argv = list(MANAGE_ARGV)
        with mock.patch.object(quote_lock, "try_acquire") as acquire:
            started = scheduler.autostart(factory=FakeScheduler)
        self.assertFalse(started)
        acquire.assert_not_called()

    def test_抢不到锁就不起任务(self):
        lock = FakeLock()
        with mock.patch.object(quote_lock, "try_acquire", return_value=None):
            started = scheduler.autostart(factory=FakeScheduler)
        self.assertFalse(started, "多 worker 部署里抢不到锁的那个必须先退，不能各起一个")
        self.assertIsNone(scheduler._scheduler)
        self.assertIsNone(scheduler._lock)

    def test_拿到锁才起任务(self):
        lock = FakeLock()
        with mock.patch.object(quote_lock, "try_acquire", return_value=lock) as acquire:
            started = scheduler.autostart(factory=FakeScheduler)
        self.assertTrue(started)
        self.assertTrue(scheduler._scheduler.started)
        self.assertEqual(len(scheduler._scheduler.jobs), 1)
        acquire.assert_called_once()

    def test_锁的位置用设置里的那个(self):
        lock = FakeLock()
        with mock.patch.object(quote_lock, "try_acquire", return_value=lock) as acquire:
            scheduler.autostart(factory=FakeScheduler)
        self.assertEqual(acquire.call_args[0][0], "/nonexistent/x.lock")

    def test_调度器起不来要把锁放掉(self):
        lock = FakeLock()
        with mock.patch.object(quote_lock, "try_acquire", return_value=lock):
            started = scheduler.autostart(factory=BrokenScheduler)
        self.assertFalse(started)
        self.assertTrue(lock.released, "不放锁 = 这个进程之后再也没人能抓行情")
        self.assertIsNone(scheduler._lock)

    def test_stop也会放锁(self):
        lock = FakeLock()
        with mock.patch.object(quote_lock, "try_acquire", return_value=lock):
            scheduler.autostart(factory=FakeScheduler)
        scheduler.stop()
        self.assertTrue(lock.released)
        self.assertIsNone(scheduler._scheduler)
        self.assertIsNone(scheduler._lock)

    def test_同一个进程里第二次调用不再起第二个(self):
        lock = FakeLock()
        with mock.patch.object(quote_lock, "try_acquire", return_value=lock) as acquire:
            self.assertTrue(scheduler.autostart(factory=FakeScheduler))
            self.assertFalse(scheduler.autostart(factory=FakeScheduler))
        self.assertEqual(acquire.call_count, 1)


if __name__ == "__main__":
    unittest.main()
