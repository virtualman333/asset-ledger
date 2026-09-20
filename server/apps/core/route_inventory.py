# -*- coding: utf-8 -*-
"""服务端**路由清单** —— 从源码里读出来的真值，不 import Django。

为什么在这里
------------
原先这份解析器长在 `apps/core/tests/test_api_surface_contract.py` 里，只服务那一份契约
（README 手抄的接口清单 ↔ 真路由）。这一轮多了第二份消费者：客户端 `.ets` 里打出去的那些
路径也得对着同一份真值核 —— 而「同一件事写两处必然漂移」是本仓反复栽过的那条，
所以解析器挪进这个纯模块，两份契约都从这儿拿。

只读源码、不 import Django
--------------------------
本仓的测试承诺「不需要数据库、不需要 Django」（`apps/core/tests/test_no_django_required.py`
会把 Django 拦掉再把整套跑一遍，要求 `OK (skipped=1)`），而开发机就是**裸 Python**
（`import django` 直接 ModuleNotFoundError）。所以这里用 AST 读 `urls.py`：
不 import django、不连库、不 import 任何 app —— 于是它能待在「不需要 Django」的那一侧。

刻意收窄的地方（写下来，免得日后以为它管得比实际宽）
----------------------------------------------------
1. **只认字面量。** f-string、字符串拼接、变量、`include(变量)` 一律不认 —— 认了就变成猜。
   漏认的代价是「少收集到一些真路由」，会从**正向**漏出（文档多写一条会被反向断言抓住），
   不会静默变成「一切正常」。
2. **只收显式 `path()` 与 router 注册的资源**，不展开 `@action` 装饰器生成的额外 URL。
   所以明细路由只能按 DRF 的默认形状 `{id}/` 推；自定义 `url_path` 的动作要靠 app 的
   `urls.py` 里显式 `path()` 声明才看得见 —— 本仓那几个（`drafts/{id}/confirm|discard`）
   恰好都是显式声明的。**这一条不是「猜不出来」，是「还没认」**：`@action` 一旦用上，
   `scripts/check_routes.py` 里的 Django 本体（它认得出那条 URL）会把它报成
   「Django 独有且未登记」，逼着这里补上 —— 不会静默漏过。
3. **方法只认「表里有的」。** 这一条是本轮补的，原先写的是「不判 HTTP 方法 —— 方法由
   mixin 组出来，静态判不出来」。那句话**是错的**：mixin 与基类都是写在类声明上的静态事实
   （`class TransactionViewSet(viewsets.ModelViewSet)`），路由器生成哪些路由也是照着
   `hasattr(viewset, 动作名)` 判的。所以方法完全可以静态推出来，代价只是要维护两张表；
   而维护一张会腐烂的表的正确姿势，是**另配一把尺子**（`check_routes.py` 拿 Django 本体的
   `callback.actions` / `view.cls` 核），不是「不用表」。
   现在推不出来的是**框架行为**：`head` / `options` / `trace` 三个动词 Django 与 DRF 都会
   无条件回，与视图写没写无关，所以 `BUSINESS_METHODS` 只认五个业务动词，别的不进契约。
   不认识的基类名一律**抛异常**，不猜（猜出来的方法集会让契约为一堆 405 放行）。
"""
import ast
import re
from pathlib import Path

#: 本文件在 `server/apps/core/` 下 —— 往上第二层是 `server/`
SERVER = Path(__file__).resolve().parents[2]
if not (SERVER / "manage.py").exists():  # pragma: no cover - 结构被人挪了才会走到
    raise AssertionError(f"算错了 server 根：{SERVER} 下没有 manage.py")

#: 仓库根
ROOT = SERVER.parent
if not (ROOT / "README.md").exists():  # pragma: no cover
    raise AssertionError(f"算错了仓库根：{ROOT} 下没有 README.md")

ROOT_URLCONF = SERVER / "config" / "urls.py"


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


