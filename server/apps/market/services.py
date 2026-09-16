"""行情抓取。

约定：每种数据源实现 fetch_xxx(asset) -> dict(price, currency, change_pct, source) 或 None。
任何一家挂了不影响其它源，失败返回 None 由上层用上一次价格兜底。

腾讯那一家的代码规范化与批量解析在 `tencent.py`（**刻意不 import django**，可离线断言）。
这里只负责 HTTP 与落库：走腾讯的三个市场**合并成一次请求**，其余市场逐只问各自的数据源。
"""
from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal
import logging
import threading
from typing import NamedTuple

import requests
from django.conf import settings
from django.utils import timezone

from apps.assets.models import Asset
from apps.core.models import Market

from . import tencent
from .models import PriceQuote

logger = logging.getLogger(__name__)
TIMEOUT = 6
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) asset-ledger/0.1"}

#: 走腾讯行情的市场。取值只有一份（`tencent.TENCENT_MARKETS`）。
_TENCENT_MARKETS = tuple(tencent.TENCENT_MARKETS)

# 这一轮真的发了几条 HTTP。数字报给用户看不是装饰：README 的已知约束里写着
# 「免费源没有推送，轮询太快会被源限流」，而就在这次改动之前，7 只标的 = 7 条请求。
# 用 thread local 而不是全局变量：`/market/quotes/` 是请求线程、定时任务是另一个
# 线程，两边同时抓时全局计数会互相污染，报出来的数就不可信了。
_http_state = threading.local()


def _reset_http_count() -> None:
    _http_state.count = 0


def _http_count() -> int:
    return getattr(_http_state, "count", 0)


def _http_get(url: str, **kwargs):
    """本模块**唯一**的出网点 —— 所有数据源都从这里走，好把次数数清楚。"""
    _http_state.count = _http_count() + 1
    return requests.get(url, **kwargs)


def _tencent_body(codes: list[str]) -> str | None:
    """问腾讯要一批代码的行情原文。**一次调用 = 一条请求。**

    网络类异常必须吞掉（返回 None），避免一只票挂着把整轮记账流程拖垮；
    失败只影响这一批，上层还有逐只兜底与上一次的旧价。
    """
    try:
        resp = _http_get(tencent.batch_url(codes), headers=UA, timeout=TIMEOUT)
        resp.encoding = "gbk"
        return resp.text
    except Exception as exc:
        logger.warning("tencent quote failed %s: %s", ",".join(codes), exc)
        return None


def _currency(market: str) -> str:
    return "HKD" if market == Market.HK else ("USD" if market == Market.US else "CNY")


def _tencent_data(asset: Asset, parsed: dict) -> dict:
    return {
        "price": parsed["price"],
        "currency": _currency(str(asset.market)),
        "change_pct": parsed["change_pct"],
        "source": "tencent",
    }


def fetch_tencent(asset: Asset) -> dict | None:
    """A股/港股/美股（腾讯行情，免费无需 Key）。单只版。

    批量走 `fetch_quotes()`；这一条留着给「就查这一只」的场景，也留作
    批量路径里没抓到的那些标的的兜底口径。
    """
    code = tencent.tencent_code(str(asset.market), asset.symbol)
    if not code:
        if str(asset.market) in _TENCENT_MARKETS:
            # 代码认不出来是**静默**的（腾讯对认不出的代码不报错、整行不出现）。
            # 以前这里一声不吭，用户只能看到「现价是空的」而没有任何线索。
            logger.warning("tencent 代码认不出来，本轮跳过：%s %s", asset.market, asset.symbol)
        return None
    body = _tencent_body([code])
    parsed = tencent.parse_batch(body or "").get(code)
    if not parsed:
        return None
    return _tencent_data(asset, parsed)


