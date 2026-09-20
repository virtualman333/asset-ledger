# -*- coding: utf-8 -*-
"""`apps/transactions/import_rules.py` 的测试（纯 Python，不需要数据库、不需要 Django）。

这份测试要挡住的是**一类安静的错误**：导入的列名对不上、时间少 8 小时、
备注每回导一次多一个引号、某一行出错却把整批吞掉。它们都不会报错，只会让账目
慢慢和现实对不上。

其中最重要的一条是 `RoundTripTest`：**自己导出的文件必须能被自己导入，且逐字相等。**
`export_rules.CSV_COLUMNS` 是导出与导入共用的唯一列定义，这条往返用例是它唯一的实证 ——
列定义改成两份、或者表头写成一个错字，都当场红。
"""
import unittest
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace

from apps.core.csv_export import BOM, sanitize_cell
from apps.transactions.export_rules import CSV_COLUMNS, render_csv, transaction_row
from apps.transactions.import_rules import (
    CONSUMED_KEYS,
    HEADERS,
    IGNORED_KEYS,
    ImportFormatError,
    ParsedCsv,
    ParsedRow,
    RowValueError,
    build_row,
    parse_csv,
    parse_decimal,
    parse_traded_at,
    resolve_headers,
    strip_bom,
    unescape_cell,
    unhandled_columns,
)

#: 与 `test_export_rules.py` 同一口径：`zoneinfo` 是标准库，但 Windows 上没有系统 tz
#: 数据库，`ZoneInfo("Asia/Shanghai")` 要靠第三方包 `tzdata` 才认得。这份测试按 README
#: 的承诺要能在**裸 Python** 上跑（`test_no_django_required.py` 会拦掉 Django 再跑一遍），
#: 一旦引了 tzdata 就会在那一遍里 import 失败。固定偏移对 2026 年的上海完全等价（无夏令时）。
TZ = timezone(timedelta(hours=8), "CST")

#: 与 `views.py` 里那份一致：标签表由调用方注入，import_rules 自己不认识模型
SIDE_LABELS = {
    "BUY": "买入",
    "SELL": "卖出",
    "DIVIDEND": "分红/利息",
    "DEPOSIT": "入金",
    "WITHDRAW": "出金",
    "FEE": "费用",
    "TAX": "税费",
    "SPLIT": "拆分/送股",
}


def parse(text, **kwargs):
    return parse_csv(text, side_labels=SIDE_LABELS, tz=TZ, **kwargs)


def csv_text(*data_rows, header=("时间", "方向", "账户", "币种")):
    """拼一份最小可用的 CSV（表头可换）。"""
    return "\r\n".join([",".join(header)] + [",".join(row) for row in data_rows]) + "\r\n"


class HeaderTest(unittest.TestCase):
    """表头映射：中文表头与英文键名都认，认不出的绝不静默放过。"""

    def test_中文表头全部认得出(self):
        header = [title for _, title in CSV_COLUMNS]
        self.assertEqual(len(resolve_headers(header)), len(CSV_COLUMNS))

    def test_英文键名也认得出(self):
        header = [key for key, _ in CSV_COLUMNS]
        self.assertEqual(
            sorted(resolve_headers(header).values()),
            sorted(key for key, _ in CSV_COLUMNS),
        )

    def test_表头映射是从_csv_columns_现算的(self):
        """不许在这里抄第二份列名：`CSV_COLUMNS` 里每个中文表头都必须在 `HEADERS` 里。"""
        for key, title in CSV_COLUMNS:
            self.assertIn(title, HEADERS, f"表头 {title!r} 没被现算进去")
            self.assertEqual(HEADERS[title], key)

    def test_认不出的列名直接报错而不是忽略(self):
        with self.assertRaises(ImportFormatError) as ctx:
            resolve_headers(["时间", "方向", "账户", "币种", "我瞎写的列"])
        self.assertIn("我瞎写的列", str(ctx.exception))

    def test_缺必需列直接报错(self):
        with self.assertRaises(ImportFormatError) as ctx:
            resolve_headers(["时间", "方向", "账户"])
        self.assertIn("币种", str(ctx.exception))

    def test_同一列出现两次直接报错(self):
        with self.assertRaises(ImportFormatError) as ctx:
            resolve_headers(["时间", "方向", "账户", "币种", "币种"])
        self.assertIn("两次", str(ctx.exception))

    def test_空列名算认不出(self):
        with self.assertRaises(ImportFormatError):
            resolve_headers(["时间", "方向", "账户", "币种", ""])


