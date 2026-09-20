# -*- coding: utf-8 -*-
"""持仓导出的**取值**契约：一条持仓怎么变成 CSV 里的一行。

这里管的是「每格填什么」，不是「文件长什么样」（BOM / CRLF / 转义那一层由
`apps/core/tests/test_export_contract.py` 与 `apps/core/csv_export.py` 管，
三份导出共用）。

三个「错了也看不出来」的地方，都在这个文件里钉住
------------------------------------------------
1. **拿不到报价的三格留空 ≠ 0。** 现价 / 市值 / 浮动盈亏在标的没有行情快照时是 `None`。
   写 `0` 等于替用户宣布「这个标的现在不值钱」—— 而真相是库里连一条快照都没有。
   两种都会出现在同一个文件里，所以必须分得清。
2. **字段缺失必须炸，不许落成空串。** 上游 `build_positions()` 的形状变了，落成空串
   就是整列悄悄清空，而文件本身看起来完全正常。
3. **市场中文名只许有一处来源**（模型的 `TextChoices`）。手抄第二份的那一刻起，两边
   就会在「模型加了新市场」时分开漂，而漂了不报错 —— 文件里多一格英文。

本文件**不 import django**（`positions_export` 是纯模块），所以它能待在
「不需要数据库、不需要 Django」的那一侧。

跑法（在 server/ 下）：python -m unittest discover -s apps -t .
"""
import ast
import unittest
from datetime import datetime
from decimal import Decimal
from pathlib import Path

from apps.analytics import positions_export
from apps.analytics.positions_export import (
    POSITIONS_CSV_COLUMNS,
    POSITION_FIELDS,
    export_names,
    positions_cells,
    positions_row,
    render_csv,
)
from apps.core import csv_export

SERVER = Path(__file__).resolve().parents[3]
CORE_MODELS = SERVER / "apps" / "core" / "models.py"
ANALYTICS_VIEWS = SERVER / "apps" / "analytics" / "views.py"
POSITIONS_MODULE = SERVER / "apps" / "analytics" / "positions_export.py"

#: 测试里注入的标签表 —— 与 `views.MARKET_LABELS`（`dict(Market.choices)`）同形。
MARKET_LABELS = {"A": "A股", "HK": "港股", "US": "美股", "CRYPTO": "数字货币"}


def position(**over) -> dict:
    """一条**有报价**的持仓（`build_positions()` 的输出形状：数值是字符串）。"""
    base = dict(
        account_id=7,
        account_name="华泰证券",
        asset_id=1,
        symbol="600050",
        name="中国联通",
        market="A",
        currency="CNY",
        quantity="1000.00000000",
        avg_cost="5.20000000",
        cost_basis="5200.00000000",
        realized_pnl="120.00000000",
        dividend_total="810.00000000",
        annual_dividend="810.00000000",
        dividend_yield="0.15576923",
        last_price="6.10000000",
        market_value="6100.00000000",
        unrealized_pnl="900.00000000",
    )
    base.update(over)
    return base


def cells_by_title(pos, market_labels=None) -> dict:
    """一行 → `{中文表头: 值}`，让断言读起来就是文件里的样子。"""
    labels = MARKET_LABELS if market_labels is None else market_labels
    cells = positions_cells(pos, labels)
    return {title: cells[key] for key, title in POSITIONS_CSV_COLUMNS}


class TestTheColumnMappingIsComplete(unittest.TestCase):
    """列清单与取值表必须**互相覆盖** —— 少一边就是「多一列空值」或「少一列数据」。"""

    def test_every_column_has_a_value(self):
        cells = positions_cells(position(), MARKET_LABELS)
        self.assertEqual(
            sorted(cells), sorted(key for key, _ in POSITIONS_CSV_COLUMNS),
            "取值表的键与列清单对不上 —— 加列时漏了给取值。",
        )

    def test_no_duplicate_column_keys_or_titles(self):
        keys = [key for key, _ in POSITIONS_CSV_COLUMNS]
        titles = [title for _, title in POSITIONS_CSV_COLUMNS]
        self.assertEqual(len(set(keys)), len(keys), f"列键重复：{keys}")
        self.assertEqual(len(set(titles)), len(titles), f"中文表头重复：{titles}")

    def test_the_field_tables_do_not_overlap(self):
        """同一条持仓字段被两张表都读走 → 出两列一样的数，而没人会觉得有问题。"""
        fields = [field for _, field in positions_export.TEXT_FIELDS + positions_export.NUMERIC_FIELDS]
        self.assertEqual(len(set(fields)), len(fields), f"字段被读了两次：{fields}")
        self.assertEqual(
            len(POSITION_FIELDS), len(fields) + len(positions_export.EXTRA_FIELDS),
            "POSITION_FIELDS 与两张字段表对不上 —— 缺字段守卫会漏掉东西。",
        )

    def test_the_column_list_is_long_enough(self):
        """自证：清单被削了要红，而不是「反正相等」。"""
        self.assertGreaterEqual(len(POSITIONS_CSV_COLUMNS), 10)