def fetch_okx(asset: Asset) -> dict | None:
    """数字货币（OKX 公开接口）。BTC-USDT 形式，或裸 BTC 默认补 -USDT。"""
    if asset.market != Market.CRYPTO:
        return None
    symbol = asset.symbol.upper()
    inst = symbol if "-" in symbol else f"{symbol}-USDT"
    try:
        resp = _http_get(
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
        resp = _http_get(
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


#: 数据源的尝试顺序。名字显式写出来，是为了 `fetch_quote(skip=...)` ——
#: 批量路径已经问过腾讯的标的，兜底时不该再问它第二遍（再问一遍只会再失败一次、
#: 还多一条请求，正是 README 里「轮询太快会被源限流」那句所指的压力）。
SOURCE_ORDER = (
    ("tencent", fetch_tencent),
    ("okx", fetch_okx),
    ("yahoo", fetch_yahoo),
)
SOURCES = tuple(fn for _, fn in SOURCE_ORDER)


def fetch_quote(asset: Asset, *, skip: tuple[str, ...] = ()) -> dict | None:
    """按市场优先级依次尝试各数据源。`skip` 里列出的源名直接跳过。"""
    for name, fn in SOURCE_ORDER:
        if name in skip:
            continue
        data = fn(asset)
        if data and data.get("price"):
            return data
    return None


def fetch_quotes(
    assets,
    *,
    batch_size: int | None = None,
) -> tuple[dict[int, dict | None], int]:
    """一批标的的行情，返回 `(asset_id -> data|None, 批数)`。

    走腾讯的三个市场**合并成一条请求**（每 `batch_size` 个代码一条，见 `tencent.py`）；
    没回来的（代码认不出、源不认、或者源挂了）再逐只走**剩下的**数据源 ——
    「任何一家挂了不影响其它源」这条约定不变。

    发了多少条 HTTP 由 `_http_count()` 统一数（腾讯 + OKX + Yahoo 都算），
    这里只回批数。
    """
    assets = list(assets)
    if not assets:
        return {}, 0

    by_code = tencent.group_by_code(
        (asset.id, str(asset.market), asset.symbol) for asset in assets
    )
    parsed, _requests_made, batches = tencent.fetch_all(
        list(by_code), _tencent_body, batch_size=batch_size
    )

    by_id = {asset.id: asset for asset in assets}
    data: dict[int, dict | None] = {}
    for code, asset_ids in by_code.items():
        hit = parsed.get(code)
        if not hit:
            continue
        for asset_id in asset_ids:
            data[asset_id] = _tencent_data(by_id[asset_id], hit)

    # 逐只兜底。问过腾讯的不再问第二遍（`skip`），否则每只失败的票都会变成两条请求。
    asked = {asset_id for asset_ids in by_code.values() for asset_id in asset_ids}
    for asset in assets:
        if data.get(asset.id):
            continue
        data[asset.id] = fetch_quote(asset, skip=("tencent",) if asset.id in asked else ())
    return data, batches


def fetch_fx(base: str, quote: str) -> Decimal | None:
    """汇率。主源 Frankfurter（欧洲央行），备用 open.er-api.com，均免费无需 Key。"""
    if base == quote:
        return Decimal("1")
    try:
        resp = _http_get(
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
        resp = _http_get(f"https://open.er-api.com/v6/latest/{base}", timeout=TIMEOUT)
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


#: 哨兵：区分「批量路径没给过 data」与「批量路径给了 data、结果是 None」。
#: 前者要自己出网取价，后者说明这一批已经问过了，不许再问第二遍。
_UNSET = object()


def _latest_quote(asset: Asset) -> PriceQuote | None:
    return PriceQuote.objects.filter(asset=asset).order_by("-fetched_at").first()


def _is_fresh(latest: PriceQuote | None, *, force: bool, cut) -> bool:
    """缓存期内（且没要求强制重抓）就直接复用，不出网。"""
    return bool(latest) and not force and latest.fetched_at >= cut


def _refresh_one(asset: Asset, *, latest, force: bool, cut, data) -> QuoteRefresh:
    """一只标的的「查缓存 -> 取价 -> 落库」。

    **全仓库唯一一处写 `PriceQuote` 的路径。** 单只与批量都从这里过，
    所以缓存判据、空价兜底、落库字段只有一份，不会漂。
    """
    if _is_fresh(latest, force=force, cut=cut):
        return QuoteRefresh(latest, stale=False, fetched=False)

    if data is _UNSET:
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


def refresh_quote(
    asset: Asset,
    *,
    force: bool = False,
    now=None,
    cache_seconds: int | None = None,
) -> QuoteRefresh:
    """抓一只标的的行情并落库。

    在这之前，会写行情快照的只有两条不持续的路：接口（客户端从不调，只有手工
    调试用过）与一次性的 `seed_demo`。于是行情只在被人手动点一下时更新，
    没跑过演示数据的账号连一条都没有 —— 现价恒为空、总资产恒 0.00、浮盈恒为空，
    整条链路一声不吭（见 `quote_schedule.py` 的模块说明）。
    抓不到就什么都不写，把上一次的价标成 stale 交出去 —— 不写空记录。
    """
    cache_seconds = settings.QUOTE_CACHE_SECONDS if cache_seconds is None else cache_seconds
    now = now or timezone.now()
    return _refresh_one(
        asset,
        latest=_latest_quote(asset),
        force=force,
        cut=now - timedelta(seconds=cache_seconds),
        data=_UNSET,
    )


def refresh_quote_map(
    assets,
    *,
    force: bool = False,
    now=None,
    cache_seconds: int | None = None,
    batch_size: int | None = None,
) -> tuple[dict[int, QuoteRefresh], int, int]:
    """一批标的各刷一次，返回 `(asset_id -> QuoteRefresh, HTTP 次数, 批数)`。

    **抓行情的唯一入口** —— 定时任务、管理命令、`/market/quotes/` 都从这里进。
    先按缓存判据筛掉不用抓的（**一个请求都不发**），再对剩下的走批量取价。

    `HTTP 次数` 是这一轮真的发出去了几条（腾讯 + OKX + Yahoo + 汇率源），
    `批数` 是腾讯切了几批 —— 前者比值后者大是正常的（其余市场逐只问）。
    """
    assets = list(assets)
    cache_seconds = settings.QUOTE_CACHE_SECONDS if cache_seconds is None else cache_seconds
    now = now or timezone.now()
    cut = now - timedelta(seconds=cache_seconds)

    # latest 只查一遍：`_refresh_one` 也要用它来兜底（抓不到就交旧价）。
    latest = {asset.id: _latest_quote(asset) for asset in assets}
    pending = [
        asset for asset in assets if not _is_fresh(latest[asset.id], force=force, cut=cut)
    ]

    _reset_http_count()
    batches = 0
    prefetched: dict[int, dict | None] = {}
    if pending:
        prefetched, batches = fetch_quotes(pending, batch_size=batch_size)

    results = {}
    for asset in assets:
        results[asset.id] = _refresh_one(
            asset,
            latest=latest[asset.id],
            force=force,
            cut=cut,
            data=prefetched[asset.id] if asset.id in prefetched else _UNSET,
        )
    # 计数在读之前不能重算：待抓的那些标的的 HTTP 全发生在 fetch_quotes 里
    # （批量 + 逐只兜底），`_refresh_one` 拿到预取的 data 后只落库、不出网。
    return results, _http_count(), batches


def refresh_quotes(
    assets=None,
    *,
    force: bool = False,
    now=None,
    cache_seconds: int | None = None,
    batch_size: int | None = None,
) -> dict:
    """抓一轮：不传 `assets` 就是全部标的。定时任务与管理命令调这个。

    返回 `{"fetched", "skipped", "failed", "requests", "batches"}` —— 前三种结果
    分开报，否则「全都失败」和「全在缓存里」看起来都是 0 条，又是一次静默。
    后两个是这一轮真的发了几条 HTTP，用来盯批量有没有生效（见 `fetch_quotes`）。
    """
    if assets is None:
        assets = list(Asset.objects.all())

    results, requests_made, batches = refresh_quote_map(
        assets, force=force, now=now, cache_seconds=cache_seconds, batch_size=batch_size
    )
    fetched = sum(1 for result in results.values() if result.fetched)
    failed = sum(1 for result in results.values() if result.stale)
    return {
        "fetched": fetched,
        "skipped": len(results) - fetched - failed,
        "failed": failed,
        "requests": requests_made,
        "batches": batches,
    }
