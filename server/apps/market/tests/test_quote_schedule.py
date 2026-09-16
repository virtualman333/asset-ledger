# -*- coding: utf-8 -*-
"""定时抓行情的判据与接线（不需要数据库、不需要 Django、不需要 APScheduler）。

背景：README 第一行卖点写着「后端定时抓行情，持仓浮盈自动更新」、
技术栈写着「APScheduler 调度」、`requirements.txt` 也装了 APScheduler，
但在这次改动之前，**全仓库 import apscheduler 的次数是 0**。

会写行情快照的路只有两条，都不持续：`GET /market/quotes/`（客户端从不调，
`harmony/` 下搜不到 `quotes`，只有手工调试与冒烟脚本用过）与一次性的
`seed_demo`。实测库里最后一批快照就是 `seed_demo` 在 2026-09-16 16:55
同一秒写下的 4 条，此后 8 小时一格没动；没跑过演示数据的账号则连一条都没有
—— 现价恒为空、总资产恒 0.00。

所以这里压三样东西：
  1. `should_autostart` 的每一条判据（尤其是 autoreload 双进程那个坑）
  2. 定时任务注册成什么样（`max_instances=1` / `coalesce`，用假调度器断言）
  3. 接线本身没被拆掉（结构锁：只有一处写 PriceQuote、ready() 真的接了）
"""
import os
from pathlib import Path
import unittest

from apps.market.quote_schedule import build_scheduler, should_autostart

APP_DIR = Path(__file__).resolve().parent.parent  # server/apps/market
APPS_DIR = APP_DIR.parent  # server/apps
SERVER_DIR = APPS_DIR.parent  # server

MANAGE = os.path.join("somewhere", "manage.py")


class FakeScheduler:
    """只记录注册了什么，不起线程。"""

    def __init__(self):
        self.jobs = []
        self.started = False

    def add_job(self, job, trigger, **kwargs):
        self.jobs.append({"job": job, "trigger": trigger, "kwargs": kwargs})

    def start(self):
        self.started = True


class ShouldAutostartTest(unittest.TestCase):
    """该不该起后台抓取"""

    def test_间隔为0或非法一律不起(self):
        server_argv = ["gunicorn", "config.wsgi:application"]
        self.assertFalse(should_autostart(server_argv, None, 0))
        self.assertFalse(should_autostart(server_argv, None, "0"))
        self.assertFalse(should_autostart(server_argv, None, -5))
        self.assertFalse(should_autostart(server_argv, None, None))
        self.assertFalse(should_autostart(server_argv, None, "abc"))

    def test_已经起过就不再起(self):
        # AppConfig.ready() 在同一个进程里可能被调用多次
        self.assertFalse(should_autostart(["gunicorn"], None, 15, already_started=True))

    def test_服务进程会起(self):
        self.assertTrue(should_autostart(["gunicorn", "config.wsgi:application"], None, 15))
        self.assertTrue(should_autostart(["uvicorn", "config.asgi:application"], None, 15))
        self.assertTrue(should_autostart([], None, 15))

    def test_一次性管理命令不起线程(self):
        # migrate / shell / 以及抓取命令自己都不该在进程里留个后台线程
        for sub in ("migrate", "shell", "refresh_quotes", "test", "makemigrations", "seed_demo"):
            with self.subTest(sub=sub):
                self.assertFalse(should_autostart([MANAGE, sub], None, 15))

    def test_没有子命令的manage_py不起(self):
        self.assertFalse(should_autostart([MANAGE], None, 15))

    def test_autoreload的父进程不起子进程起(self):
        # runserver 默认开 autoreload：父进程是看门狗、子进程干活。
        # 两个都起就是每轮抓两遍。
        argv = [MANAGE, "runserver"]
        self.assertFalse(should_autostart(argv, None, 15))  # 父进程（没有 RUN_MAIN）
        self.assertTrue(should_autostart(argv, "true", 15))  # 子进程

    def test_noreload时只有单个进程也要起(self):
        # ★ 判据不能写成「必须有 RUN_MAIN」：--noreload 只有一个进程、
        # 也没有 RUN_MAIN，那样会漏掉整个 runserver --noreload。
        argv = [MANAGE, "runserver", "--noreload"]
        self.assertTrue(should_autostart(argv, None, 15))
        self.assertTrue(should_autostart(argv, False, 15))

    def test_django_admin也按manage_py处理(self):
        self.assertFalse(should_autostart(["django-admin", "migrate"], None, 15))
        self.assertTrue(should_autostart(["django-admin", "runserver", "--noreload"], None, 15))


