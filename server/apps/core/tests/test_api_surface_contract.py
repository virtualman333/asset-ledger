# -*- coding: utf-8 -*-
"""README 的「主要接口」是**手抄**的接口清单，真值在 `config/urls.py` 加各 app 的
`urls.py` 里 —— 同一件事写两遍，就一定会漂。

手抄清单错起来是**安静**的
--------------------------
少列一条接口，读文档的人只会以为那功能不存在（于是重写一遍、或者干脆不做）；多列一条
不存在的接口，照着调只能撞 404，而且是在**写完一大段客户端代码之后**才撞上。两种都不报错。

两边确实已经漂了：`docs/DESIGN.md` 第 6 节的草案与实现早就对不上（草案写 `/positions/`，
实现是 `/analytics/positions/`；草案写 `/transactions/`，实现是 `/transactions/records/`），
README 这份漏掉了 `auth/me/` 与 `ingest/jobs/{id}/` —— 而 `jobs/{id}` 恰好是收件箱轮询
要用的那条。

这份检查把两边钉在一起
----------------------
- **正向**：根 urlconf 里每个显式 `path()` 声明、以及每个 router 注册的资源，都必须在
  README 的「主要接口」里出现。漏一条就红。
- **反向**：README 里写的每条路径都必须真实存在。凭空写一条就红。
- **根路由完整性**：`server/apps/*/urls.py` 每个文件都必须在根 urlconf 里 `include` 且
  只 include 一次。一个 app 有 urls.py 却没被挂上去，它的接口**存在但访问不到**，全程
  不报错，只有 404 —— 这是最像「代码明明写了」的那种故障。
- **动词**（本轮补的）：第一列那个 `GET/POST` 原先被直接扔掉。它现在对着
  `route_inventory.route_methods()` 推出来的真值核一遍：文档写了、服务端不接的动词，
  照着调只会撞 405 —— 与「路径写错撞 404」是同一类故障，只是更晚才发现。
- 客户端默认地址那句「默认已填」也是手抄的：同样钉在代码里的真值上。

为什么是解析源码、不是 import 路由表
------------------------------------
本仓库的测试承诺「不需要数据库、不需要 Django」（`test_no_django_required.py` 会拦掉
Django 再把整套跑一遍，要求 `OK (skipped=1)`），而开发机就是**裸 Python**（`import django`
直接 ModuleNotFoundError）。所以这里用 AST 读 `urls.py` 的源码：不 import django、不连库、
不 import 任何 app —— 于是它能待在「不需要 Django」的那一侧。

刻意收窄的地方（写下来，免得日后以为它管得比实际宽）
----------------------------------------------------
1. **动词这一面（本轮补的）只查一个方向：文档写了、服务端不接 → 红。** 服务端接了、
   文档没写 → 不查。理由在 `method_problems()` 的 docstring 里：文档列的是「主要」用法，
   反向查会逼着文档去抄框架行为（`/health/` 真值是五个动词全接 —— 因为裸函数视图
   就是任何动词都会被调用，给它报 `GET` 才是猜）。
   原文这里是「**只钉路径，不钉方法。** 方法是 ViewSet 由 mixin 组出来的 action，
   静态判不出来，硬判就变成猜」—— 前半句对、后半句错，推翻的记录见
   `route_inventory.route_methods()` 上面那一段。
2. **router 注册的资源只要求「集合 URL 被列出来」**，不要求把 DRF 自动生成的 `{id}/`
   明细路由逐条写进 README —— 那是框架产物，不是人写的契约。反向校验接受集合与明细
   两种形态，所以想把 `{id}/` 也列出来是允许的。
3. README 里那些「不用斜杠结尾」的紧凑写法（`/ingest/image|text`）会被补上结尾的 `/`，
   因为真值（Django 的 `path()`）全都带。

一条自己撞到的坑
----------------
README 用 `A|B` 压行（`/api/v1/auth/register|token|token/refresh`），展开时前缀要取
**第一个 `|` 之前最后一个 `/`**；取整个 token 就是错的（`register|token` 会被当成一段）。
这一点有反向对照盯着（见 `TestTheParserIsNotVacuous`）。

跑法（在 server/ 下）：python -m unittest discover -s apps -t .
"""
import re
import unittest
from pathlib import Path

