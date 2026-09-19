# -*- coding: utf-8 -*-
"""客户端 `.ets` 打出去的每个接口路径，都必须在服务端路由表里真实存在。

为什么要有它
------------
本仓的客户端契约只有两条（README「已知约束」自己写着）：客户端页面清单 / import 落点 /
路由登记（`test_client_pages_contract.py`），以及默认后端地址两处一致
（`test_api_surface_contract.py`）。**这两条都没覆盖到「客户端打的接口到底存不存在」** ——
而这一面恰好是最看不见的一面：

  - 本机没有 DevEco / hvigor 工具链，`.ets` **编译不了**，类型错误在这台机器上不存在；
  - 就算在真机上跑，路径写错的后果是**运行期 404**：请求打出去了、返回了个错误、
    界面上一句「加载失败」，而没有任何一条自测红过；
  - 更糟的是接口改名：服务端把 `/transactions/` 收敛成 `/transactions/records/` 时，
    代码侧一切正常，只有真机上那个页面永远转圈。

真值不是我抄的，是 `apps.core.route_inventory` 从 `config/urls.py` 与各 app 的
`urls.py` 里读出来的 —— 与 README 那份手抄清单用的是**同一个**解析器（这次特意把它
从测试文件里挪了出来：抄第二份解析器，就是把「两份会漂的东西」又造了一遍）。

只钉一个方向，这一点是刻意的
----------------------------
客户端打的路径 → 必须存在（不存在的会 404，是缺陷）。
服务端存在的路由 → 客户端有没有打，**不查**：一个接口给别的客户端用、或者还没有页面
接入（M5 里就有一批），都不是缺陷。写下来是为了日后别把它当漏掉的检查补上，
补上只会得到一张需要长期维护的例外表。

第四面：动词（本轮补的）
------------------------
上一轮钉的是「路径存在」，但**路径存在不等于这个动词能用** ——
`ApiClient.post('/analytics/summary/')` 里路径是真的、方法不是，真机上收到的是 405，
界面同样只显示一句「加载失败」，而这台机器上没有鸿蒙工具链、`.ets` 编译不了。
所以动词也得核：真值来自 `route_inventory.route_methods()`（它把视图类、基类与 mixin
静态推成方法集，并与 Django 本体的 `callback.actions` / `view.cls` 对过账 ——
见 `scripts/check_routes.py`）。

解析面怎么认
------------
`.ets` 不是 Python，AST 用不上，只能按文本认。这里用**两遍扫描对账**，抄的是
`test_client_pages_contract.py` 对 import 那四种写法的做法：

  - **宽扫**：任何 `ApiClient.<动词><…>(` 的调用形态（泛型、跨行都算）；
  - **窄扫**：只认紧跟其后的**字面量**首参（单引号 / 双引号 / 模板串）；
  - 某个文件里宽扫有、窄扫一条都没有 → 红。那说明这个文件在用第五种写法
    （变量、拼接、函数返回……），而**看不见的调用等于没检查**。宁可在这里吵一声，
    也不要静默漏过 —— 「不会响的检查」比「没有检查」更坏。
"""
import re
import unittest

from apps.core.client_inventory import client_sources, declared_base_url
from apps.core.route_inventory import all_routes, norm, route_methods

#: 会**带路径**的封装方法。`send()` 是内部实现（首参恒为变量 `path`），
#: `getBaseUrl()` / `getStore()` / `login()` / `register()` 是包装层，都不在这个清单里。
CLIENT_VERBS = ("get", "post", "put", "patch", "delete")

_VERBS = "|".join(CLIENT_VERBS)

#: 宽扫：认得出调用的**存在**，不要求首参是字面量。
#: `\b` 让 `ApiClient.getBaseUrl()` 落选 —— `get` 后面紧跟 `B`，两边都是词字符，
#: 没有边界。漏掉这个 `\b`，`getBaseUrl` 会被当成「一个我读不出路径的 get 调用」。
CALL_ANY_RE = re.compile(r"ApiClient\s*\.\s*(?:" + _VERBS + r")\b\s*[<(]", re.M)

