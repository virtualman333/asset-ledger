# -*- coding: utf-8 -*-
"""冒烟脚本的底座（`scripts/_smoke_lib.py`）—— 它自己也得有人看着。

为什么这些断言值得存在
----------------------
两个冒烟脚本原先各自写死 `BASE = "http://127.0.0.1:8000/api/v1"`，于是它们能回答的问题
只有「8000 端口上那个进程对不对」，而不是「我这份代码对不对」。实测撞上旧进程时：
26 条检查 5 条 FAIL（**全是旧字段造成的假缺陷**），最后在 `summary["dividend_yield"]` 上
KeyError 中断 —— **失败小结没打印，退出码也不再反映检查结果**。

所以这条契约要钉住三件事：

1. 断言抛异常 → 折算成一条 FAIL + 退出码非零，绝不是「跑了一半没有结论」；
2. 目标规则只有一条 —— 给了 `--base` / `AL_SMOKE_BASE` 就连过去，没给就自启；
   **没有「默认连 127.0.0.1:8000」这一档**，因为那一档正是误诊的来源；
3. `scripts/` 下的脚本与 README 里点名的脚本互为对方的清单（新脚本忘了写文档、
   或文档点着不存在的脚本，都要红 —— 不手工维护白名单，两边各自现算）。

本模块不 import django（README 承诺整套单测不需要它），也不连外网、不碰数据库：
`run()` 那两条用例走的是 `--base` 分支，body 第一行就抛异常 / 只记一条通过 —— 它们自己不碰
任何业务接口。指纹核对那一节（那两条用例也在其中）会在 `127.0.0.1` 上起一个**只应答
`/health/` 的一次性假服务端**（标准库），让 `check_code_identity()` 面对真 HTTP，
而不是被打桩的返回值。

跑法（在 server/ 下）：python -m unittest discover -s apps -t .
"""
import ast
import contextlib
import io
import json
import os
import re
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

#: 本文件在 `server/apps/core/tests/` 下 —— 往上第三层才是 `server/`（跑测试的 cwd）
SERVER = Path(__file__).resolve().parents[3]
assert (SERVER / "manage.py").exists(), f"算错了 server 根：{SERVER} 下没有 manage.py"

#: `scripts/` 在仓库根下
ROOT = SERVER.parent
SCRIPTS = ROOT / "scripts"
assert SCRIPTS.is_dir(), f"算错了 scripts 目录：{SCRIPTS}"

sys.path.insert(0, str(SCRIPTS))
import _smoke_lib  # noqa: E402  （必须先把它所在目录塞进 sys.path）

from apps.core import source_stamp  # noqa: E402

from _smoke_lib import Report, api_base, resolve_target, run  # noqa: E402

#: 一个**确定连不上**的地址：验「连不上也得给出结论」，也给不该真的出网的用例兜底
UNREACHABLE_BASE = "http://127.0.0.1:1"


def call_run(body, argv):
    """跑 `_smoke_lib.run()` 并把 stdout 收走，返回 `(退出码, 输出)`。"""
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        code = run(body, argv=argv, prog="smoke_x.py")
    return code, buf.getvalue()


class TestApiBase(unittest.TestCase):
    """`--base` 允许只给 host:port，规整必须是幂等的。"""

    def test_appends_api_v1(self):
        self.assertEqual(api_base("http://host:8000"), "http://host:8000/api/v1")

    def test_strips_trailing_slashes(self):
        self.assertEqual(api_base("http://host:8000/api/v1///"), "http://host:8000/api/v1")

    def test_idempotent(self):
        """已经带 /api/v1 的再传一次，不许变成 /api/v1/api/v1。"""
        once = api_base("http://host:8000")
        self.assertEqual(api_base(once), once)

    def test_blank_stays_blank(self):
        for blank in ("", "   ", None):
            self.assertEqual(api_base(blank), "")


