# -*- coding: utf-8 -*-
"""客户端页面清单：README 的「客户端页面」表、`Index.ets` 的 Tab 栏、`main_pages.json`
的页面登记 —— 三处必须指向同一批名字，且每一处都指向真实存在的文件。

为什么要有它
------------
README 的「客户端页面」表是第一眼能看懂的**产品说明**：客户端有几个 Tab、每个 Tab 干什么。
它的真值却不在文档里：

  - `harmony/.../pages/Index.ets` 的 `MainTabs` 里，`tabBar(this.tabItem('持仓', 0))`
    这样的调用才是「到底有哪几个 Tab、什么顺序」的真值；
  - `harmony/.../resources/base/profile/main_pages.json` 是**路由登记表**，
    页面不登记在里面就进不去（本仓库只有一个入口页 `pages/Index`，Tab 都在它内部切换）。

同一件事写三遍，就一定会漂 —— 上一条检查（`test_api_surface_contract.py`）钉的
「主要接口」清单已经漂了两条，这是同一类东西：**手抄的清单错起来是安静的**。
少写一个 Tab，读文档的人以为那功能不存在；多写一个，他会在 DevEco 里翻半天找不到。

而 `.ets` 这一侧格外没有安全网：本机没有 DevEco / hvigor 工具链，
**改 `.ets` 不会在任何自测里被发现**（README 的「已知约束」里写明了这一点）。
所以这一侧更要靠读源码的契约检查来兜。

锁什么
------
1. **Tab 清单双向一致**：README 表第一列的名字与 `Index.ets` 里 `tabItem(...)` 的名字
   必须**逐个相等且顺序相同**；`tabItem` 的下标还必须是 `0..n-1` 连续 ——
   跳号在鸿蒙里表现为「点了没反应」，而且是运行期才看得出来。
2. **每个 Tab/视图都有落点**：`Index.ets` 里 `import { X } from './X.ets'` 这种**本地**
   导入，解析出来的文件必须真的存在。写了却不存在，本机编译不了，谁也不会当场知道。
3. **每个页面文件都有入口**：`pages/` 下除 `Index.ets` 以外的每个 `.ets`，都必须被
   `Index.ets` import 到 —— 页面写了却没人 import，就是一份**永远到不了**的代码
   （本仓库在别的项目里栽过同一形态：「能力在、入口不在」）。
4. **import 进来就得用**：`Index.ets` import 进来的页面组件，必须在 import 行以外
   再出现一次（`HoldingsPage()` 这种调用形态）。只 import 不调用是编译告警级别的
   死代码，而这台机器上看不到告警。
5. **路由登记表与磁盘一致**：`main_pages.json` 里每条 `pages/X` 都要有对应的
   `pages/X.ets`；并且入口页 `pages/Index` 必须在表里。
6. **解析面自己的覆盖面**：文件里每条相对路径说明符（`'./X'` / `'../X'`）都要被
   `IMPORT_RE` 收进去。它是**按形态枚举**的（具名 / 默认 / 命名空间 / 副作用四种），
   漏掉一种写法时第 2 / 3 / 4 条会**安静地少看一条**，只有第 6 条会发现。

刻意**不**锁
------------
- README 表第二列（「能力」那一段散文）—— 「持仓页显示浮盈与累计股息」这种话，
  要判就得猜渲染逻辑，硬判就变成猜。它属于人工维护的部分。
- Tab 里用到的 `@Builder`/组件内部结构、颜色、字号 —— 那是 DevEco 预览的事。
- `main_pages.json` 是否**只**登记入口页 —— 以后加真正的多页路由是合理的，
  只要求「登记了的必须存在」，不要求「没登记的必须不存在」。
- `export … from './X'` 目前不进导入解析面（它不算「页面入口」）—— 但第 6 条的对账
  会把它扫出来，不会静默漏过；真要收进来，得同时想清楚它算不算入口、
  以及 `test_imported_components_are_actually_used` 该不该管它。

假锁防护
--------
`TestTheContractIsNotVacuous` 做四件事：手写的 README / Index 片段断言解析结果逐字正确
（含四种 import 写法各自的名字）；拿**真文件**断言解析出了 5 个 Tab（解析器退化成空列表时
当场现形；真文件那侧的导入对账见 `TestPageFilesHaveLandingPoints`）；再把「少一个 / 多一个 /
顺序换了」三种差异喂给比对函数，断言它们**真的会被报出来**；最后拿一条解析面确实不认的写法
（`export … from`）喂给对账函数，断言它**真的会报** —— 否则第 6 条只是一句空话。

跑法（在 server/ 下）：python -m unittest discover -s apps -t .
"""
import json
import re
import unittest
from pathlib import Path

