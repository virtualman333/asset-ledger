from django.apps import AppConfig


class CoreConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.core"
    verbose_name = "通用"

    def ready(self):
        # 这一句 import **只有一个目的：让时间戳取在正确的那一刻**。
        # `source_stamp.PROCESS_STARTED_AT` 记的是「模块被 import 的瞬间」，而 ready() 是
        # `django.setup()` 的一部分 —— 等于进程刚开始跑。放到视图里 import 就晚了，那会变成
        # 「第一个请求」：启动之后被改过的源码会被算进 `newest_mtime` 之下，
        # `/health/` 于是把「对面跑的是旧代码」判成「没问题」，而后者正是它要防的事。
        from . import source_stamp  # noqa: F401  只为求值时机，不在这里用它
