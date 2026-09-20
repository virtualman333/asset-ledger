# -*- coding: utf-8 -*-
"""README 承诺的「单元测试不需要数据库、不需要 Django」—— 这条承诺没有人验证过，
而它错起来是**静默**的。

为什么必须真跑一遍
------------------
装了 `requirements.txt` 的机器上必然有 Django，于是任何一个测试模块多一句
`from django...`，在开发机上照常全绿；只有照着 README 在一台**裸 Python** 上跑的人才会
看到一片 ERROR —— 而那正是 README 承诺不会发生的事。静态扫 import 只能证明「那几行字
不在这里」，证明不了「这套测试真的不需要 Django」。

所以这里装一个导入拦截器，把 `django` / `rest_framework` / `config` 挡掉，再按 README
给的那条命令（`python -m unittest discover -s apps -t .`）原样跑一遍：

- 被拦的模块一 import 就抛 ImportError —— 与「这台机器没装 Django」等价；
- `test_scheduler_autostart.py` 正是靠 `except ImportError: raise unittest.SkipTest`
  整条跳过的（它的模块说明里写着这是被要求的行为），所以拦掉之后报告应当是
  `OK (skipped=1)` —— README 那句话的字面承诺；
- `skipped` 就是「需要 Django 的模块数」，**只允许是 1**：再多一个模块需要 Django，
  这个数就变成 2，这里立刻红。

这份检查与环境无关：本机有没有 Django 都一样（拦掉之后那条模块一律跳过）。
本仓库的开发机就是**裸 Python**（没有 venv、`import django` 直接 ModuleNotFoundError），
所以这里拦不拦都能过；**装了依赖的机器才是这条检查真正要保的场合**。

两条反向对照（都在下面），没有它们上面那些可能是恒真的：
1. 拦截器真的能拦住 `import django`（断言子进程里它抛 ImportError）；
2. AST 扫下来，`apps/*/tests/` 里**只有一个**模块 import 了 django —— 用 AST 而不是
   文本匹配，是因为本文件的说明里写满了 `django` 这五个字母。

一个自己撞到的坑
----------------
子进程跑的是同一套 discovery，它会扫到这个文件 —— 于是又 spawn 一个子进程，无限套娃
（第一次跑挂了五分钟才被手工掐掉）。所以子进程里先把本模块换成空模块（`sys.modules`
里塞一个空的 `ModuleType`），这样它贡献 0 条用例、不影响 `skipped` 这个数字。

为什么「N 条单测」这个数字也由本模块看着
----------------------------------------
上面那个 `ran` 是**整套测试的真实条数**，而仓库里曾有一句话把它手抄下来：「`452` 条单测
一条都不红」。这个数字**同时写在两处**（README 与 `scripts/run_checks.py`），两处都过期了
—— 手抄的数字必然漂，而这句话的真值没人复核、也复核不了。真正有东西兜的是**后半句**
（「一条都不红」）：本模块每次都会真的把整套跑一遍并要求 0 失败/0 错误。所以数字被删掉，
换成一个不依赖条数的说法，并且由 `TestTheSuiteSizeIsNotHandCopied` 挡住它再长回来。

跑法（在 server/ 下）：python -m unittest discover -s apps -t .
"""
import ast
import re
import subprocess
import sys
import textwrap
import unittest
from pathlib import Path

#: 本文件在 `server/apps/core/tests/` 下 —— 往上第三层才是 `server/`（跑测试的 cwd）
SERVER = Path(__file__).resolve().parents[3]
assert (SERVER / "manage.py").exists(), f"算错了 server 根：{SERVER} 下没有 manage.py"

#: 仓库根（`ROOT` 这个词在下面的「手抄数字」那一节要用：它扫 README 与 scripts/）
ROOT = SERVER.parent

#: 本模块自己的点分路径（子进程里要把它换成空模块，见 `_CHILD_PRELUDE`）
SELF_MODULE = ".".join(
    Path(__file__).resolve().relative_to(SERVER).with_suffix("").parts
)

#: 顶层模块名命中这些前缀，就等于「这个模块需要 Django」
BLOCKED_ROOTS = ("django", "rest_framework", "config")

#: README 明确点名的那条例外：它是唯一获准需要 Django 的测试模块
THE_ONE_EXCEPTION = "apps/market/tests/test_scheduler_autostart.py"

#: README 的快速开始里给的那条命令
DISCOVER_ARGV = ["run_tests", "discover", "-s", "apps", "-t", "."]

#: 子进程开头装的两样东西：导入拦截器 + 把本模块换成空模块。
#:
#: 后者是**防递归**：被拦的那次跑的是同一套 discovery，而 discovery 会扫到这个文件，
#: 于是它又会 spawn 一个子进程……实测无限套娃。换成空模块而不是 SkipTest，是为了让
#: 子进程的报告仍然是 `OK (skipped=1)` —— 那个数字是 README 的字面承诺。
_CHILD_PRELUDE = textwrap.dedent(
    '''
    import sys
    import types

    class _BlockDjango:
        def __init__(self, roots):
            self.roots = tuple(roots)

        def find_spec(self, name, path=None, target=None):
            if name.split(".")[0] in self.roots:
                raise ImportError("blocked for contract test: " + name)
            return None

    sys.meta_path.insert(0, _BlockDjango({roots!r}))

    sys.modules[{self_module!r}] = types.ModuleType({self_module!r})
    '''
)


