# -*- coding: utf-8 -*-
"""源码指纹与自证端点（`apps/core/source_stamp.py` / `apps/core/views.py`）的判据。

为什么这些断言值得存在
----------------------
`/api/v1/health/` 报出的指纹是冒烟脚本 `--base` 模式唯一的挡箭牌。上一轮的 5 条假缺陷
全部来自一个跑着旧代码的进程，而脚本当时只会声明「我无法自证」。**指纹一旦算错，挡箭牌
就变成另一种误导**：把「同一份代码」判成不同（跨机核对必然误报），或者把「不同」判成相同
（正是它要防的事）。所以这里钉的是口径本身：

1. 内容变 → 指纹变，第几个文件变都算；
2. 非 `.py`、`__pycache__`、`.venv`（那里可能躺着**另一份**被装进去的代码）都不进指纹；
3. **行尾归一**：同一份代码在 CRLF 与 LF 上必须算出同一个指纹；
4. 路径入哈希：内容一样、位置不同就是另一份代码；
5. 路径与内容之间要隔开：`a.py` 里写 `xb.py`（一个文件）与 `a.py` 写 `x` + 空的 `b.py`
   （两个文件）在「直接拼接」下会撞成同一个指纹 —— 下面有反向对照真的把它们撞给你看；
6. 读不到的文件不许静默消失（记进 `unreadable`），也不许让扫描整体崩掉；
7. 「进程落后于自己那份源码」用 mtime 判，测试用 `os.utime` 造时间点，不看真实时钟。

本模块**不 import django**（README 承诺整套单测不需要它），也不联网、不连库：只碰临时目录。
唯一的例外是最后一节 —— 那是**结构锁**，它证明的是「端点真的挂在路由上」，跑不动视图；
视图能不能真跑通由冒烟脚本自启那一遍负责（那一遍会真的去请求 `/health/`）。
"""
import ast
import os
import shutil
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from apps.core import source_stamp
from apps.core.source_stamp import scan

#: 本文件在 `server/apps/core/tests/` 下 —— 往上第三层才是 `server/`
SERVER = Path(__file__).resolve().parents[3]
assert (SERVER / "manage.py").exists(), f"算错了 server 根：{SERVER}"


class TempTree(unittest.TestCase):
    """建一棵临时源码树，随手写文件，用完删掉。"""

    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="al_stamp_"))
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)

    def write(self, rel: str, text: str, eol: str = "\n") -> Path:
        path = self.root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(text.replace("\n", eol).encode("utf-8"))
        return path

    def touch_at(self, rel: str, ts: float) -> Path:
        path = self.root / rel
        os.utime(path, (ts, ts))
        return path


