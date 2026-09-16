# -*- coding: utf-8 -*-
"""腾讯行情的代码规范化与批量解析（不需要数据库、不需要 Django、不出网）。

对应 README 里自己写着的那条约束：「免费源没有推送，只能按 QUOTE_REFRESH_MINUTES
轮询；**轮询太快会被源限流**，拿不到价时会退回上一次的旧价并把 stale 标出来」。
而改动之前，`services.refresh_quotes()` 对每只标的各发一条请求 —— 60 只标的
一轮 60 条，正好把这句话预告的限流撞上。腾讯接口本来就吃逗号分隔的多代码，
所以这里压两件事：

  1. `tencent_code` 认出来的代码**必须是这个接口真认得的写法**（区分大小写）。
     以前的实现把「已经带 us 前缀」的 symbol 整串 lower()：`usAAPL` -> `usaapl`、
     `USB`（美国合众银行）-> `usb` —— 都是查不到、也不报错的代码。
  2. 一批代码只发**一条**请求（`fetch_all`，注入假传输数它被调了几次），
     并且按行自己带的代码回填 —— 认不出的代码那一行根本不出现，照着请求
     顺序对齐必然错位。

样本取自 `tencent_fixtures.py`（真实响应原文），所以这里断言的是**这个接口
现在的行为**，不是我记忆里的行为。
"""
from decimal import Decimal
import re
from pathlib import Path
import unittest

from apps.market import tencent
from apps.market.tencent import chunk, fetch_all, parse_batch, parse_payload, tencent_code
from apps.market.tests import tencent_fixtures as fixtures
from apps.market.tests.test_quote_schedule import strip_comments

APP_DIR = Path(__file__).resolve().parent.parent  # server/apps/market
APPS_DIR = APP_DIR.parent  # server/apps
SERVER_DIR = APPS_DIR.parent  # server


def legacy_us_code(symbol: str) -> str:
    """改动前的实现（原样抄下来），只用来做反向对照。

    删掉它就是给自己留个「以前那样写也是可以的」的错觉。
    """
    return f"us{symbol.upper()}" if not symbol.lower().startswith("us") else symbol.lower()


def interface_change_pct(line: str) -> Decimal:
    """响应行里**接口自己带的**涨跌幅字段（第 33 个字段）。

    用它来给我们的 `parts[3]` / `parts[4]` 做交叉验证：现价与昨收的取位一旦
    改错，算出来的涨跌幅就跟接口自带的对不上。
    """
    payload = line.partition('="')[2].rstrip('";')
    return Decimal(payload.split("~")[32])


