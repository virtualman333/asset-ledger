# -*- coding: utf-8 -*-
"""股息导出的**取值**契约：一条归集后的股息怎么变成 CSV 里的一行。

这里管的是「每格填什么」，不是「文件长什么样」（BOM / CRLF / 转义那一层由
`apps/core/tests/test_export_contract.py` 与 `apps/core/csv_export.py` 管，
两份导出共用）。

两个「错了也看不出来」的地方，都在这个文件里钉住
------------------------------------------------
1. **明细列留空 ≠ 0。** 流水录入的股息查不到税前与税费。那几格写 `0` 等于替用户宣布
   「这笔没收过税」；写空格子才是「这里没数据可查」。两种都会出现在同一个文件里。
2. **`origin` 的中文名必须覆盖全部来源。** 归集规则将来加第三种来源时，导出里会多出
   一格英文 —— 不报错，只是文件里混了英文。

本文件**不 import django**（`dividend_export` / `dividend_income` 都是纯模块），
所以它能待在「不需要数据库、不需要 Django」的那一侧。

跑法（在 server/ 下）：python -m unittest discover -s apps -t .
"""
import ast
import unittest
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

from apps.analytics import dividend_export
from apps.analytics.dividend_export import (
    DIVIDEND_CSV_COLUMNS,
    dividend_cells,
    dividend_row,
    export_names,
    render_csv,
)
from apps.analytics.dividend_income import ORIGIN_LABELS, DividendEntry

SERVER = Path(__file__).resolve().parents[3]
DIVIDEND_INCOME = SERVER / "apps" / "analytics" / "dividend_income.py"

ASSETS = {1: ("600050", "中国联通"), 2: ("AAPL", "Apple")}
ACCOUNTS = {7: "华泰证券", 8: "富途"}


def record_entry(**over) -> DividendEntry:
    """一条「有股息明细」的条目（origin="record"）。"""
    base = dict(
        asset_id=1,
        account_id=7,
        currency="CNY",
        amount=Decimal("810.00000000"),
        pay_date=date(2026, 6, 30),
        origin="record",
        gross=Decimal("900.00000000"),
        tax=Decimal("90.00000000"),
        ex_date=date(2026, 6, 20),
        shares=Decimal("1000.00000000"),
        amount_per_share=Decimal("0.90000000"),
        reinvested=False,
    )
    base.update(over)
    return DividendEntry(**base)


def flow_entry(**over) -> DividendEntry:
    """一条「只落流水、没有明细」的条目（origin="transaction"）。"""
    base = dict(
        asset_id=2,
        account_id=8,
        currency="USD",
        amount=Decimal("12.5"),
        pay_date=date(2026, 7, 1),
        origin="transaction",
    )
    base.update(over)
    return DividendEntry(**base)


def cells_by_title(entry, assets=ASSETS, accounts=ACCOUNTS) -> dict:
    """`{中文表头: 单元格文本}` —— 断言按表头写，不按列下标写。"""
    cells = dividend_cells(entry, assets, accounts)
    return {title: cells[key] for key, title in DIVIDEND_CSV_COLUMNS}


class TestTheColumnList(unittest.TestCase):
    def test_keys_and_titles_are_each_unique(self):
        keys = [k for k, _ in DIVIDEND_CSV_COLUMNS]
        titles = [t for _, t in DIVIDEND_CSV_COLUMNS]
        self.assertEqual(len(set(keys)), len(keys), f"取值键有重复：{keys}")
        self.assertEqual(len(set(titles)), len(titles), f"表头有重复：{titles}")

    def test_the_amount_column_is_called_net(self):
        """导的是**税后到手**那笔钱（归集口径），列名必须是「税后」而不是「金额」。"""
        self.assertIn(("net", "税后"), DIVIDEND_CSV_COLUMNS)
        self.assertNotIn("amount", [k for k, _ in DIVIDEND_CSV_COLUMNS])


