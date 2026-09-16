"""账本引擎：一切从流水推导，不存第二份真相。"""
from __future__ import annotations

from collections import defaultdict
from datetime import date
from decimal import Decimal

from django.conf import settings
from django.utils import timezone

from apps.accounts.models import Account
from apps.assets.models import Asset
from apps.market.models import FxRate, PriceQuote
from apps.market.services import fetch_fx
from apps.transactions.models import DividendRecord, Transaction

from .dividend_income import (
    ANNUAL_WINDOW_DAYS,
    collect_dividends,
    dividend_yield,
    group_by_position,
    monthly_passive_income,
    sum_by_currency,
    within_window,
)
from .valuation import is_fx_missing, terminal_value, valuate_positions, valuation_note

ZERO = Decimal("0")

# 稳定币按 1:1 锚定美元处理，否则汇率源查不到会错误折算
STABLE_TO_USD = {"USDT": "USD", "USDC": "USD", "BUSD": "USD", "DAI": "USD", "TUSD": "USD", "FDUSD": "USD"}


def get_rate(currency: str, base: str | None = None) -> Decimal:
    """取当日汇率，缺失则现抓，抓不到返回 1（调用方需标注）。"""
    base = base or settings.BASE_CURRENCY
    if not currency:
        return Decimal("1")
    currency = STABLE_TO_USD.get(currency.upper(), currency.upper())
    if currency == base.upper():
        return Decimal("1")
    row = FxRate.objects.filter(base=currency, quote=base).order_by("-date").first()
    if row:
        return row.rate
    rate = fetch_fx(currency, base)
    if rate:
        FxRate.objects.update_or_create(
            base=currency, quote=base, date=timezone.localdate(), defaults={"rate": rate, "source": "frankfurter"}
        )
        return rate
    return Decimal("1")


def latest_price(asset_id: int) -> Decimal | None:
    row = PriceQuote.objects.filter(asset_id=asset_id).order_by("-fetched_at").first()
    return row.price if row else None


def dividend_entries(user) -> list:
    """股息归集（唯一口径），持仓与统计两处共用。

    归集规则本身写在 ``dividend_income.collect_dividends`` 里，本函数只负责把
    ORM 数据摘成纯 dict —— 这样规则可以被单测覆盖，不需要数据库。
    """
    records = [
        {
            "asset_id": row.asset_id,
            "account_id": row.account_id,
            "net": row.net,
            "currency": row.currency,
            "pay_date": row.pay_date,
            "transaction_id": row.transaction_id,
        }
        for row in DividendRecord.objects.filter(user=user).only(
            "asset_id", "account_id", "net", "currency", "pay_date", "transaction_id"
        )
    ]
    dividend_txs = [
        {
            "id": row.id,
            "asset_id": row.asset_id,
            "account_id": row.account_id,
            "amount": row.amount,
            "currency": row.currency,
            "traded_at": row.traded_at,
        }
        for row in Transaction.objects.filter(user=user, side="DIVIDEND").only(
            "id", "asset_id", "account_id", "amount", "currency", "traded_at"
        )
    ]
    return collect_dividends(records, dividend_txs)


