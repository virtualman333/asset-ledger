from django.urls import path

from .views import (
    CalendarView,
    DividendAnalyticsView,
    DividendExportView,
    PositionsExportView,
    PositionsView,
    SummaryView,
)

app_name = "analytics"

#: 这里全是 `path()`，**没有 ViewSet 生成的 `(?P<pk>…)` 明细路由**，所以
#: `dividends/` 与 `dividends/export/` 谁在前都一样 —— `path()` 整串匹配，
#: `dividends/` 匹配不上 `dividends/export/`。
#: （对比 `transactions/urls.py`：那边 `records/export/` 必须排在 `router.urls` 前面，
#: 否则会被 `records/(?P<pk>[^/.]+)/` 当成主键吃掉。两处的约束不同，别照搬。）
urlpatterns = [
    path("positions/", PositionsView.as_view(), name="positions"),
    path("positions/export/", PositionsExportView.as_view(), name="positions-export"),
    path("summary/", SummaryView.as_view(), name="summary"),
    path("dividends/", DividendAnalyticsView.as_view(), name="dividends"),
    path("dividends/export/", DividendExportView.as_view(), name="dividend-export"),
    path("calendar/", CalendarView.as_view(), name="calendar"),
]