class TestOneEntryMapsToOneRow(unittest.TestCase):
    def test_a_record_backed_entry_fills_every_column(self):
        row = cells_by_title(record_entry())
        self.assertEqual(row["标的代码"], "600050")
        self.assertEqual(row["标的名称"], "中国联通")
        self.assertEqual(row["账户"], "华泰证券")
        self.assertEqual(row["币种"], "CNY")
        self.assertEqual(row["除权日"], "2026-06-20")
        self.assertEqual(row["派息日"], "2026-06-30")
        self.assertEqual(row["持股数"], "1000")
        self.assertEqual(row["每股派息"], "0.9")
        self.assertEqual(row["税前"], "900")
        self.assertEqual(row["税费"], "90")
        self.assertEqual(row["税后"], "810", "税后取的是归集后的 amount（税后到手）")
        self.assertEqual(row["分红再投"], "否")
        self.assertEqual(row["来源"], "股息明细")

    def test_a_flow_only_entry_leaves_the_detail_columns_EMPTY_not_zero(self):
        """★ 本文件要钉的第一件事。

        写 `0` 等于替用户宣布「这笔没收过税」；空格子才是「这里没数据可查」。
        """
        row = cells_by_title(flow_entry())
        for title in ("除权日", "持股数", "每股派息", "税前", "税费", "分红再投"):
            self.assertEqual(
                row[title],
                "",
                f"「{title}」在没有明细的条目上写成了 {row[title]!r} —— 必须留空，"
                "0 与「查不到」对用户是两件事",
            )
        # 反面对照：同一个条目里，归集口径给的列必须有值，别把整行都写成空
        self.assertEqual(row["税后"], "12.5")
        self.assertEqual(row["派息日"], "2026-07-01")
        self.assertEqual(row["来源"], "流水录入")

    def test_reinvested_renders_as_chinese_not_as_a_boolean(self):
        self.assertEqual(cells_by_title(record_entry(reinvested=True))["分红再投"], "是")
        self.assertEqual(cells_by_title(record_entry(reinvested=False))["分红再投"], "否")
        self.assertEqual(cells_by_title(record_entry(reinvested=None))["分红再投"], "")

    def test_money_is_written_the_same_way_as_the_flow_export(self):
        """两份导出共用一套数值写法 —— 放进同一个 Excel 不会一个 `1E+2`、一个 `100`。"""
        row = cells_by_title(record_entry(gross=Decimal("100.00000000"), tax=Decimal("-0.00000000")))
        self.assertEqual(row["税前"], "100", "normalize() 的坑：100.00000000 不能写成 1E+2")
        self.assertEqual(row["税费"], "0", "-0 要归成 0")
        self.assertEqual(cells_by_title(flow_entry(amount=Decimal("0")))["税后"], "0")

    def test_a_missing_pay_date_is_an_empty_cell_not_a_guess(self):
        row = cells_by_title(flow_entry(pay_date=None))
        self.assertEqual(row["派息日"], "")
        self.assertEqual(row["税后"], "12.5")

    def test_currency_is_uppercased_by_the_collector_and_passed_through(self):
        """归集时 `currency` 已统一大写；导出不再动它（只此一处做归一）。"""
        self.assertEqual(cells_by_title(flow_entry(currency="usd"))["币种"], "usd")

    def test_a_datetime_pay_date_is_truncated_to_a_date(self):
        """传进来的是 datetime 也收 —— 但只取日期，不留一个 `00:00:00` 在格子里。"""
        row = cells_by_title(flow_entry(pay_date=datetime(2026, 7, 1, 15, 30)))
        self.assertEqual(row["派息日"], "2026-07-01")


