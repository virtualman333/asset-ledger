# -*- coding: utf-8 -*-
"""腾讯行情代码的规范化与批量响应解析。

**刻意不 import django** —— 和 `quote_schedule.py` / `amount_rules.py` 一样，
这里放的是「写错了不会报错、只会悄悄少一个价」的判据，必须能被秒级离线验证
（注入假传输即可断言「一批只请求一次」，不需要数据库、不需要网络）。

## 为什么要有这个模块

腾讯行情接口本来就吃**逗号分隔的多代码**：

    https://qt.gtimg.cn/q=sh600000,sz000001,hk00700,usAAPL,sh000001

实测一条请求 0.16s 回来 5 行。而在这之前，`services.refresh_quotes()` 是
**逐只请求**的 —— 60 只标的 = 60 次请求，一轮一轮地打。README 的已知约束里
自己写着「免费源没有推送，只能按 QUOTE_REFRESH_MINUTES 轮询；**轮询太快会被源
限流**」，等于把预告的限流亲手撞上。

## 这个接口的四个实测坑

1. **区分大小写。** `usAAPL` 有价；`usaapl` / `usAaPl` / `HK00700` / `SH600000`
   一律返回 `v_pv_none_match="1";` —— 不是 404、不是报错行，就是没这只标的。
   `_tencent_code` 以前把「已经带了 `us` 前缀」的 symbol 整串 `lower()`，
   于是一个正确的代码被改成一个查不到的代码（`usAAPL` -> `usaapl`），静默无价。
2. **认不出的代码整行不出现。** 实测 `sh600000,sz999999,usZZZZZZZ` 只回来 1 行，
   没有占位行。所以只能**按代码回填**，不能按请求顺序对齐。
3. **全不认时回来唯一一行** `v_pv_none_match="1";` —— 它不是标的。
4. **重复代码会重复出行。** 先出现的为准。
"""
from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Callable, Iterable, Sequence

TENCENT_QUOTE_URL = "https://qt.gtimg.cn/q="

#: 走腾讯行情的三个市场。**取值来自 `apps.core.models.Market`**（"A"/"HK"/"US"），
#: 两边对不上就会被 `tests/test_tencent.py` 里的结构锁按住。
TENCENT_MARKETS = ("A", "HK", "US")

#: 一条请求最多带几个代码。实测 60 个代码一次 0.28s、返回 47 行，远没到接口上限；
#: 留这个数只是别把 URL 拉成一条巨长的 GET。
TENCENT_MAX_CODES = 60

#: 一行 payload 至少要有的字段数，少于此一律当脏数据（当前实现只用到前 5 个）。
MIN_FIELDS = 33

#: 全不认时的那一行，`v_` 后面不是标的代码
_NONE_MATCH = "pv_none_match"


def tencent_code(market: str, symbol: str) -> str | None:
    """标的 -> 腾讯行情代码。**认不出来就返回 None，绝不猜。**

    `market` 传 `Market` 的原始值（"A"/"HK"/"US"…），不是枚举对象 ——
    枚举要 import django，这个模块不能有。
    """
    symbol = (symbol or "").strip()
    if not symbol:
        return None
    if market == "A":
        if symbol.lower().startswith(("sh", "sz", "bj")):
            return symbol.lower()
        return f"{'sh' if symbol.startswith('6') else 'sz'}{symbol}"
    if market == "HK":
        return f"hk{symbol.zfill(5)}" if not symbol.lower().startswith("hk") else symbol.lower()
    if market == "US":
        return us_code(symbol)
    return None


def us_code(symbol: str) -> str | None:
    """美股 symbol -> `us<TICKER>`（TICKER 必须原样大写，接口区分大小写）。

    ★ 麻烦在于：**`USB`（美国合众银行）本身就以 `us` 开头**，它和
    「`us` 前缀 + ticker `B`」在字符串上完全无法区分。所以：

    - `us`/`US` 前缀 + 后面那截**已经是全大写且 ≥2 位** -> 当前缀用（不猜）：
      `usAAPL` -> `usAAPL`、`USAAPL` -> `usAAPL`、`usBRK.B` -> `usBRK.B`
    - 其余以 us/US 开头的整串 -> 当**裸 ticker**：`USB` -> `usUSB`（实测有价）
    - 两头都不像 -> None：`usaapl`（前缀后面是小写）、`usB`（说不清是 `us`+`B`
      还是 `USB` 敲错了）。**少一个价，好过悄悄记一个错价。**

    代价写在明处：`USAA` 这种「US 前缀 + 2 位 ticker」会被当成前缀
    （`usAA`），而真实 ticker 里 `US` 开头且总长 ≥4 的极少（`USB` 是总长 3）。
    """
    if symbol[:2].lower() != "us":
        return f"us{symbol.upper()}"
    rest = symbol[2:]
    if len(rest) >= 2 and rest.isupper() and rest[0].isalpha():
        return f"us{rest}"
    if symbol.isupper() and symbol.isalpha():
        return f"us{symbol}"
    return None


