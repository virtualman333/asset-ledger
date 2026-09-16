# -*- coding: utf-8 -*-
"""持仓估值：把一串持仓折成「总资产」，并**说清楚这个数有多可信**。

单独成模块的理由和 `dividend_income` 一样：这里全是纯函数、不 import django，
可以被单测直接压。

锁住的是一个一直静默的假设 —— **「拿不到报价」被当成了「不值钱」**：

  - `build_positions` 里，没有报价的持仓 `market_value` 是 `None`；
  - `build_summary` 用 `if pos["market_value"]` 一筛，这个持仓就从合计里消失了。
    总资产因此偏小，而响应里**没有任何字段**能区分「真的没有持仓」和
    「有持仓但拿不到价」—— 页面上的 0.00 看起来完全正常。

更糟的是 XIRR：终值只在 `total_value` 非 0 时才进现金流，
于是「有持仓但没报价」时，年化会把「本金还在」算成「本金没了」，
给出一个巨额负收益，还把原因写成「现金流不足」——用户照着这句话去查流水，
永远查不出来。

所以这里的口径是：**拿不到报价的持仓按成本计，不按 0 计**，
并把「哪些标的没价」原样交出去让上层说人话。
"""
from __future__ import annotations

from decimal import Decimal

ZERO = Decimal("0")

# XIRR 终值口径：市场价值（有报价的部分）+ 无报价持仓的成本。
# 按 0 计等于宣布这笔钱蒸发了；按成本计至少说明「本金还在，盈亏未知」。
UNPRICED_AT_COST = "unpriced-at-cost"

MAX_SYMBOLS_IN_NOTE = 5


def _label(pos: dict) -> str:
    """给用户看的持仓标识：优先 symbol，退到 name，最后退到 id。"""
    return str(pos.get("symbol") or pos.get("name") or pos.get("asset_id") or "?")


def is_fx_missing(currency: str | None, base: str | None, rate: Decimal) -> bool:
    """这个币种的折算可不可信：既不是基准币、又只拿到兜底的那个 1。

    这是**唯一**一处判据。持仓汇总和股息汇总曾经各写一遍，
    两处都是拿原始字符串直接比，于是 `"hkd"` 与基准 `"HKD"` 会被判成缺汇率
    （`get_rate` 内部本来就大小写归一，返回 1 是真的相等），
    页面上就多出一条假的「部分币种缺少汇率」。
    """
    code = str(currency or base or "").upper()
    base_code = str(base or "").upper()
    return bool(code) and code != base_code and rate == 1


def valuate_positions(positions, base, rate_of) -> dict:
    """按基准货币折算持仓。

    `positions` 用 `build_positions()` 的输出；`rate_of(currency) -> Decimal`
    由调用方注入（生产环境传 `services.get_rate`，测试里传假的），
    这样这段口径不需要数据库、也不需要网络。

    返回：
      market_value        只含**拿到报价**的持仓（与旧口径一致）
      unrealized_pnl      只含拿到报价的持仓
      priced_count        拿到报价的持仓数
      unpriced_cost_basis 有数量但没报价的持仓成本合计（已折算）
      unpriced_symbols    这些持仓的标识，已排序去重，供提示文案用
      fx_missing          汇率缺失的币种，已排序去重（折算这一步不可靠）
    """
    market_value = ZERO
    unrealized = ZERO
    unpriced_cost = ZERO
    priced = 0
    unpriced_symbols: set[str] = set()
    fx_missing: set[str] = set()

    for pos in positions:
        raw_currency = pos.get("currency") or base or ""
        rate = rate_of(raw_currency)
        if is_fx_missing(raw_currency, base, rate):
            fx_missing.add(str(raw_currency).upper())

        if pos.get("market_value") is None:
            # 有数量却没价 → 盈亏是「未知」，不是 0。
            if Decimal(pos.get("quantity") or 0) != 0:
                unpriced_cost += Decimal(pos.get("cost_basis") or 0) * rate
                unpriced_symbols.add(_label(pos))
            continue

        priced += 1
        market_value += Decimal(pos["market_value"]) * rate
        if pos.get("unrealized_pnl") is not None:
            unrealized += Decimal(pos["unrealized_pnl"]) * rate

    return {
        "market_value": market_value,
        "unrealized_pnl": unrealized,
        "priced_count": priced,
        "unpriced_cost_basis": unpriced_cost,
        "unpriced_symbols": sorted(unpriced_symbols),
        "fx_missing": sorted(fx_missing),
    }


def terminal_value(valuation: dict) -> Decimal:
    """XIRR 收尾那笔现金流：整个组合此刻值多少。

    **必须**含无报价持仓的成本。只用 `market_value` 就是原来那个缺陷：
    一有持仓拿不到报价，现金流里就少了这一笔，年化于是把「本金还在」
    算成「本金没了」。真清仓时 `market_value` 与成本都归零，
    这里返回 0，调用方自然就不加终值 —— 这是对的。
    """
    return valuation["market_value"] + valuation["unpriced_cost_basis"]


def valuation_note(valuation: dict, annualized) -> str | None:
    """年化旁边那句话。**估值缺失必须先说，再谈现金流。**

    旧文案只有「现金流不足或日期过于集中」，于是「有持仓拿不到报价」
    这件完全不同的事被说成了「现金流不足」——两句话指向两个排查方向，
    用户按错的那句去查流水，永远查不出来。
    """
    parts: list[str] = []
    symbols = list(valuation.get("unpriced_symbols") or [])
    if symbols:
        shown = "、".join(symbols[:MAX_SYMBOLS_IN_NOTE])
        rest = f" 等 {len(symbols)} 个" if len(symbols) > MAX_SYMBOLS_IN_NOTE else ""
        parts.append(f"{len(symbols)} 个持仓没有报价（{shown}{rest}），已按成本计入总资产与年化，这部分盈亏未知")
    if annualized is None:
        parts.append("现金流不足或日期过于集中，暂无法计算年化")
    return "；".join(parts) if parts else None