class TestMissingPriceIsBlankNotZero(unittest.TestCase):
    """★ 本文件要钉的第一件事：拿不到报价时那三格是**空**，不是 `0`。"""

    def test_the_three_price_columns_go_blank(self):
        row = cells_by_title(
            position(last_price=None, market_value=None, unrealized_pnl=None)
        )
        self.assertEqual(row["现价"], "")
        self.assertEqual(row["市值"], "")
        self.assertEqual(row["浮动盈亏"], "")

    def test_blank_is_not_the_string_zero(self):
        row = cells_by_title(position(last_price=None, market_value=None, unrealized_pnl=None))
        for title in ("现价", "市值", "浮动盈亏"):
            self.assertNotEqual(row[title], "0", f"「{title}」把「不知道」写成了 0")

    def test_a_real_zero_is_written_as_zero(self):
        """对照：真的是 0 就写 0 —— 两者必须能分辨，否则上面那三条没意义。"""
        row = cells_by_title(position(realized_pnl="0.00000000", unrealized_pnl="0.00000000"))
        self.assertEqual(row["已实现盈亏"], "0")
        self.assertEqual(row["浮动盈亏"], "0")

    def test_a_yield_that_cannot_be_computed_goes_blank(self):
        """成本为 0 时股息率是「算不出」而不是 0% —— 口径在 `dividend_income`。"""
        self.assertEqual(cells_by_title(position(dividend_yield=None))["股息率"], "")
        self.assertEqual(cells_by_title(position(dividend_yield="0"))["股息率"], "0")


class TestTheNumberFormattingIsSharedWithTheOtherExports(unittest.TestCase):
    """金额写法必须与另两份导出逐字一致 —— 否则同一个数在两个出口读起来是两个数。"""

    def test_the_decimal_zeros_are_trimmed(self):
        row = cells_by_title(position(cost_basis="1000.00000000"))
        self.assertEqual(row["持仓成本"], "1000")

    def test_a_ratio_keeps_its_significant_digits(self):
        """`normalize()` 会产出 `1E-2` 这种写法 —— 必须已经落地成小数。"""
        self.assertEqual(cells_by_title(position(dividend_yield="0.03250000"))["股息率"], "0.0325")
        self.assertEqual(cells_by_title(position(dividend_yield="0.01000000"))["股息率"], "0.01")

    def test_a_negative_value_keeps_its_minus(self):
        self.assertEqual(cells_by_title(position(unrealized_pnl="-900.50000000"))["浮动盈亏"], "-900.5")
        self.assertEqual(cells_by_title(position(realized_pnl=Decimal("-0.00000000")))["已实现盈亏"], "0")

    def test_it_really_calls_the_shared_formatter(self):
        """不是「看起来像」—— 必须是**同一个函数对象**，共用的那一份实现。"""
        self.assertIs(positions_export.fmt_decimal, csv_export.fmt_decimal)


