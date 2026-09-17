# -*- coding: utf-8 -*-
"""导出 CSV 的纯函数：列序、数值写法、公式注入、BOM / CRLF、文件名与响应头。

为什么能在这里测
----------------
`apps/transactions/export_rules.py` 不 import django（和 `amount_rules` / `dividend_income`
/ `valuation` 一样），所以这一整个模块按 README 的承诺在**裸 Python** 上就能跑，
不用数据库、不用第三方依赖。

锁住的东西里，有几条是「写错了也看不出来」的：
  - `Decimal.normalize()` 的科学计数法（`100` 变 `1E+2`）；
  - `-0`（账目里看着像数据错了）；
  - `=` / `+` / `-` / `@` 开头的单元格在 Excel 里被当公式执行；
  - 中文文件名直接塞进 `Content-Disposition`（Django 抛异常 → 点导出没反应）；
  - UTC 时间不经转换写出去（比界面少 8 小时）。

所以下面**每条断言都配了一个「不这么写就会怎样」的对照**（`TestTheChecksAreNotVacuous`），
免得哪天这些检查退化成恒真的空话。

跑法（在 server/ 下）：python -m unittest discover -s apps -t .
"""
import unittest
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace

from apps.transactions.export_rules import (
    BOM,
    CSV_COLUMNS,
    EOL,
    GUARD,
    content_disposition,
    export_names,
    fmt_decimal,
    fmt_traded_at,
    quote_cell,
    render_csv,
    render_line,
    sanitize_cell,
    transaction_cells,
    transaction_row,
)

#: 东八区。**故意用固定偏移，不用 `ZoneInfo("Asia/Shanghai")`** ——
#: `zoneinfo` 是标准库，但 Windows 上没有系统 tz 数据库，`ZoneInfo("Asia/Shanghai")`
#: 要靠第三方包 `tzdata` 才认得。这份测试按 README 的承诺要能在**裸 Python**
#: 上跑（`test_no_django_required.py` 会拦掉 Django 再跑一遍），一旦引了 tzdata
#: 就会在那里整模块 ImportError —— 实测撞到过。中国 1991 年后没有夏令时，
#: 固定 +08:00 与 `Asia/Shanghai` 在这些用例的日期上是同一个东西。
SHANGHAI = timezone(timedelta(hours=8), "CST")

#: 空的标签表：只关心「取没取到值」的用例用它，`side` 会原样落到单元格里
NO_LABELS = {}
SIDE_LABELS = {"BUY": "买入", "SELL": "卖出", "DEPOSIT": "入金"}
SOURCE_LABELS = {"MANUAL": "手动", "AGENT": "Agent 识别"}


def fake_tx(**overrides):
    """一条流水的**假替身**：只要属性对得上，`transaction_row()` 不在乎它是什么。

    这也是它不 import django 的意义 —— 这个替身不需要数据库。
    """
    base = dict(
        traded_at=datetime(2026, 1, 10, 2, 0, tzinfo=timezone.utc),
        side="BUY",
        asset=SimpleNamespace(symbol="601398", name="工商银行"),
        account=SimpleNamespace(name="主账户"),
        quantity=Decimal("1000.00000000"),
        price=Decimal("6.50000000"),
        amount=Decimal("-6505.00000000"),
        fee=Decimal("5.00000000"),
        tax=Decimal("0.00000000"),
        currency="CNY",
        fx_rate=Decimal("1.00000000"),
        source="MANUAL",
        note="",
    )
    base.update(overrides)
    return SimpleNamespace(**base)


class TestNumbersAreWrittenTheWayExcelReadsThem(unittest.TestCase):
    def test_no_trailing_zeros(self):
        """库里是 DECIMAL(24,8)，取出来是 `1000.00000000` —— 导出不该拖着那串零。"""
        self.assertEqual(fmt_decimal(Decimal("1000.00000000")), "1000")
        self.assertEqual(fmt_decimal(Decimal("6.50000000")), "6.5")

    def test_no_scientific_notation(self):
        """★ `normalize()` 会把 100 变成 `1E+2` —— 必须先归一、再用 'f' 落地。"""
        self.assertEqual(fmt_decimal(Decimal("100.00000000")), "100")
        self.assertEqual(fmt_decimal(Decimal("1E+2")), "100")
        self.assertEqual(fmt_decimal(Decimal("0.00000001")), "0.00000001")

    def test_negative_zero_becomes_zero(self):
        """★ `-0` 在账目里看起来像数据错了；归零统一写 0。"""
        self.assertEqual(fmt_decimal(Decimal("-0.00000000")), "0")
        self.assertEqual(fmt_decimal(Decimal("0")), "0")

    def test_empty_values_are_empty_cells(self):
        self.assertEqual(fmt_decimal(None), "")
        self.assertEqual(fmt_decimal(""), "")

    def test_non_numbers_are_passed_through(self):
        """这个函数不负责替调用方判数据对错 —— 不是数就原样交出去。"""
        self.assertEqual(fmt_decimal("abc"), "abc")


