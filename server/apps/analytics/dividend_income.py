"""股息归集与指标计算。

**这是「什么算作一笔股息」这条规则的唯一定义处。**

为什么不放在 services.py 里：股息有两条合法录入路径，历史上两处各算各的，导致
同一个数字在两个页面不一样（实测：持仓页股息合计 0、统计页 810）——

  A. ``POST /transactions/dividends/`` → 只落 ``DividendRecord``（含税后净额 net）
  B. ``POST /transactions/records/`` side=DIVIDEND → 只落一条 DIVIDEND 流水（现金变动）
  C. 两者都落，且 ``DividendRecord.transaction`` 指向那条流水（docs/DESIGN.md §3 的形态）

归集规则（去重，一条股息只计一次）：

  1. 每一条 ``DividendRecord`` 计一次，金额取 ``net``（税后口径 = 用户真正到手的钱），
     并把它关联的那条流水标记为「已被明细代表」。
  2. 没有被任何 ``DividendRecord`` 代表的 DIVIDEND 流水，计一次，金额取 ``abs(amount)``。
  3. 因此形态 C 不会重复计，形态 A / B 都算数。

本模块**纯 Python、不 import django**，因此可以直接单测（不需要数据库）。
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal

ZERO = Decimal("0")

# 年度股息与股息率的观察窗口：近 365 天。
# 用「近期」而不是「本自然年」，是为了让 12 月刚建的账本在次年 1 月不显示成 0。
ANNUAL_WINDOW_DAYS = 365


#: `origin` 的中文名。
#:
#: 加入集规则产生第三种来源，这张表必须跟着加 —— `apps/analytics/tests/test_dividend_export.py`
#: 里有一条锁盯着「源码里出现的 origin 字样与这张表一一对应」，否则导出里会冒出一列英文，
#: 而且不会有人报错。
ORIGIN_LABELS = {
    "record": "股息明细",
    "transaction": "流水录入",
}


@dataclass(frozen=True)
class DividendEntry:
    """归集后的一条股息（已去重、已定金额口径）。

    `amount` 之后那几个是**明细字段**：只有 ``origin == "record"`` 的条目才有，
    经由流水录入的股息一律是 ``None``，**不是 0**。

    「没有明细可查」与「税前就是 0 / 没收过税」对用户是两件事：导出 CSV 时前者写成
    空格子、后者写成 ``0``。混成同一个值，等于替用户在文件里宣布了一件没人知道的事。
    （同一条口径也写在 `services.dividend_monthly` 的 docstring 里。）
    """

    asset_id: int | None
    account_id: int | None
    currency: str
    amount: Decimal
    pay_date: date | None
    origin: str  # "record" | "transaction"，仅用于排查来源

    # ---- 以下仅「有股息明细」的条目才有 ----
    gross: Decimal | None = None
    tax: Decimal | None = None
    ex_date: date | None = None
    shares: Decimal | None = None
    amount_per_share: Decimal | None = None
    reinvested: bool | None = None

    @property
    def position_key(self) -> tuple:
        return (self.account_id, self.asset_id)

    @property
    def has_detail(self) -> bool:
        """这一条有没有可查的股息明细 —— 明细那几列是数字还是空格子由它决定。"""
        return self.origin == "record"


def _dec(value) -> Decimal:
    """None / 空串一律当 0；字符串与 float 都收，避免调用方各转一遍。"""
    if value is None or value == "":
        return ZERO
    if isinstance(value, Decimal):
        return value
    return Decimal(str(value))


def _opt_dec(value) -> Decimal | None:
    """**可空**数值：缺了就是 ``None``，不是 0（理由见 `DividendEntry`）。

    明细字段必须走这一条而不是 `_dec()` —— 用 `_dec()` 的话，「这条没有明细」会
    变成「税前 0」，导出 CSV 里那一格就从空格子变成 `0`。
    """
    if value is None or value == "":
        return None
    return _dec(value)


def _as_date(value) -> date | None:
    """归一成 date。

    **先判 datetime**：``datetime`` 是 ``date`` 的子类，``isinstance(dt, date)`` 为真，
    所以想「不是 date 就取 .date()」的写法会把 datetime 原样放过去 —— 随后
    ``date <= datetime`` 直接 TypeError。真实数据里 ``Transaction.traded_at``
    （USE_TZ=True 时是 aware datetime）走的正是这条路。
    """
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value.date()
    return value


def collect_dividends(records, transactions) -> list[DividendEntry]:
    """按模块头部那三条规则归集股息。

    records / transactions 为 dict 序列，由调用方（services.py）从 ORM 摘出：

    - record: ``asset_id`` / ``account_id`` / ``net`` / ``currency`` / ``pay_date`` / ``transaction_id``
      ＋ 明细字段 ``gross`` / ``tax`` / ``ex_date`` / ``shares`` / ``amount_per_share`` / ``reinvested``
    - transaction: ``id`` / ``asset_id`` / ``account_id`` / ``amount`` / ``currency`` / ``traded_at``

    明细字段在**这里**就挂到条目上，而不是留给导出侧回头去 ORM 里二次匹配 ——
    按 (标的, 账户, 派息日, 金额) 回查匹配不唯一（同一天同金额的两笔股息会互相错配），
    而那种错配是安静的：金额列还是对的，只有税前/持股数那几列张冠李戴。
    """
    entries: list[DividendEntry] = []
    represented: set = set()

    for row in records:
        linked = row.get("transaction_id")
        if linked is not None:
            represented.add(linked)
        entries.append(
            DividendEntry(
                asset_id=row.get("asset_id"),
                account_id=row.get("account_id"),
                currency=(row.get("currency") or "").upper(),
                amount=_dec(row.get("net")),
                pay_date=_as_date(row.get("pay_date")),
                origin="record",
                gross=_opt_dec(row.get("gross")),
                tax=_opt_dec(row.get("tax")),
                ex_date=_as_date(row.get("ex_date")),
                shares=_opt_dec(row.get("shares")),
                amount_per_share=_opt_dec(row.get("amount_per_share")),
                reinvested=row.get("reinvested"),
            )
        )

    for row in transactions:
        if row.get("id") in represented:
            continue
        amount = _dec(row.get("amount"))
        if amount == ZERO:
            continue
        entries.append(
            DividendEntry(
                asset_id=row.get("asset_id"),
                account_id=row.get("account_id"),
                currency=(row.get("currency") or "").upper(),
                amount=abs(amount),
                pay_date=_as_date(row.get("traded_at")),
                origin="transaction",
            )
        )

    return entries


def within_window(entries, as_of: date, days: int = ANNUAL_WINDOW_DAYS) -> list[DividendEntry]:
    """近 ``days`` 天内的股息。

    ``pay_date`` 缺失的条目**不计入**窗口（但仍在总账里）——「日期未知」与
    「日期不在窗口内」是两件事，混在一起会让股息率虚高。
    """
    floor = date.fromordinal(as_of.toordinal() - days)
    return [e for e in entries if e.pay_date is not None and floor <= e.pay_date <= as_of]


def group_by_position(entries) -> dict[tuple, Decimal]:
    """按 (账户, 标的) 聚合，供持仓明细使用。"""
    buckets: dict[tuple, Decimal] = {}
    for entry in entries:
        buckets[entry.position_key] = buckets.get(entry.position_key, ZERO) + entry.amount
    return buckets


def sum_by_currency(entries) -> dict[str, Decimal]:
    """按币种聚合，供跨币种折算使用（折算汇率由调用方决定）。"""
    buckets: dict[str, Decimal] = {}
    for entry in entries:
        buckets[entry.currency] = buckets.get(entry.currency, ZERO) + entry.amount
    return buckets


def total(entries) -> Decimal:
    """不做折算的「同币种才可比」合计 —— 只在单一币种场景下使用。"""
    return sum((e.amount for e in entries), ZERO)


def dividend_yield(annual_amount: Decimal, cost_basis: Decimal) -> Decimal | None:
    """股息率 = 近一年股息 ÷ 当前成本。

    成本为 0 或负（已清仓）时返回 ``None`` 而不是 0 —— 「算不出」与「收益率是 0」
    对用户是两件事，显示成 0% 会把「没有数据」说成「没有收益」。
    """
    if cost_basis is None or cost_basis <= ZERO:
        return None
    if annual_amount is None:
        return None
    return annual_amount / cost_basis


def monthly_passive_income(annual_amount: Decimal, months: int = 12) -> Decimal | None:
    """月度被动收入 = 近一年股息 ÷ 12。"""
    if annual_amount is None or months <= 0:
        return None
    return annual_amount / Decimal(months)