def parse_app_urls_detailed(path):
    """某个 app 的 `urls.py` → `(显式路由, router 注册的资源)`。

    比 `parse_app_urls()` 多带一份**视图表达式**：显式路由是 `(子路径, 第二个位置参数)`、
    router 是 `(前缀, 注册的视图集)`。方法是长在视图身上的，`urls.py` 只写「哪个名字接」，
    所以想推方法就必须把那个表达式一起带出去 —— 只带路径字符串的话，
    「这条路由接什么方法」就永远只能是猜。
    """
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
            explicit.append((sub, node.args[1] if len(node.args) > 1 else None))
        elif name == "register":
            routers.append((sub, node.args[1] if len(node.args) > 1 else None))
    return explicit, routers


def parse_app_urls(path):
    """某个 app 的 `urls.py`：返回 `(显式 path 的子路径, router 注册的前缀)`。"""
    explicit, routers = parse_app_urls_detailed(path)
    return [sub for sub, _ in explicit], [prefix for prefix, _ in routers]


def with_slash(path):
    return path if path.endswith("/") else path + "/"


def norm(path):
    """把路径归一成「同一件事的同一个写法」：去掉查询串与前导 `/`、占位段统一成 `{id}`。

    真值写 `<int:pk>`、文档写 `{id}`、客户端写 `${id}`；文档写 `/api/v1/...`、
    根 urlconf 写 `api/v1/...` —— 不归一就会把同一段路径判成两条，两个方向各红一片假缺陷。
    """
    cleaned = path.split("?")[0].lstrip("/")
    cleaned = re.sub(r"<[^>]*>", "{id}", cleaned)
    cleaned = re.sub(r"\{[^}]*\}", "{id}", cleaned)
    cleaned = re.sub(r"\$\{[^}]*\}", "{id}", cleaned)
    return cleaned


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
            collection = with_slash(prefix + registered)
            collections.add(norm(collection))
            details.add(norm(collection + "{id}/"))
    return explicit, collections, details


def all_routes():
    """上面三份的并集 —— 「这个路径服务端认不认」只需要这一个集合。"""
    explicit, collections, details = real_routes()
    return explicit | collections | details


# =========================================================================== 方法
#
# 为什么这一节直到本轮才长出来
# ---------------------------
# 「路径存在」不等于「这个动词能用」。客户端写 `ApiClient.post('/analytics/summary/')`
# 时路径是真的、方法不是 —— 真机上收到 405，界面同样只会显示一句「加载失败」，
# 而这台机器上没有鸿蒙工具链，`.ets` 编译不了，没有任何一条自测会红。
# README 那份「主要接口」清单第一列写的就是 `GET/POST`，同样没人核过。
#
# 原先这里写的是「方法静态判不出来，硬判就变成猜」。那句话错了：视图类写在源码里，
# 基类与 mixin 都在类声明那一行上，路由是 DRF 照着 `hasattr(viewset, 动作名)` 生成的 ——
# 全都是静态事实。真正需要承认的是**表会腐烂**，所以：
#   - 不认识的基类名 → 抛异常，绝不猜；
#   - 另配一把尺子：`scripts/check_routes.py` 拿 Django 本体的 `callback.actions`
#     与 `callback.cls` 把下面推出来的结果核一遍（两向、差异要登记理由）。

#: 契约只认这五个业务动词。`head` / `options` / `trace` 不在里面：Django 的
#: `View.dispatch` 与 DRF 的 `APIView` 会无条件回它们，与视图写没写无关，
#: 那是框架行为而不是接口面 —— 把它们算进来只会让每条路由都「支持」三个动词。
BUSINESS_METHODS = ("get", "post", "put", "patch", "delete")