SERVER = Path(__file__).resolve().parents[3]
assert (SERVER / "manage.py").exists(), f"算错了 server 根：{SERVER} 下没有 manage.py"

ROOT = SERVER.parent
README = ROOT / "README.md"
HARMONY_ETS = ROOT / "harmony/entry/src/main/ets"
PAGES = HARMONY_ETS / "pages"
INDEX_ETS = PAGES / "Index.ets"
MAIN_PAGES_JSON = ROOT / "harmony/entry/src/main/resources/base/profile/main_pages.json"

#: README 里那一节（正文到下一个 `## ` 为止）
TAB_SECTION_RE = re.compile(r"^##\s*客户端页面\s*\n(.*?)^##\s", re.M | re.S)
#: 表格行；表头与分隔行由 `_looks_like_data_row` 挡掉
TABLE_ROW_RE = re.compile(r"^\|\s*([^|]+?)\s*\|(.+?)\|\s*$", re.M)
#: `Index.ets` 里 `tabBar(this.tabItem('持仓', 0))`
TAB_ITEM_RE = re.compile(r"tabBar\(\s*this\.tabItem\(\s*'([^']*)'\s*,\s*(\d+)\s*\)\s*\)")
#: `Index.ets` 的本地导入 —— **四种写法都要收**（鸿蒙工程里都会出现）：
#:
#:   import { A, B } from './X'        具名
#:   import X from './X'               默认
#:   import * as X from './X'          命名空间
#:   import './X'                      只为副作用
#:
#: 为什么不写扩展名也能解析：`./X` → `X.ets`，不写扩展名是鸿蒙的常规写法。
#:
#: ⚠ 只认具名那一种时，「导入了不存在的文件」在另外三种写法下**静默漏过**；
#: 而同一个洞的另一半（`test_every_page_file_is_imported_by_index`，孤儿那半）会**假红**。
#: 也就是说：真正漏的那半边一声不响，错报的那半边很响 —— 这种不对称最容易让人
#: 去修错报、却以为检查没问题。所以本文件另有一条
#: `test_no_import_form_escapes_the_parser`，用一个**宽到不管语句形态**的扫描
#: 给解析面本身对账。
IMPORT_RE = re.compile(
    r"""^[ \t]*import[ \t]+(?:
            \{[ \t]*([^}]*?)[ \t]*\}              # 具名：{ A, B as C }
          | \*[ \t]*as[ \t]+([A-Za-z_$][\w$]*)    # 命名空间：* as X
          | ([A-Za-z_$][\w$]*)                    # 默认：X
        )?[ \t]*(?:from[ \t]*)?'([^']+)'""",
    re.M | re.X,
)
#: 文件里出现的**所有**相对路径模块说明符 —— 不管它出现在哪种语句里。
#: 只用来说明「宽扫能看见什么」，由 `unparsed_specifiers` 与导入解析面对账。
RELATIVE_SPECIFIER_RE = re.compile(r"'((?:\.\.?/)[^']*)'")
#: `main_pages.json` 里登记的一页
MAIN_PAGE_ENTRY_RE = re.compile(r"^pages/[A-Za-z0-9_/]+$")


def parse_readme_tabs(text):
    """README「客户端页面」表的第一列 → `['持仓', '记一笔', ...]`。"""
    m = TAB_SECTION_RE.search(text)
    if not m:
        return []
    tabs = []
    for first, _rest in TABLE_ROW_RE.findall(m.group(1)):
        first = first.strip().strip("`")
        if first == "Tab" or set(first) <= {"-", ":", " "}:
            continue  # 表头 / 分隔行
        tabs.append(first)
    return tabs


def parse_tab_items(text):
    """`Index.ets` 的 Tab 栏 → `[('持仓', 0), ('记一笔', 1), ...]`（按出现顺序）。"""
    return [(name, int(idx)) for name, idx in TAB_ITEM_RE.findall(text)]


def _local_names(spec):
    """`A, B as C` → `['A', 'C']`。

    `as` **之后**才是本地能用的名字：`import { Foo as Bar }` 之后代码里写的是 `Bar`，
    拿 `Foo` 去问「用没用过」必然误报没用过。
    """
    names = []
    for part in spec.split(","):
        part = part.strip()
        if part:
            names.append(part.split(" as ")[-1].strip())
    return names


