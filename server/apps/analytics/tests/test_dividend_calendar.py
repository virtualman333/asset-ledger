"""股息日历的单元测试 —— 纯 Python、零数据库、零 Django。

日历是「哪一天收到哪笔钱」这个问题唯一的落点（DESIGN §4.5 的「除权日/派息日提醒」）。
它有三件容易错、而且错了不报错的事，都在这份文件里钉住：

1. **数据来源必须是归集后的股息。** 只落流水的那条录入路径在 `DividendRecord` 里
   一行都没有 —— 照明细表写，那些股息一个格子都不会出现，而同一个账户的
   「累计股息」是把它算进去的。本轮之前日历就是这个样子（真的跑出来过）。
   这一条在**视图层**的落点是 `test_dividend_exits.py`（读源码）与冒烟里的那两条
   （真起服务端）；本文件钉的是「归集之后，一条都不许在日历里丢」。
2. **「日期未知」既不是待派也不是已到账，但必须报出来。** 悄悄丢掉等于告诉用户
   这笔股息不存在。
3. **跨日边界。** 今天派的是「已到账」，明天派的才进「待派」。所以 `as_of` 是参数、
   不是模块里读的时钟 —— 读时钟的话这几条边界根本没法断言（`NoClockTest` 钉住这点）。
"""
import ast
from datetime import date
from decimal import Decimal
from pathlib import Path
import unittest

from apps.analytics.dividend_calendar import (
    GROUP_KEYS,
    build_calendar,
    calendar_item,
    days_until,
    split_by_date,
)
from apps.analytics.dividend_export import DIVIDEND_CSV_COLUMNS, dividend_row
from apps.analytics.dividend_income import DividendEntry, collect_dividends

AS_OF = date(2026, 9, 18)
ITEM_KEYS = {
    "asset_id", "account_id", "currency", "amount", "pay_date",
    "ex_date", "days_until", "origin", "reinvested", "record_id", "transaction_id",
}


def entry(amount="300", pay_date=AS_OF, *, asset_id=1, account_id=10, currency="CNY",
          origin="record", record_id=None, transaction_id=None, **detail):
    """一条归集后的股息。

    默认是「有明细、今天派」——`pay_date` 默认落在 `AS_OF` 上，因为**今天派的算已到账**
    是这套分组里最容易被写反的那一格。
    """
    if origin == "record" and record_id is None and transaction_id is None:
        record_id = asset_id * 100
    return DividendEntry(
        asset_id=asset_id,
        account_id=account_id,
        currency=currency,
        amount=Decimal(amount),
        pay_date=pay_date,
        origin=origin,
        record_id=record_id,
        transaction_id=transaction_id,
        **detail,
    )


