# -*- coding: utf-8 -*-
"""客户端 ArkTS 的**模型声明** ⇄ 后端真实返回的字段 —— 两向对账；外加「这几个数真的被渲染了」。

为什么要有它
------------
本仓 `.ets` 这一侧没有工具链（README「已知约束」里写明：本机没有 DevEco / hvigor，
改 `.ets` 不会被任何自测发现），所以这一侧的不变式只能靠读源码的契约检查兜。此前钉住的
是四面 —— 「客户端页面清单 / import 落点 / 路由登记」「默认后端地址只有一处且被真的用上」
「客户端打的每个接口路径真实存在」「每处调用用的动词都被那条路由接」。**那四面全是
「谁调谁」，`Models.ets` 里那些 `interface` 是「声明面」，一条检查都没有。**

它错起来是什么样（本轮的真事）
------------------------------
`analytics/services.py` 的 `build_summary()` 返回 16 个键，`Models.ets` 的 `Summary`
只声明了 13 个；`build_positions()` 每一行返回 16 个键，`Position` 只声明了 13 个。
差的那些 —— `dividend_annual` / `dividend_yield` / `monthly_passive_income` ——
在 **README 里有逐条含义**（第 576 行起那张表）、在 **`scripts/smoke_record_flow.py`
里有断言**，也就是说后端这一侧是被钉住的；而客户端这一侧：字段没声明 → 界面不会去读 →
用户永远看不到。README 甚至把它写进了「已知约束」：「股息率与月度被动收入后端已提供，
鸿蒙统计页尚未展示，端上接入在 M5」—— **一条写下来就没人再看的待办**。

它**没有任何症状**：ArkTS 里读一个没声明的字段是编译错误，所以没人会去读它；不读它，
一切照常编译、照常运行、照常显示别的行。这与本仓栽过的那些形状同族（「能力在、入口不在」、
「算出来了没人看见」），只是这次扛事的那一层是**声明**。

锁什么
------
1. **客户端声明的字段，后端必须真的会发**（`Summary` / `Position` 各一遍）。声明了不发的
   字段在界面上恒为 `undefined` → 那一列永远是 `-`，且不报错。
2. **后端发的字段，客户端要么声明、要么在 `UNCLAIMED` 里写明理由**（同上两向）。这条是
   第 1 条的**另一半**，也是本轮真正抓到东西的那一半：漏掉的字段全在后端这一侧。
3. **后端可能给 `null` 的字段，客户端声明必须允许 `null`**。`dividend_yield` 在成本为 0
   （已清仓）时返回的是 `null` 而不是 0 —— 「算不出」与「收益率是 0」对用户是两件事
   （`dividend_income.dividend_yield()` 的 docstring 写明了）。声明成 `string` 就把这个
   区分抹掉了，而且会在严格空检查下把编译期错误留到别人去撞。
4. **统计页真的把这几个数渲染出来了**。「声明了」不等于「看得见」：`total_pnl` 原本**已经**
   声明在 `Summary` 里，但 `StatsPage` 一行都没用它 —— 声明的下一层还是同一形状。
   这一条只锁两样客观的东西：① 每个键在**剥过注释**的页面源码里以 `summary.<key>` 出现；
   ② 显示面（`this.row(` 的条数）不低于下限。**不锁标签散文**（「近一年股息」这几个字
   怎么写是设计问题）。
5. **持仓行的键必须含账户**。后端按 `(账户, 标的)` 聚合，同一标的两账户会给两行；页面
   `ForEach` 的键原先只有 `asset_id-symbol`，两行的键一模一样 —— ArkUI 撞键的后果是
   「有一行不见了」，本机编译不出来也跑不起来，谁都不会知道。

真值从哪来（都不是手抄的）
--------------------------
- 后端侧：**解析** `apps/analytics/services.py` —— `build_summary()` 的 `return {…}` 与
  `build_positions()` 的 `positions.append({…})`，取真实的键；`null` 那一面的真值来自每个
  键的**取值表达式**（`… if x is not None else None`）。
- 客户端侧：**解析** `Models.ets` 里的 `export interface X {…}`。
- 两份都是现算的，没有清单。加字段忘了同步，红的就是这里。

这条 nullable 锁的边界（刻意划出来）
------------------------------------
真值只覆盖两类写法：① 取值表达式里的 `… else None`；② `NULLABLE_WRITTEN_ELSEWHERE`
里带**可验证证据**的登记项（证据正则必须在 `services.py` 里真的找得到，否则登记项自己红）。
**裸名字赋值看不见**（`"account_id": account_id`）—— 那一类要靠人读模型。这是刻意的：
把「任何可能为 null 的空外键」都收进来，会逼着 `account_id` / `asset_id` 也声明成 `| null`，
而它们只被用作分组键，加 null 检查是纯噪声。边界写在这里，比让它看起来更严要诚实。

刻意**不**锁
------------
- `Models.ets` 里 `Draft` / `DraftResult` / `DividendMonth`：它们的真值在 ingest 的序列化器
  里，而客户端目前只用了其中一部分字段（`DraftResult` 十几个键里页面只读了一个）——
  把「后端可能发但页面用不到」一律判红，会把这一层变成噪声。这里只锁 `Summary` 与
  `Position`：它们是「收益口径」这件事的出口，且 README 已把每个字段的含义逐条写死。
- 页面里的颜色、字号、行高、`@Builder` 结构 —— 那是 DevEco 预览的事。
- `null` 的**运行时**后果（比如 `null` 被当 `string` 拼接）—— 本机没有工具链，跑不了；
  这里只能保证**声明**是对的。

假锁防护
--------
`TestTheContractIsNotVacuous` 做四件事：① 手写的 Models / services 片段喂给解析器，断言
解析结果**逐字正确**（含注释里的冒号不被当字段）；② 拿**真文件**断言解析面不是空的；
③ 把「少声明一个 / 多声明一个 / 声明成不可空 / 登记表里多一条 / 登记项已能被表达式扫出来」
五种差异喂给判据函数，断言它们**真的会报**；④ 断言 `strip_ets_comments()` /
`strip_py_comments()` 真的会剥注释，而不是原样返回。

跑法（在 server/ 下）：python -m unittest discover -s apps -t .
"""
import re
import unittest
from pathlib import Path

