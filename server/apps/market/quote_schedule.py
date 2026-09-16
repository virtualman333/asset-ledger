# -*- coding: utf-8 -*-
"""定时抓行情「该不该启动」的判据 —— 纯函数，零 Django、零 APScheduler 导入。

README 从第一版就写着「**后端定时抓行情，持仓浮盈自动更新**」、技术栈里写着
「APScheduler 调度」，`requirements.txt` 也装了 APScheduler。但在此之前，
全仓库 `import apscheduler` 的次数是 **0**。

会写行情快照（`PriceQuote`）的路只有两条，都不持续：

  - `GET /market/quotes/` —— 客户端从来没调过它（`harmony/` 下搜不到 `quotes`），
    实际只有手工调试和冒烟脚本用过；
  - `seed_demo` —— 一次性，种完就完了。

所以行情只在「有人手动点一下」的时候更新。实测：库里最后一批快照是
`seed_demo` 在 2026-09-16 16:55 一次写了 4 条（同一秒），此后 8 小时一格没动；
而没跑过 `seed_demo` 的账号连一条都没有 —— 现价恒为空、总资产恒 `0.00`，
整条链路一声不吭。

这里补上那个任务，并把它**什么时候该起来**做成纯函数，好在没有数据库、
没有 Django 的机器上直接把判据压全（这正是当年漏掉它的原因：判据没有落点，
所以没人写过第二遍）。
"""
from __future__ import annotations

import os

DEFAULT_JOB_ID = "market.refresh_quotes"

# 需要「服务进程」而不是一次性命令的节目
MANAGE_PROGRAMS = ("manage.py", "django-admin", "django-admin.py")
SERVER_SUBCOMMANDS = ("runserver",)


def should_autostart(argv, run_main, minutes, *, already_started: bool = False) -> bool:
    """这一轮该不该启动抓取任务。

    `argv` 传 `sys.argv`，`run_main` 传 `os.environ.get("RUN_MAIN")`，
    `minutes` 传设置里的间隔分钟数（<=0 或非数字视为关闭）。

    三条判据，每一条都对应一种真实的坏结果：

    1. **间隔 <= 0 就不起** —— 不想要后台线程的人必须有一条干净的退路。
    2. **只有服务进程才起** —— `migrate` / `shell` / `refresh_quotes` 这些
       一次性命令起线程毫无意义，还会让命令挂住不退出。
    3. **autoreload 只让子进程起** —— `runserver` 默认开 autoreload，
       父进程是看门狗、子进程干活，两个都起就是每轮抓两遍。
       注意判据不能写成「必须有 RUN_MAIN」：`--noreload` 只有单个进程、
       也没有 RUN_MAIN，那样会漏掉整个 `runserver --noreload`。
    """
    if already_started:
        return False

    try:
        minutes = int(minutes or 0)
    except (TypeError, ValueError):
        return False
    if minutes <= 0:
        return False

    args = list(argv or [])
    program = os.path.basename(args[0]) if args else ""
    if program in MANAGE_PROGRAMS:
        subcommand = args[1] if len(args) > 1 else ""
        if subcommand not in SERVER_SUBCOMMANDS:
            return False
        if "--noreload" not in args and run_main != "true":
            return False
    # argv 里没有 manage.py（gunicorn / uvicorn / wsgi / asgi）：就是服务进程
    return True


def build_scheduler(minutes, job, *, factory=None, job_id: str = DEFAULT_JOB_ID):
    """装一个「每 minutes 分钟跑一次 job」的后台调度器（还没 start()）。

    `factory` 可注入，测试里传假的就能断言注册参数，不用真的起线程。
    几个参数各自挡一种坏结果：
      - `max_instances=1`：上一轮还没跑完就不许再进来（免费行情源很慢）
      - `coalesce=True`：进程睡久了积压的火次合并成一次，不补跑一堆
    """
    if factory is None:
        from apscheduler.schedulers.background import BackgroundScheduler

        factory = BackgroundScheduler
    scheduler = factory()
    scheduler.add_job(
        job,
        "interval",
        minutes=int(minutes),
        id=job_id,
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )
    return scheduler
