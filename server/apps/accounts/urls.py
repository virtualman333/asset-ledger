from rest_framework.routers import DefaultRouter

from .views import AccountViewSet

router = DefaultRouter()
router.register("", AccountViewSet, basename="account")

app_name = "accounts"
urlpatterns = router.urls