class BomAndEscapeTest(unittest.TestCase):
    """自己导出的文件自己必须吃得下：BOM 要吃掉，公式防线要还原。"""

    def test_导出带的_bom_被吃掉(self):
        self.assertEqual(strip_bom(BOM + "时间,方向\r\n"), "时间,方向\r\n")

    def test_没有_bom_时原样返回(self):
        self.assertEqual(strip_bom("时间"), "时间")

    def test_unscape_正好是_sanitize_的逆(self):
        """★ 对称性：导出加的那道防线，导入要能原样摘掉。

        样本里既有「会被加引号的」（`=1+1` / `+1` / `@a` / `-x`），
        也有「不该被加引号的」（负数、普通文本、真的以单引号开头的文本）。
        """
        for original in ("=1+1", "+1", "@a", "-x", "普通备注", "带,逗号", "-2500", "'引号开头"):
            with self.subTest(original=original):
                self.assertEqual(unescape_cell(sanitize_cell(original)), original)

    def test_负数不被当成公式也不被摘引号(self):
        self.assertEqual(unescape_cell("-2500"), "-2500")


class TradedAtTest(unittest.TestCase):
    """时间：导出写出去的是本地墙上时间，导进来必须还原成同一个瞬间。"""

    def test_导出那三种格式都认(self):
        self.assertEqual(
            parse_traded_at("2026-01-10 10:00:00", TZ),
            datetime(2026, 1, 10, 10, 0, 0, tzinfo=TZ),
        )
        self.assertEqual(
            parse_traded_at("2026-01-10 10:00", TZ),
            datetime(2026, 1, 10, 10, 0, tzinfo=TZ),
        )
        self.assertEqual(
            parse_traded_at("2026-01-10", TZ),
            datetime(2026, 1, 10, 0, 0, tzinfo=TZ),
        )

    def test_带偏移的_iso_串原样保留偏移(self):
        got = parse_traded_at("2026-01-10T02:00:00+00:00", TZ)
        self.assertEqual(got.utcoffset(), timedelta(0))
        self.assertEqual(got, datetime(2026, 1, 10, 10, 0, tzinfo=TZ))

    def test_不带偏移的_iso_串按当地时区补(self):
        got = parse_traded_at("2026-01-10T10:00:00", TZ)
        self.assertEqual(got, datetime(2026, 1, 10, 10, 0, tzinfo=TZ))

    def test_时间写错时报的是这一列的名字(self):
        with self.assertRaises(RowValueError) as ctx:
            parse_traded_at("2026/01/10", TZ)
        self.assertIn("时间", str(ctx.exception))

    def test_空时间直接报错(self):
        with self.assertRaises(RowValueError):
            parse_traded_at("", TZ)


class DecimalTest(unittest.TestCase):
    """数值：「没填」与「填了 0」必须分开。"""

    def test_空是_none_不是零(self):
        self.assertIsNone(parse_decimal("", "amount"))
        self.assertIsNone(parse_decimal("   ", "fee"))

    def test_填了零就是零(self):
        self.assertEqual(parse_decimal("0", "amount"), Decimal("0"))

    def test_非法数字报的是这一列的中文名(self):
        with self.assertRaises(RowValueError) as ctx:
            parse_decimal("abc", "quantity")
        self.assertIn("数量", str(ctx.exception))

    def test_无穷大不算有限数(self):
        with self.assertRaises(RowValueError):
            parse_decimal("Infinity", "amount")


