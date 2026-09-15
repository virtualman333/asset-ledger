"""根路由。"""
from django.contrib import admin
from django.urls import include, path
from django.conf import settings
from django.conf.urls.static import static

urlpatterns = [
    path("admin/", admin.site.urls),
    path("api/v1/auth/", include("apps.users.urls")),
    path("api/v1/accounts/", include("apps.accounts.urls")),
    path("api/v1/assets/", include("apps.assets.urls")),
    path("api/v1/transactions/", include("apps.transactions.urls")),
    path("api/v1/market/", include("apps.market.urls")),
    path("api/v1/ingest/", include("apps.ingest.urls")),
    path("api/v1/analytics/", include("apps.analytics.urls")),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
