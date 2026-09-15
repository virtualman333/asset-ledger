from django.urls import path

from .views import FxView, QuoteView

app_name = "market"

urlpatterns = [
    path("quotes/", QuoteView.as_view(), name="quotes"),
    path("fx/", FxView.as_view(), name="fx"),
]
