"""行情抓取。

约定：每种数据源实现 fetch_xxx(asset) -> dict(price, currency, change_pct, source) 或 None。
任何一家挂了不影响其它源，失败返回 None 由上层用上一次价格兜底。
"""
from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal
import logging
from typing import NamedTuple

import requests
from django.conf import settings
from django.utils import timezone

from apps.assets.models import Asset
from apps.core.models import Market

from .models import PriceQuote

logger = logging.getLogger(__name__)
TIMEOUT = 6
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) asset-ledger/0.1"}


def _tencent_code(asset: Asset) -> str:
    symbol = asset.symbol.strip()
    if asset.market == Market.A:
        if symbol.lower().startswith(("sh", "sz", "bj")):
            return symbol.lower()
        prefix = "sh" if symbol.startswith("6") else "sz"
        return f"{prefix}{symbol}"
    if asset.market == Market.HK:
        return f"hk{symbol.zfill(5)}" if not symbol.lower().startswith("hk") else symbol.lower()
    if asset.market == Market.US:
        return f"us{symbol.upper()}" if not symbol.lower().startswith("us") else symbol.lower()
    return symbol.lower()


def fetch_tencent(asset: Asset) -> dict | None:
    """A股/港股/美股（腾讯行情，免费无需 Key）。"""
    if asset.market not in (Market.A, Market.HK, Market.US):
        return None
    code = _tencent_code(asset)
    try:
        resp = requests.get(f"https://qt.gtimg.cn/q={code}", headers=UA, timeout=TIMEOUT)
        resp.encoding = "gbk"
        body = resp.text
        if '="' not in body:
            return None
        payload = body.split('="', 1)[1].rstrip('";\n')
        parts = payload.split("~")
        if len(parts) < 33:
            return None
        price = Decimal(parts[3])
        prev_close = Decimal(parts[4]) if parts[4] else None
        change_pct = None
        if prev_close and prev_close != 0:
            change_pct = (price - prev_close) / prev_close * 100
        return {
            "price": price,
            "currency": "HKD" if asset.market == Market.HK else ("USD" if asset.market == Market.US else "CNY"),
            "change_pct": change_pct,
            "source": "tencent",
        }
    except Exception as exc:  # 网络类异常必须吞掉，避免拖垮整个记账流程
        logger.warning("tencent quote failed %s: %s", asset.symbol, exc)
        return None


def fetch_okx(asset: Asset) -> dict | None:
    """数字货币（OKX 公开接口）。BTC-USDT 形式，或裸 BTC 默认补 -USDT。"""
    if asset.market != Market.CRYPTO:
        return None
    symbol = asset.symbol.upper()
    inst = symbol if "-" in symbol else f"{symbol}-USDT"
    try:
        resp = requests.get(
            "https://www.okx.com/api/v5/market/ticker",
            params={"instId": inst},
            headers=UA,
            timeout=TIMEOUT,
        )
        data = resp.json()
        rows = data.get("data") or []
        if not rows:
            return None
        last = Decimal(rows[0]["last"])
        open24 = Decimal(rows[0]["open24h"])
        change_pct = (last - open24) / open24 * 100 if open24 else None
        return {"price": last, "currency": "USDT", "change_pct": change_pct, "source": "okx"}
    except Exception as exc:
        logger.warning("okx quote failed %s: %s", asset.symbol, exc)
        return None


def fetch_yahoo(asset: Asset) -> dict | None:
    """美股备用源（Yahoo Finance，约 15 分钟延迟）。"""
    if asset.market != Market.US:
        return None
    try:
        resp = requests.get(
            f"https://query1.finance.yahoo.com/v8/finance/chart/{asset.symbol.upper()}",
            params={"interval": "1d", "range": "1d"},
            headers=UA,
            timeout=TIMEOUT,
        )
        result = (resp.json() or {}).get("chart", {}).get("result") or []
        if not result:
            return None
        meta = result[0]["meta"]
        price = Decimal(str(meta.get("regularMarketPrice")))
        prev = meta.get("chartPreviousClose") or meta.get("previousClose")
        change_pct = (price - Decimal(str(prev))) / Decimal(str(prev)) * 100 if prev else None
        return {"price": price, "currency": meta.get("currency", "USD"), "change_pct": change_pct, "source": "yahoo"}
    except Exception as exc:
        logger.warning("yahoo quote failed %s: %s", asset.symbol, exc)
        return None


