# -*- coding: utf-8 -*-
"""抓一轮行情并落库。

cron / 宝塔计划任务 / systemd timer 都能直接调它 —— 不想让后端进程内挂
APScheduler 的人，用这条命令 + 系统定时器就够了：

    python manage.py refresh_quotes
    python manage.py refresh_quotes --force     # 忽略缓存，强制重抓
"""
from django.core.management.base import BaseCommand

from apps.market.services import refresh_quotes


class Command(BaseCommand):
    help = "抓一轮行情快照（默认跳过缓存期内的标的）"

    def add_arguments(self, parser):
        parser.add_argument("--force", action="store_true", help="忽略缓存期，强制重抓")

    def handle(self, *args, **options):
        stats = refresh_quotes(force=options["force"])
        self.stdout.write(
            f"行情刷新：成功 {stats['fetched']}、缓存内跳过 {stats['skipped']}、失败 {stats['failed']}"
        )
