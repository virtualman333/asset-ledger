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
   恰好都是显式声明的。
3. **不判 HTTP 方法。** 方法是 ViewSet 由 mixin 组出来的，静态判不出来。
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
