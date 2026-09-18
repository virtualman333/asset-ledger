from zoneinfo import ZoneInfo

from django.conf import settings
from django.db import transaction as db_transaction
from django.http import StreamingHttpResponse
from rest_framework import viewsets
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.models import Account
from apps.assets.models import Asset
from apps.core.models import TxSide, TxSource

from .date_range import DateRangeError, range_bounds
from .export_rules import (
    CSV_COLUMNS,
    content_disposition,
    export_names,
    render_rows,
    transaction_row,
)
from .import_rules import ImportFormatError, ParsedCsv, parse_csv
from .models import DividendRecord, Transaction
from .serializers import DividendRecordSerializer, TransactionSerializer

#: 导出用的中文标签，直接取自模型自己的 `TextChoices.choices` ——
#: 这里**不抄第二份对照表**：模型新增一种 side / source，导出会自动跟上，
#: 不存在「加了枚举忘了加标签」这条漂移路径。
SIDE_LABELS = dict(TxSide.choices)
SOURCE_LABELS = dict(TxSource.choices)


def _flatten_errors(errors) -> str:
    """DRF 的嵌套错误字典 → 一行能读的中文。

    `{'amount': [ErrorDetail('入金必须填金额')]}` 直接 `str()` 出来带着 `ErrorDetail`
    与方括号，用户读不出「到底哪一列错了」。导入是按行报错的，一条错误必须自带列名。
    """
    parts = []
    for field, messages in errors.items():
        if isinstance(messages, (list, tuple)):
            detail = "；".join(str(item) for item in messages)
        else:
            detail = str(messages)
        parts.append(f"{field}: {detail}")
    return "；".join(parts)


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