class CodeTest(unittest.TestCase):
    """symbol -> 腾讯代码"""

    def test_A股(self):
        for symbol, expected in {
            "600000": "sh600000",
            "sh600000": "sh600000",
            "SH600000": "sh600000",  # 前缀大小写由我们归一化（这一个接口认小写）
            "000001": "sz000001",
            "sz000001": "sz000001",
            "300750": "sz300750",
            "688981": "sh688981",
            "BJ430047": "bj430047",
        }.items():
            with self.subTest(symbol=symbol):
                self.assertEqual(tencent_code("A", symbol), expected)

    def test_港股(self):
        self.assertEqual(tencent_code("HK", "700"), "hk00700")  # 不足 5 位补零
        self.assertEqual(tencent_code("HK", "00700"), "hk00700")
        self.assertEqual(tencent_code("HK", "HK00700"), "hk00700")

    def test_美股裸ticker(self):
        for symbol, expected in {
            "AAPL": "usAAPL",
            "aapl": "usAAPL",
            "BRK.B": "usBRK.B",
            "TSLA": "usTSLA",
        }.items():
            with self.subTest(symbol=symbol):
                self.assertEqual(tencent_code("US", symbol), expected)

    def test_美股写成腾讯格式也要认(self):
        """`usAAPL` 这种写法以前会被整串 lower() 掉，变成查不到的 `usaapl`。"""
        for symbol, expected in {
            "usAAPL": "usAAPL",
            "USAAPL": "usAAPL",
            "usBRK.B": "usBRK.B",
            "usTSLA": "usTSLA",
        }.items():
            with self.subTest(symbol=symbol):
                self.assertEqual(tencent_code("US", symbol), expected)

    def test_以US开头的真实代码不会被当成前缀(self):
        """`USB`（美国合众银行）实测代码是 `usUSB`，有价。

        改动前的实现看到 `us` 开头就当已有前缀，返回 `usb` —— 静默无价。
        """
        self.assertEqual(tencent_code("US", "USB"), "usUSB")
        self.assertEqual(tencent_code("US", "USA"), "usUSA")
        # 反向对照：旧实现产出的那两个代码，实测都是 v_pv_none_match
        self.assertEqual(legacy_us_code("USB"), "usb")
        self.assertEqual(legacy_us_code("usAAPL"), "usaapl")
        self.assertNotEqual(tencent_code("US", "USB"), legacy_us_code("USB"))
        self.assertNotEqual(tencent_code("US", "usAAPL"), legacy_us_code("usAAPL"))

    def test_说不清的一律返回None不猜(self):
        """`usB` 是「us 前缀 + B」还是「USB 敲错了」—— 分不出来，就不猜。"""
        for symbol in ("usB", "usaapl", "usAaPl", "us", "us1"):
            with self.subTest(symbol=symbol):
                self.assertIsNone(tencent_code("US", symbol))

    def test_不走来腾讯的市场返回None(self):
        for market in ("CRYPTO", "FUND", "FOREX", "OTHER", ""):
            with self.subTest(market=market):
                self.assertIsNone(tencent_code(market, "600000"))

    def test_空symbol返回None(self):
        for symbol in ("", "   ", None):
            with self.subTest(symbol=symbol):
                self.assertIsNone(tencent_code("US", symbol))

    def test_腾讯只认小写前缀的实证(self):
        """把「接口区分大小写」这条实测结论钉住：`HK00700` / `SH600000` / `usaapl`
        实测都返回 `v_pv_none_match="1";`，所以代码里**不许**出现「大小写无所谓」的写法。
        """
        source = (APP_DIR / "tencent.py").read_text(encoding="utf-8")
        self.assertIn("if not symbol.lower().startswith(\"hk\")", source)
        self.assertIn('rest.isupper()', source)

    def test_us前缀的歧义只在一处处理(self):
        """服务层与视图层不许再写一遍「以 us 开头怎么办」。

        两份实现必然漂 —— 这个仓库栽过好几回。
        """
        for name in ("services.py", "views.py"):
            with self.subTest(name=name):
                code = strip_comments((APP_DIR / name).read_text(encoding="utf-8"))
                self.assertNotIn('startswith("us")', code)
                self.assertNotIn("startswith('us')", code)