class TestUnknownIdsDoNotBreakTheFile(unittest.TestCase):
    """查不到标的是**空**，不是异常。

    导出不该因为一条脏外键整份失败 —— 账目文件拿不到手，比某一格标的代码空着严重得多。
    """

    def test_an_unknown_asset_id_leaves_two_blank_cells(self):
        row = cells_by_title(record_entry(asset_id=999))
        self.assertEqual(row["标的代码"], "")
        self.assertEqual(row["标的名称"], "")
        self.assertEqual(row["税后"], "810", "同一条里别的列照常填")

    def test_a_missing_asset_id_is_blank_too(self):
        row = cells_by_title(record_entry(asset_id=None))
        self.assertEqual(row["标的代码"], "")

    def test_an_unknown_account_id_leaves_a_blank_cell(self):
        self.assertEqual(cells_by_title(record_entry(account_id=999))["账户"], "")
        self.assertEqual(cells_by_title(record_entry(account_id=None))["账户"], "")

    def test_empty_lookups_do_not_crash(self):
        row = cells_by_title(record_entry(), assets={}, accounts={})
        self.assertEqual(row["标的代码"], "")
        self.assertEqual(row["账户"], "")


class TestTheRowFollowsTheColumnOrder(unittest.TestCase):
    def test_the_row_is_the_columns_in_order(self):
        entry = record_entry()
        row = dividend_row(entry, ASSETS, ACCOUNTS)
        self.assertEqual(len(row), len(DIVIDEND_CSV_COLUMNS))
        cells = dividend_cells(entry, ASSETS, ACCOUNTS)
        self.assertEqual(row, [cells[k] for k, _ in DIVIDEND_CSV_COLUMNS])

    def test_a_column_without_a_value_raises_instead_of_adding_a_blank(self):
        """加了列却忘了给取值 —— 宁可当场炸，也不要悄悄多一列空值。

        异常里点的是**取值键**（`dividend_cells()` 的字典键），不是中文表头 ——
        照着报错去改代码时，要找的正是那个键。
        """
        with self.assertRaises(KeyError) as ctx:
            dividend_row(record_entry(), ASSETS, ACCOUNTS, columns=(("没人填的键", "新的列"),))
        self.assertIn("没人填的键", str(ctx.exception))


class TestTheFileName(unittest.TestCase):
    def test_the_chinese_name_says_dividend(self):
        _ascii, unicode_name = export_names(datetime(2026, 9, 18, 6, 30))
        self.assertEqual(unicode_name, "asset-ledger-股息-20260918-0630.csv")

    def test_the_ascii_name_is_pure_ascii(self):
        """HTTP 头的值只能是 latin-1；中文名进 `filename=` 会让 Django 抛异常。"""
        ascii_name, unicode_name = export_names(datetime(2026, 9, 18, 6, 30))
        self.assertEqual(ascii_name, "asset-ledger-20260918-0630.csv")
        self.assertEqual(
            ascii_name.encode("ascii").decode("ascii"),
            ascii_name,
            f"ASCII 名里出现了非 ASCII 字符：{ascii_name!r}",
        )
        self.assertIn("股息", unicode_name)


class TestTheWholeFile(unittest.TestCase):
    def test_bom_header_and_crlf(self):
        entries = [record_entry(), flow_entry()]
        rows = [dividend_row(e, ASSETS, ACCOUNTS) for e in entries]
        text = render_csv(rows)

        # 断言里**写死字面量**，不引用 BOM / EOL 两个常量 —— 引用常量就变成自证
        self.assertTrue(text.startswith("\ufeff"), "文件开头没有 BOM，Excel 会拿 GBK 解 UTF-8")
        self.assertEqual(text.count("\ufeff"), 1, "BOM 只该出现在文件最开头")

        body = text.lstrip("\ufeff")
        self.assertTrue(body.endswith("\r\n"))
        self.assertNotIn("\n", body.replace("\r\n", ""), "出现了裸 LF")
        lines = body.split("\r\n")
        self.assertEqual(len(lines), 4, "表头 + 2 行数据 + 末尾空段")

        self.assertEqual(lines[0], ",".join(t for _, t in DIVIDEND_CSV_COLUMNS))
        self.assertEqual(lines[1].split(",")[-1], "股息明细")
        self.assertEqual(lines[2].split(",")[-1], "流水录入")

    def test_a_formula_in_a_cell_is_defused(self):
        """公式注入防护来自共用那一层 —— 这里证明它**真的流经**了股息导出。"""
        rows = [dividend_row(flow_entry(currency="=1+1"), ASSETS, ACCOUNTS)]
        body = render_csv(rows).lstrip("\ufeff").split("\r\n")[1]
        self.assertIn("'=1+1", body, "以 = 开头的单元格必须被中和")


