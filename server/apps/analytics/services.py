"""账本引擎：一切从流水推导，不存第二份真相。"""
from __future__ import annotations

from collections import defaultdict
from datetime import date
from decimal import Decimal

from django.conf import settings
from django.utils import timezone

from apps.market.models import FxRate, PriceQuote
from apps.market.services import fetch_fx
from apps.transactions.models import DividendRecord, Transaction

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
        elif tx.side == "DIVIDEND":
            bucket["dividend_total"] += abs(tx.amount)
        elif tx.side == "SPLIT":
            bucket["quantity"] += qty

    positions = []
    for bucket in buckets.values():
        if bucket["quantity"] == 0 and bucket["realized_pnl"] == 0 and bucket["dividend_total"] == 0:
            continue
        qty = bucket["quantity"]
        avg_cost = (bucket["cost_basis"] / qty) if qty else ZERO
        price = latest_price(bucket["asset_id"]) if bucket["asset_id"] else None
        market_value = (price * qty) if (price and qty) else None
        unrealized = (market_value - bucket["cost_basis"]) if market_value is not None else None
        positions.append(
            {
                **bucket,
                "quantity": str(qty),
                "avg_cost": str(avg_cost),
                "cost_basis": str(bucket["cost_basis"]),
                "realized_pnl": str(bucket["realized_pnl"]),
                "dividend_total": str(bucket["dividend_total"]),
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
    total_value = ZERO
    total_realized = ZERO
    total_unrealized = ZERO
    fx_warning = False

    for pos in positions:
        currency = pos["currency"] or base
        rate = get_rate(currency, base)
        if currency != base and rate == 1:
            fx_warning = True
        total_cost += Decimal(pos["cost_basis"]) * rate
        total_realized += Decimal(pos["realized_pnl"]) * rate
        if pos["market_value"]:
            total_value += Decimal(pos["market_value"]) * rate
            total_unrealized += Decimal(pos["unrealized_pnl"]) * rate

    dividend_qs = DividendRecord.objects.filter(user=user)
    dividend_total = ZERO
    for row in dividend_qs:
        rate = get_rate(row.currency, base)
        dividend_total += (row.net or ZERO) * rate

    flows: list[tuple[date, float]] = []
    deposits = Transaction.objects.filter(user=user, side__in=("DEPOSIT", "WITHDRAW")).values_list("traded_at", "amount", "currency")
    for traded_at, amount, currency in deposits:
        rate = get_rate(currency, base)
        flows.append((traded_at.date(), -float(Decimal(amount) * rate)))
    if total_value:
        flows.append((timezone.localdate(), float(total_value)))
    annualized = xirr(flows) if len(flows) >= 2 else None

    return {
        "base_currency": base,
        "market_value": str(total_value),
        "cost_basis": str(total_cost),
        "unrealized_pnl": str(total_unrealized),
        "realized_pnl": str(total_realized),
        "dividend_total": str(dividend_total),
        "total_pnl": str(total_unrealized + total_realized + dividend_total),
        "annualized": annualized,
        "annualized_note": None if annualized is not None else "现金流不足或日期过于集中，暂无法计算年化",
        "fx_warning": fx_warning,
        "position_count": len(positions),
    }


def dividend_monthly(user, year: int | None = None) -> list[dict]:
    qs = DividendRecord.objects.filter(user=user)
    if year:
        qs = qs.filter(pay_date__year=year)
    buckets: dict[str, dict] = defaultdict(lambda: {"net": ZERO, "gross": ZERO, "tax": ZERO, "count": 0})
    for row in qs:
        key = f"{row.pay_date.year}-{row.pay_date.month:02d}" if row.pay_date else "未定"
        buckets[key]["net"] += row.net or ZERO
        buckets[key]["gross"] += row.gross or ZERO
        buckets[key]["tax"] += row.tax or ZERO
        buckets[key]["count"] += 1
    return [
        {"month": k, "net": str(v["net"]), "gross": str(v["gross"]), "tax": str(v["tax"]), "count": v["count"]}
        for k, v in sorted(buckets.items())
    ]