class TransactionImportView(APIView):
    """`POST /transactions/records/import/`：把导出的 CSV 原样吃回去。

    为什么值得有一条导入
    --------------------
    这是 `docs/DESIGN.md` 第 4.1 节从 M0 起就承诺的能力，而 `TxSource.IMPORT`
    这个枚举值一直**没有生产者** —— 一个枚举值没人产生，读代码的人只能猜它是没做完
    还是没人用。券商导出的成交明细、手工在 Excel 里补的一批流水，现在都能进来。

    三件事刻意这么定
    ----------------
    1. **行级失败不整批回滚。** 第 3 行写错了不该让第 4..200 行也进不来；
       响应里逐行报「第几行、哪一列、为什么」，用户改一行再传一次即可。
       代价是「一次导入只成功了一部分」，所以响应里 `created` / `skipped` / `failed`
       三个数都要给全 —— 只说「失败」不说「成了几条」，用户不知道要不要重传。
    2. **文件级问题才是 400。** 空文件、没有表头、列名认不出、缺必需列、行数超上限
       都发生在**读文件**这一步，跟用户的某一格内容无关，直接 400 让他改文件。
    3. **金额与买卖校验复用序列化器。** 不另写一套：`TransactionSerializer` 走
       `amount_rules.resolve_amount()`，于是「入金没填金额必须报错」「拆分不产生现金变动」
       这些口径在导入这条路径上**自动**成立，不需要第二份实现，也就不会漂。

    幂等是可选的，不是默认
    ----------------------
    给 `client_request_id_prefix` 时，每行的幂等键是 `<前缀>-L<行号>` ——
    **同一份文件传两次不会记两遍**，且不用改文件内容。不给前缀就一律新建。
    不做「按内容自动去重」：两笔真实存在的同金额同备注手续费会被当成重复而**静默丢掉一次**，
    那是账目里的钱消失，比重复导入难查得多。
    """

    #: 一次最多吃多少行。**不是性能上限，是「拿错文件」的兜底** ——
    #: 上限不是静默截断：超了直接 400 并说明，绝不「导了前 5000 条还说成功」。
    MAX_ROWS = 5000

    #: 这些真值写法都算「预览」，常见于表单 / 命令行
    TRUTHY = ("1", "true", "yes", "on")

    def post(self, request):
        text = self._read_upload(request)
        dry_run = self._flag(request.data.get("dry_run"))
        prefix = (request.data.get("client_request_id_prefix") or "").strip()

        try:
            parsed = parse_csv(
                text,
                side_labels=SIDE_LABELS,
                tz=ZoneInfo(settings.TIME_ZONE),
            )
        except ImportFormatError as exc:
            raise ValidationError(str(exc)) from exc

        if parsed.total > self.MAX_ROWS:
            raise ValidationError(
                f"这份文件有 {parsed.total} 行，超过一次 {self.MAX_ROWS} 行的上限 ——"
                "请先按日期区间拆分（导出接口认 from/to，也可以直接 import 分片）。"
                "这里不截断：只导入一部分的账目比多传几次危险。"
            )

        failed = [{"line": err.line, "error": err.message} for err in parsed.errors]
        created = skipped = 0

        for row in parsed.rows:
            error, was_skipped = self._store(request, row.data, row.line, prefix, dry_run)
            if error is not None:
                failed.append({"line": row.line, "error": error})
            elif was_skipped:
                skipped += 1
            else:
                created += 1

        failed.sort(key=lambda item: item["line"])
        return Response(
            {
                "ok": not failed,
                "dry_run": dry_run,
                "total": parsed.total,
                "created": created,
                "skipped": skipped,
                "failed": failed,
            }
        )

    # ------------------------------------------------------------------ 内部

    def _flag(self, value) -> bool:
        """把表单 / JSON 里那几种「真」的写法都认下来。"""
        if isinstance(value, bool):
            return value
        return str(value or "").strip().lower() in self.TRUTHY

    def _read_upload(self, request) -> str:
        """拿到 CSV 文本：优先 multipart 的 `file`，其次 JSON 里的 `csv`。

        两种都支持是为了让它既能被浏览器表单用，也能被脚本 / 鸿蒙端用（端上拿不到
        multipart 文件选择器时，整份文本直接塞进请求体更省事）。
        """
        upload = request.FILES.get("file")
        if upload is not None:
            try:
                return upload.read().decode("utf-8-sig")
            except UnicodeDecodeError as exc:
                raise ValidationError(
                    "文件不是 UTF-8 编码 —— Excel 另存为「CSV UTF-8（逗号分隔）」再传一次"
                ) from exc
        text = request.data.get("csv")
        if isinstance(text, str) and text.strip():
            return text
        raise ValidationError(
            "没拿到 CSV：请用 `file` 字段上传（multipart），或者在 JSON 里给 `csv`（整份文本）"
        )

    def _store(self, request, data, line, prefix, dry_run):
        """一行 → 落库。返回 `(错误信息 或 None, 是否跳过)`。"""
        account = Account.objects.filter(user=request.user, name=data["account_name"]).first()
        if account is None:
            return f"账户「{data['account_name']}」不存在 —— 导入只认本用户已有的账户，不会顺手新建", False

        asset = None
        symbol = data["asset_symbol"]
        if symbol:
            matches = list(Asset.objects.filter(symbol__iexact=symbol)[:2])
            if not matches:
                return f"标的「{symbol}」不存在 —— 请先在标的里建好（代码打错时顺手新建，会多出一堆没听过的持仓）", False
            if len(matches) > 1:
                return (
                    f"标的代码「{symbol}」在多个市场都有（%s）—— 代码不唯一，"
                    "导入按代码匹配，请先把要用的那个改成不冲突的代码"
                    % "、".join(f"{a.symbol}({a.market})" for a in matches)
                ), False
            asset = matches[0]

        payload = {
            "account": account.id,
            "asset": asset.id if asset else None,
            "side": data["side"],
            "quantity": data["quantity"],
            "price": data["price"],
            "amount": data["amount"],
            # 费用 / 税费**空着就是 0**（模型的 `default=0`），不是「未知」。与
            # 「现金变动」刻意不同：那个空着要按 side 推、推不出来必须报错 ——
            # 一条入金的金额猜成 0 会让这笔钱在年化里消失，而手续费没填就是没花手续费。
            # 这一条是冒烟真跑出来的：留空过 `None` 进来，DRF 报「该字段不能为 null」，
            # 于是「只有必需四列的手写文件」一条都导不进来。
            "fee": data["fee"] if data["fee"] is not None else 0,
            "tax": data["tax"] if data["tax"] is not None else 0,
            "currency": data["currency"],
            "fx_rate": data["fx_rate"] if data["fx_rate"] is not None else 1,
            "traded_at": data["traded_at"],
            # 来源是「导入」，**不从文件里读** —— 抄回来的「Agent 识别」会冒充一次
            # 从未发生过的识别，而原始凭证是空的。理由见 import_rules 模块说明。
            "source": TxSource.IMPORT,
            "note": data["note"],
        }
        key = f"{prefix}-L{line}" if prefix else None
        if key:
            payload["client_request_id"] = key
            if Transaction.objects.filter(client_request_id=key).exists():
                return None, True

        serializer = TransactionSerializer(data=payload, context={"request": request})
        if not serializer.is_valid():
            return _flatten_errors(serializer.errors), False
        if dry_run:
            return None, False
        with db_transaction.atomic():
            serializer.save()
        return None, False


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
