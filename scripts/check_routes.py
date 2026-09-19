# -*- coding: utf-8 -*-
"""**用 Django 本体核对路由清单** —— `apps/core/route_inventory.py` 自己信不信得过。

为什么需要这个脚本
------------------
`route_inventory` 是两份契约的共同真值：README 手抄的接口清单、客户端 `.ets` 里打出去的
每个路径，都拿它做对账。可它本身是个**手写的 AST 解析器** —— 「自己写、自己信」，
从来没有任何东西核对过它认出来的东西和 Django 实际认识的东西是不是同一批。

两个方向都会出事，而且都是静默的：

- **幻影路由**（清单有、Django 没有）：契约会为一条 404 的路径放行，客户端照着写，
  页面永远转圈；
- **漏认路由**（Django 有、清单没有）：契约会把一条真实存在的路径判成非法，
  于是没人敢用，或者每次都要去改清单。

所以这里把 Django 自己的 URL resolver 走一遍（`get_resolver().url_patterns` 递归展开，
得到的是运行时真正会用到的路由表），与 `route_inventory.all_routes()` 做**两向**比对。
**每条差异都必须落在下面那两张登记表里并写明理由**，没登记的就是失败 ——
不是「打一批 warning 然后退出码 0」。

第二件事：**方法**
------------------
同一轮里 `route_inventory` 还学会了推「这条路由接哪些动词」（`route_methods()`）。
它推得对不对，也只有 Django 本体说了算 —— `urls.py` 里没有动词，动词长在视图对象上：

  - router 生成的路由：`callback.actions` 就是 DRF 塞进去的「方法 → 动作名」那张映射；
  - `.as_view()` 出来的类视图：`callback.cls` 是那个类，看它有没有 `get` / `post`…；
  - 裸函数视图：Django 不限制方法，五个业务动词全接。

只比五个业务动词（`get` / `post` / `put` / `patch` / `delete`）：`head` / `options` / `trace`
是框架无条件回的，算进来只会让每条路由都「支持」它们。差集同样要登记理由 ——
下面那张 `METHOD_DIFF_OK` 现在**是空的**，实测 0 分歧；它是留给「知道为什么不同」的情况的。

为什么是脚本而不是单测
----------------------
本仓的测试有一条硬承诺：「单元测试不需要数据库、不需要 Django」，而且
`apps/core/tests/test_no_django_required.py` 把这个数钉死在 **skipped=1**
（唯一获准 import django 的模块是 `apps/market/tests/test_scheduler_autostart.py`）。
这个脚本必须 import Django，所以它**不能**住进 `apps/*/tests/`。
它和 `smoke_api.py` 一样属于「需要有真环境才跑得动」的那一层。

跑法（在 `server/` 下，或用任何装了 requirements.txt 的解释器）：

    python scripts/check_routes.py          # 看结论
    python scripts/check_routes.py --diff   # 顺带把两边差异逐条打出来

退出码：0 = 两向一致（差异全部有登记）；1 = 有未登记的差异 / 任何异常。
"""
import argparse
import os
import re
import sys
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SERVER = ROOT / "server"
if not (SERVER / "manage.py").exists():  # pragma: no cover
    raise AssertionError(f"算错了 server 根：{SERVER} 下没有 manage.py")

sys.path.insert(0, str(SERVER))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

#: 只核对面向客户端的这一片。`admin/`、`media/` 不是接口面（与 route_inventory 的口径一致）
API_PREFIX = "api/v1/"


# --------------------------------------------------------------------------- 归一化
def canonical(route):
    """把「同一个东西的不同写法」归一成同一个字符串。

    真值写 `<int:pk>`、Django 的 router 写 `^(?P<pk>[^/.]+)/$`、清单写 `{id}` ——
    不归一就会把同一段路径判成两条，两个方向各红一片假缺陷。

    ⚠ 这里**不能**复用 `route_inventory.norm()`：它第一刀是 `path.split("?")`（用来切查询串），
    而 Django 的 regex 路由里到处是 `?`（`(?P<pk>[^/.]+)`、`/?`），会被它拦腰切开。
    两边归一化口径一致才是「同一件事的同一写法」，所以这个函数必须两边都用。
    """
    s = route.strip()
    # `^` / `$` 是整条 regex 的锚，但 include 前缀拼上去之后它们落在中间
    # （`api/v1/accounts/^$`）—— 所以整串删掉，别只削头尾。
    s = s.replace("^", "").replace("$", "")
    s = re.sub(r"\(\?P<[^>]+>[^)]*\)", "{id}", s)   # `(?P<pk>[^/.]+)` → `{id}`
    s = re.sub(r"<[^>]+>", "{id}", s)               # `<int:pk>` / `<drf_format_suffix:format>`
    s = re.sub(r"\{[^}]*\}", "{id}", s)             # `{id}` / `${id}`
    s = s.lstrip("/")
    return s if s.endswith("/") else s + "/"


