# -*- coding: utf-8 -*-
"""导出 CSV 的三份契约：**列清单**、**路由顺序**、**渲染出口只有一个**。

一、README 的「导出的列」表是手抄的
------------------------------------
真值在 `apps/transactions/export_rules.py` 的 `CSV_COLUMNS` 里。手抄的清单错起来是
**安静**的：少列一列，读文档的人以为导出里没那个字段；把两列的顺序写反，照着文档写脚本
解析的人会拿错列 —— 两种都不报错。所以这里把两边**双向**钉住（少一列 / 多一列 / 顺序换了
都红），并且要求解析面不许为空（否则「两边都空」会让「相等」恒真）。

同理，README 的「导出股息明细（CSV）」表对 `apps/analytics/dividend_export.py` 的
`DIVIDEND_CSV_COLUMNS`、「导出持仓（CSV）」表对 `apps/analytics/positions_export.py` 的
`POSITIONS_CSV_COLUMNS`。三份列清单走同一套解析与同一条判据 —— 不为第二份抄一遍检查。

二、`records/export/` 必须排在 `router.urls` 前面
--------------------------------------------------
DRF 给 ViewSet 生成的明细路由是 `records/(?P<pk>[^/.]+)/`，`export` 完全符合那个 `pk`
的形状 —— 排到后面就会被它吃掉，拿 `"export"` 去查整型主键，当场 500 而不是 404。
**「换个位置就坏」读代码看不出来**，所以由这个文件盯住声明顺序。判据是「谁在前」，
不是「某一行长什么样」。

三、★ 全仓只有一个 CSV 渲染出口
--------------------------------
三份导出（流水、股息、持仓）的列清单不同，面对的外部世界却是同一个：BOM、CRLF、
公式注入、RFC 4180 转义、latin-1 响应头。各写一份的后果**不是重复几十行，而是分头漂** ——
而漂了不报错：一个导出带 BOM、另一个不带，用户在同一个 Excel 里双击两个文件，
一个中文正常、一个乱码。

所以这里钉的是「定义只有一处」，不是「像不像」：
- `render_line` / `sanitize_cell` / `quote_cell` 三个渲染原语**只许在
  `apps/core/csv_export.py` 定义**；
- `BOM` / `EOL` 两个字面量只许出现在那个文件里（别处只能是 re-export）；
- 各导出模块同名的 `export_names` / `render_rows` / `render_csv` 必须**真的转调**
  那一个实现，不许自己拼（判据是 AST：函数体剥掉 docstring 后只剩一条 `return`，
  且被调方是 `csv_export.<同名>`）。

刻意不在这里做的
----------------
- 导出接口有没有写进 README 的「主要接口」：`test_api_surface_contract.py` 已经把它与
  `config/urls.py` 双向对齐了，这里再抄一遍就是第三份清单。
- 「导出的行数与列表一致」：那是运行期行为，纯读源码判不出来，交给
  `scripts/smoke_api.py` 真发一次请求。
- 扫描面**只覆盖 `server/apps/` 下的产品代码，不含 `tests/`**。刻意收窄：测试里会出现
  「本该只有一处」的那些名字与字面量，数进来这条检查会自己判红（本仓库栽过 ——
  「写 X 的地方只有一处」把测试里的字符串常量也数了进去）。`scripts/smoke_api.py`
  同理刻意保留写死的 `"\\ufeff"`：它验的正是**字节**，引用常量就变成自证。

跑法（在 server/ 下）：python -m unittest discover -s apps -t .
"""
import ast
import re
import unittest
from pathlib import Path

#: 本文件在 `server/apps/core/tests/` 下 —— 往上第三层才是 `server/`
SERVER = Path(__file__).resolve().parents[3]
assert (SERVER / "manage.py").exists(), f"算错了 server 根：{SERVER} 下没有 manage.py"

ROOT = SERVER.parent
README = ROOT / "README.md"
TRANSACTIONS_URLS = SERVER / "apps" / "transactions" / "urls.py"

