# -*- coding: utf-8 -*-
"""导出 CSV 的两份契约：**列清单**与**路由顺序**。

一、README 的「导出的列」表是手抄的
------------------------------------
真值在 `apps/transactions/export_rules.py` 的 `CSV_COLUMNS` 里。手抄的清单错起来是
**安静**的：少列一列，读文档的人以为导出里没那个字段；把两列的顺序写反，照着文档写脚本
解析的人会拿错列 —— 两种都不报错。所以这里把两边**双向**钉住（少一列 / 多一列 / 顺序换了
都红），并且要求解析面不许为空（否则「两边都空」会让「相等」恒真）。

二、`records/export/` 必须排在 `router.urls` 前面
--------------------------------------------------
DRF 给 ViewSet 生成的明细路由是 `records/(?P<pk>[^/.]+)/`，`export` 完全符合那个 `pk`
的形状 —— 排到后面就会被它吃掉，拿 `"export"` 去查整型主键，当场 500 而不是 404。
**「换个位置就坏」读代码看不出来**，所以由这个文件盯住声明顺序。判据是「谁在前」，
不是「某一行长什么样」。

刻意不在这里做的
----------------
- 导出接口有没有写进 README 的「主要接口」：`test_api_surface_contract.py` 已经把它与
  `config/urls.py` 双向对齐了，这里再抄一遍就是第三份清单。
- 「导出的行数与列表一致」：那是运行期行为，纯读源码判不出来，交给
  `scripts/smoke_api.py` 真发一次请求。

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

#: README 里那一节的标题（正文到下一个标题为止）
EXPORT_SECTION_TITLE = "导出流水（CSV）"
EXPORT_SECTION_RE = re.compile(r"^###\s*" + re.escape(EXPORT_SECTION_TITLE) + r"\s*\n(.*?)(?=^#)", re.M | re.S)

#: 表格行；表头与分隔行由下面的 `HEADER_WORDS` / 全横线两条规则挡掉
TABLE_ROW_RE = re.compile(r"^\|\s*([^|]+?)\s*\|(.+?)\|\s*$", re.M)

#: 表头那几格不该被当成列名（`列 / 列名 / 说明` 都可能被写成表头）
HEADER_WORDS = ("列", "列名", "表头", "字段", "说明")

#: 真的导出路由，以及它在 `urlpatterns` 里该待的位置
EXPORT_ROUTE = "records/export/"

#: 列数下限：低于这个数说明要么 README 被削了、要么解析器塌了
MIN_COLUMNS = 10

# 只 import 纯模块：`export_rules` 不 import django，所以这份检查能待在
# 「不需要数据库、不需要 Django」的那一侧（`test_no_django_required.py` 会拦掉
# Django 再把整套跑一遍）。
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


def documented_export_columns(text=None):
    """README「导出流水（CSV）」一节里**第一张表**的第一列 → 列名列表（按文档顺序）。

    章节没了会**抛异常**而不是返回空列表 —— 返回空列表会让「与真值相等」变成恒真。
    """
    source = README.read_text(encoding="utf-8") if text is None else text
    block = EXPORT_SECTION_RE.search(source)
    if block is None:
        raise AssertionError(
            f"README 里找不到「### {EXPORT_SECTION_TITLE}」这一节 —— 这份契约的落点没了，"
            "正向断言会退化成恒真"
        )
    columns = []
    for first, _rest in first_table_rows(block.group(1)):
        first = first.strip().strip("`")
        if first in HEADER_WORDS or set(first) <= {"-", ":", " "}:
            continue
        columns.append(first)
    return columns


def declared_columns():
    """真值：`CSV_COLUMNS` 的表头，按列序。"""
    return [title for _, title in CSV_COLUMNS]


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


if __name__ == "__main__":
    unittest.main()
