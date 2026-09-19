# -*- coding: utf-8 -*-
"""**「要有真环境才跑得动」的那一层，唯一入口。**

为什么要有它
------------
`check_routes.py` / `smoke_api.py` / `smoke_record_flow.py` 做的是同一件事：拿一个**真环境**
去核对本仓自己的判断。它们住不进 `apps/*/tests/` —— 本仓单测有一条硬承诺「不需要数据库、
不需要 Django」，`apps/core/tests/test_no_django_required.py` 把「需要 Django 的测试模块数」
钉死在 `skipped=1`。于是它们只能待在 `scripts/` 下，而**此前没有任何东西会跑它们**：

  · README 与三处测试里的原话是「**记得顺手跑** `python scripts/check_routes.py`」——
    「记得」不是检查，写下来没人跑的东西比没有更坏（它看上去像有人在管）；
  · `check_routes.py` 里有**棘轮**（`FORMAT_VARIANT_COUNT = 11`）和**登记表**
    （`METHOD_DIFF_OK`、路由登记表），它们的用处正是「变了就逼人回来看一眼」——
    **一个没人跑的棘轮不是棘轮**，它连「没响过」都说不出来；
  · README 里那句「实测 `api/v1/` 下 Django 认得 29 条、清单 28 条、方法 28 条共有路径
    0 分歧」是**某一次**手跑的结果，之后谁也没复核过。

这个脚本本身不判断任何业务，它只做两件事：**把每个脚本按它需要的环境决定跑还是跳过，
并把结果折成一个退出码。**

判据（这一层唯一能静态回答的问题）
----------------------------------
不是「脚本写得对不对」，而是：**每个脚本都有一个会真的执行的入口，而且这个入口不会
把失败吞掉。** 具体三条，都由 `apps/core/tests/test_checks_runner_contract.py` 钉着：

  1. **两向对账**：`scripts/` 下每个对外的脚本都必须在 `CHECKS` 里露面；`CHECKS` 里点名的
     脚本必须真的存在。新加一个脚本忘了接进来 → 红（而不是安静地没人跑）。
  2. **失败折进退出码**：任一脚本非 0 退出 → 本脚本非 0 退出。这条是这一层的命门 ——
     一个「跑完打印 OK 就 exit 0」的调度器比没有调度器更坏。
  3. **跳过必须出声**：环境不满足时打印 `SKIPPED：…` 并在结论里点名，不许静默通过；
     `--strict`（发版前用）把跳过也算失败。

跑法（在仓库根，或用任何解释器）：
    python scripts/run_checks.py            # 跑得动的都跑，跑不动的说明原因
    python scripts/run_checks.py --strict   # 跳过也算失败
    python scripts/run_checks.py --live     # 连冒烟脚本一起跑（要先起服务 + 可写数据库）

退出码：0 = 没有失败（`--strict` 时还要求没有跳过）；1 = 有失败（或 `--strict` 下有跳过）。
"""
import argparse
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
SERVER = ROOT / "server"
assert (SERVER / "manage.py").exists(), f"算错了 server 根：{SERVER} 下没有 manage.py"

#: 入口自己：它是调度器，不进 `CHECKS`（否则就变成自己调度自己）
ENTRY = "run_checks.py"

#: `needs` 的合法取值 —— 每个值都对应一个**真的能判**的环境条件，不是一句说明文字
NEEDS = {
    #: 解释器里 `import django` 能成（本机是裸 Python，装了 requirements.txt 的解释器才行）
    "django": "需要 `import django` 能成的解释器（route_inventory 的真值来自 Django 本体）",
    #: 真的有个服务在跑，而且那个库可写（冒烟脚本会造数据）
    "live-service": "需要先起服务 + 一个可写的数据库（冒烟脚本会真的建账户、记流水）",
}

#: 每个对外脚本：它需要什么环境、以及**为什么它必须被跑**（不是「它是干什么的」）
CHECKS = [
    {
        "script": "check_routes.py",
        "needs": "django",
        "why": "`apps/core/route_inventory.py` 是**手写的 AST 解析器**，却是「主要接口」清单与"
               "客户端 `.ets` 契约的共同真值，从来没有别的东西核过它认出来的路由与 Django "
               "实际认识的是不是同一批。这一面只有脚本兜得住：单测那侧与它读同一份源码，"
               "改视图时两边一起变（负向验证实测：抹掉 ModelViewSet 明细的 delete，"
               "452 条单测一条都不红，只有这个脚本会红）。它里面的棘轮与登记表没人跑就永远不响。",
    },
    {
        "script": "smoke_api.py",
        "needs": "live-service",
        "why": "真发 HTTP 请求跑注册→记账→行情→统计→Agent 识别→入账→导出 CSV 这整条链。"
               "它验的是「接口真的能跑通」，这一层任何不连库的测试都替代不了。",
    },
    {
        "script": "smoke_record_flow.py",
        "needs": "live-service",
        "why": "覆盖鸿蒙「记一笔」页那条链：标的自动建→买卖→股息→出入金→持仓推导→股息口径一致。"
               "端上没有工具链、`.ets` 编译不了，这条链只能靠真发请求兜。",
    },
]


# --------------------------------------------------------------------------- 判据（纯函数，可被契约测试直接调）
def declared_scripts(checks):
    """`CHECKS` 里点名的脚本名。"""
    return {c["script"] for c in checks}