class TestEveryOriginHasALabel(unittest.TestCase):
    """★ 本文件要钉的第二件事：`ORIGIN_LABELS` 必须覆盖归集能产生的每一种来源。

    加集规则将来多一条来源、忘了加中文名，导出里就会多出一格英文 —— 不报错。
    真值直接从 `dividend_income.collect_dividends` 的源码里读（`origin="…"` 那个
    关键字实参），不是抄一份清单。
    """

    @staticmethod
    def origins_in_source():
        tree = ast.parse(DIVIDEND_INCOME.read_text(encoding="utf-8"))
        found = set()
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            for kw in node.keywords:
                if kw.arg != "origin":
                    continue
                if isinstance(kw.value, ast.Constant) and isinstance(kw.value.value, str):
                    found.add(kw.value.value)
        return found

    def test_the_source_really_has_origins_to_check(self):
        """自证：一个都没解出来就不叫通过，叫扫描面塌了。"""
        self.assertGreaterEqual(len(self.origins_in_source()), 2, "没从源码里解出 origin 字样")

    def test_labels_cover_every_origin(self):
        self.assertEqual(
            self.origins_in_source() - set(ORIGIN_LABELS),
            set(),
            "有 origin 没有中文名 —— 导出里会冒出一格英文，而不会有任何报错",
        )

    def test_labels_do_not_invent_origins(self):
        """反向：表里有源码里不存在的来源（删了规则忘了删标签）。"""
        self.assertEqual(set(ORIGIN_LABELS) - self.origins_in_source(), set())

    def test_the_parser_can_actually_find_an_origin(self):
        """反向对照：喂一段有 origin 字样的源码，必须解出来。"""
        sample = 'DividendEntry(amount=1, origin="record")\n'
        tree = ast.parse(sample)
        found = {
            kw.value.value
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            for kw in node.keywords
            if kw.arg == "origin" and isinstance(kw.value, ast.Constant)
        }
        self.assertEqual(found, {"record"})


class TestTheChecksAreNotVacuous(unittest.TestCase):
    def test_an_unknown_origin_falls_back_to_the_raw_value(self):
        """没登记的来源照原样显示（英文）—— 这**正是**上面那条锁要防的形态，
        所以这里把它写下来：锁一旦失效，用户看到的就是这个。"""
        self.assertEqual(dividend_export.ORIGIN_LABELS.get("brand_new"), None)
        row = cells_by_title(record_entry(origin="brand_new"))
        self.assertEqual(row["来源"], "brand_new")

    def test_the_net_column_is_not_reusing_gross(self):
        """对照：把税前/税后互换，上面那些断言必须能看出来。"""
        row = cells_by_title(record_entry())
        self.assertNotEqual(row["税前"], row["税后"])

    def test_a_blank_detail_column_is_told_apart_from_zero(self):
        """对照：0 与空串在这一列上是两件事 —— 断言确实能分辨。"""
        zero = cells_by_title(record_entry(gross=Decimal("0")))["税前"]
        blank = cells_by_title(flow_entry())["税前"]
        self.assertEqual((zero, blank), ("0", ""))
        self.assertNotEqual(zero, blank)


if __name__ == "__main__":
    unittest.main()
