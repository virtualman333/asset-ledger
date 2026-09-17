from django.urls import path
from rest_framework.routers import DefaultRouter

from .views import DividendRecordViewSet, TransactionExportView, TransactionViewSet

router = DefaultRouter()
router.register("records", TransactionViewSet, basename="transaction")
router.register("dividends", DividendRecordViewSet, basename="dividend")

app_name = "transactions"

#: ⚠ `records/export/` **必须排在 `router.urls` 前面**。
#:
#: DRF 给 ViewSet 生成的明细路由是 `records/(?P<pk>[^/.]+)/`，而 `export` 完全符合那个
#: `pk` 的形状 —— 排到后面就会被它吃掉：拿字符串 `"export"` 去查整型主键，当场 500，
#: 而不是 404。这一条有测试盯着（`test_export_contract.py` 断言声明顺序），
#: 因为「换个位置就坏」是读代码看不出来的。
urlpatterns = [
    path("records/export/", TransactionExportView.as_view(), name="transaction-export"),
    *router.urls,
]
