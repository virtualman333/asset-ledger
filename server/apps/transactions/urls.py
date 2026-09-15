from rest_framework.routers import DefaultRouter

from .views import DividendRecordViewSet, TransactionViewSet

router = DefaultRouter()
router.register("records", TransactionViewSet, basename="transaction")
router.register("dividends", DividendRecordViewSet, basename="dividend")

app_name = "transactions"
urlpatterns = router.urls