def parse_local_imports(text):
    """`Index.ets` 里的**本地**导入 → `[(相对路径, [符号名…]), …]`（跳过包名导入）。

    只收 `./` 与 `../` 开头的：`@ohos/xxx` 这种是 SDK，磁盘上没有对应文件。
    只为副作用的 `import './X'` 没有符号名（返回空列表），但它**照样是一条指向
    某个文件的依赖**，所以仍然要过存在性检查。
    """
    out = []
    for named, namespace, default, source in IMPORT_RE.findall(text):
        if not source.startswith("."):
            continue
        if named:
            names = _local_names(named)
        else:
            names = [n for n in (namespace, default) if n]
        out.append((source, names))
    return out


def unparsed_specifiers(text):
    """宽扫到的相对路径说明符里，导入解析面**没**收进去的那些（空 = 解析面跟得上写法）。

    这是给**解析面自己**配的对账，不是给页面文件配的：`IMPORT_RE` 是按形态枚举的，
    漏掉一种写法时所有调用点都会安静地少看一条依赖。判据刻意做得很宽
    （只问「有没有一个引号包起来的相对路径」，不管它在哪种语句里），
    因为「多喊一次」的代价远小于「静默漏过一条导入」。
    """
    broad = set(RELATIVE_SPECIFIER_RE.findall(text))
    narrow = {src for src, _ in parse_local_imports(text)}
    return sorted(broad - narrow)


def resolve_page(source, base=PAGES):
    """把 `./HoldingsPage` / `../common/ApiClient` 解析成磁盘路径（带 `.ets`）。"""
    target = (base / source).resolve()
    return target.with_suffix(".ets")


def tab_problems(readme_tabs, code_tabs):
    """两份事实之间的差异 → 人类可读的问题清单（空 = 契约成立）。"""
    problems = []
    names = [name for name, _ in code_tabs]
    missing = [t for t in names if t not in readme_tabs]
    extra = [t for t in readme_tabs if t not in names]
    if missing:
        problems.append("README 的「客户端页面」表漏了这些 Tab（Index.ets 里有）：" + "、".join(missing))
    if extra:
        problems.append("README 的「客户端页面」表多了这些 Tab（Index.ets 里没有）：" + "、".join(extra))
    if not missing and not extra and readme_tabs != names:
        problems.append(
            "两边 Tab 名字一样但**顺序不同** —— README: %s / Index.ets: %s"
            % ("、".join(readme_tabs), "、".join(names)))
    indices = [idx for _, idx in code_tabs]
    if indices and indices != list(range(len(indices))):
        problems.append(
            "tabItem 的下标不是 0..n-1 连续（%s）—— 跳号在鸿蒙里表现为点了没反应" % indices)
    return problems


class TestClientPagesMatchTheTabBar(unittest.TestCase):
    """README 的 Tab 表 ↔ `Index.ets` 的 Tab 栏。"""

    def setUp(self):
        self.readme_tabs = parse_readme_tabs(README.read_text(encoding="utf-8"))
        self.index_src = INDEX_ETS.read_text(encoding="utf-8")
        self.code_tabs = parse_tab_items(self.index_src)

    def test_tab_lists_agree(self):
        problems = tab_problems(self.readme_tabs, self.code_tabs)
        self.assertEqual(problems, [], (
            "README 的「客户端页面」表与 Index.ets 的 Tab 栏对不上：\n  "
            + "\n  ".join(problems)
            + "\n改 README 的表（Tab 栏才是真值）—— 顺手看一下那张表的「能力」列要不要一起改。"))

    def test_readme_section_was_found(self):
        """这一节改标题就会让上面那条变成「两边都是空的」—— 那种恒真必须挡住。"""
        self.assertTrue(self.readme_tabs, "README 里没解析到「客户端页面」那一节的表格")
        self.assertGreaterEqual(len(self.readme_tabs), 3,
                                f"只解析到 {len(self.readme_tabs)} 个 Tab，解析面塌了")
        self.assertGreaterEqual(len(self.code_tabs), 3,
                                f"Index.ets 只解析到 {len(self.code_tabs)} 个 tabItem，解析面塌了")


