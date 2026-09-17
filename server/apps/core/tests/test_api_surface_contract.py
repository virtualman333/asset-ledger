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
- 客户端默认地址那句「默认已填」也是手抄的：同样钉在代码里的真值上。

为什么是解析源码、不是 import 路由表
------------------------------------
本仓库的测试承诺「不需要数据库、不需要 Django」（`test_no_django_required.py` 会拦掉
Django 再把整套跑一遍，要求 `OK (skipped=1)`），而开发机就是**裸 Python**（`import django`
直接 ModuleNotFoundError）。所以这里用 AST 读 `urls.py` 的源码：不 import django、不连库、
不 import 任何 app —— 于是它能待在「不需要 Django」的那一侧。

刻意收窄的地方（写下来，免得日后以为它管得比实际宽）
----------------------------------------------------
1. **只钉路径，不钉方法。** README 写的是 `GET/POST /api/v1/accounts/`，这里只校验
   `api/v1/accounts/` 存在。方法是 ViewSet 由 mixin 组出来的 action，静态判不出来，
   硬判就变成猜。
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
import ast
import re
import unittest
from pathlib import Path

#: 本文件在 `server/apps/core/tests/` 下 —— 往上第三层才是 `server/`（跑测试的 cwd）
SERVER = Path(__file__).resolve().parents[3]
assert (SERVER / "manage.py").exists(), f"算错了 server 根：{SERVER} 下没有 manage.py"

#: 仓库根
ROOT = SERVER.parent
assert (ROOT / "README.md").exists(), f"算错了仓库根：{ROOT} 下没有 README.md"

README = ROOT / "README.md"
DESIGN = ROOT / "docs" / "DESIGN.md"
ROOT_URLCONF = SERVER / "config" / "urls.py"

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

#: 客户端默认后端地址的真值所在（同一个字面量现在写了两遍）
CLIENT_DEFAULT_SOURCES = {
    "ApiClient": ROOT / "harmony/entry/src/main/ets/common/ApiClient.ets",
    "EntryAbility": ROOT / "harmony/entry/src/main/ets/entryability/EntryAbility.ets",
}

#: 解析器在真仓库上至少要解出这么多条，否则说明扫描面塌了（下面有自证用例）
MIN_EXPLICIT_ROUTES = 12
MIN_DOCUMENTED_PATHS = 15


def _call_name(node):
    """`path(...)` → `'path'`；`admin.site.urls` → `'urls'`；其它 → None。"""
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return None


def _str_const(node):
    """字符串字面量取原值，其它一律 None（f-string、拼接都不认 —— 认了就变成猜）。"""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def root_includes():
    """根 urlconf 里的 `(前缀, 目标模块)`，只认 `path("...", include("a.b.urls"))`。"""
    tree = ast.parse(ROOT_URLCONF.read_text(encoding="utf-8"))
    found = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or _call_name(node.func) != "path":
            continue
        if len(node.args) < 2:
            continue
        prefix = _str_const(node.args[0])
        target = node.args[1]
        if prefix is None or not isinstance(target, ast.Call):
            continue
        if _call_name(target.func) != "include" or not target.args:
            continue
        module = _str_const(target.args[0])
        if module is not None:
            found.append((prefix, module))
    return found


def app_urls_file(module):
    """`apps.users.urls` → `server/apps/users/urls.py`"""
    return SERVER / Path(*module.split(".")).with_suffix(".py")


def parse_app_urls(path):
    """某个 app 的 `urls.py`：返回 `(显式 path 的子路径, router 注册的前缀)`。"""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    explicit, routers = [], []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not node.args:
            continue
        name = _call_name(node.func)
        sub = _str_const(node.args[0])
        if sub is None:
            continue
        if name == "path":
            explicit.append(sub)
        elif name == "register":
            routers.append(sub)
    return explicit, routers


def _with_slash(path):
    return path if path.endswith("/") else path + "/"


