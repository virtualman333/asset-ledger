# -*- coding: utf-8 -*-
"""把定时抓行情接进 Django 进程。

判据全在 `quote_schedule.py`（纯函数、可单测），这里只做三件事：
读设置、按判据决定起不起、起了之后记一条日志。

`refresh_quotes` 是**延迟导入**的：`AppConfig.ready()` 跑的时候 app registry
还没完全就绪，在模块顶层 import 模型会踩到「模型尚未注册」。
"""
from __future__ import annotations

import logging
import os
import sys

from django.conf import settings

from .quote_schedule import build_scheduler, should_autostart

logger = logging.getLogger(__name__)

_scheduler = None


def refresh_quotes_job():
    """定时任务真正干的事：抓一轮行情并落库。"""
    from .services import refresh_quotes

    stats = refresh_quotes()
    logger.info(
        "行情刷新：成功 %s、缓存内跳过 %s、失败 %s", stats["fetched"], stats["skipped"], stats["failed"]
    )
    return stats


def autostart() -> bool:
    """按判据决定是否启动后台抓取。返回是否真的启动了。"""
    global _scheduler

    minutes = getattr(settings, "QUOTE_REFRESH_MINUTES", 0)
    if not should_autostart(sys.argv, os.environ.get("RUN_MAIN"), minutes, already_started=_scheduler is not None):
        return False

    try:
        _scheduler = build_scheduler(minutes, refresh_quotes_job)
        _scheduler.start()
    except Exception as exc:  # APScheduler 缺失/起不来都不该拖垮整个服务
        _scheduler = None
        logger.warning("行情定时任务没起来（%s 分钟一轮）：%s", minutes, exc)
        return False

    logger.info("行情定时任务已启动：每 %s 分钟抓一轮", minutes)
    return True


def stop() -> None:
    """（测试/关闭用）停掉调度器。"""
    global _scheduler
    if _scheduler is not None:
        _scheduler.shutdown(wait=False)
        _scheduler = None