def shippable_scripts(scripts_dir):
    """`scripts/` 下**对外**的脚本 —— `_` 开头的是内部模块（`_smoke_lib.py` 那样的底座），
    它们已经有无需真环境的单测（`apps/core/tests/test_smoke_lib.py`）盯着，不进这张表。
    入口自己也不进。"""
    if not scripts_dir.is_dir():
        return set()
    return {
        f.name for f in scripts_dir.glob("*.py")
        if not f.name.startswith("_") and f.name != ENTRY
    }


def missing_from_checks(scripts_dir, checks):
    """`scripts/` 下有、`CHECKS` 里没有 —— 新脚本忘了接进来。"""
    return shippable_scripts(scripts_dir) - declared_scripts(checks)


def ghost_checks(scripts_dir, checks):
    """`CHECKS` 里点了、`scripts/` 下没有 —— 脚本删了或改名了，表没跟上。"""
    return declared_scripts(checks) - shippable_scripts(scripts_dir)


def probe_needs(need, *, python, live=False):
    """这个环境条件现在满不满足。**这是唯一与机器有关的一步**，所以单独拆出来：
    契约测试要能在合成条件上把「跳过策略」与「退出码折叠」各验一遍，而不是依赖
    跑测试的那台机器上恰好装了什么。"""
    if need == "live-service":
        return bool(live)
    if need == "django":
        p = subprocess.run(
            [python, "-c", "import django"],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
        )
        return p.returncode == 0
    return False


def run(checks, *, scripts_dir=SCRIPTS, python=None, live=False, strict=False, probe=None):
    """跑一遍。返回 `{'ran', 'skipped', 'failed', 'rc'}`（`rc` 就是本脚本的退出码）。

    子进程**继承 stdio**（不 capture）：这些脚本的输出本来就是给人看的，
    吞掉再打印只会多一层可能失真的转发。
    """
    python = python or sys.executable
    probe = probe or (lambda need: probe_needs(need, python=python, live=live))

    ran, skipped, failed = [], [], []

    for check in checks:
        name = check["script"]
        need = check["needs"]
        if need not in NEEDS:
            print(f"[SKIPPED] {name}：`needs` 写的是 {need!r}，不在 {sorted(NEEDS)} 里 —— "
                  f"环境条件认不出来，不敢当成「跑过了」")
            skipped.append((name, f"needs={need!r} 不认识"))
            continue

        if not probe(need):
            print(f"[SKIPPED] {name}：{NEEDS[need]}")
            skipped.append((name, NEEDS[need]))
            continue

        print(f"\n[RUN] {name}")
        print("-" * 68)
        # 子进程继承 stdio、直接写 fd，而这里的 print 走的是缓冲过的 stdout：
        # 被管道接走时，不先 flush 会出现「子进程的输出跑到标题前面」——
        # 实测过，读起来像是跑错了脚本。
        sys.stdout.flush()
        p = subprocess.run([python, str(scripts_dir / name)], cwd=str(SERVER))
        sys.stdout.flush()
        print("-" * 68)
        ran.append(name)
        if p.returncode != 0:
            failed.append((name, p.returncode))
            print(f"[FAIL] {name} 退出码 {p.returncode}")

    print("\n===== 结论 =====")
    print(f"  跑过（{len(ran)}）：" + ("、".join(ran) if ran else "（无）"))
    print(f"  跳过（{len(skipped)}）：" + ("、".join(n for n, _ in skipped) if skipped else "（无）"))
    for name, reason in skipped:
        print(f"        - {name}：{reason}")
    print(f"  失败（{len(failed)}）："
          + ("、".join(f"{n}(exit={rc})" for n, rc in failed) if failed else "（无）"))

    if ran and not failed and not skipped:
        print("\n[OK] 这一层的脚本全跑过、全过。")
    elif not ran and skipped:
        print("\n[注意] 这台机器上一个都没跑成 —— 上面那些「跳过」不是通过，"
              "换一台装好环境的机器再跑一次（或 `--live`）。")

    rc = 1 if failed else 0
    if strict and skipped:
        print(f"\n[FAIL] --strict：有 {len(skipped)} 项被跳过，跳过不算通过。")
        rc = 1
    return {"ran": ran, "skipped": skipped, "failed": failed, "rc": rc}


def main(argv=None):
    parser = argparse.ArgumentParser(description="跑「要有真环境才跑得动」的那一层脚本")
    parser.add_argument("--live", action="store_true",
                        help="连冒烟脚本一起跑（需要先起服务 + 一个可写的数据库）")
    parser.add_argument("--strict", action="store_true",
                        help="跳过也算失败 —— 发版前用这个，免得「没跑」被读成「通过」")
    parser.add_argument("--list", action="store_true", help="只列出登记表，不跑")
    args = parser.parse_args(argv)

    missing = sorted(missing_from_checks(SCRIPTS, CHECKS))
    ghost = sorted(ghost_checks(SCRIPTS, CHECKS))
    if missing or ghost:
        # 这一条判据的主场在单测里（test_checks_runner_contract.py），这里只是顺手也说一句：
        # 入口自己不该在「表已经烂了」的时候还给出一个漂亮的 OK。
        print("[FAIL] CHECKS 与 scripts/ 对不上：")
        if missing:
            print("  scripts/ 下有脚本没登记：" + "、".join(missing))
        if ghost:
            print("  CHECKS 点了不存在的脚本：" + "、".join(ghost))
        return 1

    if args.list:
        for c in CHECKS:
            print(f"{c['script']:26} needs={c['needs']}")
        return 0

    return run(CHECKS, live=args.live, strict=args.strict)["rc"]


if __name__ == "__main__":
    sys.exit(main())
