# -*- coding: utf-8 -*-
"""流水导出 CSV：**列定义与取值** —— 纯函数，不 import django。

通用部分（数值写法、公式注入防护、RFC 4180 转义、BOM / CRLF、文件名与响应头）在
`apps/core/csv_export.py`：那几条是「外部世界的要求」，与导出的是什么列无关，
股息导出要用同一套。放两份的后果见那边的模块说明。

时间为什么不能直接 `str()`
---------------------------
`USE_TZ = True`，库里存的是 UTC。直接写出去就是 `2026-01-10 02:00:00+00:00` ——
Excel 里那一列既不是时间（尾巴带 `+00:00`），又整整差 8 小时，而界面上显示的是
`10:00`。所以按 `settings.TIME_ZONE` 转一次再格式化；时区由调用方注入，
本模块不 import django。
"""
from __future__ import annotations

from datetime import datetime

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
#:
#: 这是唯一一处列定义 —— README 里那张「导出的列」表由
#: `apps/core/tests/test_export_contract.py` 与它双向对齐（少一列、多一列、顺序换了都红），
#: 数据行则由 `transaction_row()` 按这里的键取值（加了列却没给取值会直接 KeyError，
#: 而不是悄悄多一列空值）。
CSV_COLUMNS = (
    ("traded_at", "时间"),
    ("side_label", "方向"),
    ("asset_symbol", "标的代码"),
    ("asset_name", "标的名称"),
    ("account_name", "账户"),
    ("quantity", "数量"),
    ("price", "单价"),
    ("amount", "现金变动"),
    ("fee", "手续费"),
    ("tax", "税费"),
    ("currency", "币种"),
    ("fx_rate", "入账汇率"),
    ("source_label", "来源"),
    ("note", "备注"),
)

__all__ = [
    "BOM",
    "EOL",
    "GUARD",
    "NEEDS_QUOTE",
    "CSV_COLUMNS",
    "content_disposition",
    "export_names",
    "fmt_decimal",
    "fmt_traded_at",
    "quote_cell",
    "render_csv",
    "render_line",
    "render_rows",
    "sanitize_cell",
    "transaction_cells",
    "transaction_row",
]


def export_names(now=None) -> tuple:
    """流水导出的 `(ASCII 文件名, 中文文件名)`。

    本模块只提供「中文名里那一段」这个知识（`"流水"`）；文件名的形状
    （`asset-ledger-<标签>-<时刻>.csv`）由 `apps.core.csv_export` 定，
    全仓只此一处。
    """
    return csv_export.export_names("流水", now=now)


def render_rows(rows, columns=CSV_COLUMNS):
    """流水 CSV 文本行（表头 + 数据）。渲染实现在 `apps.core.csv_export`。"""
    return csv_export.render_rows(rows, columns)


def render_csv(rows, columns=CSV_COLUMNS) -> str:
    """`render_rows()` 的整串版本（单测与「一次给完」的调用点用）。"""
    return csv_export.render_csv(rows, columns)


def fmt_traded_at(value, tz=None) -> str:
    """发生时间 → `YYYY-MM-DD HH:MM:SS`。

    带时区的值先按 `tz` 转一次（不给就用本机时区）；`tz` 由调用方注入
    `settings.TIME_ZONE`，因为本模块不 import django。不带时区的值原样格式化
    —— 它已经是「当地墙上时间」了，再转一次就是错的。
    """
    if value is None:
        return ""
    if isinstance(value, datetime):
        if value.tzinfo is not None:
            value = value.astimezone(tz)
        return value.strftime("%Y-%m-%d %H:%M:%S")
    return str(value)


def transaction_cells(tx, side_labels, source_labels, tz=None) -> dict:
    """一条流水 → `{列键: 原始值}`（尚未净化 / 转义）。

    标签表由调用方注入（`dict(TxSide.choices)` / `dict(TxSource.choices)`）：
    本模块刻意不认识模型，这样它才不需要 Django；而调用点一旦忘了传，
    就是一个 TypeError，不是某天悄悄少一列中文。模型新增一种 side 时，
    `dict(TxSide.choices)` 会自己带上它 —— 不需要在这里再抄一份对照表。
    """
    asset = getattr(tx, "asset", None)
    account = getattr(tx, "account", None)
    side = getattr(tx, "side", "") or ""
    source = getattr(tx, "source", "") or ""
    return {
        "traded_at": fmt_traded_at(getattr(tx, "traded_at", None), tz=tz),
        "side_label": side_labels.get(side, side),
        "asset_symbol": getattr(asset, "symbol", "") or "",
        "asset_name": getattr(asset, "name", "") or "",
        "account_name": getattr(account, "name", "") or "",
        "quantity": fmt_decimal(getattr(tx, "quantity", None)),
        "price": fmt_decimal(getattr(tx, "price", None)),
        "amount": fmt_decimal(getattr(tx, "amount", None)),
        "fee": fmt_decimal(getattr(tx, "fee", None)),
        "tax": fmt_decimal(getattr(tx, "tax", None)),
        "currency": getattr(tx, "currency", "") or "",
        "fx_rate": fmt_decimal(getattr(tx, "fx_rate", None)),
        "source_label": source_labels.get(source, source),
        "note": getattr(tx, "note", "") or "",
    }


def transaction_row(tx, side_labels, source_labels, columns=CSV_COLUMNS, tz=None) -> list:
    """按 `columns` 的顺序把 `transaction_cells()` 摊平成一行。

    列序只由 `CSV_COLUMNS` 决定；`columns` 里出现一个没人填的键就 **KeyError** ——
    加了列却忘了给取值是「多一列空值」还是「少一列数据」的分水岭，
    与其静默，不如在这里就炸。
    """
    cells = transaction_cells(tx, side_labels, source_labels, tz=tz)
    missing = [key for key, _ in columns if key not in cells]
    if missing:
        raise KeyError(f"CSV_COLUMNS 里有列没人填：{missing} —— 加了列就得在 transaction_cells 里给出取值")
    return [cells[key] for key, _ in columns]
