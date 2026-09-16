from django.apps import AppConfig


class MarketConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.market"
    verbose_name = "行情与汇率"

    def ready(self):
        """接上定时抓行情。

        README 第一行卖点就写着「后端定时抓行情，持仓浮盈自动更新」，
        但在此之前没有任何**持续**的代码会去写 PriceQuote —— 这条 `ready()`
        就是那句话的落点。判据（要不要起、什么时候起）全在
        `quote_schedule.should_autostart`，那里有单测。
        """
        from . import scheduler

        scheduler.autostart()
