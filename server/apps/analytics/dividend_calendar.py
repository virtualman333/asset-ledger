"""股息日历：把归集后的股息排成「哪一天收到哪笔钱」。

**数据来源只有归集后的股息（`services.dividend_entries`），不是 `DividendRecord`。**
理由与导出那一节完全相同（见 `dividend_export` 的模块说明）：股息有两条合法录入路径，
走 `/transactions/records/` 且 `side=DIVIDEND` 的那条**只落一条流水、明细表里一行都没有**。
日历照「把明细表列出来」写，这些股息就一条都不出现 —— 而同一个账户的「累计股息」
是把两种都算进去的。两边都不报错，只是数字对不上。

这条缺陷**真的发生过**：本轮之前 `CalendarView` 直接查 `DividendRecord`，
只落流水的那笔股息在日历里查不到，而它在导出 CSV 与总览里都在。同一个「股息有两条
录入路径」的坑，导出那条上一轮已经填过，日历这条留到了这一轮
（`apps/analytics/tests/test_dividend_exits.py` 现在盯着「三个出口都必须经由归集函数」）。

三组，一个都不许丢：

  - ``upcoming``：派息日在 ``as_of`` 之后 —— DESIGN §4.5 说的「派息日提醒」就是它；
  - ``received``：派息日在 ``as_of`` 当天或之前（**当天算已到账**）；
  - ``undated``：**派息日为空**的条目。它们不属于任何一天，所以既不进待派也不进已到账，
    但必须报出来：悄悄丢掉等于告诉用户这笔股息不存在。

「今天」由调用方传进来（``as_of``），本模块**不读时钟** —— 一个读时钟的纯函数在测试里
只能「跑一遍看结果」，而日历的正确性恰好全在跨日边界上（今天派的算哪一组、明天派的
`days_until` 是几）。时钟读一次、当参数传进来，边界就能被断言。

币种：``by_currency`` 分开汇总。把 CNY 与 USD 加在一起是个没有意义的数 —— 它既不是
人民币也不是美元，任何汇率变了它都不动。折算是展示层的事（`summary` 里的
`dividend_total` 才是折算到基准货币的那个数）。**三个分项加起来等于合计**这件事也被
断言盯着：漏掉一整组时，账面上会缺一块，而缺的那块正好是「日期未知」那组最容易。

金额的写法只有一处
------------------
``amount`` 与分币种合计都过 ``apps.core.csv_export.fmt_decimal`` —— **与导出 CSV 用
同一个函数**。这不是洁癖：库里是 ``DECIMAL(24,8)``，一条流水录入的股息取出来是
``77.25000000``，直接 ``str()`` 写进 JSON 就是这个样子，而同一个数在导出的文件里
是 ``77.25``。同一笔股息在两个出口里读起来是两个数，用户只会以为其中一个是错的。
（这条是本轮的冒烟脚本跑出来的：导出那条断言过、日历那条红，差的就是这一个函数。）

本模块纯 Python、不 import django（``csv_export`` 只用标准库），因此可以直接单测
（不需要数据库）。
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal

from apps.core.csv_export import fmt_decimal

from .dividend_income import DividendEntry, sum_by_currency

ZERO = Decimal("0")

#: 三组的键名与固定顺序（视图、README 与测试都按这个顺序读，别在别处再排一次）
GROUP_KEYS = ("upcoming", "received", "undated")


def days_until(pay_date: date | None, as_of: date) -> int | None:
    """距离派息日还有几天：还没派是正数、当天是 0、已派是负数；日期未知给 ``None``。"""
    if pay_date is None:
        return None
    return (pay_date - as_of).days


def split_by_date(entries, as_of: date) -> tuple[list, list, list]:
    """按「哪一天」分成三组，返回 ``(待派, 已到账, 日期未知)``。

    排序写死在这里而不是交给调用方：待派按日期**升序**（最近的一笔在最前 ——
    提醒关心的就是这个），已到账整表**倒序**（刚收到的在最前）。同一天的多笔按
    ``(标的, 账户)`` 定序，于是同一份输入永远给同一个顺序，断言才立得住。

    分组判据只看 ``pay_date`` 与 ``as_of`` 的大小：``pay_date == as_of`` 算**已到账**
    （今天派的就是收到了），``as_of + 1`` 天才算待派。
    """
    upcoming: list[DividendEntry] = []
    received: list[DividendEntry] = []
    undated: list[DividendEntry] = []

    for entry in entries:
        if entry.pay_date is None:
            undated.append(entry)
        elif entry.pay_date > as_of:
            upcoming.append(entry)
        else:
            received.append(entry)

    def order(entry: DividendEntry):
        return (entry.pay_date, entry.asset_id or 0, entry.account_id or 0)

    upcoming.sort(key=order)
    received.sort(key=order, reverse=True)
    undated.sort(key=lambda e: (e.asset_id or 0, e.account_id or 0, e.pay_date or date.min))
    return upcoming, received, undated


def calendar_item(entry: DividendEntry, as_of: date) -> dict:
    """一条股息在日历里的样子。

    明细字段（``ex_date`` / ``reinvested`` / ``record_id``）对**纯流水录入**的条目是
    ``None``、不是 ``0`` / ``False`` —— 「没有明细可查」与「除权日就是 1 月 1 日」
    「没有分红再投」对用户是两件事（同一条口径写在 `DividendEntry` 上）。
    """
    return {
        "asset_id": entry.asset_id,
        "account_id": entry.account_id,
        "currency": entry.currency,
        # 到手口径：有明细取 net、纯流水取流水金额的绝对值 —— 与「累计股息」同一个数
        "amount": fmt_decimal(entry.amount),
        "pay_date": entry.pay_date.isoformat() if entry.pay_date else None,
        "ex_date": entry.ex_date.isoformat() if entry.ex_date else None,
        "days_until": days_until(entry.pay_date, as_of),
        "origin": entry.origin,
        "reinvested": entry.reinvested,
        "record_id": entry.record_id,
        "transaction_id": entry.transaction_id,
    }


def _money(buckets: dict) -> dict:
    """分币种金额 → 字符串映射（``Decimal`` 进不了 JSON）。

    写法与单条 ``amount`` 一样走 ``fmt_decimal``：合计和明细要在同一张页面上并排显示，
    一个 ``300.00`` 一个 ``300`` 会被读成两个数。该函数不四舍五入，只收掉拖着的零。
    """
    return {currency or "": fmt_decimal(amount) for currency, amount in sorted(buckets.items())}


def build_calendar(entries, as_of: date) -> dict:
    """日历响应体：三组 + 分币种合计。

    ``totals`` 里四个分币种映射满足 ``by_currency == upcoming + received + undated``
    （同一币种下逐项相加）。这不是装饰：三个分项各自对着不同的页面区域，合计是用户
    拿来对账的那个数，少了哪一组都要能当场看出来。
    """
    upcoming, received, undated = split_by_date(entries, as_of)
    return {
        "as_of": as_of.isoformat(),
        **{key: [calendar_item(e, as_of) for e in group] for key, group in zip(
            GROUP_KEYS, (upcoming, received, undated))},
        "totals": {
            "count": len(entries),
            "by_currency": _money(sum_by_currency(entries)),
            "upcoming_by_currency": _money(sum_by_currency(upcoming)),
            "received_by_currency": _money(sum_by_currency(received)),
            "undated_by_currency": _money(sum_by_currency(undated)),
            # 待派的笔数与下一次派息日 —— 「提醒」这件事的两个最短答案。
            # 日期未知的那些不计入 next_pay_date：拿一个未知日期去当「下一次」是编的。
            "upcoming_count": len(upcoming),
            "next_pay_date": upcoming[0].pay_date.isoformat() if upcoming else None,
        },
    }