from apps.core.client_inventory import declared_base_url  # noqa: E402
from apps.core.route_inventory import (  # noqa: E402
    BUSINESS_METHODS,
    ROOT,
    SERVER,
    all_routes,
    norm,
    real_routes,
    root_includes,
    route_methods,
    with_slash as _with_slash,
)

#: `SERVER` / `ROOT` 由 `apps.core.route_inventory` 算出来并在 import 时自证 ——
#: 定位这种事只该有一份算法，两份就是两份会漂的东西（路由解析器挪过去也是同一个理由）。
README = ROOT / "README.md"
DESIGN = ROOT / "docs" / "DESIGN.md"

#: README 里那份手抄清单所在的章节名。它同时是 `docs/DESIGN.md` 草案上那个指针的落点
#: —— 指针指向一个**真的存在**的章节，所以它不是一句空话。
API_SECTION_TITLE = "主要接口"

#: `## 主要接口` 紧跟的那个围栏代码块
API_SECTION_RE = re.compile(
    r"^##\s*" + API_SECTION_TITLE + r"[^\n]*\n+```[^\n]*\n(.*?)^```",
    re.M | re.S,
)

#: `docs/DESIGN.md` 第 6 节（API 草案）的正文
DRAFT_SECTION_RE = re.compile(r"^##\s*6\.[^\n]*\n(.*?)^##\s", re.M | re.S)

#: README 运行环境表里「本地模拟器」那一行
SIMULATOR_ROW_RE = re.compile(r"^\|\s*本地模拟器\s*\|(.+)\|\s*$", re.M)

#: markdown 里用反引号括起来的带协议头 URL
BACKTICK_URL_RE = re.compile(r"`([a-z][a-z0-9+.-]*://[^`\n]*)`")

#: `.ets` 里用单引号括起来的带协议头字符串字面量
URL_LITERAL_RE = re.compile(r"'([a-z][a-z0-9+.-]*://[^'\n]*)'")

#: 客户端默认后端地址的真值所在。它**只有这一处定义**（`DEFAULT_API_BASE_URL`），
#: `EntryAbility` import 它 —— 见下面 `TestClientDefaultUrlMatchesTheDocs`。
CLIENT_DEFAULT_SOURCES = {
    "ApiClient": ROOT / "harmony/entry/src/main/ets/common/ApiClient.ets",
    "EntryAbility": ROOT / "harmony/entry/src/main/ets/entryability/EntryAbility.ets",
}

#: 解析器在真仓库上至少要解出这么多条，否则说明扫描面塌了（下面有自证用例）
MIN_EXPLICIT_ROUTES = 12
MIN_DOCUMENTED_PATHS = 15


def expand_alternatives(token):
    """README 的紧凑写法 `A|B|C` 展开成多条路径。

    前缀取**第一个 `|` 之前最后一个 `/`**；每个备选都补上结尾的 `/`（真值全都带）。
    """
    path = token.split("?")[0]
    if "|" not in path:
        return [_with_slash(path)]
    head, tail = path.split("|", 1)
    cut = head.rfind("/") + 1
    prefix, first = head[:cut], head[cut:]
    return [_with_slash(prefix + alt) for alt in [first] + tail.split("|")]


def documented_routes(text=None):
    """README「主要接口」→ `{归一化路径: frozenset(动词)}`。

    第一列那个 `GET` / `GET/POST` 是**手抄的动词**，原先只有第二列被读走、第一列直接扔掉
    —— 「先钉路径，方法是 ViewSet 由 mixin 组出来的、静态判不出来」是本文件原来的说法。
    那句话这一轮被推翻了：基类与 mixin 都写在类声明上，方法完全推得出来
    （见 `route_inventory.route_methods()`），于是第一列不再有理由没人管。

    认不出的动词**抛异常**：`README` 里写了 `FETCH` 这种词，是文档错了，不是「跳过这一行」。
    """
    source = README.read_text(encoding="utf-8") if text is None else text
    block = API_SECTION_RE.search(source)
    if block is None:
        raise AssertionError(
            f"找不到「## {API_SECTION_TITLE}」紧跟的代码块 —— 这份契约的落点没了，"
            "正向断言会退化成恒真"
        )
    found = {}
    for line in block.group(1).splitlines():
        fields = line.split()
        # 一行形如 `GET/POST  /api/v1/xxx  说明`；只认第二格是路径的那些行
        if len(fields) < 2 or not fields[1].startswith("/api/"):
            continue
        verbs = parse_verbs(fields[0])
        for path in expand_alternatives(fields[1]):
            found.setdefault(norm(path), set()).update(verbs)
    return {path: frozenset(verbs) for path, verbs in found.items()}


