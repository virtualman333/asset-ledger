# -*- coding: utf-8 -*-
"""流水 CSV 批量导入：**表头映射 + 逐行解析** —— 纯函数，不 import django。

为什么要有导入
--------------
`docs/DESIGN.md` 第 4.1 节从 M0 起就写着「支持从券商导出文件批量导入」，而实现那一侧
只有**导出**（`GET /transactions/records/export/`）。`TxSource.IMPORT` 这个枚举值从建模
那天就在，却**一个生产者都没有** —— 一个枚举值没有生产者，读代码的人只能猜它是不是
还没做。这次把它接上。

为什么列清单是「现算」的
------------------------
导入的表头认两种写法：`CSV_COLUMNS` 里的**中文表头**，以及那组**英文取值键**。
前者是从 `export_rules.CSV_COLUMNS` 现算出来的（`_aliases()`），**不在这里抄第二份**：
抄一份就等于把「导出改了列、导入还认旧名」这件事交给运气，而它错起来是安静的
—— 用户拿着自己刚导出的文件回导，得到一屏「不认识的列名」。

于是得到一条可以当契约用的性质：**自己导出的文件必然能被自己导入**
（`apps/transactions/tests/test_import_rules.py` 里那条往返用例就是照这条写的）。

不认识的列名一律报错，不静默忽略
--------------------------------
「忽略读不懂的列」听起来很宽容，代价是用户写错了列名却拿到了「导入成功 12 条」，
而 12 条里没一条带着他想要的那个字段。所以文件级错误（空文件 / 没有表头 / 有认不出的列 /
缺少必需列）直接抛 `ImportFormatError` → 由视图回 **400**；行级错误则逐行收集，
不中断整批（第 3 行错了不该让第 4 行也进不来）。

导入方不负责的三件事
--------------------
1. **`来源` 列不读。** 导入进来的流水，来源就是「导入」（`TxSource.IMPORT`）——
   把文件里写的 `Agent 识别` 抄回来会让它去冒充一条从未发生过的识别。
   同理 `标的名称` 只作展示，不参与匹配。
2. **账户与标的按名字/代码在本用户范围内查**，查不到就是这一行的错误（不会顺手建一个）。
   顺手建标的会把「代码打错了」变成「多了十只没听过的持仓」。
3. **金额不在这里推导。** `现金变动` 为空时该按 `数量×单价` 推、还是必须报错，
   口径在 `amount_rules.resolve_amount()`（全系统唯一实现）。这里只负责把字符串
   解析成 `Decimal`，把 `None` 原样交给上层 —— 于是导入与 `POST /records/` 走的是
   同一套金额规则，包括「入金没填金额必须报错」那条。

列集合是一份**划分**，而且会当场对账
------------------------------------
`CSV_COLUMNS` 里的每一列，要么被这一行**读走**（`CONSUMED_KEYS`），要么被**声明忽略**
（`IGNORED_KEYS`），没有第三类。这条以前是「写在注释里的君子协定」：`CONSUMED_KEYS`
是一份手抄的 12 项清单，`IGNORED_KEYS` 手抄 2 项，**两者全仓都没有消费方**
（`git grep` 只有定义处）—— 一份没人读的清单既不会响，也拦不住漂移。

于是真实的漏口在这里：往 `export_rules.CSV_COLUMNS` 加一列，`_aliases()` 会**现算**出
新表头并放行，`build_row()` 按固定字段取值 —— 新列的值连一声都没响就没了。用户拿着
新版导出的文件回导，得到的是一条条「成功」，而那一列全空。

现在：`CONSUMED_KEYS` 从 `CSV_COLUMNS` 现算，`unhandled_columns()` 在 `resolve_headers()`
里被**真的调用**，第三类列直接抛 `ImportFormatError`（400），点名是哪些列。
行为层面的对账在 `tests/test_import_rules.py::ColumnCoverageTest`：按「换掉这一格的值，
`build_row()` 的输出会不会变」现算消费集，再与声明的两份清单比对。

本模块只用标准库，且不出现 BOM / EOL 字面量（`test_export_contract.py` 盯着
「全仓只有 `apps/core/csv_export.py` 一处定义」）—— 它从那里 import。
"""
from __future__ import annotations

import csv
import io
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal, InvalidOperation

from apps.core.csv_export import BOM, FORMULA_CONTROL, FORMULA_PREFIXES, GUARD
from apps.transactions.export_rules import CSV_COLUMNS


