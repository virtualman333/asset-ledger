# -*- coding: utf-8 -*-
"""日期区间边界：`?from=` / `?to=` 折成带时区的时间。

为什么值得单独一份测试
----------------------
这段代码原本是两行 `filter(traded_at__date__gte=..., traded_at__date__lte=...)`，
看着没问题，实际在 `USE_TZ = True` + MySQL 上**恒返回 0 条**（Django 生成 `CONVERT_TZ`，
而 MySQL 的时区表默认没装，`CONVERT_TZ` 返回 NULL）—— 不报错，只是一条都选不出来。
`docs/DESIGN.md` 第 6 节从 M0 起就写着 `GET /transactions/?...&from=&to=`，
这条承诺一直没有落点。

现在口径在 Python 里算（本地零点折成带时区的瞬间），不依赖数据库的时区表。
所以这里能断言的就是那三件容易写错的事：**含当天**、**一天的宽度恰好 24 小时**、
**时区真的参与**。

按 README 的承诺，本文件在裸 Python 上就能跑（`date_range` 不 import django）。
"""
import unittest
from datetime import date, datetime, timedelta, timezone

from apps.transactions.date_range import (
    DateRangeError,
    day_start,
    parse_day,
    range_bounds,
)

#: 东八区。用固定偏移而不是 `ZoneInfo("Asia/Shanghai")`：Windows 上没有系统 tz 数据库，
#: `ZoneInfo` 要靠第三方包 `tzdata`，而这份测试要能在裸 Python 上跑。
SHANGHAI = timezone(timedelta(hours=8), "CST")
UTC = timezone.utc


class TestParseDay(unittest.TestCase):
    def test_reads_the_documented_format(self):
        self.assertEqual(parse_day("2027-01-01", "from"), date(2027, 1, 1))
        self.assertEqual(parse_day(" 2027-01-01 ", "from"), date(2027, 1, 1), "两边的空格该忽略")

    def test_rejects_everything_else(self):
        """★ 只认 `YYYY-MM-DD`。

        `date.fromisoformat` 自己很宽松（`20270101`、`2027-01-01T10:00` 都收），
        宽松的代价是「填错了」被悄悄当成另一件事办 —— 这里刻意收紧，并**报出字段名**，
        否则用户不知道是 from 写错了还是 to 写错了。
        """
        for bad in ("", "  ", "abc", "20270101", "2027-1-1", "2027/01/01",
                    "2027-01-01T10:00", "01-01-2027", "2027-13-01"):
            with self.subTest(bad=bad):
                with self.assertRaises(DateRangeError) as ctx:
                    parse_day(bad, "from")
                self.assertIn("from", str(ctx.exception), "报错里没点名是哪个字段")

    def test_rejects_a_day_that_does_not_exist(self):
        """格式对、日子不存在（2027 不是闰年）—— 也是 400，不是静默。"""
        with self.assertRaises(DateRangeError) as ctx:
            parse_day("2027-02-30", "to")
        self.assertIn("to", str(ctx.exception))


class TestRangeBounds(unittest.TestCase):
    def test_both_missing_means_no_bounds(self):
        self.assertEqual(range_bounds(None, None, SHANGHAI), (None, None))

    def test_from_only_starts_at_local_midnight(self):
        start, end = range_bounds("2027-01-01", None, SHANGHAI)
        self.assertEqual(start, datetime(2027, 1, 1, 0, 0, tzinfo=SHANGHAI))
        self.assertIsNone(end)

    def test_to_only_ends_at_the_next_local_midnight(self):
        """★ `to` **含当天**：所以右端是次日零点，而且必须是开区间。"""
        start, end = range_bounds(None, "2027-01-01", SHANGHAI)
        self.assertIsNone(start)
        self.assertEqual(end, datetime(2027, 1, 2, 0, 0, tzinfo=SHANGHAI))

    def test_one_day_is_exactly_24_hours_wide(self):
        """★ 同一天的 from/to 只圈一天 —— 不是 0 天也不是 2 天。"""
        start, end = range_bounds("2027-01-01", "2027-01-01", SHANGHAI)
        self.assertEqual(end - start, timedelta(days=1))

    def test_the_last_moment_of_the_day_is_inside(self):
        """★ 「含当天」的行为判据：当天 23:59:59 在区间里，次日 00:00 在区间外。"""
        start, end = range_bounds("2027-01-01", "2027-01-01", SHANGHAI)
        self.assertTrue(start <= datetime(2027, 1, 1, 23, 59, 59, tzinfo=SHANGHAI) < end)
        self.assertFalse(datetime(2027, 1, 2, 0, 0, 0, tzinfo=SHANGHAI) < end)

    def test_a_multi_day_range_covers_the_whole_span(self):
        start, end = range_bounds("2027-01-01", "2027-01-03", SHANGHAI)
        self.assertEqual(end - start, timedelta(days=3))

    def test_reversed_bounds_raise_instead_of_returning_nothing(self):
        """to 早于 from 是一次笔误 —— 静默返回空列表会让人以为「这段时间没记过账」。"""
        with self.assertRaises(DateRangeError):
            range_bounds("2027-01-02", "2027-01-01", SHANGHAI)

    def test_a_missing_timezone_raises_instead_of_producing_naive_times(self):
        """★ 不给时区折出的是「无时区」的时间，拿去跟库里的 UTC 值比是静默差 8 小时。"""
        with self.assertRaises(ValueError):
            range_bounds("2027-01-01", "2027-01-01", None)
        with self.assertRaises(TypeError):
            range_bounds("2027-01-01", "2027-01-01")  # type: ignore[call-arg]


class TestTheTimezoneActuallyParticipates(unittest.TestCase):
    """这一条正是 `CONVERT_TZ` 想做的事 —— 只是它在没装时区表的 MySQL 上做不了。"""

    def test_the_same_local_day_is_a_different_instant_in_another_zone(self):
        start_cn, _ = range_bounds("2027-01-01", None, SHANGHAI)
        start_utc, _ = range_bounds("2027-01-01", None, UTC)
        self.assertNotEqual(start_cn, start_utc)
        # 东八区的 1 月 1 日零点 = 前一天 16:00 UTC
        self.assertEqual(start_cn.astimezone(UTC), datetime(2026, 12, 31, 16, 0, tzinfo=UTC))

    def test_day_start_keeps_the_zone(self):
        self.assertEqual(day_start(date(2027, 1, 1), SHANGHAI).utcoffset(), timedelta(hours=8))


if __name__ == "__main__":
    unittest.main()
