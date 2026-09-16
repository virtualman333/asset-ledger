# -*- coding: utf-8 -*-
"""`apps/analytics/valuation.py` 的测试（纯 Python，不需要数据库、不需要 Django）。

锁住的是一个曾经静默的假设：**「拿不到报价」被当成了「不值钱」**。
`build_positions` 里没有报价的持仓 `market_value` 是 `None`，
`build_summary` 用 `if pos["market_value"]` 一筛就把它从合计里删掉了 ——
总资产偏小、浮盈偏小，而响应里没有任何字段能区分
「真的没有持仓」和「有持仓但拿不到价」。

XIRR 那边更狠：终值只在市值非 0 时才进现金流，
于是有持仓没报价时年化会把「本金还在」算成「本金没了」，
还把原因写成「现金流不足」，把排查方向指错。
"""
from decimal import Decimal
from pathlib import Path
import unittest

from apps.analytics.valuation import (
    is_fx_missing,
    terminal_value,
    valuate_positions,
    valuation_note,
)


def position(
    symbol="600519",
    currency="CNY",
    quantity="100",
    cost_basis="100000",
    market_value=None,
    unrealized_pnl=None,
):
    """一条 build_positions 形态的持仓（只保留估值用得上的字段，值都是字符串）。"""
    return {
        "asset_id": 1,
        "symbol": symbol,
        "name": f"{symbol} 名称",
        "currency": currency,
        "quantity": quantity,
        "cost_basis": cost_basis,
        "market_value": market_value,
        "unrealized_pnl": unrealized_pnl,
    }


def flat_rate(_currency=None):
    return Decimal("1")


class PricedAndUnpricedTest(unittest.TestCase):
    """有报价的算市值，没报价的单独归到成本里 —— 两边都不许消失"""

    def test_有报价的持仓进市值(self):
        got = valuate_positions(
            [position(market_value="120000", unrealized_pnl="20000")], "CNY", flat_rate
        )
        self.assertEqual(got["market_value"], Decimal("120000"))
        self.assertEqual(got["unrealized_pnl"], Decimal("20000"))
        self.assertEqual(got["priced_count"], 1)
        self.assertEqual(got["unpriced_cost_basis"], Decimal("0"))
        self.assertEqual(got["unpriced_symbols"], [])

    def test_没报价的持仓按成本计入且点名(self):
        # 这是旧行为下彻底消失的那一类
        got = valuate_positions(
            [
                position(symbol="600519", market_value="120000", unrealized_pnl="20000"),
                position(symbol="000001", cost_basis="50000"),
            ],
            "CNY",
            flat_rate,
        )
        self.assertEqual(got["market_value"], Decimal("120000"))  # 只含有价的
        self.assertEqual(got["unpriced_cost_basis"], Decimal("50000"))  # 没价的没丢
        self.assertEqual(got["unpriced_symbols"], ["000001"])
        self.assertEqual(got["priced_count"], 1)

    def test_已清仓的持仓不算没报价(self):
        # 数量为 0：它是「卖光了」，不是「拿不到价」。前者成本本来就该是 0，
        # 把它算进 unpriced 会让提示文案天天喊「有持仓没有报价」。
        got = valuate_positions([position(quantity="0", cost_basis="0")], "CNY", flat_rate)
        self.assertEqual(got["unpriced_cost_basis"], Decimal("0"))
        self.assertEqual(got["unpriced_symbols"], [])

    def test_没报价且没数量也没成本的一行不会凭空多出成本(self):
        got = valuate_positions([position(quantity="0", cost_basis="0")], "CNY", flat_rate)
        self.assertEqual(got["unpriced_cost_basis"], Decimal("0"))

    def test_多个没报价的持仓按符号排序去重(self):
        got = valuate_positions(
            [
                position(symbol="000001", cost_basis="10"),
                position(symbol="600519", cost_basis="20"),
                position(symbol="000001", cost_basis="5"),
            ],
            "CNY",
            flat_rate,
        )
        self.assertEqual(got["unpriced_symbols"], ["000001", "600519"])
        self.assertEqual(got["unpriced_cost_basis"], Decimal("35"))

    def test_符号为空时退到名称再退到id(self):
        # 提示文案里必须有个能认出来的东西，不能打印 "None"
        named = position(symbol="", cost_basis="7")
        named["name"] = "某某股份"
        self.assertEqual(valuate_positions([named], "CNY", flat_rate)["unpriced_symbols"], ["某某股份"])

        anonymous = position(symbol="", cost_basis="7")
        anonymous["name"] = ""
        anonymous["asset_id"] = 42
        self.assertEqual(valuate_positions([anonymous], "CNY", flat_rate)["unpriced_symbols"], ["42"])


