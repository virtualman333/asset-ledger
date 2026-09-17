from zoneinfo import ZoneInfo

from django.conf import settings
from django.http import StreamingHttpResponse
from rest_framework import viewsets
from rest_framework.exceptions import ValidationError
from rest_framework.views import APIView

from apps.core.models import TxSide, TxSource

from .date_range import DateRangeError, range_bounds
from .export_rules import (
    CSV_COLUMNS,
    content_disposition,
    export_names,
    render_rows,
    transaction_row,
)
from .models import DividendRecord, Transaction
from .serializers import DividendRecordSerializer, TransactionSerializer

#: 导出用的中文标签，直接取自模型自己的 `TextChoices.choices` ——
#: 这里**不抄第二份对照表**：模型新增一种 side / source，导出会自动跟上，
#: 不存在「加了枚举忘了加标签」这条漂移路径。
SIDE_LABELS = dict(TxSide.choices)
SOURCE_LABELS = dict(TxSource.choices)


class TransactionViewSet(viewsets.ModelViewSet):
    serializer_class = TransactionSerializer
    filterset_fields = ("account", "asset", "side", "currency", "source")
    search_fields = ("note", "asset__symbol", "asset__name")
    ordering_fields = ("traded_at", "amount", "created_at")

    def get_queryset(self):
        qs = Transaction.objects.filter(user=self.request.user).select_related("asset", "account")
        params = self.request.query_params
        try:
            start, end = range_bounds(
                params.get("from"), params.get("to"), ZoneInfo(settings.TIME_ZONE)
            )
        except DateRangeError as exc:
            # 日期写错回 400 并把话说清楚。以前这里是 `traded_at__date__gte=<原样字符串>`：
            # 写错格式当场 500，而**格式写对时在没装时区表的 MySQL 上恒返回 0 条**
            # （见 `date_range.py` 的说明）—— 两条路都不好，这一条至少说得清。
            raise ValidationError(str(exc)) from exc
        if start is not None:
            qs = qs.filter(traded_at__gte=start)
        if end is not None:
            qs = qs.filter(traded_at__lt=end)
        return qs


class TransactionExportView(APIView):
    """`GET /transactions/records/export/`：把流水导成 Excel 能直接打开的 CSV。

    筛选条件与 `/transactions/records/` **完全一致** —— 不是把 `get_queryset` 里那两行
    from/to 抄一遍，而是直接复用 ViewSet 的 `get_queryset()` + `filter_queryset()`，
    于是 filter / search / ordering 三个后端一个不落地都在这儿生效。
    抄一遍迟早会漂，而漂了之后两处返回的行不一样：列表里看到 12 笔、导出只有 9 笔，
    用户只会以为「少记了几笔」，不会想到是导出的筛选没跟上。

    为什么流式吐：账本条数不该决定内存，也不能因为「行数太多」就静默截断 ——
    截掉一部分的账目比慢一点严重得多。
    """

    def get(self, request):
        viewset = TransactionViewSet()
        viewset.request = request
        viewset.format_kwarg = None
        queryset = viewset.filter_queryset(viewset.get_queryset())

        tz = ZoneInfo(settings.TIME_ZONE)
        rows = (
            transaction_row(tx, SIDE_LABELS, SOURCE_LABELS, CSV_COLUMNS, tz=tz)
            for tx in queryset.iterator()
        )

        ascii_name, unicode_name = export_names()
        response = StreamingHttpResponse(
            render_rows(rows, CSV_COLUMNS), content_type="text/csv; charset=utf-8"
        )
        response["Content-Disposition"] = content_disposition(ascii_name, unicode_name)
        return response


class DividendRecordViewSet(viewsets.ModelViewSet):
    serializer_class = DividendRecordSerializer
    filterset_fields = ("asset", "account", "currency", "reinvested")
    search_fields = ("asset__symbol", "asset__name")
    ordering_fields = ("pay_date", "ex_date", "net")

    def get_queryset(self):
        qs = DividendRecord.objects.filter(user=self.request.user).select_related("asset", "account")
        year = self.request.query_params.get("year")
        if year:
            qs = qs.filter(pay_date__year=year)
        return qs
