"""股息出口的单一来源契约 —— 谁在替「股息有没有全」这件事负责。

`apps/analytics/` 有四个地方把股息数字给用户看：持仓页的 `dividend_total`、
统计页的 `dividend_total` / 月度分布、CSV 导出、日历。股息有两条合法录入路径，
走 `/transactions/records/` 且 `side=DIVIDEND` 的那条**只落一条流水、明细表里一行都没有**。
所以任何一处「照明细表（`DividendRecord`）写」都会漏掉那部分 —— 而同一个页面上的
「累计股息」是两种都算的。两边都不报错，只是数字对不上，而用户恰恰是拿它们互相对账的。

这条坑**填过两次**（导出一次、日历一次），第二次就该有落点，而不是等第三次：

1. `DividendRecord` 的查询只许出现在 `services.py`，而且只许出现在那两个负责摘数据的
   函数里。视图层、纯计算层（`dividend_export` / `dividend_calendar`）碰它就红。
2. 视图层里凡「数字来自股息归集」的类，都必须调用一个**本身经由归集函数**的函数。

两边的清单都是**从源码自动发现的**（不手抄）：第 1 条的「谁在碰」按 AST 扫，
第 2 条的「哪些函数算归集」按「谁的代码里调了 `dividend_entries(`」算出来。
手抄的清单会漂，而漂的那一刻不会有人知道 —— 这正是本文件要防的东西。

本文件纯 Python、不 import django：读源码而已，不需要数据库，也不能进不了
`test_no_django_required` 那条链（整仓测试要在没有 Django 的子进程里也全绿）。
"""
import ast
import os
from pathlib import Path
import tempfile
import unittest

SERVER = Path(__file__).resolve().parents[3]
ANALYTICS = SERVER / "apps" / "analytics"
VIEWS = ANALYTICS / "views.py"

#: 归集这条线的根：其它「经由归集」的函数都由它推出来（见 `aggregation_routed_functions`）。
ROOT_AGGREGATION = "dividend_entries"