#: 显式 `path()` 指到的**类视图**：基类名 → 允许的方法。
#:
#: 表里的名字取点号最后一段（`generics.CreateAPIView` → `CreateAPIView`）。
#: 自己写了 `def get` 的类，方法由类体给；基类只补「类体没写、但框架会接」的那些
#: —— `RegisterView(generics.CreateAPIView)` 自己一个动词都没写，POST 是 `CreateAPIView`
#: 的 `post = CreateModelMixin.create` 带来的。
VIEW_BASE_METHODS = {
    "APIView": frozenset(),
    "View": frozenset(),
    "CreateAPIView": frozenset({"post"}),
    "ListAPIView": frozenset({"get"}),
    "RetrieveAPIView": frozenset({"get"}),
    "DestroyAPIView": frozenset({"delete"}),
    "UpdateAPIView": frozenset({"put", "patch"}),
    "ListCreateAPIView": frozenset({"get", "post"}),
    "RetrieveUpdateAPIView": frozenset({"get", "put", "patch"}),
    "RetrieveDestroyAPIView": frozenset({"get", "delete"}),
    "RetrieveUpdateDestroyAPIView": frozenset({"get", "put", "patch", "delete"}),
    # simplejwt 的四个 Token 视图都继承 `TokenViewBase`，只有 POST。
    "TokenObtainPairView": frozenset({"post"}),
    "TokenRefreshView": frozenset({"post"}),
    "TokenVerifyView": frozenset({"post"}),
    "TokenBlacklistView": frozenset({"post"}),
}

#: router 注册的 ViewSet：基类/动作 mixin 名 → `(集合 URL 的方法, 明细 URL 的方法)`。
#:
#: 集合 = `list` / `create`，明细 = `retrieve` / `update` / `partial_update` / `destroy`；
#: 一个动词落在哪一边是 DRF 路由表定死的，不是我们选的
#: （`SimpleRouter.routes` 里 detail 路由那张映射：get→retrieve、put→update、
#: patch→partial_update、delete→destroy —— 所以 PUT/PATCH/DELETE **只在明细 URL 上**，
#: 这正是 README 给集合写 `GET/POST` 的原因）。
VIEWSET_BASE_ACTIONS = {
    "ModelViewSet": (
        frozenset({"get", "post"}),
        frozenset({"get", "put", "patch", "delete"}),
    ),
    "ReadOnlyModelViewSet": (frozenset({"get"}), frozenset({"get"})),
    "GenericViewSet": (frozenset(), frozenset()),
    "ViewSet": (frozenset(), frozenset()),
    "ListModelMixin": (frozenset({"get"}), frozenset()),
    "CreateModelMixin": (frozenset({"post"}), frozenset()),
    "RetrieveModelMixin": (frozenset(), frozenset({"get"})),
    "UpdateModelMixin": (frozenset(), frozenset({"put", "patch"})),
    "DestroyModelMixin": (frozenset(), frozenset({"delete"})),
}

#: ViewSet 里的动作名 → `(方法, 它长在集合还是明细上)`。
#: 用来认「自己写了 `def list` 但没继承 `ListModelMixin`」这种写法 —— DRF 判的是
#: `hasattr(viewset, 动作名)`，动作名从哪来它不管。
VIEWSET_DECLARED_ACTIONS = {
    "list": ("get", "collection"),
    "create": ("post", "collection"),
    "retrieve": ("get", "detail"),
    "update": ("put", "detail"),
    "partial_update": ("patch", "detail"),
    "destroy": ("delete", "detail"),
}

#: 认不出来的基类名该往哪张表里补
_BASE_TABLE_HINT = (
    "把它的方法补进 `VIEW_BASE_METHODS` / `VIEWSET_BASE_ACTIONS`"
    "（顺便跑一遍 `scripts/run_checks.py`：它会把这件事交给 Django 本体核一下补得对不对）"
)


def _simple_name(node):
    """`viewsets.ModelViewSet` → `'ModelViewSet'`；`APIView` → `'APIView'`；其它 → None。"""
    if isinstance(node, ast.Attribute):
        return node.attr
    if isinstance(node, ast.Name):
        return node.id
    return None


def _target_name(node):
    """`urls.py` 里那个视图表达式 → 在 `views.py` 里能查的名字。

    - `PositionsView.as_view()` → `PositionsView`
    - `views.register_view`（模块级别名）→ `register_view`
    - `health`（裸函数视图）→ `health`

    认不出来返回 None —— 调用方必须抛异常，不能「没有方法就算了」。
    """
    if node is None:
        return None
    if isinstance(node, ast.Call):
        if not isinstance(node.func, ast.Attribute) or node.func.attr != "as_view":
            return None
        node = node.func.value
    return _simple_name(node)


