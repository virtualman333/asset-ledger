# -*- coding: utf-8 -*-
"""抓一轮行情并落库。

cron / 宝塔计划任务 / systemd timer 都能直接调它 —— 不想让后端进程内挂
APScheduler 的人，用这条命令 + 系统定时器就够了：

    python manage.py refresh_quotes
    python manage.py refresh_quotes --force            # 忽略缓存，强制重抓
    python manage.py refresh_quotes --batch-size 1     # 逐只请求（排查批量接口用）

一轮里 A 股 / 港股 / 美股会**合成一条请求**（每 `--batch-size` 个代码一条），
其余市场逐只走各自的数据源 —— 免费源有限流，见 README 的已知约束。
"""
from django.core.management.base import BaseCommand

from apps.market.services import refresh_quotes
from apps.market.tencent import TENCENT_MAX_CODES


class Command(BaseCommand):
    help = "抓一轮行情快照（默认跳过缓存期内的标的）"

    def add_arguments(self, parser):
        parser.add_argument("--force", action="store_true", help="忽略缓存期，强制重抓")
        parser.add_argument(
            "--batch-size",
            type=int,
            default=None,
            help=f"一条请求最多带几个代码（默认 {TENCENT_MAX_CODES}）；填 1 即逐只请求",
        )

    def handle(self, *args, **options):
        stats = refresh_quotes(force=options["force"], batch_size=options["batch_size"])
        self.stdout.write(
            f"行情刷新：成功 {stats['fetched']}、缓存内跳过 {stats['skipped']}、"
            f"失败 {stats['failed']}（HTTP {stats['requests']} 次，分 {stats['batches']} 批）"
        )
