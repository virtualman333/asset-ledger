# -*- coding: utf-8 -*-
"""汇率：源名单、响应解析、「这次是谁给的」，以及两条口径的唯一性。

（不需要数据库、不需要 Django、不出网。）

对应 README 里那两条已经写下来的承诺：

  · 「免费源没有推送…拿不到价时会退回上一次的旧价并把 `stale` 标出来，不会写空记录」
    —— 行情是这么做的，汇率当时**不是**：它只回一个 `Decimal`，抓不到就返回 1；
  · 「写 `PriceQuote` 的地方只有一处」—— 汇率当时有**两处**，而且判据不一样：

        analytics/services.py::get_rate   库里有记录就用（三个月前那条照用）
        market/views.py::FxView           记录是当天的才用

    两边都不报错，只是同一天里 `/analytics/summary/` 折算用的汇率与
    `/market/fx/` 报出来的汇率可以不是同一个数 —— 而用户是拿这两个数对账的。

还压住一件只能靠注入假传输验证的事：**备用源真的会被轮到**。
`fetch_fx` 的 HTTP 由调用方注入，所以「第一家挂了 / 它不认这个币种时会不会问第二家」
可以离线数出来；而它回的是 `FxQuote(rate, source)`，`source` 必须是**实际给价的那一家**
（改动前两个调用方都把第一家的名字硬编码进 `FxRate.source`，备用源说话时那就是个假值）。
"""
from datetime import date
from decimal import Decimal
from pathlib import Path
import re
import unittest

from apps.market.fx_source import (
    FX_SOURCE_NAMES,
    FX_SOURCES,
    er_api_request,
    fetch_fx,
    frankfurter_request,
    is_fresh,
    latest,
    parse_er_api,
    parse_frankfurter,
)
from apps.market.tests.test_quote_schedule import strip_comments

APP_DIR = Path(__file__).resolve().parent.parent  # server/apps/market
APPS_DIR = APP_DIR.parent  # server/apps
SERVER_DIR = APPS_DIR.parent  # server
REPO_DIR = SERVER_DIR.parent  # 仓库根
README = REPO_DIR / "README.md"

# --------------------------------------------------------------------------- 真实响应形状（裁下来的原文）
#: Frankfurter：目标币种写在查询串里
FRANKFURTER_OK = {"amount": 1.0, "base": "USD", "date": "2026-09-19", "rates": {"CNY": 7.1234}}
#: Frankfurter 认不出目标币种时**不报错**，rates 直接是空表 —— 最容易读错的那一种
EMPTY_RATES = {"amount": 1.0, "base": "USD", "date": "2026-09-19", "rates": {}}
#: open.er-api.com：不接受目标币种，一次回一整张表
ER_API_OK = {"result": "success", "base_code": "USD", "rates": {"CNY": 7.1234, "JPY": 147.2}}
ER_API_FAIL = {"result": "error", "error-type": "unsupported-code"}


def legacy_fetch_fx(base: str, quote: str, transport) -> Decimal | None:
    """**改动前的实现，原样抄下来**（只用来做反向对照）。

    它给出的数与新实现一样 —— 但它说不出这个数是**谁**给的。删掉它等于给自己
    留一个「以前那样写也没问题」的错觉。
    """
    if base == quote:
        return Decimal("1")
    try:
        payload = transport("https://api.frankfurter.app/latest", {"from": base, "to": quote})
        rates = (payload or {}).get("rates") or {}
        value = rates.get(quote)
        if value:
            return Decimal(str(value))
    except Exception:
        pass
    try:
        payload = transport(f"https://open.er-api.com/v6/latest/{base}", None)
        rates = (payload or {}).get("rates") or {}
        value = rates.get(quote)
        if value:
            return Decimal(str(value))
    except Exception:
        pass
    return None


def scripted(payloads, *, raise_on=(), counter=None):
    """假传输：按 URL 认源，返回 `payloads` 里给的那份。

    `payloads` 里没有的源 = 这一家挂了（回 `None`）；`raise_on` 里的源直接抛异常。
    `counter` 是一份可选的 `{源名: 被问次数}`，用来断言「同一家只问一次」。
    """
    calls: list = []

    def transport(url, params=None):
        name = next(n for n in FX_SOURCE_NAMES if n in url)
        calls.append((name, url, params))
        if name in raise_on:
            raise RuntimeError(f"{name} 挂了")
        if counter is not None:
            counter[name] = counter.get(name, 0) + 1
        return payloads.get(name)

    transport.calls = calls
    return transport