def _base_names(cls_node):
    """类声明的基类名（取点号最后一段，`viewsets.ModelViewSet` → `ModelViewSet`）。"""
    return [name for name in (_simple_name(b) for b in cls_node.bases) if name]


def _declared_names(cls_node):
    """直接写在类体里的 `def` 名字（继承来的不算 —— 那些由基类那张表负责）。"""
    return {
        stmt.name
        for stmt in cls_node.body
        if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef))
    }


def _http_method_names(cls_node):
    """类体里 `http_method_names = [...]` 的字面量；没写或认不出返回 None。

    「认不出返回 None」是刻意的：它只可能**收窄**方法集，而收窄错了会让契约把真能用的
    动词判成非法（假红、很响，能自己暴露）；反过来把认不出的当成空集合去交集，
    就会静默把每条路由的方法清空。
    """
    for stmt in cls_node.body:
        if not isinstance(stmt, ast.Assign):
            continue
        if not any(isinstance(t, ast.Name) and t.id == "http_method_names" for t in stmt.targets):
            continue
        try:
            values = ast.literal_eval(stmt.value)
        except (ValueError, SyntaxError):
            return None
        if isinstance(values, (list, tuple, set, frozenset)):
            return frozenset(str(v).lower() for v in values)
    return None


def app_views_file(module):
    """`apps.users.urls` → `server/apps/users/views.py`。

    视图名只在**同一个 app 的 `views.py`** 里找。本仓每一条路由都是这么组织的
    （`from .views import X` / `from . import views`）；哪天某条路由指向别的模块，
    这里会以「名字查不到」抛出来，而不是安静地返回一个空方法集。
    """
    return SERVER / Path(*module.split(".")[:-1]) / "views.py"


class _AppViews:
    """一个 app 的 `views.py` 的静态索引：类 / 模块级函数 / 模块级别名。

    只读 AST，不 import 这个模块 —— 本仓的测试承诺「不需要 Django、不连库」，
    而 `views.py` 一 import 就会把 DRF 与模型全拖进来。
    """

    def __init__(self, path):
        self.path = path
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        self.classes = {}
        self.functions = {}
        self.assigned = {}
        for node in tree.body:
            if isinstance(node, ast.ClassDef):
                self.classes[node.name] = node
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                self.functions[node.name] = node
            elif isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Name):
                        self.assigned[target.id] = node.value

    # ---------------------------------------------------------------- 名字 → 东西
    def resolve(self, name, _seen=()):
        """视图名 → `('class', ClassDef)` / `('function', None)` / `('table', 名字)`。

        `'table'` 指这个名字 `views.py` 里没有，只在下面那两张基类表里 —— DRF 的通用视图
        与 simplejwt 的 Token 视图都是这种（`from … import TokenObtainPairView`）。
        三层都不中时**不返回 None、交给调用方抛异常**：这正是「不认识的别猜」那条。
        """
        if name in _seen:
            raise AssertionError(
                f"{self.path} 里的视图别名绕回了自己：{' → '.join(_seen + (name,))}"
            )
        if name in self.classes:
            return ("class", self.classes[name])
        if name in self.assigned:
            # 模块级别名：`register_view = RegisterView.as_view()`
            real = _target_name(self.assigned[name])
            if real is None:
                raise AssertionError(
                    f"{self.path} 里 {name} 的赋值认不出视图来源："
                    f"{ast.dump(self.assigned[name])[:120]}\n只认 `X.as_view()` 这一种写法。"
                )
            return self.resolve(real, _seen + (name,))
        if name in self.functions:
            return ("function", None)
        return ("table", name)