SOURCES = (fetch_tencent, fetch_okx, fetch_yahoo)


def fetch_quote(asset: Asset) -> dict | None:
    """按市场优先级依次尝试各数据源。"""
    for fn in SOURCES:
        data = fn(asset)
        if data and data.get("price"):
            return data
    return None


def fetch_fx(base: str, quote: str) -> Decimal | None:
    """汇率。主源 Frankfurter（欧洲央行），备用 open.er-api.com，均免费无需 Key。"""
    if base == quote:
        return Decimal("1")
    try:
        resp = requests.get(
            "https://api.frankfurter.app/latest",
            params={"from": base, "to": quote},
            timeout=TIMEOUT,
        )
        rates = (resp.json() or {}).get("rates") or {}
        value = rates.get(quote)
        if value:
            return Decimal(str(value))
    except Exception as exc:
        logger.warning("frankfurter failed %s/%s: %s", base, quote, exc)

    try:
        resp = requests.get(f"https://open.er-api.com/v6/latest/{base}", timeout=TIMEOUT)
        rates = (resp.json() or {}).get("rates") or {}
        value = rates.get(quote)
        if value:
            return Decimal(str(value))
    except Exception as exc:
        logger.warning("er-api failed %s/%s: %s", base, quote, exc)
    return None


def today() -> date:
    return date.today()


class QuoteRefresh(NamedTuple):
    """一轮报价刷新的结果。

    quote    最新快照（缓存期内直接复用，也可能是上一次的旧价）
    stale    这一轮想抓但没抓到，交出去的是旧价
    fetched  这一轮真的抓到了新价并落库
    """

    quote: PriceQuote | None
    stale: bool
    fetched: bool


def refresh_quote(
    asset: Asset,
    *,
    force: bool = False,
    now=None,
    cache_seconds: int | None = None,
) -> QuoteRefresh:
    """抓一只标的的行情并落库。

    **全仓库唯一一处写 `PriceQuote` 的代码** —— 定时任务、管理命令、
    `/market/quotes/` 接口都调它。

    在这之前，会写行情快照的只有两条不持续的路：接口（客户端从不调，只有手工
    调试用过）与一次性的 `seed_demo`。于是行情只在被人手动点一下时更新，
    没跑过演示数据的账号连一条都没有 —— 现价恒为空、总资产恒 0.00、浮盈恒为空，
    整条链路一声不吭（见 `quote_schedule.py` 的模块说明）。
    抓不到就什么都不写，把上一次的价标成 stale 交出去 —— 不写空记录。
    """
    cache_seconds = settings.QUOTE_CACHE_SECONDS if cache_seconds is None else cache_seconds
    now = now or timezone.now()
    cut = now - timedelta(seconds=cache_seconds)

    latest = PriceQuote.objects.filter(asset=asset).order_by("-fetched_at").first()
    if latest and not force and latest.fetched_at >= cut:
        return QuoteRefresh(latest, stale=False, fetched=False)

    data = fetch_quote(asset)
    if not data or not data.get("price"):
        return QuoteRefresh(latest, stale=True, fetched=False)

    quote = PriceQuote.objects.create(
        asset=asset,
        price=data["price"],
        currency=data.get("currency") or asset.currency,
        change_pct=data.get("change_pct"),
        source=data.get("source", ""),
    )
    return QuoteRefresh(quote, stale=False, fetched=True)


def refresh_quotes(
    assets=None,
    *,
    force: bool = False,
    now=None,
    cache_seconds: int | None = None,
) -> dict:
    """抓一轮：不传 `assets` 就是全部标的。定时任务与管理命令调这个。

    返回 `{"fetched": n, "skipped": n, "failed": n}` —— 三种结果分开报，
    否则「全都失败」和「全在缓存里」看起来都是 0 条，又是一次静默。
    """
    if assets is None:
        assets = list(Asset.objects.all())

    fetched = skipped = failed = 0
    for asset in assets:
        result = refresh_quote(asset, force=force, now=now, cache_seconds=cache_seconds)
        if result.fetched:
            fetched += 1
        elif result.stale:
            failed += 1
        else:
            skipped += 1
    return {"fetched": fetched, "skipped": skipped, "failed": failed}