#: 本文件在 `server/apps/core/tests/` 下 —— 往上第三层是 `server/`
SERVER = Path(__file__).resolve().parents[3]
assert (SERVER / "manage.py").exists(), f"算错了 server 根：{SERVER} 下没有 manage.py"
ROOT = SERVER.parent

MODELS_ETS = ROOT / "harmony/entry/src/main/ets/common/Models.ets"
SERVICES_PY = SERVER / "apps/analytics/services.py"
STATS_PAGE = ROOT / "harmony/entry/src/main/ets/pages/StatsPage.ets"
HOLDINGS_PAGE = ROOT / "harmony/entry/src/main/ets/pages/HoldingsPage.ets"

# ---------------------------------------------------------------- 锚点（唯一定位到那一块）
#: `build_summary()` 里紧挨着 `return {` 的那两行 —— 用它当锚，避免拿 `return {` 去撞别的函数
SUMMARY_ANCHOR = "yield_value = dividend_yield(annual_total, total_cost)"

#: 持仓那一行的形状是**三处字面量拼出来的**：两处建 bucket（有流水的 / 只有股息明细的）+
#: 一处 `positions.append({**bucket, …})`。只读 append 那一处的 10 个键会**漏掉**
#: `account_id` / `symbol` / `market` / `currency` 等 7 个 —— 这本身就是本仓的一个真事实
#: （`**bucket` 不是 `"键": 值` 形态，任何按行解析的检查都看不见它）。
POSITION_ANCHORS = (
    "buckets.setdefault(",
    "buckets[key] = {",
    "positions.append(",
)