def _method_set(views, cls_node, seen):
    """类视图允许的方法 = 类体自己写的 ∪ 基类给的（同文件的基类递归进去）。"""
    methods = set(_declared_names(cls_node)) & set(BUSINESS_METHODS)
    for base in _base_names(cls_node):
        if base in seen:
            continue
        if base in views.classes:
            methods |= _method_set(views, views.classes[base], seen | {base})
        elif base in VIEW_BASE_METHODS:
            methods |= VIEW_BASE_METHODS[base]
        elif base == "object":
            continue
        else:
            raise AssertionError(
                f"{views.path} 里 {cls_node.name} 的基类 {base!r} 认不出来。\n{_BASE_TABLE_HINT}"
            )
    allowed = _http_method_names(cls_node)
    if allowed is not None:
        methods &= set(allowed)
    return frozenset(methods & set(BUSINESS_METHODS))


def _actions_set(views, cls_node, seen):
    """ViewSet → `(集合 URL 的方法, 明细 URL 的方法)`。"""
    collection, detail = set(), set()
    declared = _declared_names(cls_node)
    for action, (verb, side) in VIEWSET_DECLARED_ACTIONS.items():
        if action in declared:
            (collection if side == "collection" else detail).add(verb)
    for base in _base_names(cls_node):
        if base in seen:
            continue
        if base in views.classes:
            got = _actions_set(views, views.classes[base], seen | {base})
        elif base in VIEWSET_BASE_ACTIONS:
            got = VIEWSET_BASE_ACTIONS[base]
        elif base == "object":
            got = (frozenset(), frozenset())
        else:
            raise AssertionError(
                f"{views.path} 里 {cls_node.name} 的基类 {base!r} 认不出来。\n{_BASE_TABLE_HINT}"
            )
        collection |= got[0]
        detail |= got[1]
    return frozenset(collection), frozenset(detail)


def view_methods(views, name):
    """显式 `path()` 指到的视图 → 允许的业务动词集合。

    裸函数视图返回**全部五个**：管方法是 Django 的 `View`，一个 `def health(request)`
    是任何动词都会被调用的。给它报 `{get}` 才是猜 —— README 写 `GET /health/` 说的是
    「你就这么用」，不是「别的动词会被拒」。
    """
    kind, payload = views.resolve(name)
    if kind == "function":
        return frozenset(BUSINESS_METHODS)
    if kind == "table":
        # `payload` 才是表里那个名字：别名（`register_view`）已经在这里被跟到真名了，
        # 拿别名去查表当然查不到 —— 第一版就是这么错的，负向验证前先被自己的表打回来。
        if payload in VIEW_BASE_METHODS:
            return VIEW_BASE_METHODS[payload]
        raise AssertionError(
            f"{views.path} 里找不到视图 {payload!r}（{name!r} 解析到它），"
            f"它也不在通用视图表里。\n{_BASE_TABLE_HINT}"
        )
    return _method_set(views, payload, frozenset({payload.name}))


def viewset_actions(views, name):
    """router 注册的 ViewSet → `(集合 URL 的方法, 明细 URL 的方法)`。"""
    kind, payload = views.resolve(name)
    if kind == "function":
        raise AssertionError(
            f"{views.path} 里的 {name} 是个函数，不是 ViewSet —— router.register 注册不了它"
        )
    if kind == "table":
        if payload in VIEWSET_BASE_ACTIONS:
            return VIEWSET_BASE_ACTIONS[payload]
        raise AssertionError(
            f"{views.path} 里找不到视图集 {payload!r}（{name!r} 解析到它），"
            f"它也不在 ViewSet 表里。\n{_BASE_TABLE_HINT}"
        )
    return _actions_set(views, payload, frozenset({payload.name}))


