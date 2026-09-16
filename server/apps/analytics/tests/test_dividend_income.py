"""股息归集规则的单元测试。

**纯 Python、零数据库、零 Django**：`dividend_income` 不 import django，
所以这条最容易写错、又最影响账目的口径可以秒级验证。

放在 unittest 里而不是 pytest：仓库 requirements.txt 没有 pytest，
不为一个测试文件引入新依赖。
"""
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
import unittest

from apps.analytics.dividend_income import (
    ZERO,
    collect_dividends,
    dividend_yield,
    group_by_position,
    monthly_passive_income,
    sum_by_currency,
    total,
    within_window,
)

AS_OF = date(2026, 9, 16)


def record(asset_id=1, account_id=10, net="100", currency="CNY", pay_date=date(2026, 6, 1), transaction_id=None):
    """一条股息明细（DividendRecord）。"""
    return {
        "asset_id": asset_id,
        "account_id": account_id,
        "net": net,
        "currency": currency,
        "pay_date": pay_date,
        "transaction_id": transaction_id,
    }


def tx(rid=100, asset_id=1, account_id=10, amount="100", currency="CNY", traded_at=None):
    """一条 DIVIDEND 流水（Transaction）。"""
    return {
        "id": rid,
        "asset_id": asset_id,
        "account_id": account_id,
        "amount": amount,
        "currency": currency,
        "traded_at": traded_at or date(2026, 6, 1),
    }


class CollectTest(unittest.TestCase):
    """三种录入形态都要算数，且一条股息只能算一次。"""

    def test_形态A_只有股息明细也算数(self):
        entries = collect_dividends([record(net="270")], [])
        self.assertEqual(len(entries), 1)
        self.assertEqual(total(entries), Decimal("270"))
        self.assertEqual(entries[0].origin, "record")

    def test_形态B_只有股息流水也算数(self):
        entries = collect_dividends([], [tx(amount="270")])
        self.assertEqual(len(entries), 1)
        self.assertEqual(total(entries), Decimal("270"))
        self.assertEqual(entries[0].origin, "transaction")

    def test_形态C_明细与流水都落时只算一次(self):
        """documents/DESIGN.md §3 的形态：明细挂在流水之上。

        这里是最容易出错的一处：若不去重，同一笔股息会被计两次。
        金额取明细的 net（税后口径 = 用户真正到手的钱），不取流水 amount。
        """
        entries = collect_dividends([record(net="270", transaction_id=100)], [tx(rid=100, amount="300")])
        self.assertEqual(len(entries), 1)
        self.assertEqual(total(entries), Decimal("270"))

    def test_形态C_只代表它自己那一笔流水(self):
        """两条明细各自关联一条流水，第三条流水没被代表 → 计三笔。"""
        entries = collect_dividends(
            [record(net="10", transaction_id=100), record(net="20", transaction_id=101)],
            [tx(rid=100, amount="99"), tx(rid=101, amount="99"), tx(rid=102, amount="30")],
        )
        self.assertEqual(total(entries), Decimal("60"))
        self.assertEqual(len(entries), 3)

    def test_流水金额取绝对值(self):
        """流水 amount 的正负号属于现金方向，不该让股息变负。"""
        entries = collect_dividends([], [tx(amount="-270")])
        self.assertEqual(total(entries), Decimal("270"))

    def test_金额为0的流水不计入(self):
        entries = collect_dividends([], [tx(amount="0")])
        self.assertEqual(entries, [])

    def test_金额为0的明细仍计入(self):
        """0 元股息是用户主动记录的一条事实，不该被当成「没记」。"""
        entries = collect_dividends([record(net="0")], [])
        self.assertEqual(len(entries), 1)

    def test_币种统一大写(self):
        entries = collect_dividends([record(currency="usd")], [])
        self.assertEqual(entries[0].currency, "USD")

    def test_空串金额当0而不是崩掉(self):
        entries = collect_dividends([record(net="")], [])
        self.assertEqual(total(entries), ZERO)

    def test_浮点金额不丢精度(self):
        """0.1 + 0.2 若走 float 会得到 0.30000000000000004。"""
        entries = collect_dividends([record(net="0.1"), record(net="0.2")], [])
        self.assertEqual(total(entries), Decimal("0.3"))


class GroupTest(unittest.TestCase):
    """分组口径 —— 持仓与统计必须由同一份聚合喂出来。"""

    def test_按账户与标的分别聚合(self):
        entries = collect_dividends(
            [
                record(asset_id=1, account_id=10, net="100"),
                record(asset_id=1, account_id=11, net="7"),
                record(asset_id=2, account_id=10, net="3"),
            ],
            [],
        )
        grouped = group_by_position(entries)
        self.assertEqual(grouped[(10, 1)], Decimal("100"))
        self.assertEqual(grouped[(11, 1)], Decimal("7"))
        self.assertEqual(grouped[(10, 2)], Decimal("3"))

    def test_持仓合计恒等于总账合计(self):
        """核心不变量：把逐格加总必须等于总账。

        持仓页读 group_by_position、统计页读 total/sum_by_currency；
        两者一旦各写一份算式，用户就会在两个页面看到不同的股息金额
        （这正是本次修复前的实际故障：持仓页 0、统计页 810）。
        """
        entries = collect_dividends(
            [
                record(asset_id=1, account_id=10, net="270"),
                record(asset_id=2, account_id=11, net="0.55", currency="USD"),
                record(asset_id=1, account_id=10, net="12.45"),
            ],
            [tx(rid=200, asset_id=3, account_id=10, amount="-8")],
        )
        self.assertEqual(sum(group_by_position(entries).values()), total(entries))
        self.assertEqual(sum(sum_by_currency(entries).values()), total(entries))

    def test_按币种聚合不混币(self):
        entries = collect_dividends(
            [record(net="100", currency="CNY"), record(net="5", currency="USD"), record(net="1", currency="CNY")],
            [],
        )
        self.assertEqual(sum_by_currency(entries), {"CNY": Decimal("101"), "USD": Decimal("5")})

    def test_按币种聚合求和等于总账(self):
        """sum_by_currency 是统计页折算的入口，它漏一笔账目就少一笔。"""
        entries = collect_dividends([record(net="100"), record(net="5", currency="USD")], [])
        self.assertEqual(sum(sum_by_currency(entries).values()), Decimal("105"))


