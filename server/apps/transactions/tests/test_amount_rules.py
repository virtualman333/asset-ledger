# -*- coding: utf-8 -*-
"""`apps/transactions/amount_rules.py` 的测试（纯 Python，不需要数据库、不需要 Django）。

锁的是「这条流水的现金变动是多少」——它决定三件事：

  - **年化（XIRR）**：出入金是现金流的两个端点，金额为 0 就等于这笔钱没发生过；
  - **总资产与成本**：买入的金额符号错了，成本会变成负数；
  - **拆分/送股**：拆股不产生现金，凭空多出一笔流入就是虚增总资产。

实测过的三种旧行为（都静默、不报错）见下面的反证用例。
"""
import unittest
from decimal import Decimal

from apps.transactions.amount_rules import AmountError, resolve_amount


class BuySellTest(unittest.TestCase):
    """买入 / 卖出：现金变动由 数量×单价 推"""

    def test_买入是现金流出且含费用税费(self):
        got = resolve_amount("BUY", quantity="100", price="10", fee="5", tax="1")
        self.assertEqual(got, Decimal("-1006"))

    def test_卖出是现金流入且扣掉费用税费(self):
        got = resolve_amount("SELL", quantity="100", price="10", fee="5", tax="1")
        self.assertEqual(got, Decimal("994"))

    def test_买入卖出的数量单价缺省按零算(self):
        # 只给 side 的残缺草稿不该炸在算术上（但金额为 0 的买卖本身会在
        # 别处被拦：年化与持仓都依赖数量）
        self.assertEqual(resolve_amount("BUY"), Decimal("0"))
        self.assertEqual(resolve_amount("SELL"), Decimal("0"))


class DividendAndFeeTest(unittest.TestCase):

    def test_分红是现金流入(self):
        self.assertEqual(resolve_amount("DIVIDEND", amount="810"), Decimal("810"))

    def test_红利税冲销允许负的分红金额(self):
        # DIVIDEND 的负数用于「红利税收回」这类冲销，符号原样尊重，不归一
        self.assertEqual(resolve_amount("DIVIDEND", amount="-97"), Decimal("-97"))

    def test_费用与税费是现金流出(self):
        # 只给 side=FEE + fee 时，兜底那条分支给出 -(fee+tax)
        self.assertEqual(resolve_amount("FEE", fee="5"), Decimal("-5"))
        self.assertEqual(resolve_amount("TAX", tax="20"), Decimal("-20"))

    def test_给金额就用给的(self):
        self.assertEqual(resolve_amount("BUY", amount="-1234.56"), Decimal("-1234.56"))


class SplitTest(unittest.TestCase):
    """拆分 / 送股：数量变了，现金不动"""

    def test_拆分不产生现金变动(self):
        self.assertEqual(resolve_amount("SPLIT", quantity="100"), Decimal("0"))

    def test_拆分即使带了单价也是零(self):
        # 这条是修复点：拆分原先与买入卖出共用一条分支，录入时顺带填了单价
        # 就会凭空多出一笔正的现金流入。
        self.assertEqual(resolve_amount("SPLIT", quantity="100", price="10"), Decimal("0"))

    def test_拆分显式给的金额原样保留(self):
        # 极端场景（例如拆股同时退了零股现金）不做拦截，尊重调用方
        self.assertEqual(resolve_amount("SPLIT", amount="12"), Decimal("12"))