class ImportFormatError(ValueError):
    """整份文件读不了（空文件 / 没有表头 / 列名认不出 / 缺必需列）。

    调用方应回 **400**：这是用户改一下表头就能解决的事，不是平台故障。
    """


class RowValueError(ValueError):
    """某一行的值不合法。带上「哪一列」的说明，用户才知道去改哪一格。"""


@dataclass(frozen=True)
class RowError:
    """一行的问题：`line` 是**文件里的行号**（表头算第 1 行，与人眼看到的一致）。"""

    line: int
    message: str


@dataclass(frozen=True)
class ParsedRow:
    """一行解析成功的记录。

    行号跟着数据一起走：视图在**后面**还有一步可能失败（账户名查不到、序列化器校验不过），
    那时报错信息里必须能指回文件里的第几行。把行号塞进 `data` 里也行，但那样它就成了
    「要记得 pop 掉的杂质」；单独一个字段才是不会漏的写法。
    """

    line: int
    data: dict


@dataclass
class ParsedCsv:
    """解析结果。`total` = 成功行 + 出错行（空行不算）。"""

    rows: list = field(default_factory=list)
    errors: list = field(default_factory=list)
    total: int = 0


def _aliases() -> dict:
    """列名 → 取值键：中文表头与英文键名都认。**从 `CSV_COLUMNS` 现算。**

    唯一来源是 `export_rules.CSV_COLUMNS`；这里多写一个字面量就多一条漂移路径。
    """
    table = {}
    for key, title in CSV_COLUMNS:
        table[title] = key
        table[key] = key
    return table


#: 可接受的列名（中文表头 + 英文键名）
HEADERS = _aliases()

#: 标题反查（报错信息里要用中文名，而不是 `side_label` 这种取值键）
TITLES = {key: title for key, title in CSV_COLUMNS}

#: 少了这些列就没法记账
REQUIRED_KEYS = ("traded_at", "side_label", "account_name", "currency")

#: 认得出、但**刻意不消费**的列：写进来不报错（自己导出的文件里必然有它们），
#: 读进来会被忽略。理由见模块说明「导入方不负责的三件事」第 1 条。
#: 这份清单是**声明意图**用的，由 `tests/test_import_rules.py::ColumnCoverageTest`
#: 按行为反过来核对：换掉这一格的值，`build_row()` 的输出必须**不变**。
IGNORED_KEYS = ("asset_name", "source_label")

#: 这一行会真正被用到的取值键 —— **从 `CSV_COLUMNS` 现算**，不手抄第二份。
#: 手抄的那一份（曾经的 12 项元组）全仓没有消费方：导出加一列它照旧 12 项，
#: 既不会响也不会挡，只会让读代码的人以为有人管着。
CONSUMED_KEYS = tuple(key for key, _title in CSV_COLUMNS if key not in IGNORED_KEYS)


def unhandled_columns(keys) -> list:
    """`keys` 里既没被这一行读、也没被声明忽略的列 —— 它们的值会被**静默丢掉**。

    这是「导出加了列、导入还不知道」唯一会经过的地方。返回空列表＝这一批列都被安排过。
    """
    known = set(CONSUMED_KEYS) | set(IGNORED_KEYS)
    return [key for key in keys if key not in known]

#: 时间列的几种写法（文件里最可能出现的几种），按「越具体越先试」排
DATETIME_FORMATS = ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d")

#: 数值列 → 报错时用的中文名（就是它在导出的表头里的名字）
NUMERIC_KEYS = {
    "quantity": TITLES["quantity"],
    "price": TITLES["price"],
    "amount": TITLES["amount"],
    "fee": TITLES["fee"],
    "tax": TITLES["tax"],
    "fx_rate": TITLES["fx_rate"],
}


def strip_bom(text: str) -> str:
    """去掉 UTF-8 BOM。

    我们自己导出的文件是带 BOM 的（给 Excel 看的），所以导入这一侧必须吃掉它 ——
    否则第一个表头会变成 `\\ufeff时间`，于是**自己导出的文件自己导不进来**，
    报的还是「不认识的列名」。
    """
    return text[1:] if text.startswith(BOM) else text