class TestTheMarketLabelComesFromTheModel(unittest.TestCase):
    """★ 本文件要钉的第三件事：市场中文名只有一处来源。"""

    @staticmethod
    def market_codes():
        """AST 读 `class Market(models.TextChoices)` 里的市场码（不 import django）。

        `TextChoices` 的写法是 `A = "A", "A股"` —— 右值是**元组**，第一个元素才是码。
        只认「第一个元素是字符串常量」的那种赋值形态；认不出来就少一个，所以外面
        有一条下限断言盯着解析面，不许它悄悄空掉。
        """
        tree = ast.parse(CORE_MODELS.read_text(encoding="utf-8"))
        for node in tree.body:
            if not isinstance(node, ast.ClassDef) or node.name != "Market":
                continue
            codes = []
            for stmt in node.body:
                if not isinstance(stmt, ast.Assign):
                    continue
                value = stmt.value
                first = value.elts[0] if isinstance(value, ast.Tuple) and value.elts else value
                if isinstance(first, ast.Constant) and isinstance(first.value, str):
                    codes.append(first.value)
            return codes
        return []

    def test_the_source_really_has_markets_to_read(self):
        """自证：一个都没解出来就不叫通过，叫扫描面塌了。"""
        self.assertGreaterEqual(len(self.market_codes()), 5, "没从 models.py 里解出 Market 的码")

    def test_a_known_code_becomes_chinese(self):
        self.assertEqual(cells_by_title(position(market="HK"))["市场"], "港股")

    def test_an_unknown_code_is_shown_raw(self):
        """脏数据要看得见 —— 留空等于把它藏起来。"""
        self.assertEqual(cells_by_title(position(market="BRAND_NEW"))["市场"], "BRAND_NEW")

    def test_an_empty_market_is_blank(self):
        self.assertEqual(cells_by_title(position(market=""))["市场"], "")

    def test_the_labels_table_is_injected_not_guessed(self):
        """传一张空表进去，中文名必须全掉 —— 证明它真的在用调用方给的那张。"""
        row = cells_by_title(position(market="A"), market_labels={})
        self.assertEqual(row["市场"], "A")

    def test_nobody_handwrites_a_market_dictionary(self):
        """★ 结构锁：视图与纯模块里都不许出现「以市场码为键的字典字面量」。

        手抄第二份的那一刻起，模型加新市场时两边就会分开漂，而漂了不报错。
        """
        codes = set(self.market_codes())
        # 解析面自己先自证：解析不出来时这条锁会恒真（本轮实测撞过一次 ——
        # `A = "A", "A股"` 的右值是元组，只认 `Constant` 的写法解出 0 个码）。
        self.assertGreaterEqual(len(codes), 5, "市场码一个都没解出来 —— 这条锁现在是恒真的")
        for path in (ANALYTICS_VIEWS, POSITIONS_MODULE):
            found = dict_literals_with_market_keys(path.read_text(encoding="utf-8"), codes)
            self.assertEqual(
                found, [], f"{path.name} 里出现了手抄的市场字典：{found} —— 它该来自 dict(Market.choices)"
            )

    def test_the_view_computes_the_labels_from_the_model(self):
        """视图里那张表必须是 `dict(Market.choices)` 现算的，不是字面量。"""
        value = assignment_value(ANALYTICS_VIEWS.read_text(encoding="utf-8"), "MARKET_LABELS")
        self.assertIsNotNone(value, "views.py 里找不到 `MARKET_LABELS = ...`")
        self.assertTrue(
            value.startswith("dict(") and value.endswith(".choices)"),
            f"`MARKET_LABELS = {value}` 不是从模型现算的 —— 手抄一份就会漂。",
        )


def dict_literals_with_market_keys(source, codes):
    """源码里**以市场码为键**的字典字面量 → 命中的键。

    只看字面量 `{...}`：`dict(...)` / 现算出来的表不算（那正是我们要的写法）。
    """
    hits = []
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if not isinstance(node, ast.Dict):
            continue
        for key in node.keys:
            if isinstance(key, ast.Constant) and key.value in codes:
                hits.append(key.value)
    return sorted(set(hits))


def assignment_value(source, name):
    """模块级 `name = ...` 的右值写法（`ast.unparse`）；找不到返回 None。"""
    tree = ast.parse(source)
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        if not any(getattr(t, "id", None) == name for t in node.targets):
            continue
        return ast.unparse(node.value)
    return None


class TestTheStructuralLockIsNotVacuous(unittest.TestCase):
    """反向对照：把「手抄的市场字典」喂给同一条判据，该红的必须红。"""

    def test_a_handwritten_market_dict_is_reported(self):
        sample = 'MARKET_LABELS = {"A": "A股", "HK": "港股"}\n'
        self.assertEqual(dict_literals_with_market_keys(sample, {"A", "HK"}), ["A", "HK"])

    def test_a_computed_table_is_not_reported(self):
        sample = "MARKET_LABELS = dict(Market.choices)\n"
        self.assertEqual(dict_literals_with_market_keys(sample, {"A", "HK"}), [])

    def test_a_dict_of_other_things_is_not_reported(self):
        """不是所有字典字面量都算 —— 只认键落在市场码上的那些。"""
        sample = 'X = {"CNY": 1, "USD": 7.1}\n'
        self.assertEqual(dict_literals_with_market_keys(sample, {"A", "HK"}), [])

    def test_the_assignment_reader_can_report_a_literal(self):
        self.assertEqual(assignment_value("MARKET_LABELS = {}\n", "MARKET_LABELS"), "{}")
        self.assertIsNone(assignment_value("OTHER = 1\n", "MARKET_LABELS"))