class TestFingerprintScope(TempTree):
    """指纹算的是「这份代码」，不是「这个目录里的一切」。"""

    def test_content_change_changes_the_fingerprint(self):
        self.write("a.py", "x = 1\n")
        before = scan(self.root).fingerprint
        self.write("a.py", "x = 2\n")
        self.assertNotEqual(scan(self.root).fingerprint, before)

    def test_every_file_counts_not_just_the_first(self):
        """总长与首文件都不变、只动最后一个：遍历漏掉任何一个文件都该露馅。"""
        files = [f"pkg{m}/mod{n}.py" for m in range(3) for n in range(2)]
        for rel in files:
            self.write(rel, "v = 1\n")
        before = scan(self.root).fingerprint
        self.write(files[-1], "v = 2\n")
        self.assertNotEqual(scan(self.root).fingerprint, before)

    def test_non_python_files_are_out_of_scope(self):
        self.write("a.py", "x = 1\n")
        before = scan(self.root).fingerprint
        self.write("README.md", "改了文档\n")
        self.write(".env", "DB_PASSWORD=whatever\n")
        self.write("requirements.txt", "django==5.2\n")
        self.assertEqual(scan(self.root).fingerprint, before)

    def test_dependency_and_cache_dirs_are_out_of_scope(self):
        """`.venv` 里可能躺着**另一份**被装进去的同一套代码，那不该改变「这份代码」的指纹。"""
        self.write("a.py", "x = 1\n")
        before = scan(self.root).fingerprint
        self.write(".venv/Lib/site-packages/apps/core/models.py", "from django.db import models\n")
        self.write("venv/Lib/site-packages/manage.py", "import django\n")
        self.write("__pycache__/leftover.py", "z = 1\n")
        self.write("node_modules/pkg/index.py", "z = 1\n")
        self.assertEqual(scan(self.root).fingerprint, before)

    def test_line_ending_is_normalised(self):
        """同一份代码在 Windows（CRLF）与 Linux（LF）上必须算出同一个指纹。

        否则「服务端和本机是不是同一份代码」这个核对会变成**看谁的行尾碰巧一样** ——
        跨机核对必然误报成「不同」。
        """
        self.write("a.py", "x = 1\ny = 2\n", eol="\n")
        lf = scan(self.root).fingerprint
        self.write("a.py", "x = 1\ny = 2\n", eol="\r\n")
        self.assertEqual(scan(self.root).fingerprint, lf)
        # 反向对照：内容真的变了还是要变 —— 归一不是「忽略一切」
        self.write("a.py", "x = 1\ny = 3\n", eol="\r\n")
        self.assertNotEqual(scan(self.root).fingerprint, lf)

    def test_path_is_part_of_the_identity(self):
        self.write("a.py", "x = 1\n")
        before = scan(self.root).fingerprint
        (self.root / "a.py").rename(self.root / "b.py")
        self.assertNotEqual(scan(self.root).fingerprint, before)

    def test_the_tree_can_be_moved(self):
        """同一棵树换个位置（临时目录每次不同）必须算出同一个指纹，否则一切核对无从谈起。"""
        self.write("a.py", "x = 1\n")
        self.write("pkg/b.py", "y = 1\n")
        before = scan(self.root).fingerprint

        other = Path(tempfile.mkdtemp(prefix="al_stamp_"))
        self.addCleanup(shutil.rmtree, other, ignore_errors=True)
        for rel in ("a.py", "pkg/b.py"):
            target = other / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes((self.root / rel).read_bytes())
        self.assertEqual(scan(other).fingerprint, before)


class TestPathAndContentDoNotRunTogether(TempTree):
    """路径与内容之间必须隔开 —— 直接拼字节会让两棵不同的树算出同一个指纹。"""

    @staticmethod
    def naive_digest(root: Path) -> str:
        """反向对照用：**直接**把路径与内容拼起来喂给哈希的幼稚实现。"""
        import hashlib

        digest = hashlib.sha256()
        for path in source_stamp.iter_source_files(root):
            digest.update(path.relative_to(root).as_posix().encode("utf-8"))
            digest.update(path.read_bytes().replace(b"\r\n", b"\n"))
        return digest.hexdigest()

    def _colliding_pair(self):
        """构造一对「在幼稚实现下必然相撞」的树。

        一个文件 `a.py` 内容为 `xb.py`，与「`a.py` 内容 `x` + 空的 `b.py`」拼出来的
        字节流完全相同：`a.py` + `xb.py` == `a.py` + `x` + `b.py` + ``。
        """
        one = Path(tempfile.mkdtemp(prefix="al_stamp_one_"))
        two = Path(tempfile.mkdtemp(prefix="al_stamp_two_"))
        self.addCleanup(shutil.rmtree, one, ignore_errors=True)
        self.addCleanup(shutil.rmtree, two, ignore_errors=True)
        (one / "a.py").write_bytes(b"xb.py")
        (two / "a.py").write_bytes(b"x")
        (two / "b.py").write_bytes(b"")
        return one, two

    def test_the_construction_really_collides_in_the_naive_reader(self):
        """先证明这个构造不是空转：幼稚实现下它们**确实**撞了，否则下面那条测不到粘滞。"""
        one, two = self._colliding_pair()
        self.assertEqual(self.naive_digest(one), self.naive_digest(two))

    def test_the_real_fingerprint_keeps_them_apart(self):
        one, two = self._colliding_pair()
        self.assertNotEqual(scan(one).fingerprint, scan(two).fingerprint)