#: 后端会发、客户端**刻意不声明**的字段 —— 键 → 理由（必须写清为什么用户看不到它也无所谓）
UNCLAIMED = {
    # 这张表本轮是空的：`Summary` / `Position` 的每一个键都有人认领了。
    # 加新字段时要么声明进 Models.ets，要么写进这里并说清理由 —— 两向都要出声。
}

#: `null` 的写法不在取值表达式里（裸名字 / 局部变量）的键 —— 键 → (证据正则, 理由)
#: 证据正则**必须在 services.py 里真的匹配得到**，否则这一项自己红（腐烂条目也得出声）。
NULLABLE_WRITTEN_ELSEWHERE = {
    "annualized": (
        r"annualized\s*=\s*xirr\(flows\)\s*if\s+len\(flows\)\s*>=\s*2\s+else\s+None",
        "annualized 的可空性写在局部变量上（`annualized = xirr(flows) if len(flows) >= 2 "
        "else None`），取值表达式里只是 `\"annualized\": annualized`，表达式规则扫不到。",
    ),
}

#: 统计页必须真的显示出来的汇总键（README 逐条写了它们的含义）
SUMMARY_KEYS_ON_SCREEN = ("total_pnl", "dividend_annual", "dividend_yield", "monthly_passive_income")

#: 统计页 `this.row(...)` 的条数下限 —— **棘轮**：加行时把它抬上去，删行时说明为什么
MIN_STATS_ROWS = 10

#: 本文件只锁这两个 interface（理由见模块说明的「刻意不锁」）
LOCKED_INTERFACES = ("Position", "Summary")


# --------------------------------------------------------------------------- 剥注释
def strip_ets_comments(text: str) -> str:
    """把 `.ets` 的注释抹成空格（**保持长度与行结构**，方便肉眼对位）。

    为什么不能直接 `re.sub`：`'#7166F0'` 这种字符串字面量里没有 `//`，但
    `'http://10.0.2.2:8000'` 有 —— 照着 `//` 一刀切会把字符串切开、把后面的代码吃成注释。
    所以按状态走：代码 / 行注释 / 块注释 / 单引号 / 双引号 / 模板串。
    """
    out = []
    i, n = 0, len(text)
    state = "code"
    while i < n:
        ch = text[i]
        nxt = text[i + 1] if i + 1 < n else ""
        if state == "code":
            if ch == "/" and nxt == "/":
                state, i = "line", i + 2
                out.append("  ")
                continue
            if ch == "/" and nxt == "*":
                state, i = "block", i + 2
                out.append("  ")
                continue
            if ch in "'\"`":
                state = ch
            out.append(ch)
            i += 1
            continue
        if state == "line":
            out.append("\n" if ch == "\n" else " ")
            if ch == "\n":
                state = "code"
            i += 1
            continue
        if state == "block":
            if ch == "*" and nxt == "/":
                state, i = "code", i + 2
                out.append("  ")
                continue
            out.append("\n" if ch == "\n" else " ")
            i += 1
            continue
        # 字符串态（state 就是那个引号字符）
        if ch == "\\" and i + 1 < n:
            out.append(text[i:i + 2])
            i += 2
            continue
        if ch == state:
            state = "code"
        out.append(ch)
        i += 1
    return "".join(out)


def strip_py_comments(text: str) -> str:
    """把 `.py` 的 `#` 注释与三引号 docstring 抹成空格（同样保持行结构）。"""
    out = []
    i, n = 0, len(text)
    state = "code"
    while i < n:
        ch = text[i]
        three = text[i:i + 3]
        nxt = text[i + 1] if i + 1 < n else ""
        if state == "code":
            if ch == "#":
                state, i = "line", i + 1
                out.append(" ")
                continue
            if three in ('"""', "'''"):
                state, i = "t" + three[0], i + 3
                out.append("   ")
                continue
            if ch in "'\"":
                state = ch
            out.append(ch)
            i += 1
            continue
        if state == "line":
            out.append("\n" if ch == "\n" else " ")
            if ch == "\n":
                state = "code"
            i += 1
            continue
        if state.startswith("t"):
            if three == state[1] * 3:
                state, i = "code", i + 3
                out.append("   ")
                continue
            out.append("\n" if ch == "\n" else " ")
            i += 1
            continue
        if ch == "\\" and i + 1 < n:
            out.append(text[i:i + 2])
            i += 2
            continue
        if ch == state:
            state = "code"
        out.append(ch)
        i += 1
    return "".join(out)