class TestFormulaInjectionIsNeutralised(unittest.TestCase):
    """Excel / WPS / LibreOffice 会把以 `=` `+` `-` `@` 开头的单元格当公式执行。

    备注列来自用户手输与 Agent 对截图的 OCR，也就是说不完全由用户自己掌控。
    """

    DANGEROUS = ("=1+1", "=cmd|'/c calc'!A1", "+1", "@SUM(A1)", "-1+1", "\t=1+1", "\r=1+1")

    def test_dangerous_cells_get_the_guard_prefix(self):
        for raw in self.DANGEROUS:
            with self.subTest(raw=raw):
                out = sanitize_cell(raw)
                self.assertEqual(out, GUARD + raw)
                # 判据是「真的不一样」，不是「等于某个字面量」
                self.assertNotEqual(out, raw, "这条判据没有生效")

    def test_real_numbers_are_not_touched(self):
        """负数是数据，不是公式 —— 判据是「`-` 后面整个是一个数」。"""
        for raw in ("-2500", "-2500.5", "-0.00000001", "0", "1000", "6.5"):
            with self.subTest(raw=raw):
                self.assertEqual(sanitize_cell(raw), raw)

    def test_ordinary_text_is_not_touched(self):
        for raw in ("工商银行", "601398", "股息再投", "a=b", "3*4"):
            with self.subTest(raw=raw):
                self.assertEqual(sanitize_cell(raw), raw)

    def test_empty_cell_stays_empty(self):
        """空单元格不该变成一格孤零零的单引号。"""
        self.assertEqual(sanitize_cell(None), "")
        self.assertEqual(sanitize_cell(""), "")


class TestQuotingFollowsRfc4180(unittest.TestCase):
    def test_plain_cells_are_not_quoted(self):
        self.assertEqual(quote_cell("工商银行"), "工商银行")
        self.assertEqual(quote_cell("601398"), "601398")

    def test_commas_quotes_and_newlines_get_quoted(self):
        self.assertEqual(quote_cell("a,b"), '"a,b"')
        self.assertEqual(quote_cell('说"这个"'), '"说""这个"""')
        self.assertEqual(quote_cell("两\n行"), '"两\n行"')

    def test_guarding_happens_before_quoting(self):
        """`=1,2` 净化后是 `'=1,2`，里面还有逗号 —— 两道处理要能叠加。"""
        self.assertEqual(render_line(["=1,2"]), "\"'=1,2\"" + "\r\n")


class TestTheFileShape(unittest.TestCase):
    """BOM 与 CRLF：不是口味，是「双击打开就知道了」。

    断言里**刻意写死字面量** `"\\ufeff"` / `"\\r\\n"`，不用 `BOM` / `EOL` 两个常量 ——
    这两样是**对外契约**（Excel 认 BOM、RFC 4180 规定 CRLF），拿被测常量写期望，
    把 `EOL` 改成 `"\\n"` 整组断言照样全绿。**实测就是这么发现原来的版本是假锁的**
    （负向验证里 D2「行尾改成 LF」注入下去，只有一条断言红）。
    文件内部那类约定（列序由谁决定）才该引用常量。
    """

    def test_the_constants_are_what_the_outside_world_requires(self):
        self.assertEqual(BOM, "\ufeff", "BOM 常量本身被改掉了")
        self.assertEqual(EOL, "\r\n", "行尾常量本身被改掉了 —— RFC 4180 与 Excel 都要求 CRLF")

    def test_bom_is_the_first_thing_in_the_file(self):
        text = render_csv([])
        self.assertTrue(text.startswith("\ufeff"), "文件开头没有 BOM，Excel 会拿 GBK 解 UTF-8")
        self.assertEqual(text.count("\ufeff"), 1, "BOM 只该出现在文件最开头")
        self.assertTrue(text.endswith("\r\n"))

    def test_header_is_the_column_titles_in_order(self):
        first_line = render_csv([]).lstrip("\ufeff").split("\r\n")[0]
        self.assertEqual(first_line, ",".join(title for _, title in CSV_COLUMNS))
        self.assertGreaterEqual(len(CSV_COLUMNS), 10, "列清单看起来被削过了")

    def test_every_line_ends_with_crlf_and_no_bare_lf(self):
        body = render_csv([["a", "b"], ["c", "d"]]).lstrip("\ufeff")
        self.assertTrue(body.endswith("\r\n"))
        self.assertNotIn("\n", body.replace("\r\n", ""), "出现了裸 LF —— Excel 会把它当换行但不是行尾")
        self.assertEqual(body.count("\r\n"), 3, "两行数据 + 一行表头 = 3 个行尾")

    def test_a_note_with_a_newline_does_not_break_the_row_count(self):
        """★ 备注里有换行 → 该格被引号包起来，**不能**变成多一行。"""
        rows = [["a", "第一行\n第二行"]]
        body = render_csv(rows).lstrip("\ufeff")
        self.assertEqual(body.count("\r\n"), 2, f"换行把一行拆成了两行：{body!r}")
        self.assertIn('"第一行\n第二行"', body)


