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
from apps.core.route_inventory import all_routes, norm

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
CALL_LITERAL_RE = re.compile(
    r"ApiClient\s*\.\s*(?:" + _VERBS + r")\b[^(\n]*\(\s*(['\"`])([^'\"`]*)\1",
    re.M,
)

#: 解析器在真仓库上至少要解出这么多条路径，否则说明扫描面塌了
MIN_CLIENT_PATHS = 12

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


def client_paths(text):
    """窄扫：这个文件里以字面量形式打出去的路径 —— 补上 API 前缀、已归一。"""
    return [API_PREFIX + norm(match.group(2)) for match in CALL_LITERAL_RE.finditer(text)]


class TestClientCallsOnlyRealRoutes(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.sources = client_sources()
        cls.paths = {}
        for rel, text in cls.sources:
            cls.paths[rel] = client_paths(text)
        cls.used = {p for paths in cls.paths.values() for p in paths}
        cls.real = all_routes()

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