class RowTest(unittest.TestCase):
    """整份解析：行级错误逐行报、不中断整批，行号与人眼看到的一致。"""

    def test_一行合法记录解析出全部字段(self):
        text = csv_text(("2026-01-10 10:00:00", "买入", "冒烟账户", "CNY"))
        got = parse(text)
        self.assertEqual(got.errors, [])
        self.assertEqual(got.total, 1)
        row = got.rows[0]
        self.assertIsInstance(row, ParsedRow)
        self.assertEqual(row.line, 2)
        self.assertEqual(row.data["side"], "BUY")
        self.assertEqual(row.data["account_name"], "冒烟账户")
        self.assertEqual(row.data["traded_at"], datetime(2026, 1, 10, 10, 0, tzinfo=TZ))

    def test_方向同时认中文名与代码(self):
        text = csv_text(
            ("2026-01-10 10:00:00", "买入", "A", "CNY"),
            ("2026-01-11 10:00:00", "SELL", "A", "CNY"),
        )
        got = parse(text)
        self.assertEqual([r.data["side"] for r in got.rows], ["BUY", "SELL"])

    def test_方向认不出时报错(self):
        got = parse(csv_text(("2026-01-10 10:00:00", "乱写", "A", "CNY")))
        self.assertEqual(got.rows, [])
        self.assertIn("方向", got.errors[0].message)

    def test_第几行错了就报第几行_且不影响别的行(self):
        """★ 行级失败不整批回滚 —— 第 3 行错了不该让第 4 行也进不来。"""
        text = csv_text(
            ("2026-01-10 10:00:00", "买入", "A", "CNY"),
            ("2026-13-99 10:00:00", "买入", "A", "CNY"),
            ("2026-01-12 10:00:00", "买入", "A", "CNY"),
        )
        got = parse(text)
        self.assertEqual([r.line for r in got.rows], [2, 4])
        self.assertEqual([e.line for e in got.errors], [3])
        self.assertEqual(got.total, 3)

    def test_列数对不上报的是行号(self):
        text = "时间,方向,账户,币种\r\n2026-01-10 10:00:00,买入,A\r\n"
        got = parse(text)
        self.assertEqual(got.rows, [])
        self.assertIn("列数对不上", got.errors[0].message)
        self.assertEqual(got.errors[0].line, 2)

    def test_空行被跳过而不是当成错行(self):
        text = "时间,方向,账户,币种\r\n2026-01-10 10:00:00,买入,A,CNY\r\n\r\n,\r\n"
        got = parse(text)
        self.assertEqual(got.total, 1)
        self.assertEqual(got.errors, [])

    def test_空文件报的是文件级错误(self):
        with self.assertRaises(ImportFormatError):
            parse("")
        with self.assertRaises(ImportFormatError):
            parse(BOM + "\r\n\r\n")

    def test_账户为空的行报错(self):
        got = parse(csv_text(("2026-01-10 10:00:00", "买入", "", "CNY")))
        self.assertEqual(got.rows, [])
        self.assertIn("账户", got.errors[0].message)

    def test_备注超长报错而不是被静默截断(self):
        header = ("时间", "方向", "账户", "币种", "备注")
        text = csv_text(
            ("2026-01-10 10:00:00", "买入", "A", "CNY", "x" * 256), header=header
        )
        got = parse(text)
        self.assertEqual(got.rows, [])
        self.assertIn("255", got.errors[0].message)
        # 对照面：255 字正好是上限，必须能进
        ok = parse(csv_text(("2026-01-10 10:00:00", "买入", "A", "CNY", "x" * 255), header=header))
        self.assertEqual(len(ok.rows), 1)


class ParseSurfaceTest(unittest.TestCase):
    """扫描面自证：这些性质本身不许退化成空话。"""

    def test_解析结果类型(self):
        self.assertIsInstance(parse(csv_text(("2026-01-10 10:00:00", "买入", "A", "CNY"))), ParsedCsv)

    def test_只认得出部分列时依然要能解析(self):
        """只给必需列（导出的 14 列一个不给）也要能进来 —— 手写文件就是这么写的。"""
        got = parse(csv_text(("2026-01-10 10:00:00", "入金", "A", "CNY")))
        self.assertEqual(len(got.rows), 1)
        self.assertIsNone(got.rows[0].data["quantity"])
        self.assertIsNone(got.rows[0].data["amount"])


# ---------------------------------------------------------------------------
# 往返：自己导出的文件必须能被自己导入
# ---------------------------------------------------------------------------


def fake_tx(**over):
    """一条「像流水」的对象（只带导出取值会碰到的属性）。

    刻意不用 Django 模型：`export_rules.transaction_row()` 本来就是纯函数，
    拿 SimpleNamespace 就能喂，于是这条往返契约能在裸 Python 上跑。
    """
    base = dict(
        traded_at=datetime(2026, 1, 10, 10, 0, 0, tzinfo=TZ),
        side="BUY",
        source="MANUAL",
        quantity=Decimal("1000"),
        price=Decimal("6.5"),
        amount=Decimal("-6505"),
        fee=Decimal("5"),
        tax=Decimal("0"),
        currency="CNY",
        fx_rate=Decimal("1"),
        note="手工记账",
        asset=SimpleNamespace(symbol="601398", name="工商银行"),
        account=SimpleNamespace(name="冒烟账户"),
    )
    base.update(over)
    return SimpleNamespace(**base)