class TestReportNeverSwallows(unittest.TestCase):
    """报告器本身：退出码跟着失败走，取值不许抛。"""

    def test_exit_code_tracks_failures(self):
        r = Report()
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):  # 连 check() 的输出一起收走，别脏了套件的报告
            r.check("甲", True)
            r.check("乙", False, "细节")
            code = r.finish()
        self.assertEqual(code, 1)
        self.assertEqual((r.total, r.fails), (2, ["乙"]))
        self.assertIn("乙", buf.getvalue(), "失败项必须出现在小结里")

    def test_exit_code_zero_when_clean(self):
        r = Report()
        with contextlib.redirect_stdout(io.StringIO()):
            r.check("甲", True)
            self.assertEqual(r.finish(), 0)

    def test_check_returns_its_verdict(self):
        """`has_x = check("字段在不在", ...)` 这种用法依赖返回值。"""
        r = Report()
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertFalse(r.check("缺了", False))
            self.assertTrue(r.check("有", True))

    def test_field_does_not_raise(self):
        """响应字段一律走 `field()`：缺键返回 default，而不是 KeyError。

        下标取值会把「服务端的响应形状变了」变成「脚本崩了」，而崩掉的脚本没有结论。
        """
        r = Report()
        self.assertEqual(r.field({"a": 1}, "a"), 1)
        self.assertIsNone(r.field({}, "dividend_yield"))
        self.assertEqual(r.field({}, "dividend_yield", 0), 0)
        self.assertIsNone(r.field(None, "a"))
        self.assertIsNone(r.field([1, 2], "a"), "不是 dict 时也不许抛")
        self.assertIsNone(r.field("文本", "a"))


class TestExceptionBecomesOneFailure(unittest.TestCase):
    """这轮修的那个故障：断言崩溃不能让脚本失去结论。"""

    def test_body_exception_is_a_failure_not_a_traceback(self):
        def boom(base, report):
            summary = {"cost_basis": "100"}  # 少了 dividend_yield
            report.check("总览含股息率字段", "dividend_yield" in summary, sorted(summary))
            _ = summary["dividend_yield"]  # 原来是这一行：直接下标

        with stub_health(health_payload()) as base:  # 指纹核对先过，剩下的失败只来自 body
            code, out = call_run(boom, ["--base", base])
        self.assertEqual(code, 1, "崩溃之后退出码必须是 1：\n" + out)
        self.assertIn("KeyError", out, "异常类型要出现在报告里：\n" + out)
        self.assertIn("条失败", out, "小结必须打出来：\n" + out)
        self.assertNotIn("全部通过", out)

    def test_passing_body_still_exits_zero(self):
        """反向对照：兜底不许把「没崩」也判成失败，否则它是恒红的。

        指向一个**能自证**的假服务端：`--base` 模式下脚本会先核对源码指纹，核对不过本身
        就是一条 FAIL（那是刻意的）—— 换个连不上的地址，这条用例就测到别的东西上去了。
        """
        def fine(base, report):
            report.check("一切正常", True)

        with stub_health(health_payload()) as base:
            code, out = call_run(fine, ["--base", base])
        self.assertEqual(code, 0, out)
        self.assertIn("全部通过", out)


class TestTargetRules(unittest.TestCase):
    """目标规则：给了地址连过去，没给就自启 —— 没有「默认连 8000」这一档。"""

    def _resolve(self, argv, env=None):
        old = os.environ.get(_smoke_lib.ENV_BASE)
        if env is None:
            os.environ.pop(_smoke_lib.ENV_BASE, None)
        else:
            os.environ[_smoke_lib.ENV_BASE] = env
        try:
            return resolve_target(argv, prog="smoke_x.py")
        finally:
            if old is None:
                os.environ.pop(_smoke_lib.ENV_BASE, None)
            else:
                os.environ[_smoke_lib.ENV_BASE] = old

    def test_no_base_means_self_start(self):
        """防倒退：曾经的「默认」是连 127.0.0.1:8000 上那个谁也不知道来历的进程。"""
        self.assertEqual(self._resolve([]), ("self", ""))

    def test_base_flag_wins(self):
        self.assertEqual(
            self._resolve(["--base", "http://127.0.0.1:8010"]),
            ("external", "http://127.0.0.1:8010/api/v1"),
        )

    def test_base_with_api_v1_is_kept_as_is(self):
        self.assertEqual(
            self._resolve(["--base", "https://ledger.example.com/api/v1"]),
            ("external", "https://ledger.example.com/api/v1"),
        )

    def test_env_var_also_selects_external(self):
        self.assertEqual(
            self._resolve([], env="http://host:9"),
            ("external", "http://host:9/api/v1"),
        )

    def test_blank_env_var_falls_back_to_self_start(self):
        """环境变量存在但为空（.env 里留空的那种写法）不许变成「连空地址」。"""
        self.assertEqual(self._resolve([], env="   "), ("self", ""))