def run_suite(blocked_roots):
    """按 README 的命令跑一遍，返回 `(返回码, Ran 数, failures, errors, skipped, 输出)`。"""
    code = _CHILD_PRELUDE.format(
        roots=tuple(blocked_roots), self_module=SELF_MODULE
    ) + textwrap.dedent(
        f'''
        import unittest
        # `module=None` 才会走 discover 分支 —— 用默认的 `__main__` 时 discover 参数
        # 会被当成普通 TestProgram 的参数，直接报 unrecognized arguments。
        unittest.main(module=None, argv={DISCOVER_ARGV!r}, exit=True)
        '''
    )
    p = subprocess.run(
        [sys.executable, "-c", code],
        cwd=str(SERVER), capture_output=True, text=True,
        encoding="utf-8", errors="replace",
    )
    out = (p.stdout or "") + (p.stderr or "")

    def num(key):
        m = re.search(rf"{key}=(\d+)", out)
        return int(m.group(1)) if m else 0

    ran = re.search(r"Ran (\d+) tests?", out)
    return {
        "rc": p.returncode,
        "ran": int(ran.group(1)) if ran else -1,
        "failures": num("failures"),
        "errors": num("errors"),
        "skipped": num("skipped"),
        "out": out,
    }


class TestSuiteRunsWithoutDjango(unittest.TestCase):
    """拦掉 Django 之后，整套测试必须照跑不误、只有那一条例外被跳过。"""

    @classmethod
    def setUpClass(cls):
        cls.blocked = run_suite(BLOCKED_ROOTS)

    def test_no_failures_or_errors(self):
        r = self.blocked
        self.assertEqual(
            (r["failures"], r["errors"]), (0, 0),
            "拦掉 Django 之后出现了失败/错误 —— 这些模块偷偷依赖了 Django，"
            "而 README 承诺过它们不需要。装了依赖的机器上永远看不出来，"
            "照着 README 在裸 Python 上跑的人才会撞上：\n"
            + r["out"][-2500:],
        )
        self.assertEqual(r["rc"], 0, "退出码非 0：\n" + r["out"][-2500:])

    def test_exactly_one_module_needs_django(self):
        r = self.blocked
        self.assertEqual(
            r["skipped"], 1,
            "「需要 Django 的模块」不再是唯一那一个（skipped=%d）。README 说的是"
            "「唯一的例外是 test_scheduler_autostart.py，没装 Django 时它整条跳过，"
            "报告里是 OK (skipped=1)」—— 多一个模块需要 Django，这句话就不成立了：\n%s"
            % (r["skipped"], r["out"][-2000:]),
        )

    def test_report_reads_ok_skipped_1(self):
        """README 里的字面承诺：报告是 `OK (skipped=1)`，不是 `FAILED`。"""
        self.assertIn("OK (skipped=1)", self.blocked["out"])
        self.assertNotIn("FAILED", self.blocked["out"])

    def test_the_suite_really_ran(self):
        """扫描面自证：真的跑了一百多条，而不是「什么都没收上来所以全绿」。"""
        self.assertGreaterEqual(
            self.blocked["ran"], 100,
            f"只跑了 {self.blocked['ran']} 条 —— 扫描面塌了，上面几条就成了恒真",
        )


class TestOnlyOneTestModuleImportsDjango(unittest.TestCase):
    """反向对照：AST 扫一遍，能 import 到 django 的测试模块必须恰好是那一个。

    用 AST 而不是文本匹配 —— 本文件的说明里写满了 `django` 这五个字母，注释与字符串
    会把文本匹配骗过去；AST 只看真正的 import 语句。
    """

    @staticmethod
    def imports_by_module():
        found = {}
        for f in sorted((SERVER / "apps").glob("*/tests/*.py")):
            tree = ast.parse(f.read_text(encoding="utf-8"), filename=str(f))
            roots = set()
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    roots |= {a.name.split(".")[0] for a in node.names}
                elif isinstance(node, ast.ImportFrom) and node.module:
                    roots.add(node.module.split(".")[0])
            hits = sorted(roots & set(BLOCKED_ROOTS))
            if hits:
                found[f.relative_to(SERVER).as_posix()] = hits
        return found

    def test_only_the_documented_exception_imports_django(self):
        found = self.imports_by_module()
        self.assertEqual(
            found, {THE_ONE_EXCEPTION: ["django"]},
            "需要 Django 的测试模块变了：%r\nREADME 说的是「唯一的例外是 %s」；"
            "新加的那个模块请改成不 import django（绝大多数断言都做得到 —— "
            "把业务规则留在不 import django 的模块里）" % (found, THE_ONE_EXCEPTION),
        )

    def test_the_scan_actually_sees_test_modules(self):
        """扫描面自证：真扫到了测试文件，否则上面那条是恒真的。"""
        files = sorted((SERVER / "apps").glob("*/tests/*.py"))
        self.assertGreaterEqual(len(files), 5, f"只扫到 {len(files)} 个测试模块")


