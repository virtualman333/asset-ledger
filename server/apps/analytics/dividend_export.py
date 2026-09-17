# -*- coding: utf-8 -*-
"""股息明细导出 CSV：**列定义与取值** —— 纯函数，不 import django。

通用部分（数值写法、公式注入防护、RFC 4180 转义、BOM / CRLF、文件名与响应头）在
`apps/core/csv_export.py`；本模块只提供「股息这一份列清单」与「一条股息怎么摊平成一行」。

导的是「归集后的股息」，不是 `DividendRecord` 列表
--------------------------------------------------
这是本模块最容易写错、而且写错了不报错的地方。

股息有两条合法录入路径（`dividend_income` 模块头那三条规则）：

  A. `POST /transactions/dividends/` → 落 `DividendRecord`（有明细）
  B. `POST /transactions/records/` side=DIVIDEND → **只落一条流水，没有明细**
  C. 两者都落，且 `DividendRecord.transaction` 指向那条流水

「导出股息明细 = 把 `DividendRecord` 列出来」看上去最自然，但它只装了 A 与 C，
**B 一条都导不出来**。而页面上的「累计股息」走的是 `dividend_entries()`，A / B / C 全算
（实测过一个真实账本：持仓页股息 0、统计页 810，就是这两处各算各的留下的疤）。

后果：用户看到累计股息 810，点导出拿到一个空文件（或者只有一半的行）。
**两边都不报错，只是数字对不上** —— 而用户是拿这两个数字互相对账的。

所以行来源只能是 `services.dividend_entries(user)`：它既是页面上那个数的口径，
也是这份文件的清单。明细列（除权日 / 持股数 / 每股派息 / 税前 / 税费 / 分红再投）
只在**有明细的那些行**填得上，其余留空。

明细列留空 ≠ 0
--------------
流水录入的股息查不到税前与税费。那几格写 `0` 等于替用户宣布「这笔没收过税」，
写空格子才是「这里没数据可查」——两种都会出现在同一个文件里，所以必须分得清。
判定在 `DividendEntry.has_detail`，值本身是 `None`（见 `dividend_income.DividendEntry`）。

列清单是唯一的
--------------
`DIVIDEND_CSV_COLUMNS` 是这一份列定义的唯一出处；README 的「导出股息明细（CSV）」表
由 `apps/core/tests/test_export_contract.py` 与它双向对齐（少一列、多一列、顺序换了都红）。
数据行由 `dividend_row()` 按这里的键取值，加了列却没给取值会直接 KeyError。
"""
from __future__ import annotations

from apps.core import csv_export
from apps.core.csv_export import (
    BOM,
    EOL,
    GUARD,
    NEEDS_QUOTE,
    content_disposition,
    fmt_date,
    fmt_decimal,
    quote_cell,
    render_line,
    sanitize_cell,
)
from .dividend_income import ORIGIN_LABELS

#: 导出的列：`(取值键, 中文表头)`，**顺序就是 CSV 里的列序**。
DIVIDEND_CSV_COLUMNS = (
    ("asset_symbol", "标的代码"),
    ("asset_name", "标的名称"),
    ("account_name", "账户"),
    ("currency", "币种"),
    ("ex_date", "除权日"),
    ("pay_date", "派息日"),
    ("shares", "持股数"),
    ("amount_per_share", "每股派息"),
    ("gross", "税前"),
    ("tax", "税费"),
    ("net", "税后"),
    ("reinvested", "分红再投"),
    ("origin_label", "来源"),
)

#: 布尔列的中文写法。**`None` 不在表里** —— 它要留空格子，见模块说明。
REINVESTED_LABELS = {True: "是", False: "否"}

__all__ = [
    "BOM",
    "EOL",
    "GUARD",
    "NEEDS_QUOTE",
    "DIVIDEND_CSV_COLUMNS",
    "REINVESTED_LABELS",
    "content_disposition",
    "dividend_cells",
    "dividend_row",
    "export_names",
    "fmt_date",
    "fmt_decimal",
    "quote_cell",
    "render_csv",
    "render_line",
    "render_rows",
    "sanitize_cell",
]