class SplitTest(unittest.TestCase):
    """三组怎么分 —— 边界全在这一格上。"""

    def groups(self, entries):
        upcoming, received, undated = split_by_date(entries, AS_OF)
        return upcoming, received, undated

    def test_今天派的算已到账(self):
        """★ 边界：`pay_date == as_of` 是「收到了」，不是「还没到」。"""
        upcoming, received, undated = self.groups([entry(pay_date=AS_OF)])
        self.assertEqual((len(upcoming), len(received), len(undated)), (0, 1, 0))

    def test_明天派的才是待派(self):
        upcoming, received, _ = self.groups([entry(pay_date=date(2026, 9, 19))])
        self.assertEqual((len(upcoming), len(received)), (1, 0))

    def test_昨天派的是已到账(self):
        upcoming, received, _ = self.groups([entry(pay_date=date(2026, 9, 17))])
        self.assertEqual((len(upcoming), len(received)), (0, 1))

    def test_派息日未知的进第三组(self):
        """★ 它既不在待派也不在已到账 —— 但必须在结果里，不能悄悄丢。"""
        upcoming, received, undated = self.groups([entry(pay_date=None)])
        self.assertEqual((len(upcoming), len(received), len(undated)), (0, 0, 1))

    def test_三组互斥且一条都不丢(self):
        entries = [
            entry(asset_id=1, pay_date=date(2026, 10, 1)),
            entry(asset_id=2, pay_date=date(2026, 9, 1)),
            entry(asset_id=3, pay_date=None),
            entry(asset_id=4, pay_date=AS_OF),
            entry(asset_id=5, pay_date=date(2027, 1, 1)),
        ]
        upcoming, received, undated = self.groups(entries)
        seen = [e.asset_id for e in upcoming + received + undated]
        self.assertEqual(sorted(seen), [1, 2, 3, 4, 5], "分组把一个标的弄丢了或算了两遍")
        self.assertEqual(len(seen), len(set(seen)))

    def test_待派不做时间窗口截断(self):
        """日历给的是**全部**待派股息，不做「只看 30 天」那种静默截断。

        （要看窗口的调用方自己筛；日历截掉一部分而页面上没有任何提示，
        用户会以为那笔股息不存在。)
        """
        _, _, _ = self.groups([])
        payload = build_calendar([entry(pay_date=date(2030, 1, 1))], AS_OF)
        self.assertEqual(len(payload["upcoming"]), 1)
        self.assertEqual(payload["upcoming"][0]["days_until"],
                         (date(2030, 1, 1) - AS_OF).days)

    def test_days_until_的正负与所在组一一对应(self):
        entries = [
            entry(asset_id=1, pay_date=date(2026, 12, 31)),
            entry(asset_id=2, pay_date=date(2026, 1, 2)),
            entry(asset_id=3, pay_date=None),
        ]
        payload = build_calendar(entries, AS_OF)
        for key in ("upcoming", "received", "undated"):
            for item in payload[key]:
                days = item["days_until"]
                if key == "undated":
                    self.assertIsNone(days)
                elif key == "upcoming":
                    self.assertGreater(days, 0)
                else:
                    self.assertLessEqual(days, 0)

    def test_空账本不崩(self):
        payload = build_calendar([], AS_OF)
        for key in GROUP_KEYS:
            self.assertEqual(payload[key], [])
        self.assertEqual(payload["totals"]["count"], 0)
        self.assertEqual(payload["totals"]["by_currency"], {})
        self.assertIsNone(payload["totals"]["next_pay_date"])
        self.assertEqual(payload["totals"]["upcoming_count"], 0)

    def test_as_of_真的在用_换一天分组就变(self):
        """同一份数据、两个 `as_of`，分组必须不一样 —— 否则「今天」可能是写死的。"""
        entries = [entry(pay_date=date(2026, 10, 1))]
        before = build_calendar(entries, date(2026, 9, 18))
        after = build_calendar(entries, date(2026, 10, 2))
        self.assertEqual(len(before["upcoming"]), 1)
        self.assertEqual(len(after["received"]), 1)
        self.assertEqual(len(after["upcoming"]), 0)

    def test_三组的键名固定(self):
        payload = build_calendar([], AS_OF)
        for key in GROUP_KEYS:
            self.assertIn(key, payload)
        self.assertEqual(set(GROUP_KEYS), {"upcoming", "received", "undated"})


class OrderTest(unittest.TestCase):
    """顺序必须确定：同一份输入永远给同一个顺序，断言才立得住。"""

    def test_待派按日期升序(self):
        payload = build_calendar(
            [entry(asset_id=i, pay_date=d) for i, d in
             ((1, date(2026, 12, 1)), (2, date(2026, 10, 1)), (3, date(2026, 11, 1)))],
            AS_OF,
        )
        self.assertEqual([i["pay_date"] for i in payload["upcoming"]],
                         ["2026-10-01", "2026-11-01", "2026-12-01"])

    def test_已到账是倒序_刚收到的最前(self):
        payload = build_calendar(
            [entry(asset_id=i, pay_date=d) for i, d in
             ((1, date(2026, 1, 1)), (2, date(2026, 8, 1)), (3, date(2026, 3, 1)))],
            AS_OF,
        )
        self.assertEqual([i["pay_date"] for i in payload["received"]],
                         ["2026-08-01", "2026-03-01", "2026-01-01"])

    def test_同一天的多笔顺序与输入顺序无关(self):
        """同一天派两笔时不能「谁先查出来谁在前」—— 那样页面每次刷新都在抖。"""
        rows = [entry(asset_id=9, pay_date=date(2026, 10, 1)),
                entry(asset_id=2, pay_date=date(2026, 10, 1))]
        first = build_calendar(rows, AS_OF)
        second = build_calendar(list(reversed(rows)), AS_OF)
        self.assertEqual([i["asset_id"] for i in first["upcoming"]],
                         [i["asset_id"] for i in second["upcoming"]])
        self.assertEqual([i["asset_id"] for i in first["upcoming"]], [2, 9])

    def test_日期未知的也按标定序(self):
        rows = [entry(asset_id=8, pay_date=None), entry(asset_id=3, pay_date=None)]
        payload = build_calendar(list(reversed(rows)), AS_OF)
        self.assertEqual([i["asset_id"] for i in payload["undated"]], [3, 8])