#: README 里那几节的标题（正文到下一个标题为止）
EXPORT_SECTION_TITLE = "导出流水（CSV）"
DIVIDEND_SECTION_TITLE = "导出股息明细（CSV）"
POSITIONS_SECTION_TITLE = "导出持仓（CSV）"


def section_re(title):
    """某一节的正文切片正则。两份导出走同一个解析器 —— 不为了第二份抄一遍。"""
    return re.compile(r"^###\s*" + re.escape(title) + r"\s*\n(.*?)(?=^#)", re.M | re.S)


def rel(path):
    """模块路径 → 相对 `server/`、正斜杠的写法（断言里比对的是同一套写法）。"""
    return str(Path(path).relative_to(SERVER)).replace("\\", "/")


#: 表格行；表头与分隔行由下面的 `HEADER_WORDS` / 全横线两条规则挡掉
TABLE_ROW_RE = re.compile(r"^\|\s*([^|]+?)\s*\|(.+?)\|\s*$", re.M)

#: 表头那几格不该被当成列名（`列 / 列名 / 说明` 都可能被写成表头）
HEADER_WORDS = ("列", "列名", "表头", "字段", "说明")

#: 真的导出路由，以及它在 `urlpatterns` 里该待的位置
EXPORT_ROUTE = "records/export/"

#: 列数下限：低于这个数说明要么 README 被削了、要么解析器塌了
MIN_COLUMNS = 10
MIN_DIVIDEND_COLUMNS = 10
MIN_POSITIONS_COLUMNS = 10

# 只 import 纯模块：这三个都不 import django，所以这份检查能待在「不需要数据库、
# 不需要 Django」的那一侧（`test_no_django_required.py` 会拦掉 Django 再把整套跑一遍）。
from apps.analytics.dividend_export import DIVIDEND_CSV_COLUMNS  # noqa: E402
from apps.analytics.positions_export import POSITIONS_CSV_COLUMNS  # noqa: E402
from apps.transactions.export_rules import CSV_COLUMNS  # noqa: E402


def first_table_rows(body):
    """切片里的**第一张** markdown 表 → `[(第一列, 其余), …]`。

    只取第一张、遇到第一行非表格就停。为什么不靠「下一个 `#` 开头的行」切章节：
    这一节后面紧跟的是客户端的「运行环境」表，**中间没有标题**，按标题切会一路把那张表
    也吃进来 —— 实测就是这么撞到的：报出来的「文档多出三列」是别人的表头
    （运行环境 / 本地模拟器 / 真机），看着像文档写错，其实是解析器跑出了自己的地盘。
    """
    rows, started = [], False
    for line in body.splitlines():
        if line.strip().startswith("|"):
            started = True
            rows.extend(TABLE_ROW_RE.findall(line))
        elif started:
            break
    return rows


def documented_columns(title, text=None):
    """README 某一节里**第一张表**的第一列 → 列名列表（按文档顺序）。

    章节没了会**抛异常**而不是返回空列表 —— 返回空列表会让「与真值相等」变成恒真。
    """
    source = README.read_text(encoding="utf-8") if text is None else text
    block = section_re(title).search(source)
    if block is None:
        raise AssertionError(
            f"README 里找不到「### {title}」这一节 —— 这份契约的落点没了，"
            "正向断言会退化成恒真"
        )
    columns = []
    for first, _rest in first_table_rows(block.group(1)):
        first = first.strip().strip("`")
        if first in HEADER_WORDS or set(first) <= {"-", ":", " "}:
            continue
        columns.append(first)
    return columns


def documented_export_columns(text=None):
    """README「导出流水（CSV）」那一节解出的列。"""
    return documented_columns(EXPORT_SECTION_TITLE, text)


def documented_dividend_columns(text=None):
    """README「导出股息明细（CSV）」那一节解出的列。"""
    return documented_columns(DIVIDEND_SECTION_TITLE, text)


def documented_positions_columns(text=None):
    """README「导出持仓（CSV）」那一节解出的列。"""
    return documented_columns(POSITIONS_SECTION_TITLE, text)