class TestColumnOrderHasASingleSource(unittest.TestCase):
    def test_cells_cover_exactly_the_declared_columns(self):
        """★ 双向：少一个键多一个键都红。加的列没人填，就是一行空值。"""
        cells = transaction_cells(fake_tx(), SIDE_LABELS, SOURCE_LABELS)
        declared = {key for key, _ in CSV_COLUMNS}
        self.assertEqual(set(cells), declared)

    def test_column_keys_and_titles_are_unique(self):
        keys = [key for key, _ in CSV_COLUMNS]
        titles = [title for _, title in CSV_COLUMNS]
        self.assertEqual(len(keys), len(set(keys)), f"列键重复：{keys}")
        self.assertEqual(len(titles), len(set(titles)), f"表头重复：{titles}")

    def test_row_follows_the_declared_order(self):
        row = transaction_row(fake_tx(), SIDE_LABELS, SOURCE_LABELS)
        self.assertEqual(len(row), len(CSV_COLUMNS))
        self.assertEqual(row[0], "2026-01-10 10:00:00", "第一列是时间")
        self.assertEqual(row[1], "买入", "第二列是方向")
        self.assertEqual(row[-1], "", "最后一列是备注")

    def test_row_order_follows_a_custom_column_list(self):
        """★ 列序只由 `columns` 决定 —— 把两列换个位置，输出跟着换。"""
        swapped = (CSV_COLUMNS[1], CSV_COLUMNS[0])
        row = transaction_row(fake_tx(), SIDE_LABELS, SOURCE_LABELS, columns=swapped)
        self.assertEqual(row, ["买入", "2026-01-10 10:00:00"])

    def test_a_column_without_a_value_raises_instead_of_filling_blanks(self):
        """★ 加了列却忘了给取值 → KeyError，消息里点名是哪一列。

        静默多一列空值比报错难查得多：导出的文件看起来完全正常。
        """
        extended = CSV_COLUMNS + (("broker_fee", "券商其他费用"),)
        with self.assertRaises(KeyError) as ctx:
            transaction_row(fake_tx(), SIDE_LABELS, SOURCE_LABELS, columns=extended)
        self.assertIn("broker_fee", str(ctx.exception))

    def test_labels_must_be_passed_in(self):
        """标签表没有默认值：调用点忘了传就是一个 TypeError，不是少一列中文。"""
        with self.assertRaises(TypeError):
            transaction_row(fake_tx())  # type: ignore[call-arg]


class TestTimeIsLocalised(unittest.TestCase):
    def test_utc_is_converted_to_the_given_zone(self):
        """★ 库里是 UTC（`USE_TZ = True`）；不转就比界面整整少 8 小时。"""
        raw = datetime(2026, 1, 10, 2, 0, tzinfo=timezone.utc)
        self.assertEqual(fmt_traded_at(raw, tz=SHANGHAI), "2026-01-10 10:00:00")
        # 刻意**不**断言「不给 tz 时等于 10:00」：那靠的是跑测试那台机器的本机时区，
        # 换一台 UTC 的机器就红 —— 一个只在别人机器上失败的断言比没有断言更坏。
        self.assertNotEqual(fmt_traded_at(raw, tz=timezone.utc), "2026-01-10 10:00:00")

    def test_the_conversion_actually_happens(self):
        """对照：同一个瞬间在 UTC 下是另一个数 —— 证明上面那条不是恒真。"""
        raw = datetime(2026, 1, 10, 2, 0, tzinfo=timezone.utc)
        self.assertEqual(fmt_traded_at(raw, tz=timezone.utc), "2026-01-10 02:00:00")
        self.assertNotEqual(
            fmt_traded_at(raw, tz=timezone.utc), fmt_traded_at(raw, tz=SHANGHAI),
        )

    def test_naive_time_is_left_alone(self):
        """不带时区的值已经是当地墙上时间，再转一次就是错的。"""
        self.assertEqual(fmt_traded_at(datetime(2026, 1, 10, 10, 0), tz=SHANGHAI), "2026-01-10 10:00:00")

    def test_empty(self):
        self.assertEqual(fmt_traded_at(None), "")