class TestBlockerItselfIsEffective(unittest.TestCase):
    """反向对照：拦的是 Django，不是「随便拦点什么」。"""

    def test_blocking_a_module_actually_raises_importerror(self):
        """被拦的模块 import 必须抛 ImportError（与「没装」等价）。"""
        code = _CHILD_PRELUDE.format(
            roots=("django",), self_module=SELF_MODULE
        ) + textwrap.dedent(
            '''
            import sys
            try:
                import django  # noqa: F401
            except ImportError:
                print("BLOCKED_OK")
                sys.exit(0)
            print("NOT_BLOCKED")
            sys.exit(1)
            '''
        )
        p = subprocess.run(
            [sys.executable, "-c", code], cwd=str(SERVER),
            capture_output=True, text=True, encoding="utf-8", errors="replace",
        )
        self.assertIn("BLOCKED_OK", p.stdout, p.stdout + p.stderr)

    def test_unblocked_modules_still_import(self):
        """反向对照：拦截器不该把整个 import 机制搞坏（未被拦的模块照常可用）。"""
        code = _CHILD_PRELUDE.format(
            roots=("django",), self_module=SELF_MODULE
        ) + textwrap.dedent(
            '''
            import sys
            import json
            import apps.market.tencent as t
            print("NOT_BLOCKED_OK", hasattr(t, "__file__"))
            sys.exit(0)
            '''
        )
        p = subprocess.run(
            [sys.executable, "-c", code], cwd=str(SERVER),
            capture_output=True, text=True, encoding="utf-8", errors="replace",
        )
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertIn("NOT_BLOCKED_OK True", p.stdout)


class TestTheSuiteSizeIsNotHandCopied(unittest.TestCase):
    """「N 条单测」这种手抄数字会漂 —— 同一个数字（`452`）曾同时写在 README 与
    `scripts/run_checks.py` 里，两处都过期了。

    这里不是要禁止写数字，而是要禁止**把整套测试的条数当结论写在文档/脚本里**：
    它必然漂，且没有任何东西会提醒。真要提这件事，就提「整套测试都绿」—— 那半句由
    上面那个 `TestSuiteRunsWithoutDjango` 每次真跑一遍兜着。
    """

    #: 「N 条单测 / 测试 / 用例」—— 手抄整套测试规模的那种写法
    HAND_COPIED_RE = re.compile(r"\d+\s*条(?:单测|测试|用例)")

    #: **本文件自己**不受这条约束：上面的反向对照必须真的把例子写出来，否则「扫描器有
    #: 判别力」就成了空话。豁免不是白名单 —— `test_豁免不是空的` 断言这个文件里**确实**
    #: 有那种写法，哪天真删了，那条会红。
    SELF = Path(__file__).resolve()

    @classmethod
    def scanned_files(cls):
        files = [ROOT / "README.md"]
        files += [p for p in (ROOT / "scripts").glob("*.py") if "__pycache__" not in p.parts]
        files += [p for p in (SERVER / "apps").rglob("*.py") if "__pycache__" not in p.parts]
        return [p for p in files if p.is_file()]

    def test_扫描面不是空的(self):
        files = self.scanned_files()
        self.assertGreaterEqual(len(files), 20, f"只扫到 {len(files)} 个文件")
        self.assertTrue(any(p.name == "run_checks.py" for p in files), "连 run_checks.py 都没扫到")

    def test_扫描器认得出这种写法(self):
        """反向对照：不给它一个真例子，上面那条可能只是「一直没匹配上」。"""
        sample = "实测 452 条单测一条都不红"
        self.assertEqual(self.HAND_COPIED_RE.findall(sample), ["452 条单测"])
        self.assertEqual(self.HAND_COPIED_RE.findall("整套单测一条都不红"), [])

    def test_豁免不是空的(self):
        """反向对照：豁免掉本文件，是因为**本文件里真的有**那种写法。"""
        own = self.SELF.read_text(encoding="utf-8")
        self.assertGreaterEqual(
            len(self.HAND_COPIED_RE.findall(own)), 1,
            "本文件里已经没有手抄条数的例子了 —— 那 SELF 这条豁免就该删掉",
        )

    def test_没有手抄的整套测试条数(self):
        hits = []
        for path in self.scanned_files():
            if path.resolve() == self.SELF:
                continue
            for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                for found in self.HAND_COPIED_RE.findall(line):
                    hits.append(f"{path.relative_to(ROOT)}:{lineno} {found}")
        self.assertEqual(
            hits, [],
            "这些地方手抄了整套测试的条数 —— 它必然漂，而且没人会复核。"
            "改成「整套测试都绿」这种不依赖条数的说法，真值由本模块的 "
            "TestSuiteRunsWithoutDjango 每次真跑一遍给出",
        )


if __name__ == "__main__":
    unittest.main()