def parse_verbs(token):
    """`GET/POST` → `frozenset({'get', 'post'})`。"""
    verbs = set()
    for piece in token.split("/"):
        low = piece.strip().lower()
        if low not in BUSINESS_METHODS:
            raise AssertionError(
                f"「主要接口」里有个不认识的动词 {piece!r}（只认 "
                f"{'/'.join(m.upper() for m in BUSINESS_METHODS)}）—— "
                "文档写错了，不该被当成「这一行跳过」"
            )
        verbs.add(low)
    return frozenset(verbs)


def documented_paths(text=None):
    """README「主要接口」里写到的全部路径（已归一）。"""
    return set(documented_routes(text))


def method_problems(documented, truth):
    """文档写的动词 vs 真值 → 人类可读的问题清单（空 = 契约成立）。

    只查一个方向：**文档写了、服务端不接** → 照着这份文档写代码只会撞 405，
    而且是写完一大段之后才撞上。反过来（服务端接了、文档没写）不查 ——
    文档列的是「主要」用法，`/health/` 那种谁调都行的路由真值是五个动词全接，
    反向查会逼着文档去抄框架行为。
    """
    problems = []
    for path, verbs in sorted(documented.items()):
        allowed = truth.get(path)
        if allowed is None:
            problems.append(
                f"{path}：真路由表里没有这条路径（第二列那一面另有检查，"
                "但这里不能装作没看见）"
            )
            continue
        bogus = verbs - allowed
        if bogus:
            problems.append(
                f"{path}：文档写 {'/'.join(sorted(v.upper() for v in verbs))}，"
                f"真值只接 {'/'.join(sorted(m.upper() for m in allowed))}"
                f"—— 多出来的 {'/'.join(sorted(m.upper() for m in bogus))} 照着调到只会撞 405"
            )
    return problems


def _documented_simulator_url():
    """README 运行环境表里「本地模拟器」一行的地址。"""
    row = SIMULATOR_ROW_RE.search(README.read_text(encoding="utf-8"))
    if row is None:
        raise AssertionError("README 的运行环境表里找不到「本地模拟器」那一行")
    urls = BACKTICK_URL_RE.findall(row.group(1))
    if len(urls) != 1:
        raise AssertionError(f"「本地模拟器」那行里的 URL 不是恰好一条：{urls!r}")
    return urls[0]