class ParseTest(unittest.TestCase):
    """响应解析（真样本）"""

    def test_一批五行全部解析出来(self):
        parsed = parse_batch(fixtures.MIXED)
        self.assertEqual(
            sorted(parsed), ["hk00700", "sh000001", "sh600000", "sz000001", "usAAPL"]
        )
        self.assertEqual(parsed["sh600000"]["price"], Decimal("9.10"))
        self.assertEqual(parsed["sz000001"]["price"], Decimal("11.70"))
        self.assertEqual(parsed["hk00700"]["price"], Decimal("433.400"))
        self.assertEqual(parsed["usAAPL"]["price"], Decimal("332.41"))
        self.assertEqual(parsed["sh000001"]["price"], Decimal("3891.60"))

    def test_现价与昨收取位跟接口自带的涨跌幅对得上(self):
        """`parts[3]`=现价、`parts[4]`=昨收 —— 三个市场都得对得上接口自己的那个数。

        取位一旦改成别的（比如 A 股用 3、港股用 4），这里立刻红。
        """
        parsed = parse_batch(fixtures.MIXED)
        for line in fixtures.MIXED.splitlines():
            code = line.partition('="')[0][2:]
            with self.subTest(code=code):
                ours = parsed[code]["change_pct"]
                theirs = interface_change_pct(line)
                self.assertLess(abs(ours - theirs), Decimal("0.01"))

    def test_认不出的代码整行不出现_按代码回填(self):
        """实测 `sh600000,sz999999,usZZZZZZZ` 只回来 1 行，没有占位行。

        所以解析结果里只有 sh600000 —— 谁要是照着请求顺序对齐，这里就错位。
        """
        parsed = parse_batch(fixtures.PARTIAL_MISS)
        self.assertEqual(list(parsed), ["sh600000"])

    def test_全不认时的兜底行不是标的(self):
        # 真样本：`usZZZZZZZ,ukQQQ` -> 唯一一行 `v_pv_none_match="1";`
        self.assertEqual(parse_batch(fixtures.ALL_MISS), {})
        # ★ 光看真样本证不了「守卫有用」—— 那一行的 payload 只有 1 个字段，
        # 就算不认它、也会因为字段不够被丢掉。所以再给它一个**长得像行情**的
        # payload：认不认这个名字，才是这条守卫的差别。
        fat = 'v_pv_none_match="' + "~".join(["1"] * 40) + '";'
        self.assertEqual(parse_batch(fat), {})

    def test_美国合众银行那一行解析得出来(self):
        parsed = parse_batch(fixtures.USB_OK)
        self.assertEqual(list(parsed), ["usUSB"])
        self.assertEqual(parsed["usUSB"]["price"], Decimal("59.73"))

    def test_重复代码取先出现的那个(self):
        # 实测重复代码会重复出行（`sh600000,sh600000` -> 2 行）
        line = fixtures.MIXED.splitlines()[0]
        parsed = parse_batch("\n".join([line, line.replace("9.10", "9.99")]))
        self.assertEqual(parsed["sh600000"]["price"], Decimal("9.10"))

    def test_脏数据一律返回None而不是抛异常(self):
        self.assertIsNone(parse_payload("1~2~3"))  # 字段不够
        self.assertIsNone(parse_payload("~".join(["x"] * 40)))  # 现价不是数字
        self.assertEqual(parse_batch(""), {})
        self.assertEqual(parse_batch("garbage"), {})

    def test_昨收为0或缺省时不算涨跌幅(self):
        fields = ["0"] * 40
        fields[3], fields[4] = "9.10", "0"
        self.assertIsNone(parse_payload("~".join(fields))["change_pct"])
        fields[3], fields[4] = "9.10", ""
        self.assertIsNone(parse_payload("~".join(fields))["change_pct"])