class TestScriptsDoNotHardcodeAnAddress(unittest.TestCase):
    """脚本里不许再出现写死的服务地址 —— 那是本轮故障的根。"""

    @staticmethod
    def _literal_urls(path: Path):
        """扫出脚本里**写死的完整地址**。

        不下钻 f-string：`f"http://127.0.0.1:{port}/api/v1"` 是动态拼的，不算写死。
        （f-string 的字面片段在 AST 里同样是 `Constant`，直接 `ast.walk` 会被它带红 ——
        这个坑当场踩到过：`_smoke_lib.py` 被判成「写死了 http://127.0.0.1:」。）
        """
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        found: list[str] = []

        def visit(node):
            if isinstance(node, ast.JoinedStr):
                return
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                text = node.value.strip()
                # `\S+` 把多行说明排除掉：注释和 docstring 里提到地址不算写死。
                if re.fullmatch(r"https?://\S+", text):
                    found.append(text)
            for child in ast.iter_child_nodes(node):
                visit(child)

        visit(tree)
        return found

    @staticmethod
    def _scan(text: str):
        """把一段源码写进临时文件再扫 —— 用于正反两面自证。"""
        path = Path(tempfile.gettempdir()) / "_url_scan_control.py"
        path.write_text(text, encoding="utf-8")
        return TestScriptsDoNotHardcodeAnAddress._literal_urls(path)

    def test_no_script_contains_a_literal_base_url(self):
        offenders = {f.name: self._literal_urls(f) for f in sorted(SCRIPTS.glob("*.py"))}
        offenders = {k: v for k, v in offenders.items() if v}
        self.assertEqual(
            offenders, {},
            "脚本里出现了写死的地址：%r\n目标地址只能来自 --base / AL_SMOKE_BASE —— "
            "写死地址会让脚本去验「那个端口上的进程」，而不是验这份代码。" % (offenders,),
        )

    def test_the_scan_actually_catches_a_hardcoded_url(self):
        """反向对照一：这条检查抓得住写死的地址，不是恒真。"""
        self.assertEqual(
            self._scan('BASE = "http://127.0.0.1:8000/api/v1"\n'),
            ["http://127.0.0.1:8000/api/v1"],
        )

    def test_the_scan_ignores_urls_inside_prose(self):
        """反向对照二：docstring 里提到地址不算写死 —— 否则说明文字会把检查带红。"""
        self.assertEqual(self._scan('"""跑之前先起 http://127.0.0.1:8000 。"""\n'), [])
        self.assertEqual(self._scan("# 以前这里写的是 http://127.0.0.1:8000/api/v1\nBASE = None\n"), [])


class TestScriptsAndReadmeAgree(unittest.TestCase):
    """`scripts/` 与 README 里点名的脚本互为对方的清单。"""

    @staticmethod
    def scripts():
        """`scripts/` 下对外可执行的脚本。`_` 开头的是内部模块，不进清单。"""
        return {f.name for f in SCRIPTS.glob("*.py") if not f.name.startswith("_")}

    @staticmethod
    def documented():
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        return set(re.findall(r"scripts/([A-Za-z0-9_.-]+\.py)", readme))

    def test_every_script_is_documented(self):
        missing = self.scripts() - self.documented()
        self.assertEqual(
            missing, set(),
            "scripts/ 下有脚本没在 README 里出现：%s\n"
            "README 的「目录结构」把 scripts/ 写成「冒烟测试等工具脚本」，那就得点得出名字。"
            % sorted(missing),
        )

    def test_every_documented_script_exists(self):
        ghost = self.documented() - {f.name for f in SCRIPTS.glob("*.py")}
        self.assertEqual(
            ghost, set(),
            "README 点了不存在的脚本：%s（改名或删掉之后文档没跟上）" % sorted(ghost),
        )

    def test_the_scan_actually_sees_scripts_and_docs(self):
        """扫描面自证：两边都真扫到了东西，否则上面两条是恒真的。"""
        self.assertGreaterEqual(len(self.scripts()), 2, f"只扫到 {sorted(self.scripts())}")
        self.assertGreaterEqual(len(self.documented()), 2, f"只扫到 {sorted(self.documented())}")