class TestReadmeApiListMatchesTheRoutes(unittest.TestCase):
    """手抄清单与真值必须逐条对上，两个方向都要管。"""

    @classmethod
    def setUpClass(cls):
        cls.explicit, cls.collections, cls.details = real_routes()
        cls.real = cls.explicit | cls.collections | cls.details
        cls.documented = documented_paths()

    def test_every_route_is_documented(self):
        missing = sorted((self.explicit | self.collections) - self.documented)
        self.assertEqual(
            missing, [],
            "有接口没写进 README 的「主要接口」：\n  "
            + "\n  ".join(missing)
            + "\n手抄的清单天生会漏；漏掉的那条，读文档的人只会以为这功能不存在。",
        )

    def test_no_documented_route_is_imaginary(self):
        extra = sorted(self.documented - self.real)
        self.assertEqual(
            extra, [],
            "README 写了不存在的接口：\n  "
            + "\n  ".join(extra)
            + "\n照着这份文档写客户端只能撞 404，而且是写完一大段之后才撞上。",
        )

    def test_the_scan_surface_is_intact(self):
        """扫描面自证：解析器真的走到了每个 app，而不是「啥都没解出来所以全绿」。"""
        self.assertGreaterEqual(
            len(self.explicit), MIN_EXPLICIT_ROUTES,
            f"只解出 {len(self.explicit)} 条显式路由 —— 解析器可能已经不认得 urls.py 的写法了",
        )
        self.assertGreaterEqual(
            len(self.documented), MIN_DOCUMENTED_PATHS,
            f"README 只解出 {len(self.documented)} 条路径 —— 解析器或文档块塌了",
        )
        for probe in ("api/v1/auth/me/", "api/v1/ingest/jobs/{id}/", "api/v1/health/"):
            self.assertIn(probe, self.explicit, f"解析器漏了 {probe}")

    def test_dropping_one_documented_line_would_be_caught(self):
        """反向对照：正向检查真的会红 —— 验「文档集合少一条真路由」这个情形。"""
        without_health = self.documented - {norm("api/v1/health/")}
        missing = (self.explicit | self.collections) - without_health
        self.assertIn(
            norm("api/v1/health/"), missing,
            "少了一条真路由却检不出来 —— 正向断言是恒真的",
        )

    def test_every_app_urls_file_is_mounted_exactly_once(self):
        """有 urls.py 却没被 include 的 app：接口存在但访问不到，且全程不报错。"""
        mounted = [m for _, m in root_includes() if m.startswith("apps.")]
        self.assertEqual(
            sorted(mounted), sorted(set(mounted)),
            "某个 app 的 urls 被 include 了不止一次（后一份会盖掉前一份）：%r" % (mounted,),
        )
        files = sorted(
            ".".join(p.relative_to(SERVER).with_suffix("").parts)
            for p in (SERVER / "apps").glob("*/urls.py")
        )
        self.assertEqual(
            sorted(mounted), files,
            "根 urlconf 与 apps/*/urls.py 对不上：\n  没被 include 的：%r\n  include 了不存在的：%r"
            % (sorted(set(files) - set(mounted)), sorted(set(mounted) - set(files))),
        )


class TestDocumentedMethodsMatchTheRoutes(unittest.TestCase):
    """README 第一列那个动词 ⇄ `route_inventory.route_methods()` 推出来的真值。

    这一面原先**根本不存在**：`documented_paths()` 读走第二格路径、把第一格 `GET/POST`
    扔掉，而它当时有一句看起来很合理的理由（「方法静态判不出来」）。既然推得出来，
    这条理由就没了 —— 而「文档给一条只读路由写了 POST」这种错的代价是：
    照文档写完一整个页面，才在真机上撞到 405。
    """

    @classmethod
    def setUpClass(cls):
        cls.documented = documented_routes()
        cls.truth = route_methods()

    def test_every_documented_method_is_really_accepted(self):
        problems = method_problems(self.documented, self.truth)
        self.assertEqual(
            problems, [],
            "README 的动词列与服务端真值对不上：\n  " + "\n  ".join(problems) +
            "\n改 README 的动词列（`route_inventory.route_methods()` 才是真值）——"
            "如果改的是服务端，记得顺手跑 `python scripts/check_routes.py`，"
            "让 Django 本体核一下推出来的方法集还对不对。",
        )

    def test_the_verb_column_was_actually_read(self):
        """扫描面自证：动词真的被解出来了，否则上面那条是恒真的。"""
        self.assertGreaterEqual(
            len(self.documented), MIN_DOCUMENTED_PATHS,
            f"只从「主要接口」里解出 {len(self.documented)} 条路径 —— 解析面塌了",
        )
        declared = set()
        for verbs in self.documented.values():
            declared |= verbs
        # 下限是 `GET`/`POST` 两种**都**出现过：这份清单列的是「主要」用法，
        # 只出现 `GET`（说明第一列整列没被读出来）或只出现 `POST`（说明读串了列）
        # 都会在这里现形。不要求五个动词全出现 —— PUT/PATCH/DELETE 不在「主要接口」里。
        self.assertTrue(
            {"get", "post"} <= declared,
            f"README 的动词列里只解出 {sorted(declared)}，`GET` 与 `POST` 没有都出现 —— "
            "要么解析器没读第一列，要么读串了列",
        )
        self.assertTrue(
            declared <= set(BUSINESS_METHODS),
            f"解出了不该出现的动词 {sorted(declared - set(BUSINESS_METHODS))}",
        )

    def test_the_truth_side_really_has_methods(self):
        """真值那边也不能是空的（两边都空同样会让上面那条恒真）。"""
        self.assertGreaterEqual(
            len(self.truth), len(self.documented),
            "真值里的路径比文档还少 —— 两张表已经不是同一批东西了",
        )
        empty = sorted(path for path, methods in self.truth.items() if not methods)
        self.assertEqual(
            empty, [],
            "这些路由一个业务动词都不接：%r\n一条谁调都 405 的路由，要么是基类表没命中"
            "（那就补表），要么这条路由本身就是坏的。" % (empty,),
        )

    def test_the_two_truth_tables_share_the_same_keys(self):
        """`route_methods()` 与 `all_routes()` 必须**同键**。

        差集里那一条的表现是调用方 `.get(path)` 拿到 `None`，而「查不到就跳过」正是
        静默漏过的入口 —— 所以这件事单独钉住，不靠两个消费者各自小心。
        """
        self.assertEqual(
            sorted(set(self.truth) ^ all_routes()), [],
            "方法表与路径表对不上：\n  只在方法表里：%r\n  只在路径表里：%r"
            % (sorted(set(self.truth) - all_routes()),
               sorted(all_routes() - set(self.truth))),
        )