def asked(transport) -> list:
    """这次一共问了哪几家（按顺序，含重复）。"""
    return [name for name, _url, _params in transport.calls]


# =========================================================================== 解析
class ParseTest(unittest.TestCase):
    """两家源各自的响应形状。"""

    def test_frankfurter正常(self):
        self.assertEqual(parse_frankfurter(FRANKFURTER_OK, "CNY"), Decimal("7.1234"))

    def test_er_api正常(self):
        self.assertEqual(parse_er_api(ER_API_OK, "CNY"), Decimal("7.1234"))

    def test_目标币种认不出时不出现而不是报错(self):
        """★ 这一家最容易读错的地方：`to` 里的币种认不出来它**不报错**，
        也不给 `null` —— `rates` 直接是空表。把「响应里有 rates」当成成功判据，
        这里就会拿到一个空值，而这一路只会表现为总资产悄悄不对。"""
        for parser in (parse_frankfurter, parse_er_api):
            with self.subTest(parser=parser.__name__):
                self.assertIsNone(parser(EMPTY_RATES, "CNY"))

    def test_er_api报失败时不算拿到(self):
        self.assertIsNone(parse_er_api(ER_API_FAIL, "CNY"))

    def test_不是字典的响应一律None(self):
        for payload in (None, [], "7.1", 7.1, True):
            with self.subTest(payload=payload):
                self.assertIsNone(parse_frankfurter(payload, "CNY"))
                self.assertIsNone(parse_er_api(payload, "CNY"))

    def test_rates不是字典一律None(self):
        for payload in ({}, {"rates": None}, {"rates": []}, {"rates": "7.1"}):
            with self.subTest(payload=payload):
                self.assertIsNone(parse_frankfurter(payload, "CNY"))

    def test_脏数字一律None而不是抛异常(self):
        for raw in ("abc", "", " ", "7,1234", {"a": 1}, [1]):
            with self.subTest(raw=raw):
                self.assertIsNone(parse_frankfurter({"rates": {"CNY": raw}}, "CNY"))

    def test_0和负数不是汇率(self):
        """存一个 0 进去，总资产会变成 0，而表面上什么异常都没有。"""
        for raw in (0, "0", 0.0, -1, "-7.1"):
            with self.subTest(raw=raw):
                self.assertIsNone(parse_frankfurter({"rates": {"CNY": raw}}, "CNY"))

    def test_浮点先过str不走二进制(self):
        """JSON 里的数取出来是二进制浮点。`Decimal(7.1234)` 与 `Decimal("7.1234")`
        是两个不同的数 —— 直接拿浮点喂 Decimal，库里会存一串 54 位小数，
        而页面上看还是 7.1234。"""
        self.assertNotEqual(Decimal(7.1234), Decimal("7.1234"))
        self.assertEqual(parse_frankfurter({"rates": {"CNY": 7.1234}}, "CNY"), Decimal("7.1234"))

    def test_整数与字符串也认(self):
        self.assertEqual(parse_frankfurter({"rates": {"CNY": 7}}, "CNY"), Decimal("7"))
        self.assertEqual(parse_frankfurter({"rates": {"CNY": "7"}}, "CNY"), Decimal("7"))

    def test_币种键是大小写敏感的(self):
        """`rates` 的键就是大写币种代码。归一化是调用方的事，解析层不猜 ——
        猜出来的「差不多」，最后会变成一个差不多对的总资产。"""
        self.assertIsNone(parse_frankfurter({"rates": {"cny": 7.1}}, "CNY"))