def norm(path):
    """把路径归一成「同一件事的同一个写法」：去掉查询串与前导 `/`、占位段统一成 `{id}`。

    真值写 `<int:pk>`、文档写 `{id}`；文档写 `/api/v1/...`、根 urlconf 写 `api/v1/...`
    —— 不归一就会把同一段路径判成两条，正向和反向各红一片假缺陷。
    """
    cleaned = path.split("?")[0].lstrip("/")
    cleaned = re.sub(r"<[^>]*>", "{id}", cleaned)
    cleaned = re.sub(r"\{[^}]*\}", "{id}", cleaned)
    return cleaned


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


def documented_paths(text=None):
    """README「主要接口」里写到的全部路径（已归一）。

    章节或代码块没了会**抛异常**而不是返回空集合 —— 返回空集合会让正向断言变成恒真。
    """
    source = README.read_text(encoding="utf-8") if text is None else text
    block = API_SECTION_RE.search(source)
    if block is None:
        raise AssertionError(
            f"找不到「## {API_SECTION_TITLE}」紧跟的代码块 —— 这份契约的落点没了，"
            "正向断言会退化成恒真"
        )
    found = set()
    for line in block.group(1).splitlines():
        fields = line.split()
        # 一行形如 `GET  /api/v1/xxx  说明`；只认第二格是路径的那些行
        if len(fields) < 2 or not fields[1].startswith("/api/"):
            continue
        found.update(norm(p) for p in expand_alternatives(fields[1]))
    return found


def real_routes():
    """`(显式 path, router 集合 URL, router 明细 URL)`，三份都带 `/api/` 前缀且已归一。"""
    explicit, collections, details = set(), set(), set()
    for prefix, module in root_includes():
        if not prefix.startswith("api/"):
            continue  # `admin/` 不是面向客户端的接口面
        path = app_urls_file(module)
        if not path.exists():
            raise AssertionError(f"根 urlconf include 了一个不存在的模块：{module} → {path}")
        subs, routers = parse_app_urls(path)
        for sub in subs:
            explicit.add(norm(prefix + sub))
        for registered in routers:
            collection = _with_slash(prefix + registered)
            collections.add(norm(collection))
            details.add(norm(collection + "{id}/"))
    return explicit, collections, details


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
    """README 说模拟器地址「默认已填」。真值在两个 `.ets` 文件里：`ApiClient.getBaseUrl()`
    的兜底字面量，与 `EntryAbility.onCreate()` 预置进 `AppStorage` 的那个。

    同一件事在客户端写了两遍。本机没有 DevEco / hvigor 工具链，改 `.ets` 无法自测
    （改坏了只有真机跑起来才知道），所以这一轮不收敛它，先把两边钉在一起：谁改了一处、
    忘了另一处，这条就红，并直接告诉你该收敛成单一常量。
    """

    @classmethod
    def setUpClass(cls):
        cls.literals = {
            name: URL_LITERAL_RE.findall(path.read_text(encoding="utf-8"))
            for name, path in CLIENT_DEFAULT_SOURCES.items()
        }

    def test_the_documented_default_is_the_one_the_client_uses(self):
        documented = _documented_simulator_url()
        self.assertEqual(
            self.literals["ApiClient"], [documented],
            "README 说的默认地址与 ApiClient.getBaseUrl() 的兜底值不一致：\n  README: %r\n"
            "  ApiClient: %r\n" % (documented, self.literals["ApiClient"]),
        )

    def test_the_two_copies_agree(self):
        self.assertEqual(
            self.literals["EntryAbility"], self.literals["ApiClient"],
            "同一个默认地址在 EntryAbility 与 ApiClient 里写成了不同的值：\n"
            "  EntryAbility: %r\n  ApiClient: %r\n"
            "该收敛成一个常量了 —— 在 `ApiClient.ets` 里 export 一个 `DEFAULT_API_BASE_URL`，"
            "`EntryAbility.ets` import 它。注意本机没有鸿蒙工具链，改完要在 DevEco 里过一遍编译。"
            % (self.literals["EntryAbility"], self.literals["ApiClient"]),
        )

    def test_the_scan_surface_is_intact(self):
        for name, hits in self.literals.items():
            self.assertEqual(
                len(hits), 1,
                f"{name} 里带协议头的字面量不是恰好一条：{hits!r} —— 提取规则该跟着改了",
            )


if __name__ == "__main__":
    unittest.main()