def declared_columns():
    """真值：`CSV_COLUMNS` 的表头，按列序。"""
    return [title for _, title in CSV_COLUMNS]


def declared_dividend_columns():
    """真值：`DIVIDEND_CSV_COLUMNS` 的表头，按列序。"""
    return [title for _, title in DIVIDEND_CSV_COLUMNS]


def declared_positions_columns():
    """真值：`POSITIONS_CSV_COLUMNS` 的表头，按列序。"""
    return [title for _, title in POSITIONS_CSV_COLUMNS]


def _call_name(node):
    """`path(...)` → `'path'`；其它 → None。"""
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return None


def _str_const(node):
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def urlpatterns_order(text):
    """`urlpatterns = [...]` 里每个元素的**顺序** → `[('path', 'records/export/'), ('splat', 'router.urls'), …]`。

    只读字面量列表：写成变量、拼出来的都不认（认了就变成猜）。
    """
    tree = ast.parse(text)
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign):
            continue
        if not any(getattr(t, "id", None) == "urlpatterns" for t in node.targets):
            continue
        if not isinstance(node.value, (ast.List, ast.Tuple)):
            raise AssertionError("`urlpatterns` 不是字面量列表，这条检查读不懂了")
        entries = []
        for element in node.value.elts:
            if isinstance(element, ast.Call):
                first = _str_const(element.args[0]) if element.args else None
                entries.append((_call_name(element.func), first))
            elif isinstance(element, ast.Starred):
                target = element.value
                name = getattr(target, "attr", None) or getattr(target, "id", None)
                entries.append(("splat", name))
            else:
                entries.append(("?", ast.dump(element)[:40]))
        return entries
    raise AssertionError(f"{TRANSACTIONS_URLS.name} 里找不到 `urlpatterns = [...]`")


def export_route_comes_first(entries):
    """`records/export/` 是否排在 `*router.urls` **之前**。

    两者缺一个都会抛异常：少了 `path(...)` 是路由没了；少了 `*router.urls` 是**对照面**
    没了 —— 那种情况下这个函数会退化成一句空话，必须炸出来而不是返回 True。
    """
    if ("path", EXPORT_ROUTE) not in entries:
        raise AssertionError(f"urlpatterns 里没有 {EXPORT_ROUTE} 的 path()")
    index = entries.index(("path", EXPORT_ROUTE))
    router_at = [i for i, (kind, _) in enumerate(entries) if kind == "splat"]
    if not router_at:
        raise AssertionError("urlpatterns 里没有 `*router.urls` —— 对照面没了，这条判据会变成恒真")
    return index < router_at[0]


class TestReadmeColumnListMatchesTheCode(unittest.TestCase):
    def test_documented_columns_are_the_code_ones(self):
        documented = documented_export_columns()
        expected = declared_columns()
        self.assertEqual(
            documented, expected,
            "README 的「导出流水（CSV）」表与 `export_rules.CSV_COLUMNS` 对不上：\n"
            f"  README: {documented}\n  代码:   {expected}\n"
            "改列就得改那张表，反之亦然 —— 手抄的清单漏一列是安静的。",
        )

    def test_the_section_is_intact(self):
        """解析面自证：真的解出了一张表，而不是「啥都没解出来所以相等」。"""
        documented = documented_export_columns()
        self.assertGreaterEqual(
            len(documented), MIN_COLUMNS,
            f"只解出 {len(documented)} 列 —— 表被削了，或者解析器塌了",
        )
        self.assertEqual(
            len(set(documented)), len(documented),
            f"文档表里出现了重复的列名：{documented}",
        )