class TestUnreadableIsNotSilent(TempTree):
    """读不到的文件：不抛、不算进指纹，但也不许不声不响。"""

    def test_unreadable_paths_are_reported_and_not_counted(self):
        good = self.write("a.py", "x = 1\n")
        missing = self.root / "gone.py"  # 根本不存在
        is_a_directory = self.root / "dir.py"
        is_a_directory.mkdir()  # read_bytes() 会抛：真的存在、但读不出内容

        snap = scan(self.root, paths=[good, missing, is_a_directory])
        self.assertEqual(snap.files, 1)
        self.assertEqual(sorted(snap.unreadable), ["dir.py", "gone.py"])

    def test_nothing_unreadable_when_everything_reads(self):
        """反向对照：全都读得到时那个列表必须是空的，否则它是恒非空的噪声。"""
        good = self.write("a.py", "x = 1\n")
        self.assertEqual(scan(self.root, paths=[good]).unreadable, ())

    def test_a_file_vanishing_mid_scan_does_not_raise(self):
        """扫描面是「先列目录、再逐个读」，中间被删掉不该让整个 `/health/` 崩掉。"""
        self.write("a.py", "x = 1\n")
        listed = source_stamp.iter_source_files(self.root)
        (self.root / "a.py").unlink()
        snap = scan(self.root, paths=listed)
        self.assertEqual(snap.files, 0)
        self.assertEqual(snap.unreadable, ("a.py",))


class TestStaleness(TempTree):
    """「进程起来之后源码又被改过」的判据：只看 mtime，不看真实时钟。"""

    def test_changed_after_lists_only_the_newer_files(self):
        self.touch_at(self.write("old.py", "x = 1\n").relative_to(self.root).as_posix(), 1_600_000_000)
        self.touch_at(self.write("new.py", "y = 1\n").relative_to(self.root).as_posix(), 1_700_000_000)
        snap = scan(self.root)
        self.assertEqual(snap.changed_after(1_650_000_000), ["new.py"])
        self.assertEqual(snap.changed_after(1_750_000_000), [], "比所有文件都新 = 没人落后")
        self.assertEqual(snap.newest_file, "new.py")
        self.assertAlmostEqual(snap.newest_mtime, 1_700_000_000.0)

    def test_limit_keeps_the_most_recent(self):
        for i in range(4):
            rel = self.write(f"f{i}.py", "x = 1\n").relative_to(self.root).as_posix()
            self.touch_at(rel, 1_600_000_000 + i)
        snap = scan(self.root)
        self.assertEqual(snap.changed_after(1_500_000_000, limit=2), ["f3.py", "f2.py"])

    def test_empty_and_missing_roots_are_not_errors(self):
        empty = scan(self.root)
        self.assertEqual((empty.files, empty.newest_file, empty.newest_mtime), (0, "", 0.0))
        self.assertEqual(empty.changed_after(0), [])
        self.assertTrue(empty.fingerprint, "空树也该有个指纹，只是没有文件")

        ghost = scan(self.root / "no_such_dir")
        self.assertEqual(ghost.files, 0)
        self.assertEqual(source_stamp.iter_source_files(self.root / "no_such_dir"), [])


