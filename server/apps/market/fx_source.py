# -*- coding: utf-8 -*-
"""汇率源：源名单、响应解析，以及「这次是**谁**给的」。

**刻意不 import django** —— 与 `tencent.py` / `quote_schedule.py` / `amount_rules.py`
同族：这里放的是「写错了不会报错、只会悄悄用一个错数」的判据，必须能注入假传输、离线秒级验证。

## 一、为什么要有这个模块

`services.fetch_fx()` 原来长这样：先试 Frankfurter（欧洲央行），不行再试
`open.er-api.com`，都失败返回 `None` —— **只回一个 `Decimal`**。
于是「这次是谁给的」在调用方那边是不可知的，而两个调用方都把第一家的名字
硬编码进了 `FxRate.source`：

    analytics/services.py   ...update_or_create(..., defaults={..., "source": <硬编码>})
    market/views.py         ...update_or_create(..., defaults={..., "source": <硬编码>})

备用源顶上来的时候，库里那一行、以及 `GET /market/fx/` 回给客户端的 `source`，
写的都是第一家的名字 —— **这个值代码根本无从知道，是猜的**。行情那边不是这样：
`fetch_tencent` / `fetch_okx` / `fetch_yahoo` 各自把自己那家的名字填进 `source`，
所以「这个价是谁给的」是真值。汇率这边补上同一条性质。

## 二、两家的形状（都是实测过的）

Frankfurter（欧洲央行，按目标币种过滤）：

    GET https://api.frankfurter.app/latest?from=USD&to=CNY
    {"amount": 1.0, "base": "USD", "date": "2026-09-19", "rates": {"CNY": 7.1234}}

**`to` 里的币种它认不出来就不出现**（不报错、不给 `null`，`rates` 直接是空表）：

    {"amount": 1.0, "base": "USD", "date": "2026-09-19", "rates": {}}

open.er-api.com（备用，**不接受目标币种**，一次回一整张表）：

    GET https://open.er-api.com/v6/latest/USD
    {"result": "success", "base_code": "USD", "rates": {"CNY": 7.1234, ...}}

所以命中判据两家是同一条：**`rates` 里有没有这个币种**。
「拿到了 `rates`」不等于「拿到了这个币种」—— 写成「响应里有 rates 就算成功」，
第二种响应会让汇率取到空值或被当成 0，而这一路只会表现为总资产悄悄不对。
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal, InvalidOperation
import logging
from typing import Callable, Iterable, NamedTuple

logger = logging.getLogger(__name__)

#: 两家的名字 —— 这两个字符串就是 `FxRate.source` 的取值域。
#: 全仓只许出现在本文件里（`tests` 除外），由 `test_fx_source.py` 按住。
FRANKFURTER_NAME = "frankfurter"
ER_API_NAME = "er-api"

FRANKFURTER_URL = "https://api.frankfurter.app/latest"
ER_API_URL = "https://open.er-api.com/v6/latest"


class FxQuote(NamedTuple):
    """一家源给的价，连同**是谁给的**。"""

    rate: Decimal
    source: str


class CachedFx(NamedTuple):
    """库里已经躺着的一条汇率。"""

    rate: Decimal
    source: str
    date: date


def frankfurter_request(base: str, quote: str) -> tuple[str, dict]:
    """这一家的 URL 与查询串：目标币种写在**查询串**里。"""
    return FRANKFURTER_URL, {"from": base, "to": quote}


def er_api_request(base: str, quote: str) -> tuple[str, dict]:
    """这一家把源币种写在**路径**里，而且不接受目标币种（`quote` 用不上）。"""
    return f"{ER_API_URL}/{base}", {}


def _rate_from(payload, quote: str) -> Decimal | None:
    """两家的公共取数：`rates[quote]`，取不到 / 不是正数一律 `None`。

    `Decimal(str(raw))` 而不是 `Decimal(raw)`：JSON 里的数是二进制浮点读出来的，
    直接塞给 Decimal 会把 `7.1234` 变成 `7.1234000000000002037268210043907165527343750`。
    先 `str()` 走一遍，拿到的才是「人写下的那个数」。

    0 与负数不当汇率：库里存一个 0，总资产会变成 0，而表面上什么异常都没有。
    （改动前那版 `if value:` 也把 0 排除了，这条口径没变。）
    """
    if not isinstance(payload, dict):
        return None
    rates = payload.get("rates")
    if not isinstance(rates, dict):
        return None
    raw = rates.get(quote)
    if raw is None:
        return None
    try:
        rate = Decimal(str(raw))
    except (InvalidOperation, ValueError):
        return None
    if rate <= 0:
        return None
    return rate


def parse_frankfurter(payload, quote: str) -> Decimal | None:
    """Frankfurter 的响应。取不到这个币种就是 `None`（它不报错、只是不出现）。"""
    return _rate_from(payload, quote)


def parse_er_api(payload, quote: str) -> Decimal | None:
    """open.er-api.com 的响应。

    先看一眼 `result`：它报失败时 `rates` 也可能是空表，那时「源报了失败」与
    「这个币种不存在」取值一样，但让两种情形走同一条路会把源自己说的话抹掉。
    """
    if isinstance(payload, dict) and payload.get("result") not in (None, "success"):
        logger.warning("er-api 报的不是 success：%r", payload.get("result"))
        return None
    return _rate_from(payload, quote)


class FxSource(NamedTuple):
    """一家汇率源：叫什么、怎么问、怎么读。"""

    name: str
    request: Callable[[str, str], tuple]
    parse: Callable[..., object]


#: 尝试顺序。**名单与顺序只有这一份** —— `FxRate.source` 的取值域就是这里的 `name`，
#: README 那张表也拿它做两向对账（`test_fx_source.py`）。
FX_SOURCES = (
    FxSource(FRANKFURTER_NAME, frankfurter_request, parse_frankfurter),
    FxSource(ER_API_NAME, er_api_request, parse_er_api),
)
FX_SOURCE_NAMES = tuple(source.name for source in FX_SOURCES)


def fetch_fx(base: str, quote: str, *, transport) -> FxQuote | None:
    """按 `FX_SOURCES` 的顺序问，返回**实际给价的那一家**。

    `transport(url, params) -> dict | None` 由调用方注入 —— HTTP 细节留在
    `market/services.py`，而「第一家挂了、或者它根本不认这个币种时，会不会轮到
    第二家」这条能被离线断言（注入假传输，数它被问了几次、问的是谁）。
    `None` 表示这一家这次给不了；抛异常也一样（一个源挂了不该把整个请求带崩）。
    """
    for source in FX_SOURCES:
        url, params = source.request(base, quote)
        try:
            payload = transport(url, params)
        except Exception as exc:
            logger.warning("汇率源 %s 出网失败：%s", source.name, exc)
            continue
        if payload is None:
            continue
        rate = source.parse(payload, quote)
        if rate is not None:
            return FxQuote(rate=rate, source=source.name)
    return None


def is_fresh(row_date: date | None, today: date) -> bool:
    """库里这条汇率今天还能不能直接用。

    **两边口径必须一样。** 改动前 `analytics.services.get_rate` 的判据是「库里
    有记录吗」（三个月前那一条也照用），`market.views.FxView` 的判据是「记录是不是
    当天的」。于是同一天里 `/analytics/summary/` 折算用的汇率与 `/market/fx/`
    报出来的汇率可以是两个数，两边都不报错 —— 用户拿这两个数互相对账时才发现。

    `>=` 而不是 `==`：未来日期也算新鲜，跨时区写进来的行不该被判成过期。
    """
    return row_date is not None and row_date >= today


def latest(rows: Iterable[tuple]) -> CachedFx | None:
    """`(日期, 汇率, 来源)` 里最新的那一条 —— 一条都没有就 `None`。

    自己挑日期最大的那条，而不是 `rows[0]`：查询有没有 ORDER BY 是调用方的事，
    而「最后用了哪一条」这条判据不该跟着调用方的排序漂（`Meta.ordering` 一旦被
    谁改掉，`first()` 就会安静地换一条）。日期或汇率残缺的行直接跳过 ——
    宁可说「没有」，也不要交出一条不知道哪一天的汇率。
    """
    best: CachedFx | None = None
    for row in rows:
        row_date, rate, source = row
        if row_date is None or rate is None:
            continue
        if best is None or row_date > best.date:
            best = CachedFx(rate=rate, source=source or "", date=row_date)
    return best