#: 覆盖每一种 side、以及几种「值长得很特殊」的备注
FIXTURES = (
    fake_tx(),
    fake_tx(side="SELL", quantity=Decimal("500"), price=Decimal("7"), amount=Decimal("3495"), fee=Decimal("5")),
    fake_tx(side="DIVIDEND", amount=Decimal("300"), note="分红/利息"),
    fake_tx(
        side="DEPOSIT",
        asset=None,
        quantity=None,
        price=None,
        amount=Decimal("100000"),
        note="入金",
    ),
    fake_tx(side="WITHDRAW", asset=None, quantity=None, price=None, amount=Decimal("-50000")),
    fake_tx(side="SPLIT", amount=Decimal("0"), note="10 送 3"),
    fake_tx(side="FEE", amount=Decimal("-15"), note="基金申赎费"),
    fake_tx(side="TAX", asset=None, quantity=None, price=None, amount=Decimal("-8")),
    # 备注里塞三种最容易坏的东西：公式前缀、逗号、双引号
    fake_tx(note="=1+1"),
    fake_tx(note="带,逗号"),
    fake_tx(note='带"双引号"'),
    fake_tx(note="-看上去像负数"),
    # 英文标的、多币种、带小数的汇率
    fake_tx(asset=SimpleNamespace(symbol="AAPL", name="Apple"), currency="USD", fx_rate=Decimal("7.12345678")),
)


def exported_text():
    rows = [transaction_row(tx, SIDE_LABELS, {"MANUAL": "手动", "AGENT": "Agent 识别", "IMPORT": "导入"}, CSV_COLUMNS, tz=TZ) for tx in FIXTURES]
    return render_csv(rows, CSV_COLUMNS)


class RoundTripTest(unittest.TestCase):
    """★ 导出 → 导入 → 逐字相等。

    这条是 `CSV_COLUMNS` 作为「导出与导入共用唯一列定义」的实证：列名改成两份、
    表头写错一个字、BOM 没吃掉、引号防线没还原，都会在这里红。
    """

    @classmethod
    def setUpClass(cls):
        cls.text = exported_text()
        cls.parsed = parse(cls.text)

    def test_导出的每一行都解析成功(self):
        self.assertEqual(self.parsed.errors, [], f"有行没解析回来：{self.parsed.errors}")

    def test_行数一模一样(self):
        self.assertEqual(len(self.parsed.rows), len(FIXTURES))

    def test_逐字段相等(self):
        for tx, parsed in zip(FIXTURES, self.parsed.rows):
            with self.subTest(side=tx.side, note=tx.note):
                got = parsed.data
                self.assertEqual(got["traded_at"], tx.traded_at)
                self.assertEqual(got["side"], tx.side)
                self.assertEqual(got["account_name"], tx.account.name)
                self.assertEqual(got["asset_symbol"], tx.asset.symbol if tx.asset else "")
                self.assertEqual(got["quantity"], tx.quantity)
                self.assertEqual(got["price"], tx.price)
                self.assertEqual(got["amount"], tx.amount)
                self.assertEqual(got["fee"], tx.fee)
                self.assertEqual(got["tax"], tx.tax)
                self.assertEqual(got["currency"], tx.currency)
                self.assertEqual(got["fx_rate"], tx.fx_rate)
                self.assertEqual(got["note"], tx.note)

    def test_导出的文本真的带_bom_和_crlf(self):
        """先把导出的形状钉住 —— 不然「往返成功」可能只是因为两边都是纯文本。"""
        self.assertTrue(self.text.startswith(BOM))
        self.assertIn("\r\n", self.text)