class CurrencyTest(unittest.TestCase):
    """多币种折算：汇率缺失要报出来，但缺汇率不等于没报价"""

    def test_外币按汇率折算后再归集(self):
        got = valuate_positions(
            [position(currency="USD", market_value="10000", unrealized_pnl="1000")],
            "CNY",
            lambda c: Decimal("7.2") if c == "USD" else Decimal("1"),
        )
        self.assertEqual(got["market_value"], Decimal("72000.0"))
        self.assertEqual(got["unrealized_pnl"], Decimal("7200.0"))

    def test_没报价的外币持仓也按汇率折算成本(self):
        got = valuate_positions(
            [position(currency="USD", cost_basis="10000")],
            "CNY",
            lambda c: Decimal("7.2") if c == "USD" else Decimal("1"),
        )
        self.assertEqual(got["unpriced_cost_basis"], Decimal("72000.0"))

    def test_汇率缺失的币种被点名(self):
        got = valuate_positions([position(currency="HKD")], "CNY", flat_rate)
        self.assertEqual(got["fx_missing"], ["HKD"])

    def test_基准币自己不算缺汇率(self):
        got = valuate_positions([position(currency="CNY")], "CNY", flat_rate)
        self.assertEqual(got["fx_missing"], [])

    def test_大小写不同不算缺汇率(self):
        # 旧写法是拿原始字符串直接比：get_rate 内部本来就归一大小写、
        # 真的相等时返回 1，于是 "cny" 与基准 "CNY" 会被判成缺汇率，
        # 页面上白多一条「部分币种缺少汇率」。
        self.assertFalse(is_fx_missing("cny", "CNY", Decimal("1")))
        self.assertFalse(is_fx_missing("HKD", "hkd", Decimal("1")))
        self.assertTrue(is_fx_missing("HKD", "CNY", Decimal("1")))
        self.assertFalse(is_fx_missing("HKD", "CNY", Decimal("7.1")))


class TerminalValueTest(unittest.TestCase):
    """XIRR 收尾现金流：整个组合此刻值多少"""

    def test_终值等于市值加没报价的成本(self):
        valuation = valuate_positions(
            [
                position(symbol="A", market_value="120000", unrealized_pnl="20000"),
                position(symbol="B", cost_basis="50000"),
            ],
            "CNY",
            flat_rate,
        )
        self.assertEqual(terminal_value(valuation), Decimal("170000"))

    def test_全都没报价时终值是成本而不是0(self):
        # 这一步就是那个缺陷：以前 `if total_value:` 判的是「有报价的市值」，
        # 全都没报价时终值不进场 → 现金流只剩出入金 → 年化把本金算成没了。
        valuation = valuate_positions([position(cost_basis="50000")], "CNY", flat_rate)
        self.assertNotEqual(terminal_value(valuation), Decimal("0"))
        self.assertEqual(terminal_value(valuation), Decimal("50000"))

    def test_真清仓时终值为0(self):
        # 反方向也要成立：全都卖光了就该是 0，不能为了「别算成亏损」
        # 而硬塞一笔成本进去。
        valuation = valuate_positions(
            [position(quantity="0", cost_basis="0"), position(quantity="0", cost_basis="0")],
            "CNY",
            flat_rate,
        )
        self.assertEqual(terminal_value(valuation), Decimal("0"))


