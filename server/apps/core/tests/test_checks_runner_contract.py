# -*- coding: utf-8 -*-
"""「要有真环境才跑得动」的那一层，**入口自己**也得有人看着。

为什么要有它
------------
`check_routes.py` / `smoke_api.py` / `smoke_record_flow.py` 住不进 `apps/*/tests/`
（本仓单测硬承诺「不需要数据库、不需要 Django」，`test_no_django_required.py` 把
「需要 Django 的测试模块数」钉在 `skipped=1`）。本轮之前，它们唯一的打开方式是
README 里那句「**记得顺手跑**」——「记得」不是检查：

  · `check_routes.py` 里的棘轮（`FORMAT_VARIANT_COUNT = 11`）与登记表，
    用处正是「变了就逼人回来看一眼」；一个没人跑的棘轮**连「没响过」都说不出来**；
  · README 里「实测 29 条 / 28 条 / 0 分歧」是某一次手跑的结果，之后没人复核过。

`scripts/run_checks.py` 就是补上的那个入口。但这个入口本身很危险，因为它长得像
「跑完了，一切正常」—— 而它的失败方式全是静默的：**没跑却报 OK**、**子脚本红了却被吞掉**、
**环境不满足就安静跳过**。所以这里只钉三件事：

  1. **两向对账**：`scripts/` 下每个对外脚本都在 `CHECKS` 里；`CHECKS` 点名的都真的存在。
     新脚本忘了接进来 → 红。（这一条与 `test_smoke_lib.py` 里「脚本 ⇄ README」那条是
     同一形状，但读的是**另一个消费方** —— 有文档不等于有人跑。）
  2. **退出码真的折叠**：拿几个**合成假脚本**（exit 0 / exit 1）真跑一遍入口，
     验「子脚本非 0 → 入口非 0」。这是这一层的命门：一个「跑完打印 OK 就 exit 0」
     的调度器比没有调度器更坏。
  3. **跳过必须出声**：环境不满足时点名说明，`--strict` 下算失败。

判据的可证伪面：对账用的两个纯函数（`missing_from_checks` / `ghost_checks`）
在合成输入上单独跑一遍 —— 否则「没有差异」可能只是因为函数永远返回空集。

本文件只用标准库，**不 import Django**（跑在 `test_no_django_required.py` 的拦截器下也一样过）。
"""
import contextlib
import importlib.util
import io
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

#: 本文件在 `server/apps/core/tests/` 下 —— 往上第三层是 `server/`
SERVER = Path(__file__).resolve().parents[3]
REPO = SERVER.parent
SCRIPTS = REPO / "scripts"
ENTRY = SCRIPTS / "run_checks.py"


def load_entry():
    """把 `scripts/run_checks.py` 当模块加载 —— 它不在 `apps` 包下，`import` 不到。

    加载它是安全的：模块顶层只算路径、断言 `server/manage.py` 在、定义两张表；
    `main()` 挂在 `if __name__ == "__main__"` 下，不会因为加载就跑起来。
    """
    spec = importlib.util.spec_from_file_location("ledger_run_checks_under_test", ENTRY)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


ENTRY_MODULE = load_entry()


@contextlib.contextmanager
def fake_scripts(cases):
    """造一个临时 `scripts/` 目录，里面是几个「只会按给定退出码退出」的假脚本。

    `cases`：`{'ok.py': 0, 'bad.py': 1}`。用真子进程而不是打桩 `subprocess.run`：
    这一层要验的正是「退出码有没有被读到、有没有被折叠」，打桩就把被测的东西换掉了。
    """
    with tempfile.TemporaryDirectory() as tmp:
        directory = Path(tmp)
        for name, code in cases.items():
            (directory / name).write_text(
                textwrap.dedent(
                    f"""
                    import sys
                    print("FAKE {name} running")
                    sys.exit({code})
                    """
                ),
                encoding="utf-8",
            )
        yield directory


def call_run(checks, scripts_dir, *, probe, strict=False):
    """跑一遍入口的核心函数，顺手把它的 stdout 收走（免得混进套件输出）。"""
    captured = io.StringIO()
    with contextlib.redirect_stdout(captured):
        result = ENTRY_MODULE.run(
            checks, scripts_dir=scripts_dir, python=sys.executable,
            strict=strict, probe=probe,
        )
    return result, captured.getvalue()


def always_runnable(_need):
    """合成探针：假装环境都满足。"""
    return True


def never_runnable(_need):
    """合成探针：假装环境都不满足。"""
    return False