#: 窄扫：`ApiClient.<动词>[<泛型>]( '<字面量>'`
#: 泛型用 `[^(\n]*` 吞掉（`<ListResponse<AccountItem>>` 这种嵌套 `>` 是常态，
#: 用 `<[^>]*>` 会在第一个 `>` 上停住，然后死在多出来的那个 `>` 上）。
#: 动词与引号都用**命名组**：本轮多了一个动词组，靠 `\1` / `\2` 数序号是下一次改正则时
#: 必踩的坑（数错一位就会把路径和动词悄悄对调，而它照样能跑）。
CALL_LITERAL_RE = re.compile(
    r"ApiClient\s*\.\s*(?P<verb>" + _VERBS + r")\b[^(\n]*\(\s*(?P<q>['\"`])(?P<path>[^'\"`]*)(?P=q)",
    re.M,
)

#: 解析器在真仓库上至少要解出这么多条路径，否则说明扫描面塌了
MIN_CLIENT_PATHS = 12

#: 至少要解出这么多**带动词的调用**（同一路径用两个动词算两条）
MIN_CLIENT_CALLS = 12

#: 客户端所有调用都是相对基址的（`ApiClient.send()` 拼的是 `${getBaseUrl()}${path}`），
#: 而基址以 `/api/v1` 结尾 —— 所以拿客户端的字面量去比服务端路由时，得先补这个前缀。
#: **这个前缀不是猜的**：下面 `test_the_base_url_prefix_is_not_guessed` 会去
#: `ApiClient.ets` 里把基址读出来核对。改基址忘了这条，那条会红。
API_PREFIX = "api/v1/"

#: 一定要解出来的几条（覆盖到三种形态：显式 path、router 集合、模板串里的占位段）
PROBES = (
    "api/v1/analytics/summary/",
    "api/v1/ingest/drafts/{id}/confirm/",
    "api/v1/transactions/records/",
)


def client_calls(text):
    """窄扫：这个文件里以字面量形式打出去的调用 → `[(动词, 补过前缀并归一的路径)]`。"""
    return [
        (match.group("verb"), API_PREFIX + norm(match.group("path")))
        for match in CALL_LITERAL_RE.finditer(text)
    ]


def client_paths(text):
    """只要路径那一半（老用例还在用）。"""
    return [path for _verb, path in client_calls(text)]


def method_mismatches(calls_by_file, methods):
    """`{文件: [(动词, 路径)]}` vs 真值 → 人类可读的问题清单（空 = 契约成立）。

    单独立成函数是为了能拿**编的**真值给它做负向对照 —— 挂在用例体里就只能靠改仓库
    来验它会不会响，而那种验法会随着路由变化自己失效。
    """
    problems = []
    for rel, calls in sorted(calls_by_file.items()):
        for verb, path in calls:
            allowed = methods.get(path)
            if allowed is None:
                # 路径那一面另有检查；但这里不能「查不到就跳过」——
                # 跳过会让这条断言在路径全写错的时候变成全绿
                problems.append(f"{rel}: {verb.upper()} {path} —— 真路由表里没有这条路径")
                continue
            if verb not in allowed:
                problems.append(
                    f"{rel}: {verb.upper()} {path} —— 这条路由只接 "
                    f"{'/'.join(sorted(m.upper() for m in allowed))}（真机上 405）"
                )
    return problems


