"""源码体检：整仓的 .py 都要能编译，而且 `def` / `class` 的名字得是合法标识符。

为什么值得单独一条测试
----------------------
本轮**同一个形状出现了两次**：中文方法名里带了全角标点（先是一个仓库里的 `「」`，
再是本仓库里的全角括号 `（）`）。这两种写法在编辑器里看不出来 —— 汉字进标识符是合法的，
所以 `def 记录一笔股息(self)` 完全正常，只有夹在中间的全角标点会让 Python 说话：

    SyntaxError: invalid character '（' (U+FF08)

而且它是**在 import 的时候**炸的，顺着 `unittest discover` 报出来是
「这个模块 import 不了（`unittest.loader._FailedTest`）」，真正的行号埋在 traceback 里。
同一类错发现两次之后就不再靠眼睛了。

扫描面是自动发现的
------------------
`rglob("*.py")` 扫 `server/` 与 `scripts/`，**不是手抄一份清单** —— 手抄的清单在新增
文件时不会自己长出来，而这类错最容易出现在新文件里。为了证明扫描面不是空的，下面同时
断言文件数下限，并拿一段「故意写坏」的源码反向对照（`illegal_names()` 必须报出来）。

本文件只用标准库（`re` / `pathlib`），所以裸 Python 也跑得动。
"""
import re
import unittest
from pathlib import Path

#: `server/`（本文件在 `server/apps/core/tests/` 下，往上四级）
SERVER = Path(__file__).resolve().parents[3]
#: 仓库根
REPO = SERVER.parent

#: `def` / `class` 后面那个名字：取到 `(` 或 `:` 之前
_NAME_RE = re.compile(r"^[ \t]*(?:async[ \t]+)?(?:def|class)[ \t]+([^\s(:]+)", re.MULTILINE)


def illegal_names(source: str) -> list:
    """源码里不合法的 `def` / `class` 名字 → `[(行号, 名字), ...]`。

    纯文本扫，**不先 parse**：名字非法时 `ast.parse` / `compile` 自己就会抛
    `SyntaxError`，拿不到「是哪一个名字有问题」这个信息 —— 而那正是这条测试要说的东西。
    """
    found = []
    for match in _NAME_RE.finditer(source):
        name = match.group(1)
        if not name.isidentifier():
            found.append((source.count("\n", 0, match.start()) + 1, name))
    return found


def python_files() -> list:
    """要扫的文件（自动发现，去掉 `__pycache__`）。"""
    files = []
    for base in (SERVER, REPO / "scripts"):
        files += [p for p in base.rglob("*.py") if "__pycache__" not in p.parts]
    return sorted(set(files))


class ScannerTest(unittest.TestCase):
    """先证明扫描器本身有判别力 —— 不然整条测试可能只是一直在说「没问题」。"""

    def test_扫得出全角标点的名字(self):
        bad = "class Foo:\n    def 记录一笔股息（全角括号）(self):\n        pass\n"
        self.assertEqual(illegal_names(bad), [(2, "记录一笔股息（全角括号）")])

    def test_扫得出直角引号的名字(self):
        # 本轮在另一个仓库踩到的那个形状
        bad = "def 处理「已拒绝」的返利(self):\n    pass\n"
        self.assertEqual(illegal_names(bad), [(1, "处理「已拒绝」的返利")])

    def test_正常的中文名字一个都不报(self):
        good = (
            "class 持仓列表(Base):\n"
            "    async def 聚合_按币种(self, *, as_of):\n"
            "        return None\n"
        )
        self.assertEqual(illegal_names(good), [])

    def test_带类型标注的名字也认得出(self):
        good = "def build_calendar(entries: list, as_of: date) -> dict:\n    pass\n"
        self.assertEqual(illegal_names(good), [])


class WholeTreeTest(unittest.TestCase):
    """整仓扫一遍。"""

    def test_扫描面不是空的(self):
        files = python_files()
        # 实测 110 个左右；给个下限，防止哪天 rglob 扫了个空目录还一路绿灯
        self.assertGreaterEqual(len(files), 80, f"只扫到 {len(files)} 个 .py，扫描面不对")
        self.assertTrue(any(p.name == "manage.py" for p in files), "连 manage.py 都没扫到")

    def test_整仓的_py_都能编译(self):
        broken = []
        for path in python_files():
            try:
                compile(path.read_text(encoding="utf-8"), str(path), "exec")
            except SyntaxError as exc:
                broken.append(f"{path.relative_to(REPO)}:{exc.lineno} {exc.msg}")
        self.assertEqual(broken, [], "这些文件编译不过：\n" + "\n".join(broken))

    def test_def_class_的名字都是合法标识符(self):
        bad = []
        for path in python_files():
            text = path.read_text(encoding="utf-8")
            for lineno, name in illegal_names(text):
                bad.append(f"{path.relative_to(REPO)}:{lineno} def/class {name!r}")
        self.assertEqual(
            bad, [],
            "名字里有非法字符（多半是全角标点，汉字本身是合法的）：\n" + "\n".join(bad),
        )


if __name__ == "__main__":
    unittest.main()