class TestTheParserIsNotVacuous(unittest.TestCase):
    """反向对照：解析器喂给已知形态的样本，该看的必须看到、该炸的必须炸。"""

    def test_alternatives_expand_the_way_the_readme_writes_them(self):
        self.assertEqual(
            expand_alternatives("/api/v1/auth/register|token|token/refresh"),
            ["/api/v1/auth/register/", "/api/v1/auth/token/", "/api/v1/auth/token/refresh/"],
        )
        self.assertEqual(
            expand_alternatives("/api/v1/ingest/drafts/{id}/confirm|discard"),
            ["/api/v1/ingest/drafts/{id}/confirm/", "/api/v1/ingest/drafts/{id}/discard/"],
        )
        self.assertEqual(
            expand_alternatives("/api/v1/market/quotes/?asset_ids=1,2&refresh=1"),
            ["/api/v1/market/quotes/"],
        )

    def test_placeholders_normalise_both_ways(self):
        """真值写 `<int:pk>`、文档写 `{id}`；文档带前导 `/`、真值不带 —— 都要归一。"""
        self.assertEqual(norm("/api/v1/ingest/jobs/<int:pk>/"), "api/v1/ingest/jobs/{id}/")
        self.assertEqual(norm("api/v1/ingest/jobs/{id}/"), "api/v1/ingest/jobs/{id}/")

    def test_a_missing_section_raises_instead_of_passing_silently(self):
        """落点没了必须炸 —— 悄悄返回空集合会让正向断言变成恒真。"""
        with self.assertRaises(AssertionError) as ctx:
            documented_paths("# 标题\n\n没有那个章节\n")
        self.assertIn(API_SECTION_TITLE, str(ctx.exception))

    def test_a_shrunk_section_is_detected(self):
        """样本量缩水要被看见：只留一行时就只解出一行，而不是「照样全绿」。"""
        sample = "# t\n\n## %s\n\n```\nGET /api/v1/health/ 自证\n```\n" % API_SECTION_TITLE
        self.assertEqual(documented_paths(sample), {"api/v1/health/"})

    def test_rows_without_a_path_are_ignored(self):
        """块里的说明行/空行不该被当成接口。"""
        sample = (
            "# t\n\n## %s\n\n```\n\n这是说明行\nGET /api/v1/health/ 自证\n```\n"
            % API_SECTION_TITLE
        )
        self.assertEqual(documented_paths(sample), {"api/v1/health/"})

    def test_the_verb_column_is_read_not_skipped(self):
        """★ 动词列真的被读走了 —— 而且是按 `|` 展开到每一条路径上的。"""
        sample = (
            "# t\n\n## %s\n\n```\nGET/POST /api/v1/accounts/  账户\n"
            "GET  /api/v1/analytics/positions|summary 统计\n```\n" % API_SECTION_TITLE
        )
        self.assertEqual(
            documented_routes(sample),
            {
                "api/v1/accounts/": frozenset({"get", "post"}),
                "api/v1/analytics/positions/": frozenset({"get"}),
                "api/v1/analytics/summary/": frozenset({"get"}),
            },
            "动词列没被读出来，或没跟着 `|` 展开到每一条路径上",
        )

    def test_an_unknown_verb_blows_up_instead_of_being_skipped(self):
        """★ 认不出的动词必须炸 —— 「不认识就跳过这一行」等于把整列变成可有可无。"""
        sample = "# t\n\n## %s\n\n```\nFETCH /api/v1/health/ 自证\n```\n" % API_SECTION_TITLE
        with self.assertRaises(AssertionError) as ctx:
            documented_routes(sample)
        self.assertIn("FETCH", str(ctx.exception))

    def test_a_documented_method_that_does_not_exist_is_reported(self):
        """★ 负向对照：对账判据真的会报「文档写了、服务端不接」这个情形。

        喂的是**编的**真值，不是仓库当前的 —— 这样这条用例不依赖任何一条真实路由
        恰好只读，也就不存在「哪天路由改了、这条对照自己失效」。
        """
        truth = {"api/v1/analytics/summary/": frozenset({"get"})}
        self.assertEqual(
            method_problems({"api/v1/analytics/summary/": frozenset({"get"})}, truth), [],
            "文档与真值一致时不该报任何问题",
        )
        problems = method_problems({"api/v1/analytics/summary/": frozenset({"post"})}, truth)
        self.assertTrue(problems, "文档给一条只读路由写了 POST 却没被报出来 —— 这条对账是空的")
        self.assertIn("405", " ".join(problems))
        # 文档写了真值里压根没有的路径：不能装作没看见（另一条检查管它是另一回事）
        self.assertTrue(
            method_problems({"api/v1/definitely-not-a-route/": frozenset({"get"})}, truth),
            "文档写了真值里没有的路径却没被报出来",
        )