# --------------------------------------------------------------- 解析：后端返回的键
def block_after(source: str, anchor: str) -> str:
    """`anchor` 之后第一个 `{` 起、括号平衡的那一整块。找不到返回空串。"""
    at = source.find(anchor)
    if at < 0:
        return ""
    start = source.find("{", at)
    if start < 0:
        return ""
    depth = 0
    for i in range(start, len(source)):
        if source[i] == "{":
            depth += 1
        elif source[i] == "}":
            depth -= 1
            if depth == 0:
                return source[start:i + 1]
    return ""


def dict_entries(block: str) -> list:
    """字面量字典 → `[(键, 取值表达式), …]`（按顶层逗号切，嵌套括号不算）。"""
    inner = block[1:-1] if block.startswith("{") else block
    chunks, buf, depth = [], [], 0
    for ch in inner:
        if ch in "([{":
            depth += 1
        elif ch in ")]}":
            depth -= 1
        if ch == "," and depth == 0:
            chunks.append("".join(buf))
            buf = []
            continue
        buf.append(ch)
    chunks.append("".join(buf))

    pairs = []
    for raw in chunks:
        m = re.match(r'\s*"([a-z_][a-z0-9_]*)"\s*:\s*(.*)$', raw, re.S)
        if m:
            pairs.append((m.group(1), m.group(2).strip()))
    return pairs


def dict_keys(block: str) -> list:
    return [k for k, _rhs in dict_entries(block)]


#: 取值表达式里直接写了 `… if … else None` 的那些键
_ELSE_NONE_RE = re.compile(r"\belse\s+None\s*$")


def nullable_keys(block: str) -> set:
    """从取值表达式里现算「这个键可能是 null」。"""
    return {k for k, rhs in dict_entries(block) if _ELSE_NONE_RE.search(rhs)}


# --------------------------------------------------------------- 解析：ArkTS 的 interface
#: `export interface X {` … 到**顶格**的 `}`
ETS_INTERFACE_RE = re.compile(r"export\s+interface\s+(\w+)\s*\{(.*?)\n\}", re.S)
#: interface 体里的一行字段声明（可选 `?`，类型到 `;` 为止）
ETS_FIELD_RE = re.compile(r"^[ \t]*(\w+)\??[ \t]*:[ \t]*([^;]+);[ \t]*$", re.M)


def ets_interfaces(text: str) -> dict:
    """`export interface X {…}` → `{X: {字段: 声明的类型}}`（先剥注释）。"""
    found = {}
    for name, body in ETS_INTERFACE_RE.findall(strip_ets_comments(text)):
        found[name] = {m.group(1): m.group(2).strip() for m in ETS_FIELD_RE.finditer(body)}
    return found


#: `ForEach(this.positions, (item: Position) => \`…\`)` 里的那个键模板
ROW_KEY_RE = re.compile(r"\(item:\s*Position\)\s*=>\s*`([^`]*)`")


def holdings_row_key(text: str) -> str:
    """持仓列表 `ForEach` 的键模板（找不到返回空串）。"""
    m = ROW_KEY_RE.search(strip_ets_comments(text))
    return m.group(1) if m else ""


# --------------------------------------------------------------------------- 判据（纯函数）
def unclaimed_backend_keys(backend_keys, client_fields, registry=None):
    """后端会发、客户端既没声明也没登记 —— 静默丢弃的那一半。"""
    return sorted(set(backend_keys) - set(client_fields) - set(registry or ()))