class BuildSchedulerTest(unittest.TestCase):
    """任务注册成什么样"""

    def test_按间隔分钟注册一个任务(self):
        def job():
            return None

        scheduler = build_scheduler(15, job, factory=FakeScheduler)
        self.assertEqual(len(scheduler.jobs), 1)
        entry = scheduler.jobs[0]
        self.assertIs(entry["job"], job)
        self.assertEqual(entry["trigger"], "interval")
        self.assertEqual(entry["kwargs"]["minutes"], 15)
        self.assertEqual(entry["kwargs"]["id"], "market.refresh_quotes")

    def test_上一轮没跑完不许再进来_积压的火次合并(self):
        # 免费行情源很慢；不设这两个参数就会叠着跑、进程睡醒后补跑一堆
        scheduler = build_scheduler(15, lambda: None, factory=FakeScheduler)
        kwargs = scheduler.jobs[0]["kwargs"]
        self.assertEqual(kwargs["max_instances"], 1)
        self.assertTrue(kwargs["coalesce"])
        self.assertTrue(kwargs["replace_existing"])

    def test_字符串间隔也能注册(self):
        scheduler = build_scheduler("30", lambda: None, factory=FakeScheduler)
        self.assertEqual(scheduler.jobs[0]["kwargs"]["minutes"], 30)


class WiringTest(unittest.TestCase):
    """接线本身没被拆掉 —— 这几条锁对应的都是「承诺了但没实现」的历史"""

    def test_写行情快照的地方只有一处(self):
        # 同一件事有两份实现就一定会漂（这个仓库已经栽过好几回）。
        # 之前是两处：market/views.py 与 core/management/commands/seed_demo.py。
        # 测试自己可能为了造数据写快照，不算「实现」，所以跳过 tests/。
        hits = sorted(
            str(path.relative_to(APPS_DIR)).replace("\\", "/")
            for path in APPS_DIR.rglob("*.py")
            if "tests" not in path.relative_to(APPS_DIR).parts
            and "PriceQuote.objects.create(" in path.read_text(encoding="utf-8")
        )
        self.assertEqual(hits, ["market/services.py"])

    def test_接口不再自己算缓存判据(self):
        source = (APP_DIR / "views.py").read_text(encoding="utf-8")
        self.assertIn("refresh_quote(", source)
        self.assertNotIn("QUOTE_CACHE_SECONDS", source)

    def test_ready里真的接了定时任务(self):
        # README 那句「后端定时抓行情」的落点就是这里，拆掉就退回原状
        source = (APP_DIR / "apps.py").read_text(encoding="utf-8")
        self.assertIn("def ready(", source)
        self.assertIn("scheduler.autostart()", source)

    def test_抓取命令复用同一份实现(self):
        command = APP_DIR / "management" / "commands" / "refresh_quotes.py"
        self.assertTrue(command.exists(), "README 承诺的定时抓取需要一个可被 cron 调用的命令")
        source = command.read_text(encoding="utf-8")
        self.assertIn("from apps.market.services import refresh_quotes", source)
        # 命令自己再写一遍抓取循环 = 又一份实现
        self.assertNotIn("fetch_quote(", source)

    def test_设置里有这个开关且默认开着(self):
        # 默认关掉等于 README 那句话继续不成立
        source = (SERVER_DIR / "config" / "settings.py").read_text(encoding="utf-8")
        self.assertIn("QUOTE_REFRESH_MINUTES", source)
        self.assertIn('QUOTE_REFRESH_MINUTES = int(os.getenv("QUOTE_REFRESH_MINUTES", "15"))', source)


if __name__ == "__main__":
    unittest.main()