def is_format_variant(raw):
    """DRF 的 `format` 后缀变体：`^records\\.(?P<format>[a-z0-9]+)/?$`、`<drf_format_suffix:format>`。

    它们不是「另一条路由」，是同一条资源的另一种取用方式，由 DRF 的
    `format_suffix_patterns` 批量生成，`route_inventory` 按设计不收（它只收显式 `path()`
    与 router 注册的资源）。按家族整批排除，并用下面的棘轮数把条数钉住 ——
    DRF 哪天改了生成形状，条数会变，这个脚本就会红，逼着人回来看一眼。
    """
    return "(?P<format>" in raw or "<drf_format_suffix:format>" in raw


#: 排除掉的 format 变体条数（棘轮：变了就要重新审一遍，别让它悄悄长、悄悄缩）
#: 实测 11 条 = accounts 3 + assets 3 + transactions 1 + records 2 + dividends 2
FORMAT_VARIANT_COUNT = 11


# --------------------------------------------------------------------------- 两侧枚举
def django_patterns():
    """Django 运行时真正认的那批路由 → `[(整条 pattern, 那个 pattern 对象)]`。

    返回 pattern 对象（不只是字符串）是因为**方法**只能从它身上读：`getattr(p, "callback")`
    才是真正的视图，URL 字符串里没有动词。第一版这个函数只 yield 字符串，于是「方法」
    这一面就永远只能靠「路径是对的」来间接担保 —— 那正是它上线前没人核的原因。
    """
    from django.urls import get_resolver

    def walk(patterns, prefix=""):
        for p in patterns:
            sub = getattr(p, "url_patterns", None)
            if sub is not None:
                yield from walk(sub, prefix + str(p.pattern))
            else:
                yield prefix + str(p.pattern), p

    return list(walk(get_resolver().url_patterns))


def django_routes():
    """只要路由字符串（路径那一面用）。"""
    return sorted({raw for raw, _p in django_patterns()})


#: 只比这五个业务动词：`head` / `options` / `trace` 是 Django 与 DRF 无条件回框架行为，
#: 与视图写没写无关（`View` 里带 `options`、DRF 的 `APIView.dispatch` 会兜底），
#: 算进来只会让每条路由都「支持」它们，把这张表的信号淹没。
BUSINESS_METHODS = ("get", "post", "put", "patch", "delete")


def django_route_methods(patterns):
    """`{归一化路径: frozenset(方法)}` —— 直接问 Django 本体的视图对象。

    三条分支对应三种视图：
    1. **router 生成的**：DRF 在 `ViewSetMixin.as_view()` 里把 `actions` 塞给视图函数，
       它的**键**就是允许的方法（`{'get': 'list', 'post': 'create'}`）——
       这里第一版写成了 `actions.values()`，拿到的是动作名（`list`/`create`），
       与业务动词求交集当然永远是空集，于是每条 router 路由都「一个方法都不接」。
    2. **`.as_view()` 的类视图**：`callback.cls` 是那个类（DRF 与 Django 的 `as_view`
       都会挂上，DRF 用 `cls`、Django 用 `view_class`，两个都看一遍），
       看它有没有 `get` / `post`…；
    3. **裸函数视图**：`health` 那种 —— Django 不限制方法，五个业务动词全接。
       （给它报 `{get}` 才是猜；README 写 `GET /health/` 是说「你就这么用」。）
    """
    out = {}
    for raw, p in patterns:
        if is_format_variant(raw):
            continue
        key = canonical(raw)
        if not key.startswith(API_PREFIX):
            continue
        callback = p.callback
        actions = getattr(callback, "actions", None)
        cls = getattr(callback, "cls", None) or getattr(callback, "view_class", None)
        if actions is not None:
            methods = {m for m in actions if m in BUSINESS_METHODS}
        elif cls is not None:
            methods = {m for m in BUSINESS_METHODS if hasattr(cls, m)}
        else:
            methods = set(BUSINESS_METHODS)
        out.setdefault(key, set()).update(methods)
    return {key: frozenset(value) for key, value in out.items()}


