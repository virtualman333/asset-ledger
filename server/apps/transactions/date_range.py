# -*- coding: utf-8 -*-
"""把 `?from=YYYY-MM-DD` / `?to=YYYY-MM-DD` 折成一对**带时区的时间**边界。

为什么不能用 `traded_at__date`
------------------------------
`filter(traded_at__date__gte="2027-01-01")` 看着更直白，但在 `USE_TZ = True` + MySQL 上
它是**坏的**：Django 为了把 UTC 列按本地日历切天，会生成
`CONVERT_TZ(col, 'UTC', 'Asia/Shanghai')`；而 MySQL 的时区表（`mysql.time_zone_name`）
默认没装，`CONVERT_TZ` 于是**恒返回 NULL** —— 查询不报错，只是一条都选不出来。

本机实测（8 条流水里有一条 2027-01-01）：

    SELECT CONVERT_TZ('2027-01-01 02:00:00', 'UTC', 'Asia/Shanghai')  ->  NULL
    filter(traded_at__date__gte="2027-01-01", traded_at__date__lte="2027-01-01")  ->  0 条
    filter(traded_at__year=2027)  ->  1 条        （Django 对 __year 有另外的优化，不踩这个坑）

所以「按日期区间查流水」在装了时区表的机器上是好的、在本机是恒空的：
**同一份代码在两台机器上表现不同，而两边都不报错**，这类缺陷最难查。
`docs/DESIGN.md` 第 6 节从 M0 起就写着 `GET /transactions/?account=&asset=&from=&to=`，
也就是说这条承诺一直没有落点。

口径
----
用户填的是「哪一天」，不是「几点」，所以 `from` / `to` **都含当天**，
区间是 `[from 本地零点, (to + 1 天) 本地零点)` —— 右端开，一天的宽度恰好 24 小时。
用本地零点而不是 UTC 零点，是为了让「2027-01-01」在界面上和导出里是同一天；
本地零点折成带时区的瞬间这一步在 **Python 里**做，不依赖数据库的时区表。
"""
from __future__ import annotations

import re
from datetime import date, datetime, time, timedelta

#: 只认 `YYYY-MM-DD`。刻意不用 `date.fromisoformat` 的宽松形态（它接受 `20270101`、
#: 甚至 `2027-01-01T10:00`），因为那会把「填错了」变成「悄悄按另一件事办」。
DAY_RE = re.compile(r"\d{4}-\d{2}-\d{2}", re.ASCII)


class DateRangeError(ValueError):
    """用户给的日期看不懂 —— 调用方该回 400，而不是把它当成「没有这个筛选」。"""


def parse_day(value, field: str) -> date:
    """`"2027-01-01"` → `date(2027, 1, 1)`；看不懂就抛 `DateRangeError`。"""
    text = (value or "").strip()
    if not DAY_RE.fullmatch(text):
        raise DateRangeError(f"{field} 要是 YYYY-MM-DD 格式的日期（收到 {value!r}）")
    try:
        return date.fromisoformat(text)
    except ValueError as exc:  # 形如 2027-02-30：格式对、日子不存在
        raise DateRangeError(f"{field} 不是一个真实存在的日期（收到 {value!r}）") from exc


def day_start(day: date, tz) -> datetime:
    """这一天的**本地零点**（带时区）。`tz` 由调用方注入，本模块不 import django。"""
    if tz is None:
        raise ValueError(
            "tz 必须是一个真的时区对象 —— 缺省成 None 会折出「没有时区」的时间，"
            "拿它去跟库里的 UTC 值比是静默错位（差 8 小时且不报错）"
        )
    return datetime.combine(day, time.min, tzinfo=tz)


def range_bounds(from_text, to_text, tz):
    """→ `(start, end)`，半开区间 `[start, end)`；没给的那一头是 `None`。

    `to` 会往后推一天，因为用户写的是「到哪一天为止」，那天**整天都算**。
    `to` 早于 `from` 直接报错 —— 那是一次笔误，静默返回空列表会让人以为「这段时间没记过账」。

    三个参数都必填：`tz` 没有默认值，是想让每个调用点都显式说出「按哪个时区切天」。
    """
    start = day_start(parse_day(from_text, "from"), tz) if from_text else None
    end = day_start(parse_day(to_text, "to") + timedelta(days=1), tz) if to_text else None
    if start is not None and end is not None and end <= start:
        raise DateRangeError(f"to（{to_text}）早于 from（{from_text}）—— 这两个参数写反了")
    return start, end