def phantom_client_fields(backend_keys, client_fields):
    """客户端声明了、后端不会发 —— 那一列永远是 `-` 的那一半。"""
    return sorted(set(client_fields) - set(backend_keys))


def missing_null_in_declaration(nullable, client_types):
    """后端可能给 `null`、但客户端的声明不允许 `null`。"""
    bad = []
    for key in sorted(nullable):
        decl = client_types.get(key)
        if decl is None:
            continue
        if "null" not in [part.strip() for part in decl.split("|")]:
            bad.append(f"{key} 声明成 {decl!r}，但后端会给 null")
    return bad


def stale_nullable_registry(registry, backend_keys, derived_nullable, source):
    """登记项三种腐烂：键没了 / 证据在源码里找不到了 / 其实已被表达式规则扫出来。"""
    bad = []
    for key, (evidence, _why) in registry.items():
        if key not in backend_keys:
            bad.append(f"{key}：后端已经没有这个键了，登记项该删")
            continue
        if key in derived_nullable:
            bad.append(f"{key}：取值表达式已经能被规则扫出来，登记项该删")
            continue
        if not re.search(evidence, source):
            bad.append(f"{key}：理由里说的写法在 services.py 里找不到了（{evidence!r}）")
    return bad


# --------------------------------------------------------------------------- 真值（现算）
def load():
    models = MODELS_ETS.read_text(encoding="utf-8")
    services = strip_py_comments(SERVICES_PY.read_text(encoding="utf-8"))
    position_blocks = [block_after(services, anchor) for anchor in POSITION_ANCHORS]
    return {
        "models": models,
        "services": services,
        "interfaces": ets_interfaces(models),
        "summary_block": block_after(services, SUMMARY_ANCHOR),
        "position_blocks": position_blocks,
        "position_block": "\n".join(b for b in position_blocks),
    }


def position_backend_keys(data):
    """持仓那一行**真实会带**的键 —— 三处字面量的并集（理由见 `POSITION_ANCHORS`）。"""
    keys = set()
    for block in data["position_blocks"]:
        keys |= set(dict_keys(block))
    return sorted(keys)


class TestInterfacesAreParsed(unittest.TestCase):
    """先证明解析面不是空的、也没解析错地方。"""

    @classmethod
    def setUpClass(cls):
        cls.data = load()

    def test_两个_interface_都解析到了(self):
        for name in LOCKED_INTERFACES:
            self.assertIn(name, self.data["interfaces"], f"Models.ets 里没解析到 {name}")

    def test_接口字段数不低于下限(self):
        fields = self.data["interfaces"]
        self.assertGreaterEqual(len(fields["Summary"]), 15, f"Summary 只解析出 {len(fields['Summary'])} 个字段")
        self.assertGreaterEqual(len(fields["Position"]), 16, f"Position 只解析出 {len(fields['Position'])} 个字段")

    def test_后端两个字典块都找到了(self):
        self.assertTrue(self.data["summary_block"], f"锚点 {SUMMARY_ANCHOR!r} 之后没找到字典块")
        for anchor, block in zip(POSITION_ANCHORS, self.data["position_blocks"]):
            self.assertTrue(block, f"锚点 {anchor!r} 之后没找到字典块")

    def test_后端键数不低于下限(self):
        self.assertGreaterEqual(len(dict_keys(self.data["summary_block"])), 15)
        # 三处并集实测 17 个键；只读 append 那一处只有 10 个 —— 下限就卡在这里
        self.assertGreaterEqual(len(position_backend_keys(self.data)), 15)

    def test_表达式规则真的扫出了可空键(self):
        """这条是 nullable 那一面的解析面自证：规则一旦腐烂就变成空集，必须当场现形。"""
        derived = set(nullable_keys(self.data["summary_block"]))
        for block in self.data["position_blocks"]:
            derived |= nullable_keys(block)
        self.assertGreaterEqual(len(derived), 3, f"表达式规则只扫出 {sorted(derived)}")


