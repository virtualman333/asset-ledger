# -*- coding: utf-8 -*-
"""持仓明细导出 CSV：**列定义与取值** —— 纯函数，不 import django。

通用部分（数值写法、公式注入防护、RFC 4180 转义、BOM / CRLF、文件名与响应头）在
`apps/core/csv_export.py`：那些是「外部世界的要求」，与导出的是什么列无关。
本模块只提供「持仓这一份列清单」与「一条持仓怎么摊平成一行」。

行来源只能是 `build_positions()`
--------------------------------
`GET /analytics/positions/` 与这份文件必须是**同一份数字**，所以两处都调用
`services.build_positions(user)`，没有第二条聚合路径。这不是洁癖：持仓有五种流水
（买 / 卖 / 拆股 / 股息归集 / 已清仓后只剩已实现盈亏）影响它，任何一处自己重算一遍，
都会在某个角落与页面上的数不一样 —— 而用户正是拿这两个数对账的。

**拿不到报价的三格留空，不写 0。**
现价 / 市值 / 浮动盈亏在标的没有行情快照时是 `None`（`build_positions` 里
`latest_price()` 取不到就是 `None`，市值与浮盈跟着是 `None`）。写 `0` 等于替用户在
文件里宣布「这个标的现在不值钱」「这笔一分钱没赚没亏」—— 而真相是「不知道」，
库里连一条快照都没有。同一条口径也写在股息导出（明细列留空 ≠ 0）与
`apps/analytics/valuation.py`（无报价持仓按成本计、不按 0 计）上。

股息率同理：`dividend_yield()` 在成本为 0 时返回 `None`（「算不出」与「收益率是 0」
是两件事），导出的那一格因此也是空格子。**它是个比率**（`0.0325` 就是 3.25%），
与 `/analytics/positions/` 里那个字段同值同口径 —— 两个出口用两种写法会让人以为
其中一个是错的。

金额一律经 `apps/core/csv_export.fmt_decimal`：库里是 `DECIMAL(24,8)`，
直接 `str()` 出来是 `1000.00000000`，而界面与接口上都是 `1000`。同一个数在两个出口
读起来是两个数，只会让人以为其中一个错了。

列清单是唯一的
--------------
`POSITIONS_CSV_COLUMNS` 是这一份列定义的唯一出处；README 的「导出持仓（CSV）」表
由 `apps/core/tests/test_export_contract.py` 与它双向对齐（少一列、多一列、顺序换了都红）。
数据行由 `positions_row()` 按这里的键取值，加了列却没给取值会直接 KeyError。
"""
from __future__ import annotations

from apps.core import csv_export
from apps.core.csv_export import (
    BOM,
    EOL,
    GUARD,
    NEEDS_QUOTE,
    content_disposition,
    fmt_decimal,
    quote_cell,
    render_line,
    sanitize_cell,
)

#: 导出的列：`(取值键, 中文表头)`，**顺序就是 CSV 里的列序**。
POSITIONS_CSV_COLUMNS = (
    ("asset_symbol", "标的代码"),
    ("asset_name", "标的名称"),
    ("market_label", "市场"),
    ("account_name", "账户"),
    ("currency", "币种"),
    ("quantity", "持仓数量"),
    ("avg_cost", "成本价"),
    ("cost_basis", "持仓成本"),
    ("last_price", "现价"),
    ("market_value", "市值"),
    ("unrealized_pnl", "浮动盈亏"),
    ("realized_pnl", "已实现盈亏"),
    ("annual_dividend", "近一年股息"),
    ("dividend_total", "累计股息"),
    ("dividend_yield", "股息率"),
)

#: 文本列：`(列键, 持仓字段)`。空串是合法的（没有账户名、没有币种），不报错。
TEXT_FIELDS = (
    ("asset_symbol", "symbol"),
    ("asset_name", "name"),
    ("account_name", "account_name"),
    ("currency", "currency"),
)

#: 数值列：`(列键, 持仓字段)`。值可能是字符串或 `None` —— `None` 一律落成空格子。
NUMERIC_FIELDS = (
    ("quantity", "quantity"),
    ("avg_cost", "avg_cost"),
    ("cost_basis", "cost_basis"),
    ("last_price", "last_price"),
    ("market_value", "market_value"),
    ("unrealized_pnl", "unrealized_pnl"),
    ("realized_pnl", "realized_pnl"),
    ("annual_dividend", "annual_dividend"),
    ("dividend_total", "dividend_total"),
    ("dividend_yield", "dividend_yield"),
)