class TestTheColumnParserIsNotVacuous(unittest.TestCase):
    def test_a_missing_section_raises_instead_of_passing_silently(self):
        with self.assertRaises(AssertionError) as ctx:
            documented_export_columns("# 标题\n\n没有那一节\n")
        self.assertIn(EXPORT_SECTION_TITLE, str(ctx.exception))

    def test_a_shrunk_table_is_seen(self):
        """只留一行时就只解出一列 —— 不是「照样相等」。"""
        sample = (
            "# t\n\n### %s\n\n| 列 | 说明 |\n| --- | --- |\n| 时间 | x |\n\n## 下一节\n"
            % EXPORT_SECTION_TITLE
        )
        self.assertEqual(documented_export_columns(sample), ["时间"])
        self.assertNotEqual(documented_export_columns(sample), declared_columns())

    def test_the_real_section_parses_more_than_the_shrunk_sample(self):
        """反向对照：真文件解出的列**远多于**样本 —— 否则上面那条可能一直在看样本。"""
        self.assertGreater(len(documented_export_columns()), 1)

    def test_a_following_table_is_not_swallowed(self):
        """★ 本节后面紧跟的另一张表（客户端「运行环境」）不许被算成列。

        这是实测撞到的形态：按「下一个标题」切章节，会把下面那张表一起吃掉，
        报出来的「文档多出三列」看着像文档写错了，其实是解析器越界。
        """
        sample = (
            "# t\n\n### %s\n\n| 列 | 说明 |\n| --- | --- |\n| 时间 | x |\n\n"
            "客户端用 DevEco 打开：\n\n| 运行环境 | 地址 |\n| --- | --- |\n"
            "| 本地模拟器 | y |\n| 真机 | z |\n\n## 下一节\n" % EXPORT_SECTION_TITLE
        )
        self.assertEqual(documented_export_columns(sample), ["时间"])


class TestReadmeDividendColumnListMatchesTheCode(unittest.TestCase):
    """股息那份列清单走**同一套判据** —— 不为第二份抄一遍检查。"""

    def test_documented_columns_are_the_code_ones(self):
        documented = documented_dividend_columns()
        expected = declared_dividend_columns()
        self.assertEqual(
            documented,
            expected,
            "README 的「导出股息明细（CSV）」表与 `dividend_export.DIVIDEND_CSV_COLUMNS` 对不上：\n"
            f"  README: {documented}\n  代码:   {expected}\n"
            "改列就得改那张表，反之亦然 —— 手抄的清单漏一列是安静的。",
        )

    def test_the_section_is_intact(self):
        """解析面自证：真的解出了一张表，而不是「啥都没解出来所以相等」。"""
        documented = documented_dividend_columns()
        self.assertGreaterEqual(
            len(documented), MIN_DIVIDEND_COLUMNS,
            f"只解出 {len(documented)} 列 —— 表被削了，或者解析器塌了",
        )
        self.assertEqual(
            len(set(documented)), len(documented), f"文档表里出现了重复的列名：{documented}"
        )

    def test_the_two_sections_really_are_different_tables(self):
        """★ 切片必须真的按标题分辨 —— 否则「按标题取第二节」其实一直在看第一节，
        上面那条相等断言看着在管股息，管的却是流水那张表。"""
        flow = documented_export_columns()
        dividend = documented_dividend_columns()
        self.assertNotEqual(flow, dividend, "两节解出了同一张表 —— 按标题切片没起作用")
        self.assertIn("除权日", dividend, "股息那一节里没有「除权日」—— 取到的不是那一节")
        self.assertNotIn("除权日", flow, "流水那一节里冒出了「除权日」—— 两节串了")