def route_methods():
    """`{归一化路径: frozenset(业务动词)}` —— 与 `all_routes()` **同一批键**。

    「同一批键」这件事本身有测试钉着（`set(route_methods()) == all_routes()`）：
    两张表各算各的、迟早会有一边多一条，而「方法表少一条路径」的表现是
    调用方 `.get(path)` 拿到 None —— 静默。
    """
    out = {}
    for prefix, module in root_includes():
        if not prefix.startswith("api/"):
            continue
        urls_file = app_urls_file(module)
        if not urls_file.exists():
            raise AssertionError(f"根 urlconf include 了一个不存在的模块：{module} → {urls_file}")
        views = _AppViews(app_views_file(module))
        explicit, routers = parse_app_urls_detailed(urls_file)
        for sub, target in explicit:
            name = _target_name(target)
            if name is None:
                raise AssertionError(
                    f"{module} 里 path({sub!r}, …) 的视图认不出来（只认 `X.as_view()` / "
                    f"`模块.名字` / 裸函数名）：{ast.dump(target)[:120] if target else '缺第二个参数'}"
                )
            out.setdefault(norm(prefix + sub), set()).update(view_methods(views, name))
        for registered, target in routers:
            name = _target_name(target)
            if name is None:
                raise AssertionError(
                    f"{module} 里 router.register({registered!r}, …) 的视图集认不出来："
                    f"{ast.dump(target)[:120] if target else '缺第二个参数'}"
                )
            base = with_slash(prefix + registered)
            collection, detail = viewset_actions(views, name)
            out.setdefault(norm(base), set()).update(collection)
            out.setdefault(norm(base + "{id}/"), set()).update(detail)
    return {key: frozenset(value) for key, value in out.items()}


# ---------------------------------------------------------------------------
# README 里手抄的路由条数
# ---------------------------------------------------------------------------

#: README 里那几处**手抄**的数字 → 匹配它们的正则。
#:
#: 为什么要有这一段：`scripts/check_routes.py` 每跑一次都会把实测条数打印出来，而 README
#: 里那句「实测 Django 认得 N 条、清单 M 条、共有路径 K 条」是**某一次**手跑的结果 ——
#: 之后每加一条路由它都过期，且**没有任何东西会响**。本轮实测就是：新增一条
#: `analytics/positions/export/` 之后 README 那三处数字全错，而全套检查照样绿。
#:
#: 正则失配（那句话被人改写了）**算问题**，不算「没什么可比」—— 后者是这类检查最经典的
#: 恒真形态。每个键至少要命中一次，且每次命中的数都必须等于本轮实测值
#: （同一件事在 README 里写了两遍的地方，两处都会被查 —— 那正是它们最容易漂的形态）。
README_COUNT_PATTERNS = {
    "django": r"Django\s*认得\s*\*{0,2}(\d+)\*{0,2}\s*条",
    "inventory": r"清单\s*\*{0,2}(\d+)\*{0,2}\s*条",
    "common": r"\*{0,2}(\d+)\s*条共有路径",
    "format": r"DRF\s*那\s*(\d+)\s*条",
}


def readme_route_counts(text):
    """README 文本 → `{键: [命中的数字, …]}`（按出现顺序，可能同一个键有多处）。"""
    found = {}
    for key, pattern in README_COUNT_PATTERNS.items():
        found[key] = [int(m) for m in re.findall(pattern, text)]
    return found


def readme_count_problems(text, actual):
    """README 里手抄的条数 vs 本轮实测 → 问题清单（空 = 一致）。

    `actual` 是 `{同上那几个键: 实测整数}`。跳过的键（`actual` 里没有）**算问题** ——
    静默跳过会让这条检查在「脚本哪天不再算某个数」时变成空话。
    """
    problems = []
    found = readme_route_counts(text)
    for key, pattern in README_COUNT_PATTERNS.items():
        if key not in actual:
            problems.append(f"README 条数检查少了一个实测值：{key}")
            continue
        hits = found[key]
        if not hits:
            problems.append(
                f"README 里找不到描述「{key}」条数的那句话了（正则 {pattern!r} 一处都没匹配）—— "
                "要么那句话被改写了，要么这个数被删了；改写得连正则一起改，别让它悄悄不再被检查"
            )
            continue
        wrong = sorted({n for n in hits if n != actual[key]})
        if wrong:
            problems.append(
                f"README 里手抄的「{key}」条数是 {wrong}，本轮实测是 {actual[key]} —— "
                "文档过期了（改这句时不必改别的，写实测值就行）"
            )
    return problems