#: 还要读、但**不直接映射成一列**的字段：市场码要先过一次中文表才成列（见 `positions_cells`）。
EXTRA_FIELDS = ("market",)

#: 上面三张表覆盖的持仓字段。**缺字段是错误，不是空** —— 见 `positions_cells()`。
POSITION_FIELDS = tuple(field for _, field in TEXT_FIELDS + NUMERIC_FIELDS) + EXTRA_FIELDS

__all__ = [
    "BOM",
    "EOL",
    "EXTRA_FIELDS",
    "GUARD",
    "NEEDS_QUOTE",
    "NUMERIC_FIELDS",
    "POSITIONS_CSV_COLUMNS",
    "POSITION_FIELDS",
    "TEXT_FIELDS",
    "content_disposition",
    "export_names",
    "fmt_decimal",
    "positions_cells",
    "positions_row",
    "quote_cell",
    "render_csv",
    "render_line",
    "render_rows",
    "sanitize_cell",
]


def export_names(now=None) -> tuple:
    """持仓导出的 `(ASCII 文件名, 中文文件名)`。

    与流水 / 股息两份导出共用同一套命名（`apps.core.csv_export.export_names`），
    只把中文名里那一段换成「持仓」。
    """
    return csv_export.export_names("持仓", now=now)


def render_rows(rows, columns=POSITIONS_CSV_COLUMNS):
    """持仓 CSV 文本行（表头 + 数据）。渲染实现在 `apps.core.csv_export`。"""
    return csv_export.render_rows(rows, columns)


def render_csv(rows, columns=POSITIONS_CSV_COLUMNS) -> str:
    """`render_rows()` 的整串版本（单测与「一次给完」的调用点用）。"""
    return csv_export.render_csv(rows, columns)


def positions_cells(pos, market_labels) -> dict:
    """一条持仓 → `{列键: 原始值}`（尚未净化 / 转义）。

    `market_labels` 由调用方注入（`dict(Market.choices)`）：本模块刻意不认识模型，
    这样它才不需要 Django；而调用点一旦忘了传，就是一个 TypeError，不是某天悄悄
    少一列中文。模型新增一个市场时 `dict(Market.choices)` 会自己带上它 ——
    不需要在这里再抄一份对照表。认不出的市场码**原样交出去**（不是留空），
    这样脏数据在文件里看得见。

    `pos` 的数值字段是字符串或 `None`（`build_positions()` 的输出）：
    `None` 一律经 `fmt_decimal` 变成空格子 —— 「不知道」不许被写成 0。

    **字段缺失一律 KeyError，不落成空串。** 空串是「这里本来就没有值」（没有账户名），
    缺失是「上游把它改掉了」—— 后者落成空串就是整列悄悄清空，而文件本身看起来完全正常。
    判据是 `POSITION_FIELDS`，它由上面两张字段表现算，不手抄。
    """
    missing = [field for field in POSITION_FIELDS if field not in pos]
    if missing:
        raise KeyError(
            f"持仓数据缺少字段 {missing} —— `build_positions()` 的形状变了。"
            "缺失落成空串就是整列悄悄清空，所以宁可在这里炸。"
        )

    market = pos["market"] or ""
    cells = {
        "market_label": market_labels.get(market, market),
    }
    cells.update((key, pos[field] or "") for key, field in TEXT_FIELDS)
    cells.update((key, fmt_decimal(pos[field])) for key, field in NUMERIC_FIELDS)
    return cells


def positions_row(pos, market_labels, columns=POSITIONS_CSV_COLUMNS) -> list:
    """按 `columns` 的顺序把 `positions_cells()` 摊平成一行。

    列序只由 `POSITIONS_CSV_COLUMNS` 决定；`columns` 里出现一个没人填的键就 **KeyError**
    —— 加了列却忘了给取值，是「多一列空值」还是「少一列数据」的分水岭，
    与其静默，不如在这里就炸。
    """
    cells = positions_cells(pos, market_labels)
    missing = [key for key, _ in columns if key not in cells]
    if missing:
        raise KeyError(
            f"POSITIONS_CSV_COLUMNS 里有列没人填：{missing} —— 加了列就得在 positions_cells 里给出取值"
        )
    return [cells[key] for key, _ in columns]