class TestPageFilesHaveLandingPoints(unittest.TestCase):
    """`.ets` 这一侧没有编译器兜底，所以每个 import / 每个页面文件都要落到磁盘上。"""

    def setUp(self):
        self.index_src = INDEX_ETS.read_text(encoding="utf-8")
        self.imports = parse_local_imports(self.index_src)

    def test_every_local_import_exists_on_disk(self):
        missing = []
        for source, _names in self.imports:
            target = resolve_page(source)
            if not target.is_file():
                missing.append("%s → %s" % (source, target.relative_to(ROOT).as_posix()))
        self.assertEqual(missing, [],
                         "Index.ets 导入了磁盘上不存在的文件（本机没有鸿蒙工具链，"
                         "编译期才发现得了）：\n  " + "\n  ".join(missing))

    def test_no_import_form_escapes_the_parser(self):
        """★ 解析面自身的覆盖面：文件里每条相对路径说明符都要被导入解析面收进去。

        为什么单独立一条：`IMPORT_RE` 是**按形态枚举**的，漏掉一种写法时
        `test_every_local_import_exists_on_disk` 会安静地少看一条（不报错），
        而 `test_every_page_file_is_imported_by_index` 反而会假红 ——
        「导入了不存在的文件」正是安静的那一半在漏。这里拿一个宽到不管语句形态的
        扫描（只问「有没有一个引号包起来的相对路径」）跟解析面对账。
        """
        # 解析面不许为空 —— 两边都空会让这条对账恒真
        self.assertTrue(self.imports, "Index.ets 里一条本地导入都没解析出来，对账面塌了")
        problems = unparsed_specifiers(self.index_src)
        self.assertEqual(problems, [], (
            "这些相对路径没被导入解析面收进去（多半是新的 import 写法）：\n  "
            + "\n  ".join(problems)
            + "\n  把它补进 IMPORT_RE，并在 TestTheContractIsNotVacuous."
              "test_import_parser_covers_every_form 里加一条片段用例；"
              "如果它压根不是 import（例如 `export … from`），那也说明它是一条指向本地文件的"
              "依赖 —— 要么收进解析面，要么在这里显式登记，别让它静默漏过。"))

    def test_every_page_file_is_imported_by_index(self):
        """页面文件写了却没人 import —— 永远到不了的代码。"""
        imported = {resolve_page(src).resolve() for src, _ in self.imports}
        orphans = [
            p.name for p in sorted(PAGES.glob("*.ets"))
            if p.name != INDEX_ETS.name and p.resolve() not in imported
        ]
        self.assertEqual(orphans, [],
                         "这些页面文件没有被 Index.ets import，等于没有任何入口：\n  "
                         + "\n  ".join(orphans)
                         + "\n要么在 Index.ets 里接上，要么删掉 —— 中间状态只会让人以为它在用。")

    def test_imported_components_are_actually_used(self):
        """import 进来就得在 import 行以外再出现一次（`HoldingsPage()` 那种调用形态）。"""
        # 去掉 import 行再找，否则每次都能在 import 那行自证命中
        body = "\n".join(
            line for line in self.index_src.splitlines()
            if not line.strip().startswith("import ")
        )
        unused = []
        for source, names in self.imports:
            for name in names:
                # `@Component struct X` 只用一次也算用了；这里找的是「名字还出现过」
                if not re.search(r"\b%s\b" % re.escape(name), body):
                    unused.append("%s（来自 %s）" % (name, source))
        self.assertEqual(unused, [],
                         "这些符号 import 进来之后没被用到（本机看不到编译告警）：\n  "
                         + "\n  ".join(unused))


class TestRouteTableMatchesDisk(unittest.TestCase):
    """`main_pages.json` 是路由登记表：登记了的必须存在，入口页必须在里面。"""

    def setUp(self):
        self.entries = json.loads(MAIN_PAGES_JSON.read_text(encoding="utf-8"))["src"]

    def test_registered_pages_exist(self):
        broken = [
            e for e in self.entries
            if not (HARMONY_ETS / (e + ".ets")).is_file()
        ]
        self.assertEqual(broken, [],
                         "main_pages.json 登记了不存在的页面（进不去，而且是运行期才现形）：\n  "
                         + "\n  ".join(broken))

    def test_the_entry_page_is_registered(self):
        self.assertIn("pages/Index", self.entries,
                      "入口页 pages/Index 没登记在 main_pages.json 里 —— 应用起不来")

    def test_registry_only_contains_page_paths(self):
        bad = [e for e in self.entries if not MAIN_PAGE_ENTRY_RE.match(e)]
        self.assertEqual(bad, [], "main_pages.json 里有不像页面路径的条目：%s" % bad)