class JudgeIsFalsifiable(unittest.TestCase):
    """对账判据在**合成输入**上真的报得出来 —— 不然「没有差异」是恒真的。"""

    def test_没登记的脚本会被抓到(self):
        checks = [{"script": "a.py", "needs": "django", "why": "x"}]
        with fake_scripts({"a.py": 0, "b.py": 0}) as directory:
            self.assertEqual(
                ENTRY_MODULE.missing_from_checks(directory, checks), {"b.py"},
            )
            self.assertEqual(ENTRY_MODULE.ghost_checks(directory, checks), set())

    def test_登记表里的幽灵会被抓到(self):
        checks = [{"script": "a.py", "needs": "django", "why": "x"},
                  {"script": "gone.py", "needs": "django", "why": "x"}]
        with fake_scripts({"a.py": 0}) as directory:
            self.assertEqual(ENTRY_MODULE.ghost_checks(directory, checks), {"gone.py"})
            self.assertEqual(ENTRY_MODULE.missing_from_checks(directory, checks), set())

    def test_下划线开头的内部模块与入口自己都不进表(self):
        with fake_scripts({"a.py": 0, "_lib.py": 0, ENTRY_MODULE.ENTRY: 0}) as directory:
            self.assertEqual(ENTRY_MODULE.shippable_scripts(directory), {"a.py"})


class ScriptsAndChecksAgree(unittest.TestCase):
    """正题一：`scripts/` 与 `CHECKS` 互为对方的清单。"""

    def test_没有脚本漏登记(self):
        missing = sorted(ENTRY_MODULE.missing_from_checks(SCRIPTS, ENTRY_MODULE.CHECKS))
        self.assertEqual(
            missing, [],
            "scripts/ 下有脚本没进 run_checks.py 的 CHECKS：%s\n"
            "「要有真环境才跑得动」的脚本必须有一个真的会跑它的入口，"
            "否则它就是一条写下来没人跑的检查（比没有更坏）。" % missing,
        )

    def test_登记表里没有不存在的脚本(self):
        ghost = sorted(ENTRY_MODULE.ghost_checks(SCRIPTS, ENTRY_MODULE.CHECKS))
        self.assertEqual(ghost, [], "CHECKS 点了 scripts/ 下不存在的脚本：%s" % ghost)

    def test_每条登记都写清了需要什么环境和为什么必须跑(self):
        for check in ENTRY_MODULE.CHECKS:
            with self.subTest(script=check["script"]):
                self.assertIn(
                    check["needs"], ENTRY_MODULE.NEEDS,
                    f"{check['script']} 的 needs={check['needs']!r} 不是可判的环境条件；"
                    f"合法取值：{sorted(ENTRY_MODULE.NEEDS)}",
                )
                self.assertGreaterEqual(
                    len(check["why"].strip()), 40,
                    f"{check['script']} 的 why 只有 {len(check['why'].strip())} 个字 —— "
                    "「它是干什么的」不是理由，写清**不跑它会漏掉什么**。",
                )

    def test_扫描面不是空的(self):
        scripts = ENTRY_MODULE.shippable_scripts(SCRIPTS)
        self.assertGreaterEqual(len(scripts), 3, f"只扫到 {sorted(scripts)}")
        self.assertGreaterEqual(len(ENTRY_MODULE.CHECKS), 3,
                                f"登记表只剩 {len(ENTRY_MODULE.CHECKS)} 条")


class ExitCodeReallyFolds(unittest.TestCase):
    """正题二：子脚本的退出码必须折进入口的退出码。"""

    def test_子脚本红了入口就是红的(self):
        checks = [{"script": "ok.py", "needs": "django", "why": "x"},
                  {"script": "bad.py", "needs": "django", "why": "x"}]
        with fake_scripts({"ok.py": 0, "bad.py": 1}) as directory:
            result, out = call_run(checks, directory, probe=always_runnable)
        self.assertEqual(result["rc"], 1, "子脚本 exit 1，入口却报了成功：\n" + out)
        self.assertEqual(result["failed"], [("bad.py", 1)])
        self.assertEqual(sorted(result["ran"]), ["bad.py", "ok.py"],
                         "两个都该被跑过（== 跑过但失败了，也要算跑过）")

    def test_全绿时入口才是绿的(self):
        checks = [{"script": "a.py", "needs": "django", "why": "x"},
                  {"script": "b.py", "needs": "django", "why": "x"}]
        with fake_scripts({"a.py": 0, "b.py": 0}) as directory:
            result, _out = call_run(checks, directory, probe=always_runnable)
        self.assertEqual((result["rc"], result["failed"]), (0, []))
        self.assertEqual(sorted(result["ran"]), ["a.py", "b.py"])