class MoneyTest(unittest.TestCase):
    """分币种汇总、三个分项要对得上合计、精度不丢。"""

    def test_不同币种不混在一起(self):
        """★ 把 CNY 与 USD 加在一起是个没有意义的数：它既不是人民币也不是美元。"""
        payload = build_calendar(
            [entry(amount="300", currency="CNY"), entry(amount="100", currency="USD")], AS_OF)
        self.assertEqual(payload["totals"]["by_currency"], {"CNY": "300", "USD": "100"})

    def test_三个分项逐币种相加等于合计(self):
        """★ 少一组能当场看出来 —— 「日期未知」那组最容易在算合计时被漏掉。"""
        entries = [
            entry(asset_id=1, amount="300", currency="CNY", pay_date=date(2026, 10, 1)),
            entry(asset_id=2, amount="120", currency="CNY", pay_date=date(2026, 8, 1)),
            entry(asset_id=3, amount="55.5", currency="USD", pay_date=None),
        ]
        totals = build_calendar(entries, AS_OF)["totals"]
        for currency in ("CNY", "USD", ""):
            parts = sum(
                (Decimal(totals[f"{key}_by_currency"].get(currency, "0"))
                 for key in GROUP_KEYS),
                Decimal("0"),
            )
            with self.subTest(currency=currency):
                self.assertEqual(parts, Decimal(totals["by_currency"].get(currency, "0")),
                                 f"{currency} 这一栏：三个分项加起来对不上合计")

    def test_日期未知的那部分单独有合计(self):
        payload = build_calendar([entry(amount="77.25", pay_date=None)], AS_OF)
        self.assertEqual(payload["totals"]["undated_by_currency"], {"CNY": "77.25"})
        self.assertEqual(payload["totals"]["received_by_currency"], {})
        self.assertEqual(payload["totals"]["upcoming_by_currency"], {})

    def test_金额等于逐条相加(self):
        entries = [entry(asset_id=i, amount=a) for i, a in
                   ((1, "300.10"), (2, "199.90"), (3, "0.01"))]
        payload = build_calendar(entries, AS_OF)
        self.assertEqual(Decimal(payload["totals"]["by_currency"]["CNY"]),
                         Decimal("300.10") + Decimal("199.90") + Decimal("0.01"))

    def test_金额不四舍五入_小数位原样留着(self):
        payload = build_calendar([entry(amount="300.12345678")], AS_OF)
        self.assertEqual(payload["received"][0]["amount"], "300.12345678")

    def test_金额拖着的零要收掉_用户看到的那串字符就是契约(self):
        """★ 库里是 ``DECIMAL(24,8)``：流水录入的股息取出来是 ``77.25000000``。
        直接 ``str()`` 写进 JSON 就是这个样子，而同一个数在导出的 CSV 里是 ``77.25``
        —— 同一笔股息在两个出口里读起来是两个数，用户只会以为其中一个错了。
        （这条是本轮冒烟脚本跑出来的：导出那条断言过、日历那条红。）
        """
        payload = build_calendar([entry(amount="77.25000000")], AS_OF)
        self.assertEqual(payload["received"][0]["amount"], "77.25")
        self.assertEqual(payload["totals"]["by_currency"], {"CNY": "77.25"})
        self.assertEqual(payload["totals"]["received_by_currency"], {"CNY": "77.25"})

    def test_日历与导出对同一笔股息给的是同一串字符(self):
        """★ 跨出口对账：不引用任何被测常量，两边都坏成一样才会骗过去。

        导出的 ``税后`` 那一格与日历的 ``amount`` 是同一个数（都是到手口径），
        用户会拿两个界面上的字符串互相对账 —— 写法不一致就是缺陷。
        """
        titles = [title for _, title in DIVIDEND_CSV_COLUMNS]
        assets = {1: ("601398", "工商银行")}
        accounts = {1: "冒烟账户"}
        for raw in ("77.25000000", "300.00", "0.00000000", "0.01", "300.12345678"):
            with self.subTest(amount=raw):
                e = entry(amount=raw)
                item = build_calendar([e], AS_OF)["received"][0]
                cells = dict(zip(titles, dividend_row(e, assets, accounts)))
                self.assertEqual(item["amount"], cells["税后"],
                                 f"同一笔股息（{raw}）在日历与导出里是两个写法")

    def test_币种为空不崩且键是空串(self):
        payload = build_calendar([entry(currency="", amount="12")], AS_OF)
        self.assertEqual(payload["totals"]["by_currency"], {"": "12"})

    def test_待派笔数与下一次派息日(self):
        payload = build_calendar(
            [entry(asset_id=1, pay_date=date(2026, 12, 1)),
             entry(asset_id=2, pay_date=date(2026, 10, 1)),
             entry(asset_id=3, pay_date=date(2026, 1, 1))],
            AS_OF,
        )
        self.assertEqual(payload["totals"]["upcoming_count"], 2)
        self.assertEqual(payload["totals"]["next_pay_date"], "2026-10-01")

    def test_没有待派时下一次派息日是None(self):
        payload = build_calendar([entry(pay_date=date(2026, 1, 1))], AS_OF)
        self.assertIsNone(payload["totals"]["next_pay_date"])
        self.assertEqual(payload["totals"]["upcoming_count"], 0)

    def test_下一次派息日不拿未知日期来凑(self):
        """日历里有一笔日期未知的股息时，`next_pay_date` 仍然只能是 `None`。"""
        payload = build_calendar([entry(pay_date=None)], AS_OF)
        self.assertIsNone(payload["totals"]["next_pay_date"])