# =========================================================================== 源名单
class SourcesTableTest(unittest.TestCase):
    """名单与顺序：`FxRate.source` 的取值域就是这里。"""

    def test_就是这两家且顺序固定(self):
        self.assertEqual(FX_SOURCE_NAMES, ("frankfurter", "er-api"))

    def test_名字不重复(self):
        """重名会让「是谁给的」变成二义的，而这一列是给人看的。"""
        self.assertEqual(len(set(FX_SOURCE_NAMES)), len(FX_SOURCE_NAMES))

    def test_每一家都配齐了名字_请求_解析(self):
        for source in FX_SOURCES:
            with self.subTest(source=source.name):
                self.assertTrue(source.name)
                self.assertTrue(callable(source.request), f"{source.name} 没有 request")
                self.assertTrue(callable(source.parse), f"{source.name} 没有 parse")

    def test_两家打的是两个不同的接口且都是https(self):
        seen = set()
        for source in FX_SOURCES:
            url, _params = source.request("USD", "CNY")
            self.assertTrue(url.startswith("https://"), f"{source.name} 不是 https：{url}")
            seen.add(url)
        self.assertEqual(len(seen), len(FX_SOURCES))

    def test_目标币种要么进查询串要么这家根本不支持(self):
        """这条差异决定了「命中判据」只能是 `rates` 里有没有这个币种 ——
        不能是「响应里有没有 rates」。"""
        url, params = frankfurter_request("USD", "CNY")
        self.assertEqual(url, "https://api.frankfurter.app/latest")
        self.assertEqual(params, {"from": "USD", "to": "CNY"})

        url, params = er_api_request("USD", "CNY")
        self.assertIn("USD", url, "源币种要出现在路径里")
        self.assertNotIn("CNY", url, "这一家不接受目标币种")
        self.assertEqual(params, {})


# =========================================================================== 顺序与来源
class FetchFxTest(unittest.TestCase):
    """「第一家的响应缺了这个币种时要轮到第二家」，以及 `source` 是谁。"""

    def test_第一家活着就只用第一家(self):
        transport = scripted({"frankfurter": FRANKFURTER_OK, "er-api": ER_API_OK})
        got = fetch_fx("USD", "CNY", transport=transport)
        self.assertEqual(got.rate, Decimal("7.1234"))
        self.assertEqual(got.source, "frankfurter")
        self.assertEqual(asked(transport), ["frankfurter"], "第一家成了就不该再问第二家")

    def test_第一家不认这个币种就轮到第二家(self):
        """★ 空 rates 不是「失败」，这家一句错话都没说 —— 但它给不了这个币种。
        少了这一步，认不出的币种会一路变成「没有汇率」。"""
        transport = scripted({"frankfurter": EMPTY_RATES, "er-api": ER_API_OK})
        got = fetch_fx("USD", "CNY", transport=transport)
        self.assertEqual(got.rate, Decimal("7.1234"))
        self.assertEqual(asked(transport), ["frankfurter", "er-api"])

    def test_第一家挂了才轮到第二家(self):
        transport = scripted({"frankfurter": None, "er-api": ER_API_OK})
        got = fetch_fx("USD", "CNY", transport=transport)
        self.assertEqual(got.rate, Decimal("7.1234"))
        self.assertEqual(asked(transport), ["frankfurter", "er-api"])

    def test_source就是实际给价的那一家(self):
        """★★ 本条的命门。改动前这一格永远是第一家的名字（硬编码），
        所以备用源说话时，库里那行与 `/market/fx/` 回给客户端的 `source` 都是假的。"""
        transport = scripted({"frankfurter": None, "er-api": ER_API_OK})
        self.assertEqual(fetch_fx("USD", "CNY", transport=transport).source, "er-api")

    def test_顺序真的生效_两家都有价取第一家(self):
        other = {"result": "success", "base_code": "USD", "rates": {"CNY": 9.9}}
        transport = scripted({"frankfurter": FRANKFURTER_OK, "er-api": other})
        got = fetch_fx("USD", "CNY", transport=transport)
        self.assertEqual(got.rate, Decimal("7.1234"))
        self.assertEqual(asked(transport), ["frankfurter"])

    def test_抛异常算这一家挂了而不是把整个请求带崩(self):
        transport = scripted({"er-api": ER_API_OK}, raise_on=("frankfurter",))
        got = fetch_fx("USD", "CNY", transport=transport)
        self.assertEqual(got.rate, Decimal("7.1234"))
        self.assertEqual(got.source, "er-api")

    def test_两家都挂返回None(self):
        transport = scripted({})
        self.assertIsNone(fetch_fx("USD", "CNY", transport=transport))
        self.assertEqual(asked(transport), ["frankfurter", "er-api"])

    def test_两家都抛异常也只是None(self):
        transport = scripted({}, raise_on=FX_SOURCE_NAMES)
        self.assertIsNone(fetch_fx("USD", "CNY", transport=transport))

    def test_同一家一轮里只问一次(self):
        """`fetch_fx` 是个循环，很容易写成「先试一遍再兜一遍」——
        免费源限流是这条注释里反复出现的压力，问几次得能被数出来。"""
        counter: dict = {}
        transport = scripted({}, counter=counter)
        fetch_fx("USD", "CNY", transport=transport)
        self.assertEqual(counter, {"frankfurter": 1, "er-api": 1})

    def test_反向对照_改动前那个写法分不清是谁给的(self):
        """同一个假传输喂给「改动前的实现」：数一样，但它只能给你一个数。
        这就是 `source` 那行硬编码的来历 —— 不是写错了，是**当时拿不到**。"""
        transport = scripted({"frankfurter": None, "er-api": ER_API_OK})
        self.assertEqual(legacy_fetch_fx("USD", "CNY", transport), Decimal("7.1234"))
        got = fetch_fx("USD", "CNY", transport=scripted({"frankfurter": None, "er-api": ER_API_OK}))
        self.assertEqual(got.rate, Decimal("7.1234"))
        self.assertEqual(got.source, "er-api")

    def test_反向对照_空rates在原实现里是静默的None(self):
        """原实现 `rates.get(quote)` 取不到就往下走 —— 与现在同一条判据，
        但那时它连「走到第二家了吗」都没有出口可看。"""
        transport = scripted({"frankfurter": EMPTY_RATES, "er-api": ER_API_OK})
        self.assertEqual(legacy_fetch_fx("USD", "CNY", transport), Decimal("7.1234"))


