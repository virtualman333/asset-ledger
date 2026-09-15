from django.urls import path

from .views import CalendarView, DividendAnalyticsView, PositionsView, SummaryView

app_name = "analytics"

urlpatterns = [
    path("positions/", PositionsView.as_view(), name="positions"),
    path("summary/", SummaryView.as_view(), name="summary"),
    path("dividends/", DividendAnalyticsView.as_view(), name="dividends"),
    path("calendar/", CalendarView.as_view(), name="calendar"),
]