class TestReadmePositionsColumnListMatchesTheCode(unittest.TestCase):
    """持仓那份列清单走**同一套判据** —— 不为第三份抄一遍检查。"""

    def test_documented_columns_are_the_code_ones(self):
        documented = documented_positions_columns()
        expected = declared_positions_columns()
        self.assertEqual(
            documented,
            expected,
            "README 的「导出持仓（CSV）」表与 `positions_export.POSITIONS_CSV_COLUMNS` 对不上：\n"
            f"  README: {documented}\n  代码:   {expected}\n"
            "改列就得改那张表，反之亦然 —— 手抄的清单漏一列是安静的。",
        )

    def test_the_section_is_intact(self):
        """解析面自证：真的解出了一张表，而不是「啥都没解出来所以相等」。"""
        documented = documented_positions_columns()
        self.assertGreaterEqual(
            len(documented), MIN_POSITIONS_COLUMNS,
            f"只解出 {len(documented)} 列 —— 表被削了，或者解析器塌了",
        )
        self.assertEqual(
            len(set(documented)), len(documented), f"文档表里出现了重复的列名：{documented}"
        )

    def test_it_is_really_a_third_table(self):
        """★ 三张表必须两两不同 —— 否则按标题切片没起作用，上面那条相等断言在管别人。"""
        flow = documented_export_columns()
        dividend = documented_dividend_columns()
        positions = documented_positions_columns()
        self.assertNotIn(positions, (flow, dividend), "持仓那一节解出了别的节的那张表")
        self.assertIn("持仓成本", positions, "持仓那一节里没有「持仓成本」—— 取到的不是那一节")
        self.assertNotIn("持仓成本", flow, "流水那一节里冒出了「持仓成本」—— 两节串了")
        self.assertNotIn("持仓成本", dividend, "股息那一节里冒出了「持仓成本」—— 两节串了")

    def test_a_missing_positions_section_raises(self):
        """反面对照：这一节没了要**抛异常**，不是返回空列表让相等断言恒真。"""
        with self.assertRaises(AssertionError) as ctx:
            documented_positions_columns("# 标题\n\n没有那一节\n")
        self.assertIn(POSITIONS_SECTION_TITLE, str(ctx.exception))


class TestTheExportRouteIsReachable(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.entries = urlpatterns_order(TRANSACTIONS_URLS.read_text(encoding="utf-8"))

    def test_no_later_declaration_shadows_it(self):
        self.assertTrue(
            export_route_comes_first(self.entries),
            "`%s` 排在 `*router.urls` 后面 —— DRF 的明细路由 `records/(?P<pk>[^/.]+)/` "
            "会把 `export` 当成主键，拿它去查整型主键当场 500。把它挪到 router 之前。"
            % EXPORT_ROUTE,
        )

    def test_the_router_is_still_there(self):
        """路由表本身还在（这条也是上面那条判据的对照面）。"""
        self.assertIn("splat", [kind for kind, _ in self.entries])

    def test_the_order_check_is_not_vacuous(self):
        """★ 反过来排就必须判 False —— 否则那条断言是恒真的。"""
        reversed_entries = [("splat", "router.urls"), ("path", EXPORT_ROUTE)]
        self.assertFalse(export_route_comes_first(reversed_entries))

    def test_a_missing_route_raises_rather_than_returns_true(self):
        with self.assertRaises(AssertionError):
            export_route_comes_first([("splat", "router.urls")])

    def test_a_missing_router_raises_rather_than_returns_true(self):
        """没有了对照面，`index < ...` 就没得比 —— 必须炸，不许「没得比就算过」。"""
        with self.assertRaises(AssertionError):
            export_route_comes_first([("path", EXPORT_ROUTE)])


# ---------------------------------------------------------------------------
# 三、全仓只有一个 CSV 渲染出口
# ---------------------------------------------------------------------------

#: 渲染原语：**只能有一处定义**。别处只能转调，不许自己拼。
RENDER_PRIMITIVES = ("render_line", "sanitize_cell", "quote_cell")

#: 只能有一个出处的那两个常量。**写死字面量**，不引用常量本身 ——
#: 引用就变成自证：改常量时断言跟着变，永远绿。
SOLE_SOURCE_LITERALS = ('BOM = "\\ufeff"', 'EOL = "\\r\\n"')

#: 各导出模块里同名的这三个**必须转调** core 的实现
DELEGATED_NAMES = ("export_names", "render_rows", "render_csv")

CORE_EXPORT_MODULE = SERVER / "apps" / "core" / "csv_export.py"


def production_files():
    """`server/apps/` 下的产品代码路径（**不含 `tests/`**）。

    「扫描面写死一个目录清单」在本仓库已反复出现（写死的清单会漏掉新目录，且漏了不报错），
    所以这里**现算**：`rglob` 全扫，再排除 tests。
    """
    return sorted(p for p in (SERVER / "apps").rglob("*.py") if "tests" not in p.parts)


def defined_functions(path):
    """这个模块里定义的函数名集合。

    走 AST 而不是读文本 —— 注释 / docstring 里出现的名字不算定义。
    """
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return {
        node.name
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }


def definition_sites():
    """`{原语名: [定义它的模块(相对 server/, 正斜杠), …]}`。"""
    sites = {name: [] for name in RENDER_PRIMITIVES}
    for path in production_files():
        names = defined_functions(path)
        for name in RENDER_PRIMITIVES:
            if name in names:
                sites[name].append(rel(path))
    return sites


def duplicate_definitions(sites):
    """定义了不止一处的原语 → 那些模块。空字典 = 每个原语都只有一个出处。"""
    return {name: where for name, where in sites.items() if len(where) > 1}


def literal_sites(fragment):
    """源码里含该字面量片段的模块（相对 server/）。"""
    return [rel(p) for p in production_files() if fragment in p.read_text(encoding="utf-8")]


def sole_call_in_function(source, name):
    """模块源码里 `def name` 的函数体、剥掉 docstring 后**唯一**那条 `return` 的被调方。

    形如 `"csv_export.render_rows"`。读不懂就抛 —— 返回 None 会让外面的相等断言变成空话。

    判据刻意不用正则：注释与 docstring 里写一句 `csv_export.render_rows(...)`
    就能满足文本匹配，实测栽过两次。
    """
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef) or node.name != name:
            continue
        body = list(node.body)
        if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
            body = body[1:]  # 剥掉 docstring
        if len(body) != 1 or not isinstance(body[0], ast.Return):
            raise AssertionError(
                f"`{name}()` 的函数体不是「一条 return」—— 它可能在自己拼 CSV 文本。"
                f"（`{name}` 的实现只该在 apps/core/csv_export.py）"
            )
        call = body[0].value
        if not isinstance(call, ast.Call):
            raise AssertionError(f"`{name}()` 的 return 不是一个调用：{ast.dump(call)[:60]}")
        return ast.unparse(call.func)
    raise AssertionError(f"源码里找不到 `def {name}`")