class TestClientCallsOnlyRealRoutes(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.sources = client_sources()
        cls.calls = {}
        for rel, text in cls.sources:
            cls.calls[rel] = client_calls(text)
        cls.paths = {rel: [path for _v, path in calls] for rel, calls in cls.calls.items()}
        cls.used = {p for paths in cls.paths.values() for p in paths}
        cls.real = all_routes()
        cls.methods = route_methods()

    def test_the_scan_surface_is_intact(self):
        """扫描面自证：文件找齐了、路径解出足够的条数、几个已知形态都在。

        这条必须排在别的断言前面起作用：扫描面一塌（目录改位置、正则不认新写法），
        下面那条「每个路径都存在」会**全绿**——空集合里没有不存在的路径。
        """
        self.assertGreaterEqual(len(self.sources), 9, f"只扫到 {len(self.sources)} 个 .ets")
        self.assertIn("harmony/entry/src/main/ets/common/ApiClient.ets",
                      [rel for rel, _ in self.sources])
        self.assertGreaterEqual(
            len(self.used), MIN_CLIENT_PATHS,
            f"只解出 {len(self.used)} 条客户端路径 —— 正则可能已经不认得现在的写法了",
        )
        pairs = {(verb, path) for calls in self.calls.values() for verb, path in calls}
        self.assertGreaterEqual(
            len(pairs), MIN_CLIENT_CALLS,
            f"只解出 {len(pairs)} 处「动词 + 路径」的调用 —— 动词那一组多半没配对",
        )
        verbs = {verb for verb, _ in pairs}
        self.assertTrue(
            verbs <= set(CLIENT_VERBS),
            f"解出了不在 CLIENT_VERBS 里的动词 {sorted(verbs - set(CLIENT_VERBS))} —— 正则有鬼",
        )
        self.assertTrue(
            verbs - {"get"},
            "客户端全是 GET —— 动词那一组多半恒定取到了同一个值（`get`），下面那条会同义反复",
        )
        for probe in PROBES:
            self.assertIn(probe, self.used, f"解析器漏了 {probe}")

    def test_the_base_url_prefix_is_not_guessed(self):
        """`API_PREFIX` 是「客户端字面量 → 服务端路由」这一步的桥。

        桥的两头都得是真的：客户端的基址必须以 `/api/v1` 结尾（否则 `client_paths`
        补出来的前缀就是编的，整个检查会变成一条恒不成立或恒成立的断言）。
        """
        base = declared_base_url()
        self.assertTrue(
            base.endswith("/" + API_PREFIX.rstrip("/")),
            f"客户端的默认基址是 {base!r}，不以 /{API_PREFIX.rstrip('/')} 结尾 —— "
            "这个文件里补前缀的那一步就不再成立了。",
        )

    def test_every_client_call_uses_a_method_the_route_accepts(self):
        """**第四面**：路径存在还不够，动词也得真被这条路由接。

        `ApiClient.post('/analytics/summary/')` 里路径是真的、方法不是 —— 真机上收到 405，
        界面同样只显示一句「加载失败」。这一面的真值来自
        `route_inventory.route_methods()`（静态推出的方法集，已与 Django 本体对过账）。
        """
        problems = method_mismatches(self.calls, self.methods)
        self.assertEqual(
            problems, [],
            "客户端用了路由接不了的动词（真机上 405，界面只会显示「加载失败」）：\n  "
            + "\n  ".join(problems) +
            "\n要么改客户端的动词，要么改服务端视图 —— 改完服务端记得跑 "
            "`python scripts/run_checks.py`，让 Django 本体核一遍推出来的方法集。",
        )

    def test_every_client_path_exists_on_the_server(self):
        missing = sorted(self.used - self.real)
        self.assertEqual(
            missing, [],
            "客户端打了服务端不存在的接口：\n  " + "\n  ".join(missing) +
            "\n本机没有鸿蒙工具链，`.ets` 编译不了 —— 这类错只会在真机上以 404 现形，"
            "而那时代码看起来完全正常。",
        )

    def test_every_file_says_every_path_as_a_literal(self):
        """**逐文件对账：宽扫命中数必须等于窄扫命中数。**

        第一版写的是「这个文件窄扫一条都没读出来才算问题」—— 负向验证当场证明它太松：
        把其中一个调用的路径换成变量，文件里还留着别的字面量，于是它照样绿。
        一个文件里只要**有一处**读不出的调用，那一处就是没被检查的调用，
        不该因为同文件别处读得出来而被放过。

        宁可在这里吵一声，也不要静默漏过：看不见的调用等于没检查。
        """
        problems = []
        for rel, text in self.sources:
            wide = len(CALL_ANY_RE.findall(text))
            narrow = len(CALL_LITERAL_RE.findall(text))
            if wide != narrow:
                problems.append(
                    f"{rel}: 有 {wide} 处 ApiClient 调用，其中只有 {narrow} 处带字面量路径"
                )
        self.assertEqual(
            problems, [],
            "这些文件里有读不出路径的调用：\n  " + "\n  ".join(problems) +
            "\n首参可能是变量 / 拼接 / 函数返回值。要么改成字面量，要么把这种写法教给解析器 ——"
            "现在的状态是「它没被检查」，不是「它没问题」。",
        )


class TestTheParserIsNotVacuous(unittest.TestCase):
    """反向对照：喂已知形态的样本，该看的必须看到、该炸的必须炸。"""

    def test_reads_every_literal_form_the_client_uses(self):
        self.assertEqual(client_paths("ApiClient.get<Summary>('/analytics/summary/');"),
                         ["api/v1/analytics/summary/"])
        self.assertEqual(client_paths('ApiClient.post("/assets/", {});'),
                         ["api/v1/assets/"])
        self.assertEqual(client_paths("ApiClient.get<ListResponse<AssetItem>>(`/assets/?search=x`);"),
                         ["api/v1/assets/"])
        self.assertEqual(client_paths("ApiClient.post(`/ingest/drafts/${id}/discard/`, {});"),
                         ["api/v1/ingest/drafts/{id}/discard/"])
        # 跨行：`(` 与字面量之间可以断行
        self.assertEqual(client_paths("const r = await ApiClient.get<A>(\n  '/accounts/'\n);"),
                         ["api/v1/accounts/"])

    def test_ignores_calls_that_carry_no_path(self):
        """包装层与内部实现不该被算成「一条路径」。"""
        for text in (
            "ApiClient.getBaseUrl();",
            "ApiClient.getStore();",
            "ApiClient.send<T>(path, http.RequestMethod.GET);",
            "ApiClient.token.length > 0",
        ):
            with self.subTest(text=text):
                self.assertEqual(client_paths(text), [])

    def test_the_wide_scan_still_notices_such_calls(self):
        """但宽扫要认得出来：否则「第五种写法」那一面就是恒真的。"""
        self.assertIsNotNone(CALL_ANY_RE.search("ApiClient.get(someVariable);"))
        self.assertIsNotNone(CALL_ANY_RE.search("ApiClient.post<Foo>(`/x/${y}/`);"))
        self.assertIsNone(CALL_ANY_RE.search("ApiClient.getBaseUrl();"))

    def test_the_verb_is_read_not_assumed(self):
        """★ 动词真的被读走了（而且不是恒定取到同一个值）。"""
        self.assertEqual(
            client_calls("ApiClient.post('/assets/', {});"), [("post", "api/v1/assets/")],
            "动词没被读出来，或路径与动词对调了 —— 命名组数序号最容易踩的就是这个",
        )
        self.assertEqual(
            client_calls("ApiClient.delete(`/assets/${id}/`);"),
            [("delete", "api/v1/assets/{id}/")],
        )
        self.assertEqual(
            client_calls("const r = await ApiClient.patch<A>(\n  '/accounts/'\n);"),
            [("patch", "api/v1/accounts/")],
            "跨行调用里的动词没读出来",
        )
        self.assertEqual(client_paths("ApiClient.get<A>('/accounts/');"), ["api/v1/accounts/"],
                         "只要路径的那半边也得跟着动词组一起改对")

    def test_a_wrong_verb_is_actually_reported(self):
        """★ 负向对照：真值只接 GET 的路由，客户端用 POST 必须被报出来。

        真值这里是**编的** —— 拿仓库真实路由当夹具，哪天那条路由真加了 POST，
        这条对照就自己失效了，而它看起来还是绿的。
        """
        truth = {"api/v1/analytics/summary/": frozenset({"get"})}
        self.assertEqual(
            method_mismatches({"a.ets": [("get", "api/v1/analytics/summary/")]}, truth), [],
            "动词对得上时不该报任何问题",
        )
        problems = method_mismatches({"a.ets": [("post", "api/v1/analytics/summary/")]}, truth)
        self.assertTrue(problems, "用错动词却没被报出来 —— 这条对账是空的")
        self.assertIn("405", " ".join(problems))
        # 路径真值里压根没有：不能「查不到就跳过」
        self.assertTrue(
            method_mismatches({"a.ets": [("get", "api/v1/definitely-not-a-route/")]}, truth),
            "路径不在真值里时被静默跳过了 —— 那会让整条断言在路径全写错时全绿",
        )

    def test_a_path_that_does_not_exist_is_really_rejected(self):
        """成员判定本身得有牙：编一条不存在的路径，它必须不在真路由里。"""
        self.assertNotIn("api/v1/definitely-not-a-route/", self._real())
        self.assertNotIn("api/v1/transactions/records/{id}/wrapped/", self._real())

    def test_a_genuinely_existing_route_is_accepted(self):
        """反面：真存在的必须认（否则上面那条会因为「什么都认不出来」而恒真）。"""
        real = self._real()
        self.assertIn("api/v1/analytics/summary/", real)
        self.assertIn("api/v1/transactions/records/", real)
        self.assertIn("api/v1/ingest/drafts/{id}/confirm/", real)

    @staticmethod
    def _real():
        return all_routes()


if __name__ == "__main__":
    unittest.main()