class ValuationNoteTest(unittest.TestCase):
    """年化旁边那句话必须指向正确的排查方向"""

    def test_一切正常时没有提示(self):
        valuation = valuate_positions(
            [position(market_value="120000", unrealized_pnl="20000")], "CNY", flat_rate
        )
        self.assertIsNone(valuation_note(valuation, 0.12))

    def test_有没报价的持仓必须点名(self):
        valuation = valuate_positions([position(symbol="000001", cost_basis="50000")], "CNY", flat_rate)
        note = valuation_note(valuation, 0.12)
        self.assertIn("000001", note)
        self.assertIn("没有报价", note)
        self.assertIn("按成本", note)

    def test_估值缺失时不许把原因说成现金流不足(self):
        # 旧文案对这两种情形都只会说「现金流不足或日期过于集中」，
        # 用户照着去查流水，永远查不出来。
        valuation = valuate_positions([position(symbol="000001")], "CNY", flat_rate)
        note = valuation_note(valuation, None)
        self.assertIn("没有报价", note)
        self.assertIn("现金流不足", note)  # 两个原因都要说

    def test_没有估值缺口且年化算不出来时只说现金流(self):
        valuation = valuate_positions(
            [position(market_value="120000", unrealized_pnl="20000")], "CNY", flat_rate
        )
        note = valuation_note(valuation, None)
        self.assertNotIn("没有报价", note)
        self.assertEqual(note, "现金流不足或日期过于集中，暂无法计算年化")

    def test_标的太多时只列前几个并给出总数(self):
        positions = [position(symbol=f"S{i}", cost_basis="1") for i in range(9)]
        valuation = valuate_positions(positions, "CNY", flat_rate)
        note = valuation_note(valuation, 0.1)
        self.assertIn("9 个持仓没有报价", note)
        self.assertIn("S0", note)
        self.assertNotIn("S8", note)


def code_only(source: str) -> str:
    """只留代码行，去掉注释。

    结构锁要读的是**实现**，不是注释里提到旧写法的那句话。
    （踩过：一条锁把注释里解释「旧实现不能这么写」的示例当成了实现，
    于是在已经修好的代码上变红；反过来也一样 —— 一条「不许再出现 X」的锁
    会被解释为什么删掉 X 的注释误伤。）
    """
    return "\n".join(line for line in source.splitlines() if not line.lstrip().startswith("#"))


class SummaryWiringTest(unittest.TestCase):
    """`build_summary` 必须真的走上面这套口径，而不是自己再算一遍"""

    @classmethod
    def setUpClass(cls):
        cls.source = (Path(__file__).resolve().parent.parent / "services.py").read_text(encoding="utf-8")
        cls.code = code_only(cls.source)

    def test_估值只经过纯函数(self):
        self.assertIn("valuate_positions(", self.source)

    def test_终值只经过terminal_value(self):
        self.assertIn("terminal_value(", self.source)

    def test_不许再按市值为0判断有没有持仓(self):
        # 旧写法：`if pos["market_value"]:` / `if total_value:`
        # 把它当成「没有持仓」，于是拿不到报价的持仓整段消失。
        self.assertNotIn('if pos["market_value"]', self.code)
        self.assertNotIn("if total_value:", self.code)

    def test_文案只有一处(self):
        # annualized_note 是旧的单句文案，容易和新的估值提示并存、互相矛盾
        self.assertNotIn("annualized_note", self.code)
        self.assertIn("valuation_note(", self.source)

    def test_汇率缺失判据不在services里再写一遍(self):
        # `rate == 1` 就是那条判据的第二份写法（旧代码在持仓与股息两处各写了一遍，
        # 而且都拿原始字符串比大小写）
        self.assertNotIn("rate == 1", self.code)
        self.assertIn("is_fx_missing(", self.source)


if __name__ == "__main__":
    unittest.main()