# =========================================================================== 新鲜度与取最新
class FreshnessTest(unittest.TestCase):
    """「这条记录今天还能不能用」—— 两份口径合一之后剩下的那一条。"""

    TODAY = date(2026, 9, 20)

    def test_当天算新鲜(self):
        self.assertTrue(is_fresh(date(2026, 9, 20), self.TODAY))

    def test_未来日期也算新鲜(self):
        """跨时区写进来的行可能比本地日期大一天，不该被判成过期。"""
        self.assertTrue(is_fresh(date(2026, 9, 21), self.TODAY))

    def test_昨天不新鲜(self):
        """★ 就是这一格：改动前 `get_rate` 对「昨天」也直接用。
        而汇率是日频数据，昨天那条与今天那条可以差出一大截。"""
        self.assertFalse(is_fresh(date(2026, 9, 19), self.TODAY))

    def test_三个月前不新鲜(self):
        self.assertFalse(is_fresh(date(2026, 6, 20), self.TODAY))

    def test_日期为空不新鲜(self):
        self.assertFalse(is_fresh(None, self.TODAY))


class LatestTest(unittest.TestCase):
    """`latest()` —— 取哪一条只有这一处。"""

    def test_没有记录返回None(self):
        self.assertIsNone(latest([]))

    def test_取日期最大的那条而不是第一条(self):
        """★ 自己挑最大日期，不 `rows[0]`：查询有没有 ORDER BY 是调用方的事，
        而「用了哪一条」这条判据不该跟着调用方的排序漂（`Meta.ordering` 被谁
        改掉，`first()` 就会安静地换一条）。"""
        rows = [
            (date(2026, 9, 1), Decimal("7.0"), "frankfurter"),
            (date(2026, 9, 20), Decimal("7.1234"), "er-api"),
            (date(2026, 9, 10), Decimal("7.08"), "frankfurter"),
        ]
        got = latest(rows)
        self.assertEqual(got.date, date(2026, 9, 20))
        self.assertEqual(got.rate, Decimal("7.1234"))
        self.assertEqual(got.source, "er-api")

    def test_脏行跳过而不是当成一条(self):
        """日期或汇率残缺的行宁可当作没有 —— 交出一条不知道哪天的汇率，
        比说「没有」更坏。"""
        rows = [(None, Decimal("7.1"), "x"), (date(2026, 9, 20), None, "x")]
        self.assertIsNone(latest(rows))

    def test_来源为空退成空串而不是None(self):
        """这一列要直接进 JSON 与 CSV，`None` 会在两处变成两种写法。"""
        got = latest([(date(2026, 9, 20), Decimal("7.1"), None)])
        self.assertEqual(got.source, "")

    def test_一天的记录只有一条时也取得到(self):
        got = latest([(date(2026, 9, 20), Decimal("7.1"), "frankfurter")])
        self.assertEqual(got.rate, Decimal("7.1"))


# =========================================================================== 接线（读源码）
def production_files() -> list:
    """`apps/` 下**非测试**的 .py —— 测试自己会读源码做断言，不算实现。"""
    return [
        path
        for path in APPS_DIR.rglob("*.py")
        if "__pycache__" not in path.parts
        and "tests" not in path.relative_to(APPS_DIR).parts
    ]