def delegates_to_core(source, name) -> bool:
    """`def name` 是不是**真的转调** `csv_export.<name>()`。

    把「唯一一条 return」与「被调方就是 core 那个」两件事合成一个判据 ——
    于是反向对照能直接验这一条，而不是验它的一半。

    读不懂（函数体有两条语句、return 的不是调用、压根没这个函数）一律算 **False**，
    不往外抛：这里的语义是「它没有转调」，不是「检查器坏了」。
    """
    try:
        callee = sole_call_in_function(source, name)
    except AssertionError:
        return False
    return callee == f"csv_export.{name}"


class TestThereIsOnlyOneCsvRenderer(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.sites = definition_sites()

    def test_the_primitives_are_defined_in_exactly_one_place(self):
        self.assertEqual(
            duplicate_definitions(self.sites),
            {},
            "有渲染原语被定义了不止一处：%r\n"
            "两份导出各写一份渲染，后果**不是重复几十行，而是分头漂** —— 而漂了不报错："
            "一个导出带 BOM、另一个不带，用户在同一个 Excel 里双击两个文件，"
            "一个中文正常、一个乱码。" % (duplicate_definitions(self.sites),),
        )

    def test_the_bom_and_eol_literals_exist_in_exactly_one_place(self):
        for fragment in SOLE_SOURCE_LITERALS:
            sites = literal_sites(fragment)
            self.assertEqual(
                sites,
                ["apps/core/csv_export.py"],
                f"`{fragment}` 在别处又写了一遍：{sites}\n"
                "同一件事写两遍必然漂，而这一处漂了的表现是「文件里中文乱码 / 行尾变成裸 LF」，"
                "只有双击打开才看得出来。",
            )

    def test_the_domain_modules_delegate_instead_of_reimplementing(self):
        """各导出模块可以有同名的薄封装，但**必须真的转调**那一个实现。"""
        found = 0
        for path in production_files():
            if path == CORE_EXPORT_MODULE:
                continue
            names = defined_functions(path)
            if not names & set(DELEGATED_NAMES):
                continue
            source = path.read_text(encoding="utf-8")
            for name in DELEGATED_NAMES:
                if name not in names:
                    continue
                found += 1
                self.assertTrue(
                    delegates_to_core(source, name),
                    f"{rel(path)} 的 `{name}()` 没有转调 `apps.core.csv_export.{name}()` ——"
                    "自己拼一份就是第二条渲染路径，而两条路径迟早不一样。",
                )
        self.assertGreaterEqual(found, 9, f"只扫到 {found} 个转调点 —— 扫描面塌了")

    def test_the_scan_surface_is_intact(self):
        """自证：扫描真的走到了那些模块，而不是「啥都没扫到所以全绿」。"""
        files = [rel(p) for p in production_files()]
        self.assertGreaterEqual(len(files), 60, f"只扫到 {len(files)} 个产品代码文件 —— 扫描面塌了")
        for probe in (
            "apps/core/csv_export.py",
            "apps/transactions/export_rules.py",
            "apps/analytics/dividend_export.py",
            "apps/analytics/positions_export.py",
        ):
            self.assertIn(probe, files, f"扫描面漏了 {probe}")
        self.assertIn("apps/core/csv_export.py", self.sites["render_line"])


class TestTheRendererScanIsNotVacuous(unittest.TestCase):
    """反向对照：把「第二份实现」喂给同一条判据，该红的必须红。"""

    def test_a_second_definition_site_is_reported(self):
        two_sites = {
            "render_line": ["apps/core/csv_export.py", "apps/analytics/dividend_export.py"]
        }
        self.assertEqual(duplicate_definitions(two_sites), two_sites)
        self.assertEqual(duplicate_definitions({"render_line": ["apps/core/csv_export.py"]}), {})

    def test_the_literal_scan_can_report_more_than_one_file(self):
        """它不是「反正只返回一个」—— 拿一个到处都是的片段探一下。"""
        self.assertGreater(len(literal_sites("def ")), 1)

    def test_a_reimplemented_wrapper_is_caught(self):
        """把「转调」改回「自己拼一行」，同一条判据必须判 False。"""
        sample = (
            "def render_csv(rows, columns=()):\n"
            '    """x"""\n'
            '    return "".join([",".join(str(c) for c in row) + "\\r\\n" for row in rows])\n'
        )
        self.assertFalse(
            delegates_to_core(sample, "render_csv"),
            "自己拼 CSV 文本的 render_csv 被判成了「转调」—— 这条锁是假锁",
        )

    def test_a_faithful_delegation_passes(self):
        sample = (
            "def render_csv(rows, columns=None):\n"
            '    """x"""\n'
            "    return csv_export.render_csv(rows, columns)\n"
        )
        self.assertTrue(delegates_to_core(sample, "render_csv"))

    def test_a_docstring_mentioning_the_call_does_not_fool_it(self):
        """★ 注释/docstring 里写一句 `csv_export.render_csv(...)` 骗不过它（实测栽过）。"""
        sample = (
            "def render_csv(rows, columns=None):\n"
            '    """转调 csv_export.render_csv(rows, columns) 那份实现。"""\n'
            "    return ''.join(_lines_from(rows))\n"
        )
        self.assertFalse(
            delegates_to_core(sample, "render_csv"),
            "docstring 里提了一句 `csv_export.render_csv(...)` 就被当成转调了 —— 判据在读文本而不是读 AST",
        )

    def test_a_two_statement_body_is_not_delegation(self):
        """先算一步再 return 的，也不是转调。"""
        sample = (
            "def render_csv(rows, columns=None):\n"
            "    lines = [', '.join(row) for row in rows]\n"
            "    return ''.join(lines)\n"
        )
        self.assertFalse(delegates_to_core(sample, "render_csv"))

    def test_a_missing_function_is_not_delegation(self):
        """压根没这个函数 → False，而不是「检查器坏了」。"""
        self.assertFalse(delegates_to_core("x = 1\n", "render_csv"))


if __name__ == "__main__":
    unittest.main()
