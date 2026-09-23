"""时间工具：全系统统一北京时间。

库里所有时间列都是 DATETIME（不带时区），存的就是北京墙上时间。
这里生成的 naive datetime 与它口径一致，不掺任何 tzinfo。
"""

from __future__ import annotations

import calendar
from datetime import datetime, timedelta, timezone
from typing import Optional

BEIJING = timezone(timedelta(hours=8), name="Asia/Shanghai")

DISPLAY_FORMAT = "%Y-%m-%d %H:%M:%S"

# 「永久」用一个足够远的时间点表示，而不是真的存 NULL——
# 用 NULL 的话每个比较点都要先判空，容易漏。
PERMANENT = datetime(9999, 12, 31, 23, 59, 59)


def now() -> datetime:
    """当前北京时间，精确到秒（与列精度一致）。"""
    return datetime.now(BEIJING).replace(tzinfo=None, microsecond=0)


def fmt(value: Optional[datetime]) -> Optional[str]:
    return value.strftime(DISPLAY_FORMAT) if value else None


def parse(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    raw = str(value).strip()
    for pattern in (DISPLAY_FORMAT, "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(raw, pattern)
        except ValueError:
            continue
    return None


def add_months(value: datetime, months: int) -> datetime:
    """加月份，处理月末溢出（1月31日 + 1个月 = 2月28/29日）。"""
    if months == 0:
        return value
    total = value.month - 1 + months
    year = value.year + total // 12
    month = total % 12 + 1
    if year > 9999:
        return PERMANENT
    day = min(value.day, calendar.monthrange(year, month)[1])
    return value.replace(year=year, month=month, day=day)


def add_days(value: datetime, days: int) -> datetime:
    return value + timedelta(days=days)


def later_of(a: datetime, b: datetime) -> datetime:
    """取两个时间中较晚的那个（续期时用：过期就从现在起算）。"""
    return a if a > b else b
