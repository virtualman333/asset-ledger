from django.http import StreamingHttpResponse
from django.utils import timezone
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.models import Account
from apps.assets.models import Asset
from apps.transactions.models import DividendRecord

from .dividend_export import (
    DIVIDEND_CSV_COLUMNS,
    content_disposition,
    dividend_row,
    export_names,
    render_rows,
)
from .services import (
    YearParamError,
    build_positions,
    build_summary,
    dividend_entries,
    dividend_monthly,
    parse_year,
)


def _year_param(request, default=None):
    """读 `?year=`：写错回 **400** 并把话说清楚，不是 500。

    解析只有 `services.parse_year` 一处 —— `/analytics/dividends/` 与导出接口
    读的是同一个参数、判的是同一件事（「这一年的股息」），两处各写一遍迟早会
    出现「图表按 2026 筛、导出按别的东西筛」。这里只负责把它的异常翻成 HTTP 语义，
    与带 `from`/`to` 的流水列表同一个套路（`transactions/date_range.py`）。
    """
    try:
        return parse_year(request.query_params.get("year"), default)
    except YearParamError as exc:
        raise ValidationError(str(exc)) from exc


class PositionsView(APIView):
    def get(self, request):
        return Response({"results": build_positions(request.user)})


class SummaryView(APIView):
    def get(self, request):
        base = request.query_params.get("base")
        return Response(build_summary(request.user, base))


class DividendAnalyticsView(APIView):
    def get(self, request):
        year = _year_param(request, default=timezone.localdate().year)
        return Response({"year": year, "months": dividend_monthly(request.user, year)})


class DividendExportView(APIView):
    """`GET /analytics/dividends/export/`：把股息导成 Excel 能直接打开的 CSV。

    **行来源是 `dividend_entries()`（归集后的股息），不是 `DividendRecord` 列表。**
    理由写在 `dividend_export` 的模块说明里：股息有两条录入路径，「只落流水」那条
    （`/transactions/records/` side=DIVIDEND）在 `DividendRecord` 里一行都没有，
    照明细列表导会整条漏掉 —— 而页面上的「累计股息」是算进去的。两边都不报错，
    只是数字对不上，而用户恰恰是拿这两个数字互相对账的。所以这里与「累计股息」
    共用同一个函数，页面有多少就是文件里有多少。

    `?year=2026` 与 `/analytics/dividends/` 同口径（按派息日）；不传就是全部年份。
    给了 year 时，派息日缺失的条目不计入 —— 与月度分布图一致（「日期未知」与
    「日期不在这一年」是两件事，混起来会让口径悄悄变宽）。

    为什么流式吐：账本条数不该决定内存，也不能因为「行数太多」就静默截断 ——
    截掉一部分的账目比慢一点严重得多。
    """

    def get(self, request):
        year = _year_param(request)

        entries = dividend_entries(request.user)
        if year is not None:
            entries = [e for e in entries if e.pay_date is not None and e.pay_date.year == year]

        # 只取这份文件里真正用到的 id：标的库是**全局**的（`AssetViewSet` 不按用户隔离），
        # 全表拉进来没有必要；账户按用户隔离，必须带 user 条件 —— 漏了那个条件就导出了
        # 别人的账户名，而且不会有任何报错。
        assets = {
            a.id: (a.symbol, a.name)
            for a in Asset.objects.filter(
                id__in={e.asset_id for e in entries if e.asset_id is not None}
            )
        }
        accounts = {
            a.id: a.name
            for a in Account.objects.filter(
                user=request.user,
                id__in={e.account_id for e in entries if e.account_id is not None},
            )
        }

        rows = (dividend_row(entry, assets, accounts, DIVIDEND_CSV_COLUMNS) for entry in entries)

        ascii_name, unicode_name = export_names()
        response = StreamingHttpResponse(
            render_rows(rows, DIVIDEND_CSV_COLUMNS), content_type="text/csv; charset=utf-8"
        )
        response["Content-Disposition"] = content_disposition(ascii_name, unicode_name)
        return response


class CalendarView(APIView):
    """股息日历：除权日/派息日提醒。"""

    def get(self, request):
        rows = (
            DividendRecord.objects.filter(user=request.user)
            .select_related("asset")
            .order_by("pay_date")
        )
        data = [
            {
                "id": row.id,
                "asset": row.asset.symbol,
                "asset_name": row.asset.name,
                "ex_date": row.ex_date.isoformat() if row.ex_date else None,
                "pay_date": row.pay_date.isoformat() if row.pay_date else None,
                "net": str(row.net),
                "gross": str(row.gross),
                "currency": row.currency,
                "reinvested": row.reinvested,
            }
            for row in rows
        ]
        return Response({"results": data})