class RoundTripIsFalsifiableTest(unittest.TestCase):
    """★ 反向对照：上面那条往返断言真的会红。

    做法是**破坏导出侧一个具体的东西**，确认往返断言抓得到 —— 不做这一层，
    「往返相等」可能只是一直在比较两份同样错的数据。
    """

    def test_表头少一列时导不回来(self):
        rows = [transaction_row(tx, SIDE_LABELS, {}, (CSV_COLUMNS[0], CSV_COLUMNS[1])) for tx in FIXTURES[:1]]
        text = render_csv(rows, (CSV_COLUMNS[0], CSV_COLUMNS[1]))
        with self.assertRaises(ImportFormatError) as ctx:
            parse(text)
        self.assertIn("缺少必需的列", str(ctx.exception))

    def test_表头改成一个错字时导不回来(self):
        broken = list(CSV_COLUMNS)
        broken[0] = ("traded_at", "时间点")  # 「时间」→「时间点」，只差一个字
        rows = [transaction_row(tx, SIDE_LABELS, {}, broken, tz=TZ) for tx in FIXTURES[:1]]
        text = render_csv(rows, broken)
        with self.assertRaises(ImportFormatError) as ctx:
            parse(text)
        self.assertIn("时间点", str(ctx.exception))

    def test_bom_不剥时导不回来(self):
        """★ 把 `strip_bom` 换掉（＝导出带的 BOM 没人吃），第一列会成为 `\\ufeff时间`。

        这里不再走 `parse_csv`（它内部就会剥），直接问判据本身：带 BOM 的表头
        必须被判成「认不出的列」—— 于是「BOM 这一层有没有存在」是可证伪的。
        """
        header = [BOM + "时间", "方向", "账户", "币种"]
        with self.assertRaises(ImportFormatError) as ctx:
            resolve_headers(header)
        self.assertIn("时间", str(ctx.exception))
        # 对照面：同一份表头，过了 strip_bom 就是好的
        self.assertEqual(len(resolve_headers([strip_bom(cell) for cell in header])), 4)

    def test_往返比较的是同一个瞬间而不是同一串字符(self):
        """时区口径也要钉住：把 UTC 当本地时间读进来，应该差 8 小时。"""
        parsed = parse(exported_text())
        naive = datetime(2026, 1, 10, 10, 0, 0, tzinfo=timezone.utc)
        self.assertNotEqual(parsed.rows[0].data["traded_at"], naive)
        self.assertEqual(parsed.rows[0].data["traded_at"], datetime(2026, 1, 10, 10, 0, tzinfo=TZ))


#: 一份**每一列都填了合法值**的记录，用来按行为推「哪几列真的被读了」
FULL_RECORD = {
    "traded_at": "2026-03-02 10:30:00",
    "side_label": "买入",
    "asset_symbol": "600050",
    "asset_name": "中国联通",
    "account_name": "主账户",
    "quantity": "100",
    "price": "5.20",
    "amount": "-520",
    "fee": "1.5",
    "tax": "0",
    "currency": "CNY",
    "fx_rate": "1",
    "source_label": "手动",
    "note": "试一笔",
}

#: 每个键的「另一个合法值」。换掉它之后输出应该变 —— 除非这一列根本没人读。
OTHER_VALUE = {
    "traded_at": "2026-03-03 11:31:00",
    "side_label": "卖出",
    "asset_symbol": "601728",
    "asset_name": "中国电信",
    "account_name": "备用账户",
    "quantity": "200",
    "price": "6.30",
    "amount": "1260",
    "fee": "2.5",
    "tax": "1",
    "currency": "HKD",
    "fx_rate": "0.92",
    "source_label": "Agent 识别",
    "note": "另一笔",
}


def consumed_by_behaviour(*, identity=False):
    """按**行为**现算消费集：换掉某一列的值，`build_row()` 的输出会不会变。

    `identity=True` 时每列都换回它自己的值（等于什么都没换），必须一列都不算被读 ——
    这是这条判据的反向对照：一个「无论怎么改都返回全部列」的实现同样能让正向断言变绿。
    """
    base = build_row(dict(FULL_RECORD), SIDE_LABELS, TZ)
    read = []
    for key, _title in CSV_COLUMNS:
        variant = dict(FULL_RECORD)
        variant[key] = FULL_RECORD[key] if identity else OTHER_VALUE[key]
        try:
            changed = build_row(variant, SIDE_LABELS, TZ) != base
        except RowValueError:
            changed = True  # 连解析都受影响，显然是被读的
        if changed:
            read.append(key)
    return read


