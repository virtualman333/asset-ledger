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
def django_routes():
    """Django 运行时真正认的那批路由（递归展开 URLResolver）。"""
    from django.urls import get_resolver

    def walk(patterns, prefix=""):
        for p in patterns:
            sub = getattr(p, "url_patterns", None)
            if sub is not None:
                yield from walk(sub, prefix + str(p.pattern))
            else:
                yield prefix + str(p.pattern)

    return sorted(set(walk(get_resolver().url_patterns)))


def inventory_routes():
    """`route_inventory`（AST，裸 Python）认的那批路由。"""
    from apps.core import route_inventory

    explicit, collections, details = route_inventory.real_routes()
    return explicit, collections, details, route_inventory.all_routes()


# --------------------------------------------------------------------------- 登记表
#: Django 有、清单**按设计**不收 —— 每条都要写明为什么。多一条未登记的就失败。
DJANGO_ONLY_OK = {
    "api/v1/transactions/": "DefaultRouter 的 API 根视图（`transactions/urls.py` 里那 "
                            "`*router.urls` 带的 `^$`），不是注册出来的资源；"
                            "accounts/assets 两个根与它们的资源集合同路径，已在集合里",
}

#: 清单有、Django 没有 —— 这条表最好永远是空的：幻影路由是最危险的一类差异。
INVENTORY_ONLY_OK = {}


# --------------------------------------------------------------------------- 主流程
def main(argv=None):
    ap = argparse.ArgumentParser(description="用 Django 本体核对 route_inventory 的路由清单")
    ap.add_argument("--diff", action="store_true", help="把两侧差异逐条打出来")
    args = ap.parse_args(argv)

    problems = []
    try:
        import django

        django.setup()
        raw = django_routes()
        explicit, collections, details, inventory = inventory_routes()
    except Exception:
        # 「跑一遍给结论」的工具必须把异常折算成一条 FAIL：崩掉的结论也是结论
        print("[FAIL] 没能跑起来：\n" + traceback.format_exc())
        return 1

    api_raw = [r for r in raw if canonical(r).startswith(API_PREFIX)]
    variants = [r for r in api_raw if is_format_variant(r)]
    plain = [r for r in api_raw if not is_format_variant(r)]
    django_side = {canonical(r) for r in plain}
    inventory_side = {canonical(r) for r in inventory}

    print(f"[1/4] Django 本体枚举：{len(raw)} 条（其中 api/v1/ 下 {len(api_raw)} 条）")
    print(f"[2/4] 其中 format 后缀变体 {len(variants)} 条（按家族排除，棘轮登记 {FORMAT_VARIANT_COUNT} 条）")
    print(f"[3/4] 路由清单：显式 {len(explicit)} / 集合 {len(collections)} / 明细 {len(details)} "
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

    print(f"[4/4] 两向对账：Django 独有 {len(only_django)} 条 / 清单独有 {len(only_inventory)} 条")
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

    # 登记表本身也会腐烂：登记成「Django 独有」的必须真的 Django 独有
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
          f"另 {len(only_django)} 条差异全部有登记。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