class BatchingTest(unittest.TestCase):
    """一批代码只发一条请求"""

    def collect(self, bodies: dict | None = None):
        calls = []
        bodies = bodies or {}

        def fake_get_body(group):
            calls.append(list(group))
            return bodies.get(tuple(group), fixtures.MIXED)

        return calls, fake_get_body

    def test_一批标的只发一条请求(self):
        codes = ["sh600000", "sz000001", "hk00700", "usAAPL", "sh000001"]
        calls, fake = self.collect()
        parsed, requests_made, batches = fetch_all(codes, fake)
        self.assertEqual(len(calls), 1)
        self.assertEqual((requests_made, batches), (1, 1))
        # 一条请求里带上全部代码（实测这条 URL 回来 5 行）
        self.assertEqual(calls[0], codes)
        self.assertEqual(len(parsed), 5)

    def test_反向对照_逐只请求就是60条(self):
        """`batch_size=1` 复现改动前的行为 —— 这也是 `--batch-size 1` 那个排查口。"""
        codes = [f"sh{600000 + i}" for i in range(60)]
        calls, fake = self.collect()
        _, requests_made, batches = fetch_all(codes, fake, batch_size=1)
        self.assertEqual(requests_made, 60)
        self.assertEqual(batches, 60)
        self.assertEqual(len(calls), 60)
        # 默认（不传 batch_size）是 1 条
        calls2, fake2 = self.collect()
        _, requests_made2, _ = fetch_all(codes, fake2)
        self.assertEqual(requests_made2, 1)
        self.assertEqual(len(calls2), 1)

    def test_超过上限才切批(self):
        codes = [f"sh{600000 + i}" for i in range(130)]
        calls, fake = self.collect()
        _, requests_made, batches = fetch_all(codes, fake)
        self.assertEqual((requests_made, batches), (3, 3))
        self.assertEqual([len(c) for c in calls], [60, 60, 10])

    def test_一条请求的URL就是逗号分隔(self):
        self.assertEqual(
            tencent.batch_url(["sh600000", "usAAPL"]),
            "https://qt.gtimg.cn/q=sh600000,usAAPL",
        )

    def test_切批的边界(self):
        self.assertEqual(chunk([], 60), [])
        self.assertEqual(chunk(["a"], 60), [["a"]])
        self.assertEqual(chunk(["a", "b", "c"], 2), [["a", "b"], ["c"]])
        # 非法 size 退回默认值，且不会死循环
        for size in (0, -1, None, "abc"):
            with self.subTest(size=size):
                self.assertEqual(len(chunk(["a", "b"], size)), 1)

    def test_一批挂了不影响其它批(self):
        """网络异常必须吞掉：第一批挂了，第二批照抓。"""
        codes = ["sh600000", "sz000001", "hk00700", "usAAPL", "sh000001"] + [
            f"sh{700000 + i}" for i in range(65)
        ]
        calls = []

        def fake(group):
            calls.append(list(group))
            return fixtures.MIXED if len(calls) == 1 else None

        parsed, requests_made, batches = fetch_all(codes, fake)
        self.assertEqual((requests_made, batches), (2, 2))
        self.assertEqual(len(calls), 2)
        # 第一批里那 5 只解析出来了；第二批整批没回来，不影响第一批
        self.assertEqual(sorted(parsed), ["hk00700", "sh000001", "sh600000", "sz000001", "usAAPL"])

    def test_同一代码只请求一次(self):
        """同一只票记在两个账户下时，别为它请求两遍。"""
        groups = tencent.group_by_code(
            [(1, "US", "AAPL"), (2, "US", "usAAPL"), (3, "A", "600000"), (4, "CRYPTO", "BTC")]
        )
        self.assertEqual(groups, {"usAAPL": [1, 2], "sh600000": [3]})


class WiringTest(unittest.TestCase):
    """接线没被拆掉"""

    def test_这个模块不许import_django(self):
        """整个模块的意义就是「能离线秒级验证」，一旦 import django 就没了。

        剥注释后再断言：模块的文档字符串里正解释着这条，直接读源码会自己命中。
        """
        code = strip_comments((APP_DIR / "tencent.py").read_text(encoding="utf-8"))
        self.assertNotIn("django", code)

    def test_市场取值与Market枚举一致(self):
        # 这个模块不能 import django，所以只能读源码对一遍 —— 两边漂了就会发现
        source = (APPS_DIR / "core" / "models.py").read_text(encoding="utf-8")
        block = re.search(r"class Market\(models\.TextChoices\):(.*?)(?=\nclass )", source, re.S)
        self.assertIsNotNone(block, "Market 枚举不见了？")
        pairs = dict(re.findall(r'^\s{4}(\w+) = "([^"]+)"', block.group(1), re.M))
        for name in tencent.TENCENT_MARKETS:
            with self.subTest(name=name):
                self.assertEqual(pairs.get(name), name)
        self.assertEqual(set(tencent.TENCENT_MARKETS), {"A", "HK", "US"})


if __name__ == "__main__":
    unittest.main()