class TestTheDraftIsMarkedAsSuperseded(unittest.TestCase):
    """`docs/DESIGN.md` 第 6 节也是一份手抄的接口清单，而且早就漂了（草案写 `/positions/`、
    `/transactions/`，实现是 `/analytics/positions/`、`/transactions/records/`）。

    它是 M0 的设计记录，**不该被改写成今天的接口** —— 那是历史。但两份清单长得一样权威，
    就会有人照着错的那份写代码。所以草案上必须留一个指向当前清单的指针，而那个指针的落点
    就是 README 的「主要接口」章节：上面那份检查正钉着它。
    """

    def test_the_draft_points_at_the_authoritative_list(self):
        block = DRAFT_SECTION_RE.search(DESIGN.read_text(encoding="utf-8"))
        self.assertIsNotNone(block, "DESIGN.md 里找不到第 6 节（API 草案）")
        body = block.group(1)
        self.assertIn(
            API_SECTION_TITLE, body,
            "草案上没有指向 README「主要接口」的指针 —— 两份清单长得一样权威，"
            "总会有人照着错的那份写代码",
        )
        self.assertIn(
            "test_api_surface_contract", body,
            "指针没有落到具体的检查文件上：那样它只是一句话，没人知道「以这份为准」是谁在保证",
        )

    def test_the_pointer_points_at_something_that_exists(self):
        """指针的落点必须真的在 —— 否则它是空指针。"""
        self.assertIsNotNone(
            API_SECTION_RE.search(README.read_text(encoding="utf-8")),
            "README 里没有「## %s」这一节，DESIGN.md 的指针就落空了" % API_SECTION_TITLE,
        )

    def test_the_pointer_detector_is_not_vacuous(self):
        """反向对照：草案切片正则和指针检测本身要有效（没有指针的样本判不出「有」）。"""
        sample = "# t\n\n## 6. API 草案\n\n```\nGET /positions/\n```\n\n## 7. x\n"
        block = DRAFT_SECTION_RE.search(sample)
        self.assertIsNotNone(block, "第 6 节的切片正则失效了")
        self.assertIn("GET /positions/", block.group(1))
        self.assertNotIn(API_SECTION_TITLE, block.group(1))