def unescape_cell(text: str) -> str:
    """还原导出时加上去的公式注入防线。

    `sanitize_cell()` 会给以 `=` `+` `-` `@` 开头（或带控制字符）的单元格加一个单引号，
    导入时要把那个引号摘掉，否则每回导一次备注就多一个 `'`。

    判据与那边**对称**：只有「单引号后面紧跟着一个公式前缀 / 控制字符」才摘。
    这条判据有一处认不出来：备注本身就写成 `'=1+1`（真的以单引号开头、然后是等号）时，
    导出不会再加引号，而这里会摘掉一个 —— 往返不保真的就是这一类，无法在 CSV 里区分，
    刻意选「摘」。
    """
    if text[:1] != GUARD:
        return text
    nxt = text[1:2]
    if nxt in FORMULA_PREFIXES or nxt in FORMULA_CONTROL:
        return text[1:]
    return text


def parse_traded_at(text: str, tz):
    """时间文本 → `datetime`。`tz` 由调用方注入 `settings.TIME_ZONE`。

    不带时区的写法（就是导出写出去的那种）按 `tz` 补上时区 —— `USE_TZ = True` 时
    Django 对 naive datetime 会发警告并按默认时区解释，两处口径不一致就会出现
    「导进来差 8 小时」，所以在这里就把时区定死。带偏移的 ISO 串原样保留。
    """
    raw = (text or "").strip()
    if not raw:
        raise RowValueError(f"{TITLES['traded_at']}是空的 —— 每条流水都得有时间")
    for fmt in DATETIME_FORMATS:
        try:
            parsed = datetime.strptime(raw, fmt)
        except ValueError:
            continue
        return parsed.replace(tzinfo=tz) if tz is not None else parsed
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError as exc:
        raise RowValueError(
            f"{TITLES['traded_at']}看不懂：{raw!r}（要 `YYYY-MM-DD HH:MM:SS`，"
            "或者带时区偏移的 ISO 8601）"
        ) from exc
    if parsed.tzinfo is None and tz is not None:
        parsed = parsed.replace(tzinfo=tz)
    return parsed


def parse_decimal(text: str, key: str) -> Decimal | None:
    """数值文本 → `Decimal`；空 → `None`（＝「这一格没填」，不是 0）。

    「没填」与「填了 0」必须分开：`手续费` 空着是「没填」，填 `0` 是「就是 0 手续费」，
    两者的落库结果一样，但 `现金变动` 空着要被 `resolve_amount` 推出来、
    填了 `0` 的入金则必须报错。所以这里不替它们做决定。
    """
    raw = (text or "").strip()
    if not raw:
        return None
    try:
        value = Decimal(raw)
    except (InvalidOperation, ValueError) as exc:
        raise RowValueError(f"{NUMERIC_KEYS[key]}不是有效数字：{raw!r}") from exc
    if not value.is_finite():
        raise RowValueError(f"{NUMERIC_KEYS[key]}不是有限数：{raw!r}")
    return value


def resolve_headers(header: list) -> dict:
    """表头一行 → `{列序: 取值键}`。认不出 / 重复 / 缺必需列都直接抛。

    不静默忽略认不出的列：用户的列名写错了却拿到「成功导入 12 条」，
    是最难自己发现的一种失败。
    """
    unknown = [cell for cell in header if (cell or "").strip() not in HEADERS]
    if unknown:
        raise ImportFormatError(
            "这些列名不认识：%s。可用的列名是导出的表头（%s），"
            "或者它们对应的英文键名。"
            % (
                "、".join(repr(c) for c in unknown),
                "、".join(title for _, title in CSV_COLUMNS),
            )
        )
    index_to_key = {}
    for index, cell in enumerate(header):
        key = HEADERS[(cell or "").strip()]
        if key in index_to_key.values():
            raise ImportFormatError(f"列 {cell!r} 出现了两次 —— 同一列给两份值，谁覆盖谁说不清")
        index_to_key[index] = key
    missing = [key for key in REQUIRED_KEYS if key not in index_to_key.values()]
    if missing:
        raise ImportFormatError(
            "缺少必需的列：%s" % "、".join(TITLES[key] for key in missing)
        )
    # 列集合必须是一份划分：读走的 ∪ 声明忽略的 = 全部。落在第三类里的列会被静默丢掉，
    # 而「静默丢掉」正是这个模块最不该有的一种失败（用户拿到一屏「成功」，那一列全空）。
    unhandled = unhandled_columns(index_to_key.values())
    if unhandled:
        # 中文名从**当前**的 CSV_COLUMNS 现取（它才是列清单的来源）；两边都报出来：
        # 用户认表头，改代码的人认取值键。
        titles = {key: title for key, title in CSV_COLUMNS}
        named = "、".join(
            f"{titles[key]}（{key}）" if key in titles else key for key in unhandled
        )
        raise ImportFormatError(
            "这些列还不在导入的处理范围内：%s。导出的列清单（export_rules.CSV_COLUMNS）"
            "加了新列，而 import_rules 既没读它、也没把它登记进 IGNORED_KEYS —— "
            "照收不误就会把这一列的值悄悄丢掉。" % named
        )
    return index_to_key