def stripped_module(path):
    """源码剥掉**注释与 docstring** 之后重新打印。

    剥这两样是必须的：注释里写一句「这里已经走归集了」就能骗过 `assertIn`，
    而那种断言在真正改回照明细表查之后依然是绿的。`ast.unparse` 只输出语法树上
    真有的东西。
    """
    tree = ast.parse(Path(path).read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            body = list(node.body)
            if (body and isinstance(body[0], ast.Expr)
                    and isinstance(body[0].value, ast.Constant)
                    and isinstance(body[0].value.value, str)):
                node.body = body[1:] or [ast.Pass()]
    return tree, ast.unparse(tree)


def analytics_modules():
    """`apps/analytics/` 下的业务模块（不含 tests / migrations）—— 自动扫，不手抄。"""
    return sorted(
        p for p in ANALYTICS.glob("*.py")
        if p.name != "__init__.py" and p.is_file()
    )


def functions_touching(tree, identifier):
    """语法树里代码中出现 `identifier` 的函数名 —— 自动发现。"""
    found = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if identifier in ast.unparse(node):
                found.add(node.name)
    return found


def aggregation_routed_functions(tree):
    """「本身经由归集函数」的函数名 —— 谁的代码里调了 `dividend_entries(` 就算。

    这样清单不用手抄：新写一个导出/汇总函数只要走归集，它就自动获得「出口可以调它」
    的资格；反过来，一个没走归集的函数**永远**进不了这份名单，视图调它就会被下面
    的审计判红。
    """
    return functions_touching(tree, f"{ROOT_AGGREGATION}(") | {ROOT_AGGREGATION}


def views_with_dividend_numbers(tree, sanctioned):
    """视图层里「碰股息」的类 —— 自动发现。

    判据两条任一：类里出现 `dividend` 字样，或者调了某个经由归集的函数
    （`build_summary` / `build_positions` 自己也在这份名单里，因为它们的数字就是从
    归集来的 —— 统计页的 `dividend_total` 正是这么出来的）。
    """
    found = {}
    for node in tree.body:
        if isinstance(node, ast.ClassDef):
            code = ast.unparse(node)
            if "dividend" in code.lower() or any(f"{name}(" in code for name in sanctioned):
                found[node.name] = code
    return found


def audit_dividend_exit(code, sanctioned):
    """审一个股息出口的源码，返回它踩的问题（空 = 合格）。"""
    problems = []
    if "DividendRecord" in code:
        problems.append(
            "直接查了股息明细表（DividendRecord）—— 只落流水的股息会整条消失，"
            "而「累计股息」把它们算进去了"
        )
    if not any(f"{name}(" in code for name in sanctioned):
        problems.append(
            "没有经由归集函数（%s 之一）—— 数字从别处来的，两种录入路径迟早对不上"
            % " / ".join(sorted(sanctioned))
        )
    return problems


class TestNoModuleReachesForTheDetailTable(unittest.TestCase):
    """第 1 条：`DividendRecord` 只许出现在 `services.py` 的那两个摘数据函数里。"""

    def setUp(self):
        self.touching = {
            path.name: functions_touching(stripped_module(path)[0], "DividendRecord")
            for path in analytics_modules()
        }

    def test_扫描面不是空的(self):
        """自证：文件清单真扫到了东西，否则下面的断言全是恒真。"""
        self.assertGreaterEqual(len(self.touching), 6, self.touching)
        self.assertIn("services.py", self.touching)
        self.assertIn("views.py", self.touching)

    def test_只有services在碰明细表(self):
        offenders = sorted(name for name, fns in self.touching.items() if fns)
        self.assertEqual(
            offenders, ["services.py"],
            "这些模块在代码里碰了股息明细表：%s\n"
            "股息有两条录入路径，照明细表取数会漏掉「只落流水」的那条。"
            "取数一律走 services.dividend_entries / dividend_entries_in_year。" % offenders,
        )

    def test_services里碰明细表的函数只有那两个(self):
        """第三个出现时，要么改成走归集，要么明确地把它加到这里 —— 别让它悄悄长出来。"""
        self.assertEqual(
            sorted(self.touching["services.py"]),
            ["dividend_entries", "dividend_monthly"],
            "services.py 里碰股息明细表的函数变了：%s\n"
            "`dividend_entries` 是唯一的归集入口；`dividend_monthly` 碰它是为了取"
            "gross/tax（明细表里才有的两列，口径写在它的 docstring 里）。"
            "新增的地方要么路由到归集函数，要么在这里点名说明为什么必须直接查。"
            % sorted(self.touching["services.py"]),
        )


class TestEveryDividendExitIsRouted(unittest.TestCase):
    """第 2 条：视图层的每个股息出口都得经由归集。"""

    def setUp(self):
        self.tree, self.code = stripped_module(VIEWS)
        _, services_code = stripped_module(ANALYTICS / "services.py")
        self.sanctioned = aggregation_routed_functions(ast.parse(services_code))
        self.exits = views_with_dividend_numbers(self.tree, self.sanctioned)

    def test_归集名单是自己长出来的(self):
        """自证：名单来自源码，不是手抄 —— 少了它，下面的审计会变成「怎么改都绿」。"""
        self.assertIn(ROOT_AGGREGATION, self.sanctioned)
        for name in ("dividend_entries_in_year", "build_positions", "build_summary",
                     "dividend_monthly"):
            self.assertIn(name, self.sanctioned)

    def test_发现面覆盖了四个出口(self):
        """自证：类是真被找出来的（重命名会在这里响，而不是让审计悄悄空转）。"""
        self.assertGreaterEqual(len(self.exits), 4, sorted(self.exits))
        for name in ("DividendAnalyticsView", "DividendExportView", "CalendarView",
                     "SummaryView"):
            self.assertIn(name, self.exits)

    def test_每个出口都合格(self):
        for name, code in sorted(self.exits.items()):
            with self.subTest(exit=name):
                self.assertEqual(audit_dividend_exit(code, self.sanctioned), [])

    def test_视图层一处都不碰明细表(self):
        self.assertNotIn("DividendRecord", self.code)

    def test_视图层不再自己写年份筛选(self):
        """`?year=` 的筛选只有一处（`services.dividend_entries_in_year`）。

        两个出口各筛各的，就会出现「图表按 2026 筛、日历按别的东西筛」——
        而用户是拿这两个数对账的。
        """
        self.assertNotIn("pay_date.year", self.code)

    # ---- 反向对照：这两条审计真的会响 ----

    def test_审计对旧的日历写法会报红(self):
        """旧 `CalendarView`（直接查明细表）照抄一段 —— 判别力证据。"""
        old = (
            "class CalendarView(APIView):\n"
            "    def get(self, request):\n"
            "        rows = DividendRecord.objects.filter(user=request.user)\n"
            "        return Response({'results': [{'id': r.id} for r in rows]})\n"
        )
        code = ast.unparse(ast.parse(old))
        problems = audit_dividend_exit(code, self.sanctioned)
        self.assertTrue(problems, "旧写法竟然判成合格")
        self.assertTrue(any("DividendRecord" in p for p in problems), problems)

    def test_审计对没走归集的出口会报红(self):
        """一个「碰股息但数字另有来源」的类也要被拦住。"""
        bad = (
            "class FooView(APIView):\n"
            "    def get(self, request):\n"
            "        return Response({'dividend_total': request.user.balance})\n"
        )
        code = ast.unparse(ast.parse(bad))
        problems = audit_dividend_exit(code, self.sanctioned)
        self.assertTrue(problems, "没走归集的出口竟然判成合格")
        self.assertTrue(any("归集" in p for p in problems), problems)

    def test_旧写法真的会被发现(self):
        """发现面也要能看见旧写法 —— 否则审计再好也轮不到它。"""
        old = ("class CalendarView(APIView):\n"
               "    def get(self, request):\n"
               "        return Response(DividendRecord.objects.count())\n")
        tree = ast.parse(old)
        exits = views_with_dividend_numbers(tree, self.sanctioned)
        self.assertIn("CalendarView", exits)

    def test_注释里写着已经走归集了也骗不过去(self):
        """docstring / 注释不算数：剥掉之后再判。"""
        fake = (
            "class FooView(APIView):\n"
            '    """这里的股息都来自 dividend_entries，放心。"""\n'
            "    def get(self, request):\n"
            "        # 走的是 dividend_entries(request.user)\n"
            "        return Response({})\n"
        )
        tmp = tempfile.NamedTemporaryFile("w", suffix=".py", encoding="utf-8",
                                          delete=False)
        self.addCleanup(os.unlink, tmp.name)
        with tmp:
            tmp.write(fake)
        _, code = stripped_module(tmp.name)
        self.assertNotIn("dividend_entries", code, "docstring/注释没被剥掉")
        self.assertTrue(audit_dividend_exit(code, self.sanctioned))


if __name__ == "__main__":
    unittest.main()
