from rest_framework.routers import DefaultRouter

from .views import AssetViewSet

router = DefaultRouter()
router.register("", AssetViewSet, basename="asset")

app_name = "assets"
urlpatterns = router.urls
