from django.http import StreamingHttpResponse
from django.utils import timezone
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.core.models import Market

#: 持仓导出与股息导出有四个同名函数（`render_rows` / `export_names` / `content_disposition`
#: 都是同一层的薄封装）。这里按模块 import 而不是逐个取名字：两边同名，取进来就得改名，
#: 而改名之后「哪一行用的是哪一份」只能靠读定义 —— 这样刻意让它一眼可读。
from . import positions_export
from .dividend_export import (
    DIVIDEND_CSV_COLUMNS,
    content_disposition,
    dividend_row,
    export_names,
    render_rows,
)
from .dividend_calendar import build_calendar
from .services import (
    YearParamError,
    build_positions,
    build_summary,
    dividend_entries_in_year,
    dividend_monthly,
    dividend_refs,
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


#: 市场码 → 中文名。取自模型自己的 `TextChoices`，**不在这里抄第二份** ——
#: 模型新增一个市场时这张表会自己带上它。认不出的码由 `positions_cells` 原样交出去。
MARKET_LABELS = dict(Market.choices)


class PositionsView(APIView):
    def get(self, request):
        return Response({"results": build_positions(request.user)})


class PositionsExportView(APIView):
    """`GET /analytics/positions/export/`：把持仓明细导成 Excel 能直接打开的 CSV。

    **行来源与 `GET /analytics/positions/` 是同一个函数**（`services.build_positions`），
    不另开一条聚合路径。持仓受五种流水影响（买 / 卖 / 拆股 / 股息归集 / 清仓后只剩
    已实现盈亏），任何一处自己重算一遍都会在某个角落与页面上的数不一样 ——
    而用户是拿这两个数对账的。页面有多少行，文件里就有多少行。

    三格会**留空**而不是写 0：**现价 / 市值 / 浮动盈亏**在标的没有行情快照时是 `None`，
    写 `0` 等于替用户在文件里宣布「这个标的现在不值钱」「这笔一分钱没赚没亏」，
    而真相是库里连一条快照都没有。股息率同理（成本为 0 时算不出，不是 0%）。
    口径写在 `positions_export` 的模块说明里，与股息导出那三列同一条规矩。

    为什么流式吐：账本条数不该决定内存，也不能因为「行数太多」就静默截断 ——
    截掉一部分的账目比慢一点严重得多。
    """

    def get(self, request):
        positions = build_positions(request.user)

        rows = (
            positions_export.positions_row(pos, MARKET_LABELS, positions_export.POSITIONS_CSV_COLUMNS)
            for pos in positions
        )

        ascii_name, unicode_name = positions_export.export_names()
        response = StreamingHttpResponse(
            positions_export.render_rows(rows, positions_export.POSITIONS_CSV_COLUMNS),
            content_type="text/csv; charset=utf-8",
        )
        response["Content-Disposition"] = positions_export.content_disposition(
            ascii_name, unicode_name
        )
        return response


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
    筛选只有一处（`services.dividend_entries_in_year`），日历与它共用：两个出口
    各筛各的，就会出现「图表按 2026 筛、日历按别的东西筛」，而用户是拿这两个数对账的。

    为什么流式吐：账本条数不该决定内存，也不能因为「行数太多」就静默截断 ——
    截掉一部分的账目比慢一点严重得多。
    """

    def get(self, request):
        year = _year_param(request)

        entries = dividend_entries_in_year(request.user, year)
        assets, accounts = dividend_refs(request.user, entries)

        rows = (dividend_row(entry, assets, accounts, DIVIDEND_CSV_COLUMNS) for entry in entries)

        ascii_name, unicode_name = export_names()
        response = StreamingHttpResponse(
            render_rows(rows, DIVIDEND_CSV_COLUMNS), content_type="text/csv; charset=utf-8"
        )
        response["Content-Disposition"] = content_disposition(ascii_name, unicode_name)
        return response


class CalendarView(APIView):
    """`GET /analytics/calendar/`：股息日历 —— DESIGN §4.5 的「除权日/派息日提醒」。

    **数据来源是归集后的股息，不是 `DividendRecord` 列表。** 以前这里直接查明细表，
    于是「只落流水、没有明细」的那条录入路径（`/transactions/records/` side=DIVIDEND）
    在日历里**一条都不出现** —— 而同一个账户的「累计股息」把它算进去了。这是本轮真的
    跑出来的缺陷：那笔股息在导出 CSV 与总览里都在，只有日历里没有，两边都不报错。

    `?year=` 与 `/analytics/dividends/`、导出接口同口径（按派息日；不传就是全部年份）：
    解析只有一处（`services.parse_year`，写错回 400），筛选只有一处
    （`services.dividend_entries_in_year`）。

    返回三组 + 合计，三组的分法写在 `dividend_calendar` 的模块说明里：

    | 组 | 判据 |
    | --- | --- |
    | `upcoming` | 派息日在今天之后（`days_until > 0`），按日期升序 —— 「提醒」就是它 |
    | `received` | 派息日在今天当天或之前（今天派的算已到账），倒序 |
    | `undated` | 派息日为空的条目；既不进待派也不进已到账，但必须报出来 |

    `totals` 里「待派 + 已到账 + 日期未知」逐币种相加等于 `by_currency` —— 少一组能
    当场看出来。分币种而不是加总：CNY 与 USD 加在一起是个没有意义的数。折算过的合计
    在 `summary.dividend_total` 里，那个才是折算到基准货币的。

    标的与账户名字由这里补（`dividend_refs` 只取这份结果里用到的 id）：名字查不到时是
    空串，不是缺字段 —— 与 `build_positions` 的 `account_name` 同一个写法。
    这一版换掉了旧的 `{"results": [...]}` 形状：那形状里的 `id` 是 `DividendRecord`
    的主键，纯流水录入的股息**没有**这个 id，留着它等于继续暗示「日历里的东西都是明细」。
    两个客户端都还没消费这个接口（`harmony/` 里没有 calendar 的调用）。
    """

    def get(self, request):
        year = _year_param(request)
        entries = dividend_entries_in_year(request.user, year)
        assets, accounts = dividend_refs(request.user, entries)

        payload = build_calendar(entries, timezone.localdate())
        for group in ("upcoming", "received", "undated"):
            for item in payload[group]:
                symbol, name = assets.get(item["asset_id"], ("", ""))
                item["symbol"] = symbol
                item["asset_name"] = name
                item["account_name"] = accounts.get(item["account_id"], "")
        return Response(payload)
