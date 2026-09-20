# -*- coding: utf-8 -*-
"""README 里**手抄的路由条数**必须有东西盯着 —— 判据的纯函数侧。

真值在 Django 本体那边（`scripts/check_routes.py` 的 `[7/7]`），所以本文件只验两件事：

1. **解析面不空**：真 README 上，四个键每个都至少命中一次。正则失配要能被发现 ——
   否则「那句话被改写了」会表现成「没什么可比」，而这正是这类检查最经典的恒真形态。
2. **判据本身不假**：合成的过期数字必须被报出来，正确的必须安静，缺那句话算失败，
   `actual` 少键也算失败。

本文件**不 import django**（`route_inventory` 是纯模块），能待在「不需要数据库、
不需要 Django」的那一侧。

跑法（在 server/ 下）：python -m unittest discover -s apps -t .
"""
import unittest
from pathlib import Path

from apps.core import route_inventory
from apps.core.route_inventory import (
    README_COUNT_PATTERNS,
    readme_count_problems,
    readme_route_counts,
)

ROOT = Path(__file__).resolve().parents[4]
README = ROOT / "README.md"

#: 一份「对得上」的实测值。数字本身不重要 —— 重要的是判据既能静默也能报错。
ACTUAL = {"django": 30, "inventory": 29, "common": 29, "format": 11}

#: 用真 README 里的措辞拼出来的样本（改数字即模拟「文档过期」）
SAMPLE = (
    "### 路由清单与 Django 的对账\n\n"
    "实测：`api/v1/` 下 Django 认得 **{django}** 条，清单 **{inventory}** 条，"
    "唯一那条差异是 DefaultRouter 的 API 根视图，已登记。\n"
    "DRF 那 {format} 条 `format` 后缀变体整族排除。\n"
    "实测 **{common} 条共有路径逐条一致、0 分歧**。\n"
)


def sample(django=30, inventory=29, common=29, format_=11):
    return SAMPLE.format(django=django, inventory=inventory, common=common, format=format_)


class TestTheRealReadmeIsStillParsed(unittest.TestCase):
    """解析面自证：四个键都要在真 README 上命中，不许悄悄变成「没什么可比」。"""

    @classmethod
    def setUpClass(cls):
        cls.text = README.read_text(encoding="utf-8")
        cls.found = readme_route_counts(cls.text)

    def test_the_readme_is_there(self):
        self.assertGreater(len(self.text), 1000)

    def test_every_pattern_matches_at_least_once(self):
        for key in README_COUNT_PATTERNS:
            self.assertTrue(
                self.found[key],
                f"README 里找不到描述「{key}」条数的那句话了（正则 "
                f"{README_COUNT_PATTERNS[key]!r} 一处都没匹配）—— 那句话被改写了，"
                "正则要跟着改，否则这个数就没人管了",
            )

    def test_the_numbers_it_matches_are_plausible(self):
        """解出来的数应当是两位数量级的条数 —— 解出 0 或 4 位数说明正则咬到了别的句子。"""
        for key, hits in self.found.items():
            for n in hits:
                self.assertTrue(10 <= n <= 999, f"「{key}」解出了不像是路由条数的数：{n}")


class TestTheCheckReportsStaleNumbers(unittest.TestCase):
    def test_a_matching_readme_is_silent(self):
        self.assertEqual(readme_count_problems(sample(), ACTUAL), [])

    def test_each_key_is_checked(self):
        """逐个把数字改掉 —— 每个键都必须自己会红，不许「改了别的键才红」。"""
        cases = {"django": 31, "inventory": 28, "common": 30, "format": 12}
        for key, wrong in cases.items():
            with self.subTest(key=key):
                text = sample(**{("format_" if key == "format" else key): wrong})
                problems = readme_count_problems(text, ACTUAL)
                self.assertTrue(problems, f"把「{key}」改成 {wrong} 之后没有报错")
                self.assertTrue(
                    any(key in p for p in problems),
                    f"报出来的问题里没点名「{key}」：{problems}",
                )

    def test_the_same_fact_written_twice_is_checked_twice(self):
        """同一件事在 README 里写了两遍（共有路径那句就写了两处）—— 两处都要查。

        只查第一处的话，第二处会一直是错的，而读文档的人看到的正是第二处。
        """
        text = sample() + "\n（实测 30 条共有路径 0 分歧）\n"
        problems = readme_count_problems(text, ACTUAL)
        self.assertTrue(problems, "第二处写的是 30、实测 29，却没有报错")

    def test_a_missing_sentence_is_a_problem_not_a_pass(self):
        """正则失配要**报错**，不是「没什么可比所以通过」。"""
        text = "# 标题\n\n这一节被人整段删掉了。\n"
        problems = readme_count_problems(text, ACTUAL)
        self.assertEqual(len(problems), len(ACTUAL), f"四个键都该报，实际：{problems}")
        for key in ACTUAL:
            self.assertTrue(any(key in p for p in problems), f"{key} 没报出来：{problems}")

    def test_a_missing_actual_value_is_a_problem(self):
        """脚本哪天不再算某个数了 —— 不许静默跳过。"""
        problems = readme_count_problems(sample(), {"django": 30})
        self.assertTrue(
            any("少了一个实测值" in p for p in problems),
            f"实值缺了三个键却没报：{problems}",
        )

    def test_the_report_points_this_way(self):
        """报出来的话得能照做：说清「文档写的是几、实测是几」。"""
        problems = readme_count_problems(sample(django=29), ACTUAL)
        self.assertTrue(any("29" in p and "30" in p for p in problems), problems)


class TestThePatternsAreNotVacuous(unittest.TestCase):
    def test_the_pattern_set_is_complete(self):
        """四个键是这份文档里出现的全部手抄条数 —— 少一个就少管一处。"""
        self.assertEqual(set(README_COUNT_PATTERNS), set(ACTUAL))

    def test_a_pattern_cannot_match_an_unrelated_sentence(self):
        """反向对照：拿一句无关的话，一个键都不该命中。"""
        found = readme_route_counts("本机实测 8 条流水、3 个账户、2 天前的进程。\n")
        self.assertEqual(found, {k: [] for k in README_COUNT_PATTERNS})


if __name__ == "__main__":
    unittest.main()