def inventory_routes():
    """`route_inventory`（AST，裸 Python）认的那批路由。"""
    from apps.core import route_inventory

    explicit, collections, details = route_inventory.real_routes()
    return explicit, collections, details, route_inventory.all_routes(), route_inventory.route_methods()


# --------------------------------------------------------------------------- 登记表
#: Django 有、清单**按设计**不收 —— 每条都要写明为什么。多一条未登记的就失败。
DJANGO_ONLY_OK = {
    "api/v1/transactions/": "DefaultRouter 的 API 根视图（`transactions/urls.py` 里那 "
                            "`*router.urls` 带的 `^$`），不是注册出来的资源；"
                            "accounts/assets 两个根与它们的资源集合同路径，已在集合里",
}

#: 清单有、Django 没有 —— 这条表最好永远是空的：幻影路由是最危险的一类差异。
INVENTORY_ONLY_OK = {}

#: 方法不一致、但**说得清为什么**的路径 → 理由。
#: 空表是有意的：实测两边 0 分歧。用它是「知道为什么不同」，不是「先记下来让它过去」——
#: 登记表自己也会腐烂（登记的路径哪天变一致了，下面会红）。
METHOD_DIFF_OK = {}

#: 方法这一面至少要真的比到这么多条路径，否则说明某一侧塌了。
#: 「两边都空 → 集合相等 → 全绿」是这类对账最经典的恒真形态。
METHOD_COMPARE_FLOOR = 20