class TestTheContractIsNotVacuous(unittest.TestCase):
    """解析器 + 比对函数的自证：本仓库栽过「断言恒真」，这一段是防它的。"""

    README_SAMPLE = """\
## 客户端页面

| Tab | 能力 |
| --- | --- |
| 持仓 | 看持仓 |
| 记一笔 | 记一笔 |
| 我的 | 退出登录 |

## 下一节
"""
    INDEX_SAMPLE = """\
import { A } from './APage';
import { B } from './BPage';

@Component
struct MainTabs {
  build() {
    Tabs() {
      TabContent() {
        APage()
      }.tabBar(this.tabItem('持仓', 0))

      TabContent() {
        BPage()
      }.tabBar(this.tabItem('记一笔', 1))
    }
  }
}
"""

    #: 四种 import 写法 + 一条 SDK 导入（后者必须被跳过）
    IMPORT_FORMS_SAMPLE = """\
import { ApiClient } from '../common/ApiClient';
import { A, B as C } from './APage';
import Default1 from './DefaultPage';
import * as ns from './NsPage';
import './SideEffectPage';
import { Sdk } from '@ohos.net.http';
"""

    def test_readme_table_parser(self):
        self.assertEqual(parse_readme_tabs(self.README_SAMPLE), ["持仓", "记一笔", "我的"])
        # 表头与分隔行不能被当成 Tab
        self.assertNotIn("Tab", parse_readme_tabs(self.README_SAMPLE))
        self.assertEqual(parse_readme_tabs("## 别的一节\n\n没有表\n"), [])

    def test_tab_item_parser(self):
        self.assertEqual(parse_tab_items(self.INDEX_SAMPLE), [("持仓", 0), ("记一笔", 1)])

    def test_local_import_parser_skips_sdk_imports(self):
        src = "import { ApiClient } from '@ohos.net.http';\nimport { P } from './PPage';\n"
        self.assertEqual(parse_local_imports(src), [("./PPage", ["P"])])

    def test_import_parser_covers_every_form(self):
        """★ 四种 import 写法一种都不许漏 —— 漏掉的那一种就是「导入了不存在的文件」的盲区。"""
        self.assertEqual(
            parse_local_imports(self.IMPORT_FORMS_SAMPLE),
            [
                ("../common/ApiClient", ["ApiClient"]),
                ("./APage", ["A", "C"]),  # `B as C` → 本地名是 C
                ("./DefaultPage", ["Default1"]),
                ("./NsPage", ["ns"]),
                ("./SideEffectPage", []),  # 只为副作用，没有符号名
            ],
            "有 import 写法没被解析面收进去（或符号名收错了）")

    def test_unparsed_specifier_scanner_actually_reports(self):
        """★ 负向对照：给解析面对账的那条判据必须真的会报，否则它只是一句空话。"""
        self.assertEqual(unparsed_specifiers(self.IMPORT_FORMS_SAMPLE), [],
                         "四种 import 写法都已收进解析面，对账面却报出了漏网")
        self.assertEqual(unparsed_specifiers("export { A } from './APage';\n"), ["./APage"],
                         "`export … from` 指向了本地文件却没被对账面报出来 —— 这条对账是空的")
        self.assertEqual(unparsed_specifiers("import X from './X'\n"), [],
                         "默认导入被误报成漏网（解析面已经收它了）")

    def test_differences_are_actually_reported(self):
        """★ 负向对照：少一个 / 多一个 / 顺序换了，比对函数都要报出来。"""
        code = [("持仓", 0), ("记一笔", 1)]
        self.assertEqual(tab_problems(["持仓", "记一笔"], code), [], "一致时不该报任何问题")
        self.assertTrue(tab_problems(["持仓"], code), "README 少一个 Tab 没被报出来")
        self.assertTrue(tab_problems(["持仓", "记一笔", "统计"], code), "README 多一个 Tab 没被报出来")
        self.assertTrue(tab_problems(["记一笔", "持仓"], code), "顺序换了没被报出来")
        self.assertTrue(tab_problems(["持仓", "记一笔"], [("持仓", 0), ("记一笔", 3)]),
                        "tabItem 下标跳号没被报出来")

    def test_the_real_files_parse(self):
        """真文件也要解析得出东西 —— 上面那些片段测试只证明解析器会读，不证明读到了真文件。"""
        self.assertTrue(parse_tab_items(INDEX_ETS.read_text(encoding="utf-8")))
        self.assertTrue(parse_local_imports(INDEX_ETS.read_text(encoding="utf-8")))
        self.assertTrue(json.loads(MAIN_PAGES_JSON.read_text(encoding="utf-8"))["src"])


if __name__ == "__main__":
    unittest.main()