class SkipsAreLoud(unittest.TestCase):
    """正题三：环境不满足要**出声**，而且 `--strict` 下算失败。"""

    def test_环境不满足时既不跑也不假装跑过(self):
        checks = [{"script": "a.py", "needs": "django", "why": "x"},
                  {"script": "b.py", "needs": "live-service", "why": "x"}]
        with fake_scripts({"a.py": 0, "b.py": 0}) as directory:
            result, out = call_run(checks, directory, probe=never_runnable)
        self.assertEqual(result["ran"], [], "环境不满足却把它算成跑过了")
        self.assertEqual(sorted(n for n, _ in result["skipped"]), ["a.py", "b.py"])
        self.assertIn("跳过（2）", out)
        self.assertIn("不是通过", out, "跳过必须明说「这不是通过」")

    def test_strict_下跳过算失败(self):
        checks = [{"script": "a.py", "needs": "django", "why": "x"}]
        with fake_scripts({"a.py": 0}) as directory:
            result, out = call_run(checks, directory, probe=never_runnable, strict=True)
        self.assertEqual(result["rc"], 1, "--strict 下跳过却没红：\n" + out)

    def test_认不出来的_needs_不会被当成跑过了(self):
        checks = [{"script": "a.py", "needs": "随便写的", "why": "x"}]
        with fake_scripts({"a.py": 0}) as directory:
            result, out = call_run(checks, directory, probe=always_runnable)
        self.assertEqual(result["ran"], [])
        self.assertEqual([n for n, _ in result["skipped"]], ["a.py"])
        self.assertIn("不认识", out)

    def test_真探针在合成条件上给出预期(self):
        # `live-service` 只能靠 --live 打开；这一条与本机装了什么无关
        self.assertFalse(ENTRY_MODULE.probe_needs("live-service", python=sys.executable))
        self.assertTrue(ENTRY_MODULE.probe_needs("live-service", python=sys.executable, live=True))
        # `django` 探针的行为随机器变（本仓开发机是裸 Python，装了 requirements.txt 的机器上为真），
        # 所以这里只钉「它真的去问了、且给出一个布尔」——不能钉具体值。
        self.assertIsInstance(
            ENTRY_MODULE.probe_needs("django", python=sys.executable), bool,
        )


class EntryRunsEndToEnd(unittest.TestCase):
    """入口真的能整条跑完（真实目录、真子进程）—— 前面几条都是在合成件上跑的。"""

    def test_裸解释器下入口跑得完并点名每个脚本的去向(self):
        p = subprocess.run(
            [sys.executable, str(ENTRY)],
            cwd=str(REPO), capture_output=True, text=True,
            encoding="utf-8", errors="replace",
        )
        out = (p.stdout or "") + (p.stderr or "")
        self.assertEqual(p.returncode, 0, "入口自己跑挂了：\n" + out[-2000:])
        self.assertIn("===== 结论 =====", out)
        for check in ENTRY_MODULE.CHECKS:
            self.assertIn(check["script"], out,
                          f"{check['script']} 既没出现在「跑过」也没出现在「跳过」里：\n" + out)

    def test_登记表与目录对不上时入口直接失败而不是照样_OK(self):
        """把入口的 `SCRIPTS` 换成一个空目录 —— `CHECKS` 里的三条就全成了幽灵。

        这是 `main()` 里那道前置闸的正面用例：对不上时它必须**直接失败**，
        而不是「跑过 0 个」然后打印一个漂亮的 OK。（闸门本身的两向判据在
        `ScriptsAndChecksAgree` 里，那两条走的是真目录。）
        """
        original = ENTRY_MODULE.SCRIPTS
        captured = io.StringIO()
        try:
            with tempfile.TemporaryDirectory() as tmp:
                ENTRY_MODULE.SCRIPTS = Path(tmp)
                with contextlib.redirect_stdout(captured):
                    rc = ENTRY_MODULE.main(["--list"])
        finally:
            ENTRY_MODULE.SCRIPTS = original
        out = captured.getvalue()
        self.assertEqual(rc, 1, "CHECKS 与 scripts/ 对不上，入口却回了成功：\n" + out)
        self.assertIn("对不上", out)
        self.assertIn("点了不存在的脚本", out)


if __name__ == "__main__":
    unittest.main()
