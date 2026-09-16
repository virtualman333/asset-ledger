# -*- coding: utf-8 -*-
"""把定时抓行情接进 Django 进程。

判据全在 `quote_schedule.py`（纯函数、可单测），进程间互斥在 `quote_lock.py`
（标准库、同样可单测）。这里只做四件事：读设置、按判据决定起不起、
抢一把「这一台机器上只有我能抓」的锁、起了之后记一条日志。

`refresh_quotes` 是**延迟导入**的：`AppConfig.ready()` 跑的时候 app registry
还没完全就绪，在模块顶层 import 模型会踩到「模型尚未注册」。
"""
from __future__ import annotations

import logging
import os
import sys
import tempfile

from django.conf import settings

from . import quote_lock
from .quote_schedule import build_scheduler, should_autostart

logger = logging.getLogger(__name__)

_scheduler = None
_lock = None  # 跨进程锁（quote_lock.QuoteLock）


def refresh_quotes_job():
    """定时任务真正干的事：抓一轮行情并落库。

    日志里带 HTTP 次数与批数：这是「一批只请求一次」有没有生效的唯一现场证据，
    也是 README 那句「轮询太快会被源限流」能不能被看出来的地方。
    """
    from .services import refresh_quotes

    stats = refresh_quotes()
    logger.info(
        "行情刷新：成功 %s、缓存内跳过 %s、失败 %s（HTTP %s 次，分 %s 批）",
        stats["fetched"],
        stats["skipped"],
        stats["failed"],
        stats["requests"],
        stats["batches"],
    )
    return stats


def _lock_path() -> str:
    """锁文件位置。设置里没给就退回临时目录（有一把就行，不必在仓库里）。"""
    configured = getattr(settings, "QUOTE_REFRESH_LOCK", "") or ""
    if configured:
        return str(configured)
    return os.path.join(tempfile.gettempdir(), "asset-ledger.quote_refresh.lock")


def autostart(factory=None) -> bool:
    """按判据决定是否启动后台抓取。返回是否真的启动了。

    `factory` 可注入假的调度器（测试用），会一路传给 `build_scheduler`。
    """
    global _scheduler, _lock

    minutes = getattr(settings, "QUOTE_REFRESH_MINUTES", 0)
    if not should_autostart(sys.argv, os.environ.get("RUN_MAIN"), minutes, already_started=_scheduler is not None):
        return False

    # ★ 顺序不能反：**先过判据，再抢锁**。
    # autoreload 的看门狗父进程也会走到这里，它被上面那条判据挡掉、根本不会碰锁；
    # 如果先抢锁再判据，父进程会把锁拿走却不干活，真正干活的子进程永远抢不到 —— 那就
    # 从「每轮抓两遍」变成「一遍都不抓」，比原来的毛病更坏。
    path = _lock_path()
    lock = quote_lock.try_acquire(path)
    if lock is None:
        # 多 worker 部署（gunicorn -w N）时 N 个 worker 都会走到这里，只有第一个
        # 拿到锁。这条日志是「这个 worker 为什么没起」的唯一线索。
        logger.info("行情定时任务没起：%s 上已有另一个进程在抓（多 worker 只会有一个干活）", path)
        return False
    _lock = lock

    try:
        _scheduler = build_scheduler(minutes, refresh_quotes_job, factory=factory)
        _scheduler.start()
    except Exception as exc:  # APScheduler 缺失/起不来都不该拖垮整个服务
        _scheduler = None
        _lock.release()
        _lock = None
        logger.warning("行情定时任务没起来（%s 分钟一轮）：%s", minutes, exc)
        return False

    logger.info("行情定时任务已启动：每 %s 分钟抓一轮（锁 %s）", minutes, path)
    return True


def stop() -> None:
    """（测试/关闭用）停掉调度器并放锁。"""
    global _scheduler, _lock
    if _scheduler is not None:
        _scheduler.shutdown(wait=False)
        _scheduler = None
    if _lock is not None:
        _lock.release()
        _lock = None