class DepositWithdrawTest(unittest.TestCase):
    """出入金：唯一金额说话的档"""

    def test_入金给正数就是正数(self):
        self.assertEqual(resolve_amount("DEPOSIT", amount="100000"), Decimal("100000"))

    def test_入金给负数也归一成正的(self):
        # 用户按直觉填「入金 -100000」，或抄券商对账单的符号 —— 方向由 side 定，
        # 不该因为符号写反就让这笔钱从年化里反向消失。
        self.assertEqual(resolve_amount("DEPOSIT", amount="-100000"), Decimal("100000"))

    def test_出金给正数会被归一成负的(self):
        # XIRR 用 `-amount` 定方向：出金若是正的，就等于又多投了一笔钱
        self.assertEqual(resolve_amount("WITHDRAW", amount="50000"), Decimal("-50000"))

    def test_出金给负数保持为负(self):
        self.assertEqual(resolve_amount("WITHDRAW", amount="-50000"), Decimal("-50000"))

    def test_入金缺少金额时明确报错而不是记成零(self):
        # 这是本轮的核心修复：旧实现返回 0，一笔入金从此在年化里不存在，
        # 而且全程不报错。
        with self.assertRaises(AmountError) as ctx:
            resolve_amount("DEPOSIT")
        self.assertIn("入金", str(ctx.exception))

    def test_出金缺少金额时同样报错(self):
        with self.assertRaises(AmountError):
            resolve_amount("WITHDRAW")

    def test_出入金金额为零也要报错(self):
        # 0 元的入金没有意义，而它恰恰是旧实现静默产生的那种记录
        for side in ("DEPOSIT", "WITHDRAW"):
            with self.subTest(side=side):
                with self.assertRaises(AmountError):
                    resolve_amount(side, amount="0")
                with self.assertRaises(AmountError):
                    resolve_amount(side, amount="0.00")


class BadValueTest(unittest.TestCase):

    def test_非法数字报_AmountError_而不是漏出_decimal_异常(self):
        # 漏出 decimal.InvalidOperation 会被 DRF 当成 500；用户看到的应该是 400
        for bad in ("abc", {}, []):
            with self.subTest(value=bad):
                with self.assertRaises(AmountError):
                    resolve_amount("DEPOSIT", amount=bad)

    def test_数量或单价的非法值同样走_AmountError(self):
        with self.assertRaises(AmountError):
            resolve_amount("BUY", quantity="一百", price="10")

    def test_空串与_None_按零处理(self):
        self.assertEqual(resolve_amount("BUY", quantity=None, price=None, fee="", tax=None), Decimal("0"))


class RegressionProofTest(unittest.TestCase):
    """反证：复刻修复前的写法，确认那三种静默错误确实存在。

    这些断言不是在测试被测代码，而是把「为什么必须改」钉在测试文件里 ——
    将来有人想把 `else` 分支加回去时，会先看到这三条。
    """

    @staticmethod
    def _old_resolve(side, quantity, price, fee=Decimal("0"), tax=Decimal("0"), amount=None):
        """修复前的 serializer.validate 里的那段推导逻辑。"""
        quantity = quantity or Decimal("0")
        price = price or Decimal("0")
        if amount is not None:
            return amount
        gross = quantity * price
        if side == "BUY":
            return -(gross + fee + tax)
        # 旧实现这里写的是 `elif side == "SELL": ... else: 同样一句` —— 两个分支逐字相同
        return gross - fee - tax

    def test_旧写法把入金算成零(self):
        # Agent 识别「入金 100000」的截图：草稿 schema 里没有 amount 字段，
        # 数量单价都是空的 → 旧实现给出 0
        self.assertEqual(self._old_resolve("DEPOSIT", None, None), Decimal("0"))
        # 新实现不猜，直接要求补金额
        with self.assertRaises(AmountError):
            resolve_amount("DEPOSIT")

    def test_旧写法让拆分凭空多出现金(self):
        self.assertEqual(
            self._old_resolve("SPLIT", Decimal("100"), Decimal("10")), Decimal("1000")
        )
        self.assertEqual(resolve_amount("SPLIT", quantity="100", price="10"), Decimal("0"))

    def test_旧写法不归一出入金的符号(self):
        # 出金填成正数 → 旧实现原样保留 → XIRR 把出金当成又投了一笔
        self.assertEqual(self._old_resolve("WITHDRAW", None, None, amount=Decimal("50000")), Decimal("50000"))
        self.assertEqual(resolve_amount("WITHDRAW", amount="50000"), Decimal("-50000"))


if __name__ == "__main__":
    unittest.main()