class TestSummaryContract(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.data = load()
        cls.backend = dict_keys(cls.data["summary_block"])
        cls.client = cls.data["interfaces"]["Summary"]

    def test_客户端声明的字段后端都真的会发(self):
        self.assertEqual(
            phantom_client_fields(self.backend, self.client), [],
            "Models.ets 的 Summary 声明了后端不发的字段 —— 界面上那一行永远是 '-'，且不报错",
        )

    def test_后端发的字段客户端都要么声明要么登记(self):
        self.assertEqual(
            unclaimed_backend_keys(self.backend, self.client, UNCLAIMED), [],
            "后端算了、README 也写了含义，但客户端既没声明也没登记 —— 用户看不到它，"
            "而且没有任何东西会提醒。要么加进 Models.ets，要么写进 UNCLAIMED 并说清理由",
        )

    def test_可能为_null_的字段声明必须允许_null(self):
        nullable = nullable_keys(self.data["summary_block"]) | set(NULLABLE_WRITTEN_ELSEWHERE)
        self.assertEqual(
            missing_null_in_declaration(nullable, self.client), [],
            "后端「算不出来」时给的是 null，客户端声明成不可空就把「算不出」和「等于 0」"
            "抹成了一件事",
        )

    def test_nulllable_登记表没有腐烂条目(self):
        self.assertEqual(
            stale_nullable_registry(
                NULLABLE_WRITTEN_ELSEWHERE, self.backend,
                nullable_keys(self.data["summary_block"]), self.data["services"],
            ), [],
        )


class TestPositionContract(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.data = load()
        cls.backend = position_backend_keys(cls.data)
        cls.client = cls.data["interfaces"]["Position"]

    def test_客户端声明的字段后端都真的会发(self):
        self.assertEqual(phantom_client_fields(self.backend, self.client), [])

    def test_后端发的字段客户端都要么声明要么登记(self):
        self.assertEqual(unclaimed_backend_keys(self.backend, self.client, UNCLAIMED), [])

    def test_可能为_null_的字段声明必须允许_null(self):
        nullable = set()
        for block in self.data["position_blocks"]:
            nullable |= nullable_keys(block)
        self.assertEqual(missing_null_in_declaration(nullable, self.client), [])


class TestSummaryIsActuallyOnScreen(unittest.TestCase):
    """「声明了」不等于「看得见」—— `total_pnl` 原本就声明了，但统计页一行都没用它。"""

    @classmethod
    def setUpClass(cls):
        cls.page = strip_ets_comments(STATS_PAGE.read_text(encoding="utf-8"))

    def test_四个汇总口径都在统计页出现(self):
        missing = [k for k in SUMMARY_KEYS_ON_SCREEN if f"summary.{k}" not in self.page]
        self.assertEqual(
            missing, [],
            f"这几个数后端算好了、README 也写了含义，但统计页没读它们：{missing}",
        )

    def test_显示面没有被削空(self):
        rows = self.page.count("this.row(")
        self.assertGreaterEqual(
            rows, MIN_STATS_ROWS,
            f"统计页的 this.row(...) 只剩 {rows} 处（下限 {MIN_STATS_ROWS}）—— "
            f"上面那条检查可能已经被削成了空跑；删行就把 MIN_STATS_ROWS 一起改，"
            f"并且说明为什么这几行不该在页面上",
        )


class TestHoldingsRowsAreDistinguishable(unittest.TestCase):
    """后端按 (账户, 标的) 聚合；页面得认得出两个账户持同一标的这种情形。"""

    @classmethod
    def setUpClass(cls):
        raw = HOLDINGS_PAGE.read_text(encoding="utf-8")
        cls.page = strip_ets_comments(raw)
        cls.key = holdings_row_key(raw)

    def test_解析到了_ForEach_的键(self):
        self.assertTrue(self.key, "持仓页没解析到 `(item: Position) => `…`` 这个键模板")

    def test_键里必须带账户(self):
        self.assertIn("account_id", self.key)
        self.assertIn("asset_id", self.key)

    def test_账户名有落点(self):
        self.assertIn(
            "item.account_name", self.page,
            "两行长得一模一样时，用户得看得出哪一行是哪本账",
        )


class TestTheContractIsNotVacuous(unittest.TestCase):
    """手写样本上把解析器与四个判据各验一遍 —— 没有这一节，上面那些可能是恒真的。"""

    FAKE_MODELS = (
        "export interface Summary {\n"
        "  /** 占位注释：这里有个冒号，不该被当成字段 */\n"
        "  cost_basis: string;\n"
        "  dividend_yield: string | null;\n"
        "}\n"
        "export interface Position {\n"
        "  asset_id: number;\n"
        "}\n"
    )

    FAKE_SERVICES = (
        "def build_summary(user):\n"
        "    # 注释里写个 \"fake_key\": 1 也不该被算进去\n"
        "    return {\n"
        '        "cost_basis": str(total_cost),\n'
        '        "dividend_yield": str(y) if y is not None else None,\n'
        "    }\n"
    )

    def test_剥注释真的会剥(self):
        stripped = strip_ets_comments("const a = 'x//y';\n// 中文注释\n/* 块注释 */\n")
        self.assertIn("'x//y'", stripped, "字符串里的 // 被当成注释切了")
        self.assertNotIn("中文注释", stripped)
        self.assertNotIn("块注释", stripped)

    def test_剥_py_注释真的会剥(self):
        stripped = strip_py_comments('x = 1  # "fake": 2\n"""doc "with {\'braces\'}"\n"""\ny = 2\n')
        self.assertNotIn('"fake"', stripped)
        self.assertNotIn("braces", stripped)
        self.assertIn("y = 2", stripped)

    def test_interface_解析逐字正确(self):
        self.assertEqual(
            ets_interfaces(self.FAKE_MODELS),
            {
                "Summary": {"cost_basis": "string", "dividend_yield": "string | null"},
                "Position": {"asset_id": "number"},
            },
            "注释放进字段里、或者类型被截断，都在这里现形",
        )

    def test_后端字典解析逐字正确(self):
        block = block_after(self.FAKE_SERVICES, "return {")
        self.assertEqual(dict_keys(block), ["cost_basis", "dividend_yield"])
        self.assertEqual(nullable_keys(block), {"dividend_yield"})

    def test_少声明一个字段会被报出来(self):
        self.assertEqual(
            unclaimed_backend_keys(["a", "b", "c"], {"a": "string"}, UNCLAIMED), ["b", "c"],
        )

    def test_登记过的字段不再报(self):
        self.assertEqual(
            unclaimed_backend_keys(["a", "b"], {"a": "string"}, {"b": "这里写理由"}), [],
        )

    def test_多声明一个字段会被报出来(self):
        self.assertEqual(phantom_client_fields(["a"], {"a": "string", "ghost": "string"}), ["ghost"])

    def test_声明成不可空会被报出来(self):
        self.assertEqual(
            missing_null_in_declaration({"y"}, {"y": "string"}),
            ["y 声明成 'string'，但后端会给 null"],
        )
        self.assertEqual(missing_null_in_declaration({"y"}, {"y": "string | null"}), [])

    def test_登记项腐烂会被报出来(self):
        self.assertEqual(
            stale_nullable_registry({"ghost": (r"nope", "")}, ["a"], set(), "a = 1"),
            ["ghost：后端已经没有这个键了，登记项该删"],
        )
        self.assertEqual(
            stale_nullable_registry({"a": (r"never_matches", "")}, ["a"], set(), "a = 1"),
            ["a：理由里说的写法在 services.py 里找不到了（'never_matches'）"],
        )
        self.assertEqual(
            stale_nullable_registry({"a": (r"a = 1", "")}, ["a"], {"a"}, "a = 1"),
            ["a：取值表达式已经能被规则扫出来，登记项该删"],
        )


if __name__ == "__main__":
    unittest.main()