def files_containing(needle: str) -> list:
    """含这段字面量的文件（仓库内相对 `apps/` 的路径，排序）。"""
    return sorted(
        str(path.relative_to(APPS_DIR)).replace("\\", "/")
        for path in production_files()
        if needle in path.read_text(encoding="utf-8")
    )


class WiringTest(unittest.TestCase):
    """接线没被拆掉 —— 这几条锁对应的都是「写下来了但有两份 / 没人跑」的历史。"""

    def test_扫描面不是空的(self):
        files = production_files()
        self.assertGreaterEqual(len(files), 40, f"只扫到 {len(files)} 个 .py，扫描面不对")
        self.assertIn("market/services.py", [str(p.relative_to(APPS_DIR)).replace("\\", "/") for p in files])

    def test_这个模块不许import_django(self):
        """整个模块的意义就是「能注入假传输、离线秒级验证」，一 import django 就没了。

        剥注释后再断言：模块的文档字符串里正解释着这条，直接读源码会自己命中。
        """
        code = strip_comments((APP_DIR / "fx_source.py").read_text(encoding="utf-8"))
        self.assertNotIn("django", code)

    def test_汇率表只有一个消费方(self):
        """★ 本轮修的就是这一条：改动前有两个文件碰 `FxRate`，而且判据不一样。

        多一个**写**入口 → 缓存口径必定漂（一个看「有没有记录」，一个看「是不是当天的」）；
        多一个**读**入口 → 就算写的只有一处，读的人也可以自己另定一套新鲜度判据。
        所以两个方向一起锁：这个表只许 `market/services.py` 碰。
        """
        hits = files_containing("FxRate.objects")
        self.assertEqual(hits, ["market/services.py"], f"汇率表多了一个消费方：{hits}")

    def test_汇率表只在服务层写(self):
        hits = files_containing("FxRate.objects.update_or_create(")
        self.assertEqual(hits, ["market/services.py"], f"汇率多了一个写入口：{hits}")

    def test_analytics不再自己折汇率(self):
        source = strip_comments((APPS_DIR / "analytics" / "services.py").read_text(encoding="utf-8"))
        self.assertIn("get_fx(", source, "analytics 侧没有走服务层的汇率入口")
        self.assertNotIn("FxRate", source, "analytics 侧又开始自己碰汇率表了")
        self.assertNotIn("update_or_create", source, "analytics 侧又开始自己写汇率了")

    def test_views不再自己折汇率(self):
        source = strip_comments((APP_DIR / "views.py").read_text(encoding="utf-8"))
        self.assertIn("get_fx(", source, "视图侧没有走服务层的汇率入口")
        self.assertNotIn("update_or_create", source, "视图侧又开始自己写汇率了")
        self.assertNotIn("requests.get", source, "取价/取汇率的出网只许在 services 里")

    def test_来源名字全仓只有一处定义(self):
        """★★ `source` 只许由 `FX_SOURCES` 提供。别处再写一次这个字面量，
        就是又猜了一次「谁给的」—— 而那个值是会写进库里、并且回给客户端的。

        期望值由 `FX_SOURCE_NAMES` 现算（不是手抄的两行），所以加一家源时
        这里不用改，但「谁把它抄到别处去了」会红。
        """
        for name in FX_SOURCE_NAMES:
            with self.subTest(name=name):
                hits = files_containing(f'"{name}"')
                self.assertEqual(hits, ["market/fx_source.py"], f"{name!r} 的字面量散到了：{hits}")

    def test_README里的源名单与代码一致(self):
        """两向对账：README 那张表列的是哪些源、什么顺序，由代码现算的真值核。

        多写一家、少写一家、顺序换了都会红 —— 这张表是给人看「汇率是从哪来的」
        的唯一一处，它漂了没人会发现。"""
        section = readme_fx_section()
        listed = re.findall(r"^\| `([^`]+)` \|", section, re.M)
        self.assertEqual(
            listed,
            list(FX_SOURCE_NAMES),
            "README「汇率」那节的源表与 FX_SOURCES 对不上（多一家/少一家/顺序换了）",
        )


def readme_fx_section() -> str:
    """README 里「汇率」那一节（到下一个 `##` / `###` 标题为止）。"""
    text = README.read_text(encoding="utf-8")
    match = re.search(r"^### 汇率[^\n]*\n(.*?)(?=^## |^### )", text, re.S | re.M)
    if match is None:
        raise AssertionError("README 里找不到「汇率」那一节")
    return match.group(1)


if __name__ == "__main__":
    unittest.main()