class TestFakeRowsRenderEndToEnd(unittest.TestCase):
    """一个完整样本：从「一条流水」到「CSV 里那一行」，中间不停手。"""

    def test_a_buy_row(self):
        row = transaction_row(fake_tx(), SIDE_LABELS, SOURCE_LABELS, tz=SHANGHAI)
        text = render_csv([row]).lstrip(BOM)
        lines = text.split(EOL)
        self.assertEqual(
            lines[1],
            "2026-01-10 10:00:00,买入,601398,工商银行,主账户,1000,6.5,-6505,5,0,CNY,1,手动,",
        )

    def test_a_deposit_row_without_an_asset(self):
        """入金没有标的、没有数量单价 —— 那几格必须是空的，不是 `None` 也不是 `0`。"""
        tx = fake_tx(
            side="DEPOSIT", asset=None, quantity=None, price=None,
            amount=Decimal("100000.00000000"), fee=Decimal("0"), tax=Decimal("0"),
            note="=1+1",
        )
        row = transaction_row(tx, SIDE_LABELS, SOURCE_LABELS, tz=SHANGHAI)
        text = render_csv([row]).lstrip(BOM)
        self.assertEqual(
            text.split(EOL)[1],
            "2026-01-10 10:00:00,入金,,,主账户,,,100000,0,0,CNY,1,手动,'=1+1",
        )

    def test_unknown_enum_values_fall_back_to_the_raw_code(self):
        """标签表没这一项时原样落地 —— 不猜、也不写个「未知」。"""
        tx = fake_tx(side="SPLIT", source="IMPORT")
        row = transaction_row(tx, SIDE_LABELS, SOURCE_LABELS, tz=SHANGHAI)
        self.assertIn("SPLIT", row)
        self.assertIn("IMPORT", row)


class TestFileNameAndHeaders(unittest.TestCase):
    def test_names_come_in_two_forms(self):
        ascii_name, unicode_name = export_names(datetime(2026, 9, 17, 22, 15))
        self.assertEqual(ascii_name, "asset-ledger-20260917-2215.csv")
        self.assertEqual(unicode_name, "asset-ledger-流水-20260917-2215.csv")
        self.assertTrue(ascii_name.isascii(), "给旧客户端的那个必须是纯 ASCII")

    def test_content_disposition_is_latin1_safe(self):
        """★ HTTP 头只能是 latin-1；中文名直接塞进去会让 Django 抛异常 ——
        表现为「点了导出没反应」，而用户看不到任何原因。
        """
        header = content_disposition(*export_names(datetime(2026, 9, 17, 22, 15)))
        header.encode("latin-1")  # 编码不了就会在这里抛，正是要挡的那件事
        self.assertIn('filename="asset-ledger-20260917-2215.csv"', header)
        self.assertIn("filename*=UTF-8''asset-ledger-%E6%B5%81%E6%B0%B4-20260917-2215.csv", header)

    def test_the_header_would_be_broken_by_a_raw_chinese_name(self):
        """对照：把中文名直接塞进 `filename=` 就编不了 —— 上面那条不是恒真。"""
        with self.assertRaises(UnicodeEncodeError):
            'attachment; filename="asset-ledger-流水.csv"'.encode("latin-1")


class TestTheChecksAreNotVacuous(unittest.TestCase):
    """上面那些断言都得能真的红 —— 不然它们只是好看。"""

    def test_removing_the_bom_changes_the_output(self):
        self.assertNotEqual(render_csv([["a"]]), render_csv([["a"]]).lstrip("\ufeff"))

    def test_a_guarded_cell_differs_from_the_raw_cell(self):
        for raw in TestFormulaInjectionIsNeutralised.DANGEROUS:
            self.assertNotEqual(sanitize_cell(raw), raw)

    def test_a_quoted_cell_differs_from_the_raw_cell(self):
        self.assertNotEqual(quote_cell("a,b"), "a,b")

    def test_the_row_builder_would_notice_a_resorted_column_list(self):
        original = transaction_row(fake_tx(), SIDE_LABELS, SOURCE_LABELS)
        reordered = transaction_row(
            fake_tx(), SIDE_LABELS, SOURCE_LABELS, columns=tuple(reversed(CSV_COLUMNS)),
        )
        self.assertNotEqual(original, reordered)


if __name__ == "__main__":
    unittest.main()