def batch_url(codes: Sequence[str]) -> str:
    """一批代码拼成一条请求的 URL。"""
    return TENCENT_QUOTE_URL + ",".join(codes)


def chunk(codes: Sequence[str], size: int | None = None) -> list[list[str]]:
    """按 `size` 切批。`size` 非法（0/负数/None）时退回默认值。"""
    try:
        size = int(size)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        size = TENCENT_MAX_CODES
    if size < 1:
        size = TENCENT_MAX_CODES
    return [list(codes[i : i + size]) for i in range(0, len(codes), size)]


def parse_payload(payload: str) -> dict | None:
    """一行 payload（`~` 分隔的字段串）-> `{"price", "change_pct"}`，认不出返回 None。

    字段口径与单只版完全一致（`parts[3]` 现价、`parts[4]` 昨收），
    实测 A/港/美三家的涨跌幅都对得上接口自带的那个数。
    """
    parts = payload.split("~")
    if len(parts) < MIN_FIELDS:
        return None
    try:
        price = Decimal(parts[3])
    except (InvalidOperation, ArithmeticError):
        return None
    prev_close = None
    if parts[4]:
        try:
            prev_close = Decimal(parts[4])
        except (InvalidOperation, ArithmeticError):
            prev_close = None
    change_pct = None
    if prev_close:
        change_pct = (price - prev_close) / prev_close * 100
    return {"price": price, "change_pct": change_pct}


def parse_batch(body: str) -> dict[str, dict]:
    """批量响应 -> `{代码: {"price", "change_pct"}}`。

    响应是多行 `v_<代码>="<payload>";`。按**行自己带的代码**回填：
    认不出的代码那一行根本不出现（实测），照着请求顺序对齐一定会错位。
    """
    out: dict[str, dict] = {}
    for line in body.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        line = line.strip()
        if not line.startswith("v_") or '="' not in line:
            continue
        key, _, rest = line.partition('="')
        code = key[2:]
        if code == _NONE_MATCH or code in out:
            continue  # 兜底行 / 重复代码（先出现的为准）
        data = parse_payload(rest.rstrip('";'))
        if data:
            out[code] = data
    return out


def fetch_all(
    codes: Sequence[str],
    get_body: Callable[[list[str]], str | None],
    *,
    batch_size: int | None = None,
) -> tuple[dict[str, dict], int, int]:
    """分批请求并解析，返回 `({代码: 行情}, 发了几个请求, 切了几批)`。

    `get_body` 由调用方注入 —— HTTP 细节留在 `services`，而「一批只请求一次」
    这条能被离线断言（注入假传输、数它被调了几次），不必真出网。
    """
    parsed: dict[str, dict] = {}
    requests_made = batches = 0
    for group in chunk(list(codes), batch_size):
        batches += 1
        requests_made += 1
        body = get_body(group)
        if body is None:
            continue  # 这一批挂了不影响别的批，也不影响逐只兜底
        for code, data in parse_batch(body).items():
            if code not in parsed:
                parsed[code] = data
    return parsed, requests_made, batches


def group_by_code(pairs: Iterable[tuple[str, str]]) -> dict[str, list[int]]:
    """`[(序号, 市场, symbol)]` -> `{腾讯代码: [序号…]}`。

    同一代码只请求一次；几只标的共用一个代码时（同一只票记在两个账户下）
    一次响应回填给全部。
    """
    out: dict[str, list[int]] = {}
    for key, market, symbol in pairs:
        code = tencent_code(market, symbol)
        if code:
            out.setdefault(code, []).append(key)
    return out