class ItemTest(unittest.TestCase):
    """一条股息在日历里的样子（字段与「缺就是 None」口径）。"""

    def test_字段齐全(self):
        """键集合定死：漏一个字段客户端就会拿到 `undefined`，而服务端不会报错。"""
        item = calendar_item(entry(), AS_OF)
        self.assertEqual(set(item), ITEM_KEYS)

    def test_有明细的条目带上明细(self):
        item = calendar_item(
            entry(ex_date=date(2026, 9, 30), reinvested=True), AS_OF)
        self.assertEqual(item["ex_date"], "2026-09-30")
        self.assertIs(item["reinvested"], True)
        self.assertEqual(item["record_id"], 100)
        self.assertEqual(item["origin"], "record")

    def test_纯流水条目的明细字段是None而不是False或0(self):
        """★ 「没有明细可查」与「除权日就是某天 / 没有分红再投」是两件事。"""
        item = calendar_item(
            entry(origin="transaction", record_id=None, transaction_id=555), AS_OF)
        self.assertIsNone(item["ex_date"])
        self.assertIsNone(item["reinvested"])
        self.assertIsNone(item["record_id"])
        self.assertEqual(item["transaction_id"], 555)

    def test_金额取的是到手口径而不是税前(self):
        item = calendar_item(entry(amount="800", gross="1000", tax="200"), AS_OF)
        self.assertEqual(item["amount"], "800")

    def test_流水录入的股息也进日历(self):
        """★ 本文件里最值钱的一条：只有流水、没有明细的股息，日历里必须找得到。

        （视图层「有没有照明细表查」由 `test_dividend_exits.py` 与冒烟盯着；
        这里钉的是归集之后不许再丢。）
        """
        entries = collect_dividends(
            [],
            [{"id": 9001, "asset_id": 5, "account_id": 1, "amount": "-77.25",
              "currency": "CNY", "traded_at": date(2026, 8, 20)}],
        )
        payload = build_calendar(entries, AS_OF)
        origins = [i["origin"] for i in payload["received"]]
        self.assertEqual(origins, ["transaction"])
        self.assertEqual(payload["received"][0]["amount"], "77.25",
                         "流水金额要取绝对值 —— 卖出方向记的负数不是「负股息」")
        self.assertEqual(payload["totals"]["by_currency"], {"CNY": "77.25"})

    def test_两条录入路径同时存在时两条都在(self):
        entries = collect_dividends(
            [{"id": 1, "asset_id": 5, "account_id": 1, "net": "300", "currency": "CNY",
              "pay_date": date(2026, 7, 15), "transaction_id": None}],
            [{"id": 2, "asset_id": 5, "account_id": 1, "amount": "-77.25",
              "currency": "CNY", "traded_at": date(2026, 8, 20)}],
        )
        payload = build_calendar(entries, AS_OF)
        self.assertEqual(sorted(i["origin"] for i in payload["received"]),
                         ["record", "transaction"])
        self.assertEqual(Decimal(payload["totals"]["by_currency"]["CNY"]),
                         Decimal("377.25"))

    def test_days_until_的两种取值(self):
        self.assertEqual(days_until(date(2026, 9, 20), AS_OF), 2)
        self.assertEqual(days_until(AS_OF, AS_OF), 0)
        self.assertEqual(days_until(date(2026, 9, 1), AS_OF), -17)
        self.assertIsNone(days_until(None, AS_OF))


def clock_readers(tree):
    """把语法树里「在读时钟」的调用名找出来（`date.today()` / `datetime.now()` …）。"""
    found = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            if node.func.attr in ("today", "now", "utcnow"):
                found.append(node.func.attr)
    return found


class NoClockTest(unittest.TestCase):
    """`as_of` 必须是参数，不能是模块里读的时钟。"""

    def test_模块里没有读时钟(self):
        source = Path(__file__).resolve().parents[1] / "dividend_calendar.py"
        self.assertEqual(clock_readers(ast.parse(source.read_text(encoding="utf-8"))), [])

    def test_这条检查对读时钟的样例会报红(self):
        """自证：上面那条绿，不是因为 `clock_readers` 永远返回空表。"""
        sample = ast.parse(
            "from datetime import date\n"
            "def f():\n"
            "    return date.today()\n"
        )
        self.assertEqual(clock_readers(sample), ["today"])


if __name__ == "__main__":
    unittest.main()