class TestClientDefaultUrlMatchesTheDocs(unittest.TestCase):
    """README 说模拟器地址「默认已填」。真值在客户端的 `DEFAULT_API_BASE_URL` 里。

    这一条**上一轮是「两份一致」的检查**：那个字面量在 `ApiClient.getBaseUrl()` 的兜底
    分支和 `EntryAbility.onCreate()` 里各写了一遍，而本机没有 DevEco / hvigor 工具链，
    改 `.ets` 无法自测（改坏了只有真机跑起来才知道），所以当时先把两边钉住、并在 README
    的「已知约束」里写明该怎么收敛。这一轮按那条指示收敛了：常量在 `ApiClient.ets` 里
    `export`，`EntryAbility.ets` import 它。

    于是**「两份一致」不再是需要被检查的事** —— 只有一处可改。剩下的两条断言是：
    README 说的地址与那一处一致；`EntryAbility` 里不许再长出第二个 URL 字面量
    （那正是这次要消灭的形状）。
    """

    @classmethod
    def setUpClass(cls):
        cls.literals = {
            name: URL_LITERAL_RE.findall(path.read_text(encoding="utf-8"))
            for name, path in CLIENT_DEFAULT_SOURCES.items()
        }
        cls.entry_ability = CLIENT_DEFAULT_SOURCES["EntryAbility"].read_text(encoding="utf-8")

    def test_the_documented_default_is_the_one_the_client_uses(self):
        documented = _documented_simulator_url()
        self.assertEqual(
            declared_base_url(), documented,
            "README 说的默认地址与客户端的 DEFAULT_API_BASE_URL 不一致：\n"
            "  README: %r\n  客户端: %r\n" % (documented, declared_base_url()),
        )

    def test_the_default_address_is_declared_exactly_once(self):
        """收敛之后，全客户端只该有**一个**带协议头的字面量，就在 ApiClient.ets 里。

        `declared_base_url()` 自己会在「不是恰好一处」时抛异常（找不到 ≠ 找到了对的），
        这里再对字面量总数同一件事收一遍：`EntryAbility` 里再冒出第二个 URL，
        本条直接红 —— 那是「同一件事写两处」这个形状又回来了。
        """
        declared_base_url()  # 声明恰好一处，否则它自己先炸
        self.assertEqual(
            self.literals["EntryAbility"], [],
            "EntryAbility 里又出现了 URL 字面量：%r\n"
            "默认地址的唯一定义在 `common/ApiClient.ets` —— 这边应当 import "
            "`DEFAULT_API_BASE_URL`，而不是再抄一遍。" % (self.literals["EntryAbility"],),
        )

    def test_entry_ability_really_uses_that_constant(self):
        """光把字面量删掉不算收敛：得**真的把它当值用上**。

        第一版写的是 `assertIn("DEFAULT_API_BASE_URL", text)` —— 负向验证当场证明它太松：
        把 `AppStorage.setOrCreate` 那一行的值换回字面量，上面那行 `import` 里还留着
        这个名字，于是它照样绿。提到名字不等于用了它（删掉值、只留 import 也是「提到」），
        所以这里直接钉住那句赋值。
        """
        self.assertRegex(
            self.entry_ability,
            r"AppStorage\s*\.\s*setOrCreate<string>\s*\(\s*'apiBaseUrl'\s*,\s*DEFAULT_API_BASE_URL\s*\)",
            "EntryAbility 没有把 DEFAULT_API_BASE_URL 真的写进 ApiClient 读的那个键 —— "
            "把字面量删掉却没接上单一来源，等于把默认地址弄丢了"
            "（AppStorage 里 'apiBaseUrl' 会一直是空的）。",
        )
        self.assertIn(
            "../common/ApiClient", self.entry_ability,
            "EntryAbility 没有从 ../common/ApiClient 把常量 import 进来。",
        )


if __name__ == "__main__":
    unittest.main()