def _resolve_side(text: str, side_labels: dict) -> str:
    """`买入` / `BUY` 都认。标签表由调用方注入 `dict(TxSide.choices)`。

    与导出同一套：标签表不在这里抄第二份，模型加了一种 side，导入自动就能认它的中文名。
    """
    raw = (text or "").strip()
    if not raw:
        raise RowValueError(f"{TITLES['side_label']}是空的")
    label_to_code = {label: code for code, label in side_labels.items()}
    if raw in label_to_code:
        return label_to_code[raw]
    code = raw.upper()
    if code in side_labels:
        return code
    raise RowValueError(
        f"{TITLES['side_label']}不认识：{raw!r}（可用：%s）"
        % "、".join(label for _, label in side_labels.items())
    )


def build_row(record: dict, side_labels: dict, tz) -> dict:
    """一行的原始文本 → **交给序列化器的那组字段**（账户 / 标的仍是名字，由视图解析 id）。

    键名刻意用 `account_name` / `asset_symbol`：这两个是要拿去找对象的**名字**，
    与序列化器的 `account` / `asset`（id）分开，免得有人以为可以直接喂进去。
    """
    account_name = (record.get("account_name") or "").strip()
    if not account_name:
        raise RowValueError(f"{TITLES['account_name']}是空的 —— 不知道这笔记在哪个账户上")
    currency = (record.get("currency") or "").strip()
    if not currency:
        raise RowValueError(f"{TITLES['currency']}是空的")
    note = unescape_cell(record.get("note") or "")
    if len(note) > 255:
        raise RowValueError(
            f"{TITLES['note']}太长（{len(note)} 字，模型上限 255）—— 记到备注里的应该是摘要"
        )
    return {
        "traded_at": parse_traded_at(record.get("traded_at"), tz),
        "side": _resolve_side(record.get("side_label"), side_labels),
        "account_name": account_name,
        "asset_symbol": (record.get("asset_symbol") or "").strip(),
        "quantity": parse_decimal(record.get("quantity"), "quantity"),
        "price": parse_decimal(record.get("price"), "price"),
        "amount": parse_decimal(record.get("amount"), "amount"),
        "fee": parse_decimal(record.get("fee"), "fee"),
        "tax": parse_decimal(record.get("tax"), "tax"),
        "currency": currency,
        "fx_rate": parse_decimal(record.get("fx_rate"), "fx_rate"),
        "note": note,
    }


def parse_csv(text: str, *, side_labels: dict, tz) -> ParsedCsv:
    """整份 CSV 文本 → `ParsedCsv`。

    文件级问题抛 `ImportFormatError`；行级问题进 `errors`（**不中断整批**：
    第 3 行错了不该让第 4 行也进不来）。空行跳过 —— Excel 另存常常留一串尾部空行，
    把它们当成「列数对不上」会淹没真正的问题。
    """
    body = strip_bom(text)
    if not body.strip():
        raise ImportFormatError("文件是空的 —— 既没有表头也没有数据行")
    reader = csv.reader(io.StringIO(body, newline=""))
    try:
        header = next(reader)
    except StopIteration as exc:  # pragma: no cover - 上面那条空判断已经拦住了
        raise ImportFormatError("文件里连表头都没有") from exc
    index_to_key = resolve_headers(header)

    result = ParsedCsv()
    for offset, raw in enumerate(reader):
        line = offset + 2  # 表头是文件里的第 1 行，与人眼看到的一致
        if not any((cell or "").strip() for cell in raw):
            continue
        if len(raw) != len(header):
            result.errors.append(
                RowError(line, f"列数对不上：表头 {len(header)} 列，这一行 {len(raw)} 列")
            )
            continue
        record = {index_to_key[i]: raw[i] for i in range(len(header))}
        try:
            result.rows.append(ParsedRow(line=line, data=build_row(record, side_labels, tz)))
        except RowValueError as exc:
            result.errors.append(RowError(line, str(exc)))
    result.total = len(result.rows) + len(result.errors)
    return result