class TestIso(unittest.TestCase):
    """时间戳一律 UTC + `Z`：读的人不该先做一次时区换算。"""

    def test_epoch_zero(self):
        self.assertEqual(source_stamp.iso(0), "1970-01-01T00:00:00Z")

    def test_a_known_instant(self):
        # 硬编码而非用 strftime 现算 —— 现算等于拿实现证明实现
        self.assertEqual(source_stamp.iso(1_700_000_000), "2023-11-14T22:13:20Z")

    def test_agrees_with_the_stdlib(self):
        ts = 1_755_000_000.0
        expected = (
            datetime.fromtimestamp(ts, tz=timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
        )
        self.assertEqual(source_stamp.iso(ts), expected)


class TestTheRealTree(unittest.TestCase):
    """扫描面自证：本仓库真的能扫出一堆文件，上面那些小树用例才有意义。"""

    def test_scans_this_repo(self):
        snap = scan(SERVER)
        self.assertGreaterEqual(snap.files, 50, f"只扫到 {snap.files} 个 .py")
        self.assertEqual(snap.unreadable, (), "本仓库里不该有读不到的源码文件")
        names = {rel for rel, _ in snap.entries}
        self.assertIn("manage.py", names)
        self.assertIn("apps/core/source_stamp.py", names)
        self.assertTrue(
            all("__pycache__" not in n and ".venv" not in n for n in names),
            f"扫描面混进了不该算的东西：{sorted(n for n in names if 'cache' in n or 'venv' in n)}",
        )


# --- 下面这一节是**结构锁**：证明「端点挂在路由上」，证明不了「视图能跑通」 -----------------
#
# 视图能不能真跑通，由冒烟脚本自启那一遍负责（它会真的请求 `/health/`，核对不过就是一条
# FAIL）。这里只挡住一种情况：路由或 `ready()` 里的那句 import 被删掉 —— 那时 self 模式的
# 指纹核对会以「404 / 服务端是旧版本」收场，把**自己删掉路由**伪装成别人的问题。

#: 参与自证链路的文件（相对 `server/`）
WIRING_FILES = (
    "config/urls.py",
    "apps/core/urls.py",
    "apps/core/views.py",
    "apps/core/apps.py",
)


def _parsed(path: Path):
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def _imports_name(node, name: str) -> bool:
    for child in ast.walk(node):
        if isinstance(child, ast.Import) and any(a.name.split(".")[-1] == name for a in child.names):
            return True
        if isinstance(child, ast.ImportFrom) and any(a.name == name for a in child.names):
            return True
    return False


def _string_args(node):
    for child in ast.walk(node):
        if isinstance(child, ast.Call) and child.args:
            first = child.args[0]
            if isinstance(first, ast.Constant) and isinstance(first.value, str):
                yield child, first.value


def wiring_report(server: Path) -> dict:
    """自证链路的四个必要环节，逐个现算（缺文件 = False，不抛）。"""

    def read(rel):
        path = server / rel
        return _parsed(path) if path.exists() else None

    urls, core_urls, views, apps_py = (read(f) for f in WIRING_FILES)

    report = {
        # 根路由真的把 apps.core 挂上了
        "urls_include_core": bool(urls) and any(
            isinstance(node.func, ast.Name) and node.func.id == "include" and value == "apps.core.urls"
            for node, value in _string_args(urls)
        ),
        # core 里真的有 health/ 这条
        "core_route_health": bool(core_urls) and any(value == "health/" for _, value in _string_args(core_urls)),
        # 视图真的去扫源码（而不是返回一个写死的常量）
        "view_scans_source": bool(views) and any(
            isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "scan"
            for node in ast.walk(views)
        ),
        # `ready()` 里真的把它 import 了 —— 时间戳取在「进程刚开始」而不是「第一个请求」
        "ready_imports_stamp": bool(apps_py)
        and any(
            isinstance(node, ast.ClassDef)
            and any(
                isinstance(inner, ast.FunctionDef) and inner.name == "ready" and _imports_name(inner, "source_stamp")
                for inner in node.body
            )
            for node in ast.walk(apps_py)
        ),
    }
    return report


class TestSelfProofIsWired(unittest.TestCase):
    ALL_TRUE = {key: True for key in ("urls_include_core", "core_route_health", "view_scans_source", "ready_imports_stamp")}

    def test_the_real_tree_is_wired(self):
        self.assertEqual(
            wiring_report(SERVER),
            self.ALL_TRUE,
            "自证链路缺了一环 —— `/health/` 会 404 或者时间戳取在错误的那一刻，"
            "而 self 模式的指纹核对会把这件事读成「服务端是旧版本」。",
        )

    def _mutated_tree(self, rel: str, old: str, new: str) -> Path:
        """把四个文件原样复制一份，只改一处 —— 用来证明下面那些键真的会被打红。"""
        root = Path(tempfile.mkdtemp(prefix="al_wiring_"))
        self.addCleanup(shutil.rmtree, root, ignore_errors=True)
        for name in WIRING_FILES:
            target = root / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes((SERVER / name).read_bytes())
        victim = root / rel
        text = victim.read_text(encoding="utf-8")
        self.assertIn(old, text, f"注入无效：{rel} 里没有 {old!r}")
        victim.write_text(text.replace(old, new), encoding="utf-8")
        return root

    def test_each_link_goes_red_on_its_own(self):
        """逐条注入，每条只该打红它对应的那个键（否则这四个键是互相代替的摆设）。"""
        cases = [
            ("config/urls.py", 'include("apps.core.urls")', 'include("apps.nothing.urls")', "urls_include_core"),
            ("apps/core/urls.py", '"health/"', '"nope/"', "core_route_health"),
            ("apps/core/views.py", "source_stamp.scan(", "scan(", "view_scans_source"),
            ("apps/core/apps.py", "from . import source_stamp", "pass  # 不再 import", "ready_imports_stamp"),
        ]
        for rel, old, new, expected_key in cases:
            with self.subTest(rel=rel):
                report = wiring_report(self._mutated_tree(rel, old, new))
                self.assertFalse(report[expected_key], f"{rel} 的注入没被抓住：{report}")
                for key, value in report.items():
                    if key != expected_key:
                        self.assertTrue(value, f"{rel} 的注入把无关的 {key} 也带红了：{report}")


if __name__ == "__main__":
    unittest.main()