def build_positions(user) -> list[dict]:
    """按 (账户, 标的) 聚合持仓：移动加权成本 + 已实现盈亏 + 累计股息。"""
    txs = (
        Transaction.objects.filter(user=user)
        .select_related("asset", "account")
        .order_by("traded_at", "id")
    )
    buckets: dict[tuple, dict] = {}
    for tx in txs:
        if tx.side not in ("BUY", "SELL", "DIVIDEND", "SPLIT"):
            continue
        key = (tx.account_id, tx.asset_id)
        bucket = buckets.setdefault(
            key,
            {
                "account_id": tx.account_id,
                "account_name": tx.account.name if tx.account_id else "",
                "asset_id": tx.asset_id,
                "symbol": tx.asset.symbol if tx.asset_id else "",
                "name": tx.asset.name if tx.asset_id else "",
                "market": tx.asset.market if tx.asset_id else "",
                "currency": tx.currency,
                "quantity": ZERO,
                "cost_basis": ZERO,
                "realized_pnl": ZERO,
                "dividend_total": ZERO,
            },
        )
        qty = tx.quantity or ZERO
        price = tx.price or ZERO
        fee = tx.fee or ZERO
        tax = tx.tax or ZERO

        if tx.side == "BUY":
            bucket["quantity"] += qty
            bucket["cost_basis"] += qty * price + fee + tax
        elif tx.side == "SELL":
            avg = bucket["cost_basis"] / bucket["quantity"] if bucket["quantity"] else ZERO
            proceeds = qty * price - fee - tax
            bucket["realized_pnl"] += proceeds - avg * qty
            bucket["quantity"] -= qty
            bucket["cost_basis"] -= avg * qty
            if bucket["quantity"] <= 0:
                bucket["quantity"] = ZERO
                bucket["cost_basis"] = ZERO
        elif tx.side == "SPLIT":
            bucket["quantity"] += qty
        # DIVIDEND 流水在这里只负责「把这一格建出来」，金额统一由股息归集函数给，
        # 否则「只有股息明细、没有股息流水」的标的一行都不会出现。

    entries = dividend_entries(user)
    dividend_map = group_by_position(entries)
    annual_map = group_by_position(within_window(entries, timezone.localdate(), ANNUAL_WINDOW_DAYS))

    for key in dividend_map:
        if key in buckets:
            continue
        account_id, asset_id = key
        sample = next(e for e in entries if e.position_key == key)
        asset = Asset.objects.filter(pk=asset_id).first() if asset_id else None
        account = Account.objects.filter(pk=account_id).first() if account_id else None
        buckets[key] = {
            "account_id": account_id,
            "account_name": account.name if account else "",
            "asset_id": asset_id,
            "symbol": asset.symbol if asset else "",
            "name": asset.name if asset else "",
            "market": asset.market if asset else "",
            "currency": sample.currency or (account.currency if account else ""),
            "quantity": ZERO,
            "cost_basis": ZERO,
            "realized_pnl": ZERO,
            "dividend_total": ZERO,
        }

    for key, bucket in buckets.items():
        bucket["dividend_total"] = dividend_map.get(key, ZERO)

    positions = []
    for bucket in buckets.values():
        if bucket["quantity"] == 0 and bucket["realized_pnl"] == 0 and bucket["dividend_total"] == 0:
            continue
        qty = bucket["quantity"]
        avg_cost = (bucket["cost_basis"] / qty) if qty else ZERO
        price = latest_price(bucket["asset_id"]) if bucket["asset_id"] else None
        market_value = (price * qty) if (price and qty) else None
        unrealized = (market_value - bucket["cost_basis"]) if market_value is not None else None
        annual = annual_map.get((bucket["account_id"], bucket["asset_id"]), ZERO)
        position_yield = dividend_yield(annual, bucket["cost_basis"])
        positions.append(
            {
                **bucket,
                "quantity": str(qty),
                "avg_cost": str(avg_cost),
                "cost_basis": str(bucket["cost_basis"]),
                "realized_pnl": str(bucket["realized_pnl"]),
                "dividend_total": str(bucket["dividend_total"]),
                "annual_dividend": str(annual),
                "dividend_yield": str(position_yield) if position_yield is not None else None,
                "last_price": str(price) if price is not None else None,
                "market_value": str(market_value) if market_value is not None else None,
                "unrealized_pnl": str(unrealized) if unrealized is not None else None,
            }
        )
    positions.sort(key=lambda p: Decimal(p["cost_basis"] or 0), reverse=True)
    return positions


def xirr(flows: list[tuple[date, float]]) -> float | None:
    """现金流年化收益。flows 为 (日期, 金额)，投资者视角：投出去为负，收回为正。"""
    if len(flows) < 2:
        return None
    flows = sorted(flows, key=lambda x: x[0])
    d0 = flows[0][0]

    def xnpv(rate: float) -> float:
        return sum(amt / (1 + rate) ** ((d - d0).days / 365.0) for d, amt in flows)

    lo, hi = -0.9999, 10.0
    flo, fhi = xnpv(lo), xnpv(hi)
    if flo * fhi > 0:
        return None
    for _ in range(200):
        mid = (lo + hi) / 2
        fmid = xnpv(mid)
        if abs(fmid) < 1e-7:
            return mid
        if flo * fmid < 0:
            hi, fhi = mid, fmid
        else:
            lo, flo = mid, fmid
    return (lo + hi) / 2