class TestMissingFieldsRaiseInsteadOfGoingBlank(unittest.TestCase):
    """★ 本文件要钉的第二件事：字段缺失是**错误**，不是空。

    ⚠ 这一组最初写弱了，值得写下来：只断言「异常消息里出现了字段名」是**不够的** ——
    `pos[field]` 少一个键本来就会抛 `KeyError('market_value')`，消息里自带字段名，
    于是把守卫整段清空（`missing = [...]` 的扫描面改成空）它照样绿。
    现在改成同时要求**守卫自己的那句话**（`形状变了`），守卫被拆掉时那条判据会红。
    """

    #: 守卫自己的措辞。写在断言的这一侧（而不是从模块里 import），否则改文案它跟着变。
    GUARD_WORDS = "形状变了"

    def test_a_missing_field_names_itself_and_says_why(self):
        for field in ("market_value", "currency", "market"):
            pos = position()
            pos.pop(field)
            with self.assertRaises(KeyError) as ctx:
                positions_cells(pos, MARKET_LABELS)
            message = str(ctx.exception)
            self.assertIn(field, message, "报错里没点名是哪个字段丢了")
            self.assertIn(
                self.GUARD_WORDS, message,
                f"丢 {field} 时报的是裸 KeyError（{message}）而不是守卫的说明 —— "
                "说明守卫没走到，这一组的判据是假的",
            )

    def test_the_guard_covers_every_field_the_mapping_reads(self):
        """把每个字段逐个删掉，每一次都必须炸 —— 漏一个就是一条静默路径。"""
        for field in POSITION_FIELDS:
            pos = position()
            self.assertIn(field, pos, f"字段表里的 {field} 不在夹具里，这条对照是假的")
            pos.pop(field)
            with self.assertRaises(KeyError, msg=f"删掉 {field} 没有炸"):
                positions_cells(pos, MARKET_LABELS)

    def test_every_subscripted_field_is_registered(self):
        """★ 结构锁：函数体里 `pos["…"]` 点到的每个字段都必须在 `POSITION_FIELDS` 里。

        没有这一条的话，`EXTRA_FIELDS` 被清空、或者谁新写一个 `pos["新字段"]`，
        守卫就少守一个 —— 而上面两条行为断言仍然绿（裸 KeyError 顶上来，消息里还带字段名）。
        """
        subscripted = literal_subscripts_in_positions_cells()
        self.assertTrue(subscripted, "一个 `pos[\"…\"]` 都没解出来 —— 这条锁现在是恒真的")
        self.assertEqual(
            subscripted - set(POSITION_FIELDS), set(),
            f"这些字段被读了却不在守卫面里：{sorted(subscripted - set(POSITION_FIELDS))}",
        )

    def test_a_none_value_does_not_raise(self):
        """`None` 与「没有这个键」是两件事：前者合法（留空），后者是上游坏了。"""
        positions_cells(position(last_price=None), MARKET_LABELS)


def literal_subscripts_in_positions_cells():
    """`positions_cells()` 函数体里 `pos["字面量"]` 的下标集合（AST，剥掉 docstring）。

    只认**字面量**下标：`pos[field]` 那种由字段表驱动的写法是我们要的形态，
    字段表的完整性由另一条断言管（`POSITION_FIELDS` 的长度与不重叠）。
    """
    tree = ast.parse(POSITIONS_MODULE.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef) or node.name != "positions_cells":
            continue
        found = set()
        for inner in ast.walk(node):
            if not isinstance(inner, ast.Subscript):
                continue
            target = inner.value
            if not (isinstance(target, ast.Name) and target.id == "pos"):
                continue
            if isinstance(inner.slice, ast.Constant) and isinstance(inner.slice.value, str):
                found.add(inner.slice.value)
        return found
    raise AssertionError("`positions_cells()` 不见了 —— 这条锁的落点没了")


