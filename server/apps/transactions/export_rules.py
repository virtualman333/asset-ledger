# -*- coding: utf-8 -*-
"""流水导出 CSV：列定义、单元格净化、行渲染 —— 全是纯函数，不 import django。

为什么单独成模块
----------------
和 `amount_rules` / `dividend_income` / `valuation` 同一个理由：这里全是纯函数、
不 import django，所以能被单测直接压（本仓库的测试承诺「不需要数据库、不需要 Django」）。
「导出的列有哪些、单元格怎么写、Excel 会不会把它当公式执行」全都能离线断言。

三个「错了也看不出来」的地方
----------------------------
1. **Excel / WPS / LibreOffice 会把以 `=` `+` `-` `@` 开头的单元格当公式执行。**
   备注列来自用户手输与 Agent 对截图的 OCR —— 也就是说这段文本不完全由用户自己掌控。
   不处理的话，导出的文件到了别人机器上，打开（甚至只是选中）就可能跑起来一段公式
   （DDE / 外部引用），而 CSV 本身看不出任何异常。所以每个单元格都过一次
   `sanitize_cell()`。代价要写明白：被中和的单元格前面会多一个单引号（`'=1+1`），
   在 Excel 里那一格就是**多一个引号的文本**。**安全优先于好看**，这是刻意的取舍。
2. **`Decimal.normalize()` 会产出科学计数法**：`Decimal("100.00000000").normalize()`
   就是 `Decimal("1E+2")`，写在 CSV 里 Excel 显示成 `1E+2` 而不是 `100`。
   所以数值一律再 `format(..., "f")` 落一次地，顺序不能反。
3. **`-0`**：`Decimal("-0.00000000")` 归一后是 `Decimal("-0")`，直接写出去就是
   一格 `-0` —— 账目里看起来像数据错了。归零时统一写 `0`。

为什么带 UTF-8 BOM、行尾用 CRLF
--------------------------------
BOM 是给 Excel 看的：不带 BOM 时 Excel（尤其中文 Windows 上的版本）会拿 GBK 去解
UTF-8，中文列头变成乱码。CRLF 是 RFC 4180 的规定，也是 Excel 的默认。
两条都不是「口味」，是「双击打开就知道了」的东西 —— 所以由单测钉住。

为什么文件名要给两份（ASCII + 中文）
------------------------------------
HTTP 头的值只能是 latin-1。把「asset-ledger-流水-….csv」直接塞进 `Content-Disposition`
会当场炸（Django 编码响应头时抛异常），而且是在**导出失败**的时候才炸 ——
用户点一下导出，什么都没拿到，也不知道为什么。所以 `content_disposition()` 同时给
旧客户端一个纯 ASCII 的 `filename=`，和 RFC 5987 的 `filename*=UTF-8''…`
（现代浏览器取后者，中文名照常显示）。判据也有单测：结果必须能 `encode("latin-1")`。

时间为什么不能直接 `str()`
---------------------------
`USE_TZ = True`，库里存的是 UTC。直接写出去就是 `2026-01-10 02:00:00+00:00` ——
Excel 里那一列既不是时间（尾巴带 `+00:00`），又整整差 8 小时，而界面上显示的是
`10:00`。所以按 `settings.TIME_ZONE` 转一次再格式化；时区由调用方注入，
本模块不 import django。
"""
from __future__ import annotations

import re
from datetime import datetime
from decimal import Decimal, InvalidOperation
from urllib.parse import quote

#: Excel 会把以这些字符开头的单元格当公式（实测三家电子表格都一样）
FORMULA_PREFIXES = ("=", "+", "-", "@")

#: 制表符 / 回车也被当成「后面那串是公式」的信号 —— 这是 Excel 的实际行为，不是臆测
FORMULA_CONTROL = ("\t", "\r")

#: 中和用的前缀。Excel 见到开头的单引号会强制当文本（代价见模块说明第 1 条）
GUARD = "'"

#: Excel 认它也认；给 `-` 开头的单元格定「这是负数还是公式」用
NUMBER_RE = re.compile(r"-?\d+(?:\.\d+)?", re.ASCII)

BOM = "\ufeff"
EOL = "\r\n"

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

#: 一行里出现这些字符就得整体加双引号（RFC 4180）
NEEDS_QUOTE = (",", '"', "\n", "\r")


def fmt_decimal(value) -> str:
    """数值 → 人和 Excel 都认的写法。

    - 空（`None` / `""`）→ 空单元格；
    - 整数不拖一串零（库里 DECIMAL(24,8) 取出来是 `1000.00000000`）；
    - 不走科学计数法（`normalize()` 的坑，见模块说明第 2 条）；
    - `-0` 归成 `0`（第 3 条）；
    - 不是数的东西原样交出去 —— 这个函数不该负责替调用方判数据对错。
    """
    if value is None or value == "":
        return ""
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return str(value)
    if not number.is_finite():
        return str(value)
    if number == 0:
        return "0"
    return format(number.normalize(), "f")


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


def sanitize_cell(value) -> str:
    """一个单元格的最终文本：挡住公式注入，再保证是字符串。

    负数（`-1.5`）与我们自己算出来的 `-2500` 不动 —— 那是数据不是公式。
    判据是「`-` 后面整串就是一个数」，不是「以 `-` 开头」。
    """
    text = "" if value is None else str(value)
    if not text:
        return ""
    head = text[0]
    if head in FORMULA_CONTROL:
        return GUARD + text
    if head in FORMULA_PREFIXES:
        if head == "-" and NUMBER_RE.fullmatch(text):
            return text
        return GUARD + text
    return text


def quote_cell(text: str) -> str:
    """RFC 4180 转义：含逗号 / 引号 / 换行的单元格整体加双引号，内部引号翻倍。"""
    if any(ch in text for ch in NEEDS_QUOTE):
        return '"' + text.replace('"', '""') + '"'
    return text


def render_line(cells) -> str:
    """一行（**不含 BOM**）：净化 → 转义 → 逗号连接 → CRLF。

    净化放在这里而不是 `transaction_row()` 里，是因为这里是**唯一的渲染出口**：
    不管哪条路径拼出来的行，都得过这一道。
    """
    return ",".join(quote_cell(sanitize_cell(c)) for c in cells) + EOL


def render_rows(rows, columns=CSV_COLUMNS):
    """CSV 文本行，**第一行是表头**，BOM 只加在第一行前面。生成器，便于流式吐出去。"""
    yield BOM + render_line([title for _, title in columns])
    for row in rows:
        yield render_line(row)


def render_csv(rows, columns=CSV_COLUMNS) -> str:
    """`render_rows()` 的整串版本（单测与「一次给完」的调用点用）。

    拼行只有 `render_line()` 一处实现，这里只是把它连起来 —— 不另起一份。
    """
    return "".join(render_rows(rows, columns))


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


def export_names(now=None) -> tuple:
    """`(ASCII 文件名, 中文文件名)`。两份都要，理由见模块说明。"""
    stamp = (now or datetime.now()).strftime("%Y%m%d-%H%M")
    return f"asset-ledger-{stamp}.csv", f"asset-ledger-流水-{stamp}.csv"


def content_disposition(ascii_name: str, unicode_name: str) -> str:
    """`Content-Disposition` 的值：老客户端读 `filename=`，现代客户端读 `filename*=`。

    返回值**必须**是 latin-1 可编码的 —— 否则 Django 写响应头时直接抛异常，
    用户看到的是「点导出没反应」。这一条有单测盯着。
    """
    return "attachment; filename=\"%s\"; filename*=UTF-8''%s" % (ascii_name, quote(unicode_name))