def export_names(now=None) -> tuple:
    """股息导出的 `(ASCII 文件名, 中文文件名)`。

    与流水导出共用同一套命名（`apps.core.csv_export.export_names`），只把中文名里
    那一段换成「股息」。

    ⚠ 中文件名里的标签**不进 ASCII 名**（那边必须纯 ASCII，老客户端拿它写盘），
    所以同一分钟内先导流水再导股息，`filename=` 是同一个 `asset-ledger-<时刻>.csv`。
    现代浏览器读的是 `filename*=UTF-8''…` 那条，两份名字不同、不会互相覆盖；
    但只会读 `filename=` 的老下载器会把后一份存成 `(1)`。这是 ASCII 名必须纯字符
    换来的代价，写在这里免得日后当成 bug 追。
    """
    return csv_export.export_names("股息", now=now)


def render_rows(rows, columns=DIVIDEND_CSV_COLUMNS):
    """股息 CSV 文本行（表头 + 数据）。渲染实现在 `apps.core.csv_export`。"""
    return csv_export.render_rows(rows, columns)


def render_csv(rows, columns=DIVIDEND_CSV_COLUMNS) -> str:
    """`render_rows()` 的整串版本（单测与「一次给完」的调用点用）。"""
    return csv_export.render_csv(rows, columns)


def _asset_of(assets, asset_id):
    """`assets` 是 `{asset_id: (symbol, name)}`，取不到就返回两个空串。

    查不到**不抛异常**：导出不该因为一条脏外键整份失败 —— 账目文件拿不到手，
    比某一格标的代码空着严重得多。账户同理。
    """
    if assets is None or asset_id is None:
        return "", ""
    found = assets.get(asset_id)
    if not found:
        return "", ""
    return found


def _account_of(accounts, account_id) -> str:
    if accounts is None or account_id is None:
        return ""
    return accounts.get(account_id) or ""


def dividend_cells(entry, assets, accounts) -> dict:
    """一条归集后的股息 → `{列键: 原始值}`（尚未净化 / 转义）。

    `assets` / `accounts` 由调用方注入（`{id: (代码, 名称)}` / `{id: 名称}`），
    日期与小数一律走 `apps.core.csv_export` 的格式化 —— 这样数值写法与流水导出
    逐字一致，两个文件放在同一个 Excel 里不会一个 `1E+2`、一个 `100`。

    `net` 取的是 `entry.amount`，也就是**税后到手**（归集口径见 `dividend_income`）。
    """
    symbol, name = _asset_of(assets, entry.asset_id)
    origin = entry.origin or ""
    return {
        "asset_symbol": symbol,
        "asset_name": name,
        "account_name": _account_of(accounts, entry.account_id),
        "currency": entry.currency or "",
        "ex_date": fmt_date(entry.ex_date),
        "pay_date": fmt_date(entry.pay_date),
        "shares": fmt_decimal(entry.shares),
        "amount_per_share": fmt_decimal(entry.amount_per_share),
        "gross": fmt_decimal(entry.gross),
        "tax": fmt_decimal(entry.tax),
        "net": fmt_decimal(entry.amount),
        "reinvested": REINVESTED_LABELS.get(entry.reinvested, ""),
        "origin_label": ORIGIN_LABELS.get(origin, origin),
    }


def dividend_row(entry, assets, accounts, columns=DIVIDEND_CSV_COLUMNS) -> list:
    """按 `columns` 的顺序把 `dividend_cells()` 摊平成一行。

    列序只由 `DIVIDEND_CSV_COLUMNS` 决定；`columns` 里出现一个没人填的键就 **KeyError**
    —— 加了列却忘了给取值，是「多一列空值」还是「少一列数据」的分水岭。
    """
    cells = dividend_cells(entry, assets, accounts)
    missing = [key for key, _ in columns if key not in cells]
    if missing:
        raise KeyError(
            f"DIVIDEND_CSV_COLUMNS 里有列没人填：{missing} —— 加了列就得在 dividend_cells 里给出取值"
        )
    return [cells[key] for key, _ in columns]