# --------------------------------------------------------------------------- 主流程
def main(argv=None):
    ap = argparse.ArgumentParser(description="用 Django 本体核对 route_inventory 的路由清单")
    ap.add_argument("--diff", action="store_true", help="把两侧差异逐条打出来")
    args = ap.parse_args(argv)

    problems = []
    try:
        import django

        django.setup()
        patterns = django_patterns()
        raw = sorted({route for route, _p in patterns})
        explicit, collections, details, inventory, inventory_methods = inventory_routes()
    except Exception:
        # 「跑一遍给结论」的工具必须把异常折算成一条 FAIL：崩掉的结论也是结论
        print("[FAIL] 没能跑起来：\n" + traceback.format_exc())
        return 1

    api_raw = [r for r in raw if canonical(r).startswith(API_PREFIX)]
    variants = [r for r in api_raw if is_format_variant(r)]
    plain = [r for r in api_raw if not is_format_variant(r)]
    django_side = {canonical(r) for r in plain}
    inventory_side = {canonical(r) for r in inventory}

    print(f"[1/6] Django 本体枚举：{len(raw)} 条（其中 api/v1/ 下 {len(api_raw)} 条）")
    print(f"[2/6] 其中 format 后缀变体 {len(variants)} 条（按家族排除，棘轮登记 {FORMAT_VARIANT_COUNT} 条）")
    print(f"[3/6] 路由清单：显式 {len(explicit)} / 集合 {len(collections)} / 明细 {len(details)} "
          f"→ 并集 {len(inventory_side)} 条")

    # 解析面不许为空 —— 两边都空会让「集合相等」恒真
    if len(django_side) < 10:
        problems.append(f"Django 侧只解析出 {len(django_side)} 条 api/v1/ 路由，扫描面塌了")
    if len(inventory_side) < 10:
        problems.append(f"清单侧只解析出 {len(inventory_side)} 条路由，解析器多半瞎了")
    if len(variants) != FORMAT_VARIANT_COUNT:
        problems.append(
            f"format 后缀变体是 {len(variants)} 条，登记的是 {FORMAT_VARIANT_COUNT} 条 —— "
            "DRF 的生成形状变了，回来看一眼 `is_format_variant()` 还对不对"
        )

    only_django = sorted(django_side - inventory_side)
    only_inventory = sorted(inventory_side - django_side)

    unregistered_missing = [r for r in only_django if r not in DJANGO_ONLY_OK]
    unregistered_extra = [r for r in only_inventory if r not in INVENTORY_ONLY_OK]

    print(f"[4/6] 路径两向对账：Django 独有 {len(only_django)} 条 / 清单独有 {len(only_inventory)} 条")
    if args.diff or unregistered_missing or unregistered_extra:
        for r in only_django:
            print(f"      Django 独有: {r}   {DJANGO_ONLY_OK.get(r, '← 未登记！')}")
        for r in only_inventory:
            print(f"      清单独有:   {r}   {INVENTORY_ONLY_OK.get(r, '← 未登记！')}")

    if unregistered_missing:
        problems.append(
            "以下路由 Django 认识、清单里没有，而且没在 DJANGO_ONLY_OK 里登记理由：\n  "
            + "\n  ".join(unregistered_missing)
        )
    if unregistered_extra:
        problems.append(
            "以下路由清单里有、Django 根本不认（幻影路由，契约会为 404 放行）：\n  "
            + "\n  ".join(unregistered_extra)
        )

    # ----------------------------------------------------------------- 方法
    django_methods = django_route_methods(patterns)
    # 只比两侧都认的路径：路径本身的差异上面已经管了，混进来会得到重复的报错
    shared = sorted(django_side & inventory_side)
    method_diffs = []
    for path in shared:
        want = inventory_methods.get(path)
        if want is None:
            problems.append(
                f"{path} 两侧都认这条路，可 `route_methods()` 里没有它 —— "
                "方法表与路径表的键漂了（`route_inventory` 那边有一条单测钉同一件事）"
            )
            continue
        got = django_methods.get(path, frozenset())
        if set(got) != set(want):
            method_diffs.append((path, got, want))

    print(f"[5/6] 方法两向对账：比了 {len(shared)} 条路径，不一致 {len(method_diffs)} 条"
          f"（登记 {len(METHOD_DIFF_OK)} 条）")
    if args.diff or method_diffs:
        for path, got, want in method_diffs:
            print(f"      {path}: Django={','.join(sorted(got)) or '（无）'}"
                  f"  清单={','.join(sorted(want)) or '（无）'}"
                  f"   {METHOD_DIFF_OK.get(path, '← 未登记！')}")

    if len(shared) < METHOD_COMPARE_FLOOR:
        problems.append(
            f"方法只比到 {len(shared)} 条路径（下限 {METHOD_COMPARE_FLOOR}）—— 比对面塌了，"
            "「两边都空所以相等」正是这类对账最经典的恒真形态"
        )
    for path, got, want in method_diffs:
        if path not in METHOD_DIFF_OK:
            problems.append(
                f"{path} 的方法不一致：Django 认 {'/'.join(sorted(got)) or '（无）'}，"
                f"清单推出来的是 {'/'.join(sorted(want)) or '（无）'} —— "
                "契约会照着清单那一侧放行，客户端于是撞 405。要保留差异就在 "
                "METHOD_DIFF_OK 里登记理由"
            )
    for path in METHOD_DIFF_OK:
        if path not in shared:
            problems.append(f"METHOD_DIFF_OK 里登记的 {path} 两边压根没在比 —— 登记表在腐烂")
        elif not any(path == d[0] for d in method_diffs):
            problems.append(f"METHOD_DIFF_OK 里登记的 {path} 其实两边一致了，该删掉这条登记")

    # ----------------------------------------------------------------- 登记表腐烂
    print("[6/6] 登记表自检")
    for r in DJANGO_ONLY_OK:
        if r not in django_side:
            problems.append(f"DJANGO_ONLY_OK 里登记的 {r} 在 Django 侧已经不存在了，登记表在腐烂")
        if r in inventory_side:
            problems.append(f"DJANGO_ONLY_OK 里登记的 {r} 其实清单里也有，该从登记表里删掉")
    for r in INVENTORY_ONLY_OK:
        if r in django_side:
            problems.append(f"INVENTORY_ONLY_OK 里登记的 {r} 其实 Django 也认，登记表在腐烂")

    print()
    if problems:
        print("[FAILED] 路由清单与 Django 不一致：")
        for p in problems:
            print("  ✗ " + p.replace("\n", "\n    "))
        return 1

    print(f"[OK] 路由清单与 Django 本体一致：api/v1/ 下 {len(django_side)} 条，"
          f"另 {len(only_django)} 条差异全部有登记；"
          f"方法在 {len(shared)} 条共有路径上**逐条一致**（0 分歧）。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