class TestTheRowFollowsTheColumnOrder(unittest.TestCase):
    def test_the_row_is_the_columns_in_order(self):
        pos = position()
        row = positions_row(pos, MARKET_LABELS)
        self.assertEqual(len(row), len(POSITIONS_CSV_COLUMNS))
        cells = positions_cells(pos, MARKET_LABELS)
        self.assertEqual(row, [cells[k] for k, _ in POSITIONS_CSV_COLUMNS])

    def test_a_column_without_a_value_raises_instead_of_adding_a_blank(self):
        """加了列却忘了给取值 —— 宁可当场炸，也不要悄悄多一列空值。"""
        with self.assertRaises(KeyError) as ctx:
            positions_row(position(), MARKET_LABELS, columns=(("没人填的键", "新的列"),))
        self.assertIn("没人填的键", str(ctx.exception))


class TestTheFileName(unittest.TestCase):
    def test_the_chinese_name_says_positions(self):
        _ascii, unicode_name = export_names(datetime(2026, 9, 21, 5, 30))
        self.assertEqual(unicode_name, "asset-ledger-持仓-20260921-0530.csv")

    def test_the_ascii_name_is_pure_ascii(self):
        """HTTP 头的值只能是 latin-1；中文名进 `filename=` 会让 Django 抛异常。"""
        ascii_name, unicode_name = export_names(datetime(2026, 9, 21, 5, 30))
        self.assertEqual(ascii_name, "asset-ledger-20260921-0530.csv")
        self.assertEqual(ascii_name.encode("ascii").decode("ascii"), ascii_name)
        self.assertIn("持仓", unicode_name)

    def test_the_three_exports_have_three_different_labels(self):
        """三份导出的中文名不能同标签 —— 同一分钟里下载两次会互相覆盖。"""
        from apps.analytics.dividend_export import export_names as dividend_names
        from apps.transactions.export_rules import export_names as flow_names

        when = datetime(2026, 9, 21, 5, 30)
        labels = {names(when)[1] for names in (export_names, dividend_names, flow_names)}
        self.assertEqual(len(labels), 3, f"三份导出的中文名撞了：{labels}")


class TestTheWholeFile(unittest.TestCase):
    def test_bom_header_and_crlf(self):
        rows = [
            positions_row(position(), MARKET_LABELS),
            positions_row(
                position(symbol="AAPL", market="US", currency="USD", last_price=None,
                         market_value=None, unrealized_pnl=None),
                MARKET_LABELS,
            ),
        ]
        text = render_csv(rows)

        # 断言里**写死字面量**，不引用 BOM / EOL 两个常量 —— 引用常量就变成自证
        self.assertTrue(text.startswith("\ufeff"), "文件开头没有 BOM，Excel 会拿 GBK 解 UTF-8")
        self.assertEqual(text.count("\ufeff"), 1, "BOM 只该出现在文件最开头")

        body = text.lstrip("\ufeff")
        self.assertTrue(body.endswith("\r\n"))
        self.assertNotIn("\n", body.replace("\r\n", ""), "出现了裸 LF")

        lines = body.split("\r\n")
        self.assertEqual(len(lines), 4, "表头 + 2 行数据 + 末尾空段")
        self.assertEqual(lines[0], ",".join(t for _, t in POSITIONS_CSV_COLUMNS))
        self.assertEqual(lines[1].split(",")[0], "600050")
        self.assertEqual(lines[2].split(",")[0], "AAPL")

    def test_a_formula_in_a_cell_is_defused(self):
        """公式注入防护来自共用那一层 —— 这里证明它**真的流经**了持仓导出。

        标的名称来自用户输入 / Agent 对截图的识别，也就是说不完全由用户自己掌控。
        """
        rows = [positions_row(position(name="=1+1"), MARKET_LABELS)]
        body = render_csv(rows).lstrip("\ufeff").split("\r\n")[1]
        self.assertIn("'=1+1", body, "以 = 开头的单元格必须被中和")

    def test_a_blank_price_cell_stays_blank_in_the_file(self):
        """★ 端到端再确认一次：文件里那三格是空的，不是 0。"""
        rows = [positions_row(position(last_price=None, market_value=None,
                                       unrealized_pnl=None), MARKET_LABELS)]
        body = render_csv(rows).lstrip("\ufeff").split("\r\n")[1]
        titles = [t for _, t in POSITIONS_CSV_COLUMNS]
        cells = body.split(",")
        for title in ("现价", "市值", "浮动盈亏"):
            self.assertEqual(cells[titles.index(title)], "", f"「{title}」在文件里不是空格子")


if __name__ == "__main__":
    unittest.main()