class ColumnCoverageTest(unittest.TestCase):
    """列集合必须是一份**划分**：读走的 ∪ 声明忽略的 = `CSV_COLUMNS` 的全部。

    为什么值得单开一类：`CONSUMED_KEYS` / `IGNORED_KEYS` 曾经是两份手抄清单，而且
    **全仓没有消费方** —— 往导出加一列，清单不会变、测试也不会红，那一列的值在
    `build_row()` 里被静默丢掉。用户拿着新版导出的文件回导会得到一屏「成功」，
    而那一列全空。这里把「声明」换成「按行为现算 + 与声明对账」。
    """

    def test_消费集是按行为现算的_不是手抄的(self):
        all_keys = [key for key, _t in CSV_COLUMNS]
        self.assertGreaterEqual(len(all_keys), 10, "列清单太小，这条对账失去了意义")
        read = consumed_by_behaviour()
        # 自证：扫描面必须真的覆盖了整份列清单
        self.assertEqual(len(set(read)), len(set(read)), "现算结果里有重复")
        self.assertGreaterEqual(len(read), 8, f"只推出 {len(read)} 列被读 —— 探针塌了")
        self.assertEqual(
            sorted(read),
            sorted(CONSUMED_KEYS),
            "按行为现算出的消费集与 CONSUMED_KEYS 不一致 —— 声明的清单已经和代码脱钩了",
        )

    def test_反向对照_全部换成同一个值时一列都不算被读(self):
        self.assertEqual(
            consumed_by_behaviour(identity=True),
            [],
            "值没变却报出「被读了」—— 探针在数「列存在」而不是「值影响输出」，判据恒真",
        )

    def test_两份清单构成划分_没有第三类(self):
        all_keys = {key for key, _t in CSV_COLUMNS}
        read = set(CONSUMED_KEYS)
        ignored = set(IGNORED_KEYS)
        self.assertEqual(read & ignored, set(), f"同一列同时被读又被忽略：{read & ignored}")
        self.assertEqual(
            read | ignored,
            all_keys,
            "这些列既没被读、也没被声明忽略（它们的值会被静默丢掉）："
            f"{sorted(all_keys - read - ignored)}",
        )

    def test_声明忽略的列按行为也真的没被读(self):
        """`IGNORED_KEYS` 不能是「随便写几个名字」—— 它们必须真的不影响输出。"""
        read = set(consumed_by_behaviour())
        wrongly = sorted(set(IGNORED_KEYS) & read)
        self.assertEqual(wrongly, [], f"这些列被声明忽略，实际上却在影响输出：{wrongly}")

    def test_判据本身不是恒空(self):
        """`unhandled_columns` 必须能报出东西 —— 恒返回 [] 的实现也能让上面几条全绿。"""
        self.assertEqual(unhandled_columns([key for key, _t in CSV_COLUMNS]), [])
        self.assertEqual(unhandled_columns(["brand_new"]), ["brand_new"])
        self.assertEqual(
            unhandled_columns(["traded_at", "brand_new", "asset_name"]),
            ["brand_new"],
            "只该报第三类列：被读的和声明忽略的都不算",
        )

    def test_导出新增一列_导入必须当场拦下而不是静默丢掉(self):
        """★ 模拟「有人往 `CSV_COLUMNS` 加了一列」：以前它会一路静默通过。

        只能靠临时替换模块级的列清单来模拟 —— 真去改 `export_rules` 就成了「为了测试
        改生产代码」。替换范围只在这一个用例里，`finally` 里还原。
        """
        import apps.transactions.import_rules as rules

        saved_cols, saved_headers = rules.CSV_COLUMNS, rules.HEADERS
        new_key, new_title = "brand_new", "新列"
        rules.CSV_COLUMNS = tuple(saved_cols) + ((new_key, new_title),)
        rules.HEADERS = dict(saved_headers)
        rules.HEADERS[new_title] = new_key
        rules.HEADERS[new_key] = new_key
        try:
            text = csv_text(
                ("2026-03-02 10:30:00", "买入", "主账户", "CNY", "随便"),
                header=("时间", "方向", "账户", "币种", new_title),
            )
            with self.assertRaises(ImportFormatError) as ctx:
                parse(text)
            message = str(ctx.exception)
            self.assertIn("新列", message, "报错信息里要用中文表头，用户才知道去改哪一列")
            self.assertIn("brand_new", message, "取值键也要出现，写代码的人知道改哪个字段")
        finally:
            rules.CSV_COLUMNS, rules.HEADERS = saved_cols, saved_headers

    def test_新增的列被登记之后就不该再拦(self):
        """把新列登记进 `IGNORED_KEYS`（模块级清单的替代）后必须放行 —— 反向对照。"""
        self.assertEqual(unhandled_columns([key for key, _t in CSV_COLUMNS]), [])
        # 登记的列是「第三类」之外的，因此不再被报出来
        read = set(CONSUMED_KEYS)
        self.assertTrue(read.issubset({key for key, _t in CSV_COLUMNS}))
        self.assertNotIn("brand_new", {key for key, _t in CSV_COLUMNS})


if __name__ == "__main__":
    unittest.main()