def build_summary(user, base: str | None = None) -> dict:
    base = base or settings.BASE_CURRENCY
    positions = build_positions(user)
    total_cost = ZERO
    total_realized = ZERO

    # 一次请求里同币种只算一次汇率：get_rate 查不到当日记录会去现抓，
    # 按持仓数重复调用等于把网络请求翻了倍。
    rate_cache: dict[str, Decimal] = {}

    def rate_of(currency: str | None) -> Decimal:
        key = currency or base
        if key not in rate_cache:
            rate_cache[key] = get_rate(key, base)
        return rate_cache[key]

    for pos in positions:
        rate = rate_of(pos["currency"] or base)
        total_cost += Decimal(pos["cost_basis"]) * rate
        total_realized += Decimal(pos["realized_pnl"]) * rate

    # 估值（含「哪些持仓拿不到报价」）只在 valuation 这个纯函数里判一次，
    # 汇总字段与 XIRR 终值共用同一个结果 —— 不许各算一遍。
    valuation = valuate_positions(positions, base, rate_of)
    total_value = valuation["market_value"]
    total_unrealized = valuation["unrealized_pnl"]
    fx_warning = bool(valuation["fx_missing"])

    dividend_rows = dividend_entries(user)
    dividend_total = ZERO
    for currency, amount in sum_by_currency(dividend_rows).items():
        rate = rate_of(currency or base)
        if is_fx_missing(currency, base, rate):
            fx_warning = True
        dividend_total += amount * rate

    annual_total = ZERO
    for entry in within_window(dividend_rows, timezone.localdate(), ANNUAL_WINDOW_DAYS):
        annual_total += entry.amount * rate_of(entry.currency or base)

    flows: list[tuple[date, float]] = []
    deposits = Transaction.objects.filter(user=user, side__in=("DEPOSIT", "WITHDRAW")).values_list("traded_at", "amount", "currency")
    for traded_at, amount, currency in deposits:
        rate = rate_of(currency)
        flows.append((traded_at.date(), -float(Decimal(amount) * rate)))
    # 股息是投资者实实在在收到的现金，必须作为正现金流进 XIRR —— 它不在市值里
    # （除非分红再投），漏掉就等于把年化收益率算低。日期未知的股息不进现金流：
    # 硬塞一个今天会让年化被短期样本带偏。
    for entry in dividend_rows:
        if entry.pay_date is None or entry.amount == ZERO:
            continue
        rate = rate_of(entry.currency or base)
        flows.append((entry.pay_date, float(entry.amount * rate)))
    # 终值必须走 terminal_value：它把「有数量但拿不到报价」的持仓按成本算进来。
    # 以前这里是 `if total_value:`（只看有报价的市值），于是持仓拿不到价时
    # 终值凭空消失，年化把「本金还在」算成「本金没了」。
    terminal = terminal_value(valuation)
    if terminal:
        flows.append((timezone.localdate(), float(terminal)))
    annualized = xirr(flows) if len(flows) >= 2 else None

    yield_value = dividend_yield(annual_total, total_cost)
    monthly = monthly_passive_income(annual_total)

    return {
        "base_currency": base,
        "market_value": str(total_value),
        # 「有数量但拿不到报价」的那部分成本（已折算到基准货币）。
        # 与 market_value 分开报，页面才能说清「总资产为什么不是 0」。
        "unpriced_cost_basis": str(valuation["unpriced_cost_basis"]),
        "unpriced_symbols": valuation["unpriced_symbols"],
        "cost_basis": str(total_cost),
        "unrealized_pnl": str(total_unrealized),
        "realized_pnl": str(total_realized),
        "dividend_total": str(dividend_total),
        "dividend_annual": str(annual_total),
        # 成本为 0（已清仓）时算不出股息率，返回 null 而不是 0：见 dividend_income.dividend_yield
        "dividend_yield": str(yield_value) if yield_value is not None else None,
        "monthly_passive_income": str(monthly) if monthly is not None else None,
        "total_pnl": str(total_unrealized + total_realized + dividend_total),
        "annualized": annualized,
        # 一句话解释上面的数字：估值有没有缺口、年化为什么算不出来。
        # 它由 valuation_note 统一生成 —— 别在这里另写一套文案。
        "valuation_note": valuation_note(valuation, annualized),
        "fx_warning": fx_warning,
        "position_count": len(positions),
    }


def dividend_monthly(user, year: int | None = None) -> list[dict]:
    """股息月度分布。

    net / count 走统一股息口径（``dividend_entries``），与「累计股息」必然同源 ——
    否则同一页上「累计股息」和「月度分布合计」会各说各话。
    gross / tax 只有股息明细（DividendRecord）里才有，因此仅对「有明细的那部分」
    归集；纯流水录入的股息这两列为 0，表示「没有明细可查」，不是「没收过税」。
    """
    buckets: dict[str, dict] = defaultdict(lambda: {"net": ZERO, "gross": ZERO, "tax": ZERO, "count": 0})
    for entry in dividend_entries(user):
        if year and (entry.pay_date is None or entry.pay_date.year != year):
            continue
        key = f"{entry.pay_date.year}-{entry.pay_date.month:02d}" if entry.pay_date else "未定"
        buckets[key]["net"] += entry.amount
        buckets[key]["count"] += 1

    detail_qs = DividendRecord.objects.filter(user=user)
    if year:
        detail_qs = detail_qs.filter(pay_date__year=year)
    for row in detail_qs:
        key = f"{row.pay_date.year}-{row.pay_date.month:02d}" if row.pay_date else "未定"
        buckets[key]["gross"] += row.gross or ZERO
        buckets[key]["tax"] += row.tax or ZERO

    return [
        {"month": k, "net": str(v["net"]), "gross": str(v["gross"]), "tax": str(v["tax"]), "count": v["count"]}
        for k, v in sorted(buckets.items())
    ]