@contextlib.contextmanager
def stub_health(payload=None):
    """在 `127.0.0.1` 上起一个只应答 `/health/` 的一次性服务端，yield 它的 `/api/v1` 基址。

    `payload=None` 时一律回 404 —— 模拟「对面是个没有这个端点的旧版本」，那正是本轮要
    能认出来的第一种情况。用真 HTTP 而不是打桩 `fetch_health()`：这样连「请求真的发出去了
    吗、路径拼对了吗、404 真的走的是那个分支吗」一起被验到。
    """

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802 - http.server 规定的接口名
            if payload is None:
                self.send_response(404)
                self.end_headers()
                return
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):  # 访问日志别掺进套件的输出
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(
        target=server.serve_forever, kwargs={"poll_interval": 0.02}, daemon=True
    )
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}/api/v1"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def health_payload(source=None, **top):
    """拼一份「服务端报回来的」payload —— 默认就是**一致**的那一种。"""
    local = _smoke_lib.local_snapshot()
    body = {
        "files": local.files,
        "fingerprint": local.fingerprint,
        "newest_file": "apps/core/source_stamp.py",
        "newest_mtime": "2026-09-17T00:00:00Z",
        "unreadable": [],
        "stale": False,
        "stale_files": [],
    }
    body.update(source or {})
    payload = {
        "service": "asset-ledger",
        "pid": 4321,
        "started_at": "2026-09-17T00:00:00Z",
        "uptime_seconds": 1.5,
        "source": body,
    }
    payload.update(top)
    return payload


def probe(base):
    """跑一遍 `check_code_identity()`，返回 `(报告, 输出)`。"""
    report = Report()
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        _smoke_lib.check_code_identity(base, report, mode="external")
    return report, buf.getvalue()


class TestCodeIdentityProbe(unittest.TestCase):
    """指纹核对：只有「指纹一致、且进程没落后于源码」算通过，其余结局各有各的话说。

    这是 `--base` 模式唯一能挡住上一轮那类误诊的东西（5 条假缺陷全部来自一个跑着旧代码
    的进程），所以五种结局都得真的走一遍 —— 全都算通过固然是坏的，「一律失败」也是坏的。
    """

    NAME = "服务端跑的就是当前这份代码"

    def test_agreement_passes(self):
        with stub_health(health_payload()) as base:
            report, out = probe(base)
        self.assertEqual(report.total, 1, out)
        self.assertEqual(report.fails, [], out)
        self.assertIn("已确认", out)

    def test_the_payload_decides_not_the_network(self):
        """反向对照：同一个假服务端、同一套请求，只把指纹换掉，结论就从通过变成失败。"""
        with stub_health(health_payload()) as base:
            passed, _ = probe(base)
        with stub_health(health_payload(source={"fingerprint": "0" * 64})) as base:
            failed, out = probe(base)
        self.assertEqual(passed.fails, [])
        self.assertEqual(failed.fails, [self.NAME])
        self.assertIn("别的构建", out)
        self.assertIn("不是这份源码的缺陷", out)

    def test_404_is_named_as_an_old_build(self):
        with stub_health(None) as base:
            report, out = probe(base)
        self.assertEqual(report.fails, [self.NAME])
        self.assertIn("旧版本", out)
        self.assertIn("404", out)

    def test_a_stale_process_fails(self):
        """指纹一样、但服务端自己承认「起来之后源码又被改过」，也不算通过：

        先改文件再发请求，指纹算的是**磁盘上的新内容**，而进程跑的仍是旧代码 ——
        只看指纹会把这一种漏过去。
        """
        payload = health_payload(source={"stale": True, "stale_files": ["apps/market/services.py"]})
        with stub_health(payload) as base:
            report, out = probe(base)
        self.assertEqual(report.fails, [self.NAME])
        self.assertIn("又被改过", out)
        self.assertIn("services.py", out)

    def test_a_foreign_service_fails(self):
        with stub_health(health_payload(service="some-other-app")) as base:
            report, out = probe(base)
        self.assertEqual(report.fails, [self.NAME])
        self.assertIn("指错地方", out)

    def test_an_unreachable_target_fails_without_crashing(self):
        """连不上也得给结论（一条 FAIL），不许抛出去 —— 崩掉的脚本没有结论。"""
        report, out = probe(UNREACHABLE_BASE)
        self.assertEqual(report.fails, [self.NAME])
        self.assertIn("拿不到", out)

    def test_both_sides_load_one_implementation(self):
        """指纹只许有一份实现：脚本加载的就是服务端那个文件本身，算出来的也得是同一个数。

        各写一份哈希实现是这里最容易犯的错 —— 两份实现一旦漂移，「比对」就变成自说自话。
        """
        module = _smoke_lib.load_source_stamp()
        self.assertEqual(
            Path(module.__file__).resolve(),
            (SERVER / "apps" / "core" / "source_stamp.py").resolve(),
        )
        direct = source_stamp.scan(SERVER)
        self.assertGreaterEqual(direct.files, 50, f"只扫到 {direct.files} 个 .py，比对是空的")
        self.assertEqual(_smoke_lib.local_snapshot().fingerprint, direct.fingerprint)
        self.assertEqual(_smoke_lib.local_snapshot().files, direct.files)


if __name__ == "__main__":
    unittest.main()