class WindowTest(unittest.TestCase):
    """观察窗口：近 365 天。"""

    def test_窗口内的计入(self):
        entries = collect_dividends([record(net="50", pay_date=date(2026, 1, 1))], [])
        self.assertEqual(total(within_window(entries, AS_OF)), Decimal("50"))

    def test_恰好365天前是窗口内边界(self):
        entries = collect_dividends([record(net="50", pay_date=date(2025, 9, 16))], [])
        self.assertEqual(len(within_window(entries, AS_OF)), 1)

    def test_366天前落在窗口外(self):
        entries = collect_dividends([record(net="50", pay_date=date(2025, 9, 15))], [])
        self.assertEqual(within_window(entries, AS_OF), [])

    def test_未来日期不计入窗口(self):
        entries = collect_dividends([record(net="50", pay_date=date(2026, 12, 1))], [])
        self.assertEqual(within_window(entries, AS_OF), [])

    def test_日期未知不计入窗口但仍在总账(self):
        """「日期未知」与「日期不在窗口内」是两件事。

        若把日期未知的也塞进窗口，股息率会被一笔不知什么时候到账的钱抬高。
        """
        entries = collect_dividends([record(net="999", pay_date=None)], [])
        self.assertEqual(total(entries), Decimal("999"))
        self.assertEqual(within_window(entries, AS_OF), [])

    def test_流水日期走traded_at(self):
        entries = collect_dividends([], [tx(amount="77", traded_at=date(2026, 3, 1))])
        self.assertEqual(total(within_window(entries, AS_OF)), Decimal("77"))

    def test_流水日期是datetime时不崩且能被窗口比较(self):
        """真实数据的 traded_at 是 datetime 而不是 date。

        `datetime` 是 `date` 的子类，一句 `not isinstance(v, date)` 的写法会把
        datetime 原样放过，随后 `date <= datetime` 抛 TypeError，接口直接 500。
        这条用例就是为那次真实故障立的锁 —— 参数必须是 datetime。
        """
        entries = collect_dividends([], [tx(amount="77", traded_at=datetime(2026, 3, 1, 9, 30))])
        self.assertEqual(entries[0].pay_date, date(2026, 3, 1))
        self.assertIsInstance(entries[0].pay_date, date)
        self.assertNotIsInstance(entries[0].pay_date, datetime)
        self.assertEqual(total(within_window(entries, AS_OF)), Decimal("77"))

    def test_流水日期是带时区的datetime也不崩(self):
        """USE_TZ=True 时 Django 给的是 aware datetime。"""
        aware = datetime(2026, 3, 1, 9, 30, tzinfo=timezone(timedelta(hours=8)))
        entries = collect_dividends([], [tx(amount="77", traded_at=aware)])
        self.assertEqual(entries[0].pay_date, date(2026, 3, 1))
        self.assertEqual(total(within_window(entries, AS_OF)), Decimal("77"))


class MetricTest(unittest.TestCase):
    """股息率与月度被动收入。"""

    def test_股息率(self):
        self.assertEqual(dividend_yield(Decimal("270"), Decimal("5400")), Decimal("0.05"))

    def test_成本为0时股息率是None而不是0(self):
        """已清仓算不出股息率，显示成 0% 会把「没有数据」说成「没有收益」。"""
        self.assertIsNone(dividend_yield(Decimal("270"), ZERO))

    def test_成本为负时股息率是None(self):
        self.assertIsNone(dividend_yield(Decimal("270"), Decimal("-1")))

    def test_年度股息为None时股息率是None(self):
        self.assertIsNone(dividend_yield(None, Decimal("100")))

    def test_没有股息时股息率是0而不是None(self):
        """成本和年度股息都拿得到、只是股息为 0 —— 这是真实的 0%。"""
        self.assertEqual(dividend_yield(ZERO, Decimal("100")), ZERO)

    def test_月度被动收入是年度十二分之一(self):
        self.assertEqual(monthly_passive_income(Decimal("1200")), Decimal("100"))

    def test_月度被动收入接受自定义月数(self):
        self.assertEqual(monthly_passive_income(Decimal("1200"), months=6), Decimal("200"))

    def test_月数为0时不崩也不瞎算(self):
        self.assertIsNone(monthly_passive_income(Decimal("1200"), months=0))

    def test_年度股息为None时月度收入是None(self):
        self.assertIsNone(monthly_passive_income(None))


if __name__ == "__main__":
    unittest.main()
