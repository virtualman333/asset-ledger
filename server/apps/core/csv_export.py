# -*- coding: utf-8 -*-
"""CSV 导出的**通用**部分：数值写法、公式注入防护、RFC 4180 转义、BOM / CRLF、文件名与响应头。

为什么单独一层
--------------
「导出流水」与「导出股息明细」是两份不同的列清单，但它们面对的外部世界是同一个：
Excel 会把 `=` 开头的单元格当公式执行、中文列头不带 BOM 会乱码、HTTP 头只能是 latin-1。
各写一份的后果不是重复几十行，而是**两份实现分头漂** —— 而漂了不报错：一个导出带 BOM、
另一个不带，用户在同一个 Excel 里双击两个文件，一个中文正常、一个乱码。

所以这里只放「与列无关」的东西。列清单与取值映射留在各自的规则模块里
（`apps/transactions/export_rules.py`、`apps/analytics/dividend_export.py`），
渲染一律调用这里的 ``render_rows()``。`apps/core/tests/test_export_contract.py` 里
有一条结构锁盯着「全仓只有一个渲染出口」。

三个「错了也看不出来」的地方
----------------------------
1. **Excel / WPS / LibreOffice 会把以 `=` `+` `-` `@` 开头的单元格当公式执行。**
   备注列来自用户手输与 Agent 对截图的 OCR —— 也就是说这段文本不完全由用户自己掌控。
   不处理的话，导出的文件到了别人机器上，打开（甚至只是选中）就可能跑起来一段公式
   （DDE / 外部引用），而 CSV 本身看不出任何异常。所以每个单元格都过一次
   ``sanitize_cell()``。代价要写明白：被中和的单元格前面会多一个单引号（``'=1+1``），
   在 Excel 里那一格就是**多一个引号的文本**。**安全优先于好看**，这是刻意的取舍。
2. **``Decimal.normalize()`` 会产出科学计数法**：``Decimal("100.00000000").normalize()``
   就是 ``Decimal("1E+2")``，写在 CSV 里 Excel 显示成 ``1E+2`` 而不是 ``100``。
   所以数值一律再 ``format(..., "f")`` 落一次地，顺序不能反。
3. **``-0``**：``Decimal("-0.00000000")`` 归一后是 ``Decimal("-0")``，直接写出去就是
   一格 ``-0`` —— 账目里看起来像数据错了。归零时统一写 ``0``。

为什么带 UTF-8 BOM、行尾用 CRLF
--------------------------------
BOM 是给 Excel 看的：不带 BOM 时 Excel（尤其中文 Windows 上的版本）会拿 GBK 去解
UTF-8，中文列头变成乱码。CRLF 是 RFC 4180 的规定，也是 Excel 的默认。
两条都不是「口味」，是「双击打开就知道了」的东西 —— 所以由单测钉住。

为什么文件名要给两份（ASCII + 中文）
------------------------------------
HTTP 头的值只能是 latin-1。把「asset-ledger-流水-….csv」直接塞进 ``Content-Disposition``
会当场炸（Django 编码响应头时抛异常），而且是在**导出失败**的时候才炸 ——
用户点一下导出，什么都没拿到，也不知道为什么。所以 ``content_disposition()`` 同时给
旧客户端一个纯 ASCII 的 ``filename=``，和 RFC 5987 的 ``filename*=UTF-8''…``
（现代浏览器取后者，中文名照常显示）。判据也有单测：结果必须能 ``encode("latin-1")``。

这个模块**只 import 标准库**，所以整份契约能在裸 Python 上跑（README 承诺
「单元测试不需要数据库、不需要 Django」，`test_no_django_required.py` 会真验一遍）。
"""
from __future__ import annotations

import re
from datetime import date, datetime
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


def fmt_date(value) -> str:
    """纯日期 → `YYYY-MM-DD`；空 → 空单元格。

    `datetime` 也收，但**只取日期部分**：调用方要是把一个带时刻的值传进来，多半是
    拿错了字段，而写出去的一格里多出 `00:00:00` 不会有人注意。
    """
    if value is None or value == "":
        return ""
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
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

    净化放在这里而不是各个 `*_row()` 里，是因为这里是**唯一的渲染出口**：
    不管哪条路径拼出来的行，都得过这一道。
    """
    return ",".join(quote_cell(sanitize_cell(c)) for c in cells) + EOL


def render_rows(rows, columns) -> str:
    """CSV 文本行，**第一行是表头**，BOM 只加在第一行前面。生成器，便于流式吐出去。

    `columns` 由调用方给（它就是那一份列清单），这里不认识任何具体列。
    """
    yield BOM + render_line([title for _, title in columns])
    for row in rows:
        yield render_line(row)


def render_csv(rows, columns) -> str:
    """`render_rows()` 的整串版本（单测与「一次给完」的调用点用）。

    拼行只有 `render_line()` 一处实现，这里只是把它连起来 —— 不另起一份。
    """
    return "".join(render_rows(rows, columns))


def export_names(label: str, *, now=None) -> tuple:
    """`(ASCII 文件名, 中文文件名)`。两份都要，理由见模块说明。

    `label` 是中文名里的那一小段（「流水」/「股息」），**不进 ASCII 名**：
    那边必须是纯 ASCII（老客户端与某些下载器会拿它写盘）。

    `label` 是位置参数且**没有默认值** —— 这是刻意的。给它一个默认值就等于
    替调用方选了一份身份：股息导出若忘了传 label，用户下到手的文件叫
    「asset-ledger-流水-….csv」，而且**同一分钟里两个导出的 ASCII 名一模一样**，
    浏览器会把后一个存成 `(1)`。宁可在这里 TypeError。
    各导出模块自己包一层 `export_names(now=None)` 把这个标签钉死。
    """
    stamp = (now or datetime.now()).strftime("%Y%m%d-%H%M")
    return f"asset-ledger-{stamp}.csv", f"asset-ledger-{label}-{stamp}.csv"


def content_disposition(ascii_name: str, unicode_name: str) -> str:
    """`Content-Disposition` 的值：老客户端读 `filename=`，现代客户端读 `filename*=`。

    返回值**必须**是 latin-1 可编码的 —— 否则 Django 写响应头时直接抛异常，
    用户看到的是「点导出没反应」。这一条有单测盯着。
    """
    return "attachment; filename=\"%s\"; filename*=UTF-8''%s" % (ascii_name, quote(unicode_name))
