"""站点设置（setting 表）的读写。

这些配置要能在管理后台随时改，所以存在数据库里而不是 config.json 里。
不开内存缓存——setting 表很小，按主键查一次比维护缓存失效逻辑更省心，
而且永远不会读到脏值。
"""

from __future__ import annotations

from typing import Optional

from sqlalchemy.ext.asyncio import AsyncSession

from .models import Setting

DEFAULTS: dict[str, str] = {
    "site_domain": "",
    "trial_days": "1",
    "platform_base_url": "https://test.arduey.top",
    "platform_email": "",
}

# 说明文字，安装向导和后台都用它做提示，也作为 setting 表的初始内容
REMARKS: dict[str, str] = {
    "site_domain": "站点域名，用于在后台显示访问地址",
    "trial_days": "新账户试用天数",
    "platform_base_url": "发卡平台地址，例如 https://test.arduey.top",
    "platform_email": "发卡平台的商户邮箱，订单核销时用它查单",
}


async def get(session: AsyncSession, key: str, default: Optional[str] = None) -> Optional[str]:
    row = await session.get(Setting, key)
    if row is not None and row.svalue not in (None, ""):
        return row.svalue
    if key in DEFAULTS:
        return DEFAULTS[key]
    return default


async def get_int(session: AsyncSession, key: str, default: int = 0) -> int:
    raw = await get(session, key)
    try:
        return int(str(raw))
    except (TypeError, ValueError):
        return default


async def set_value(session: AsyncSession, key: str, value: str) -> None:
    row = await session.get(Setting, key)
    if row is None:
        row = Setting(skey=key, remark=REMARKS.get(key))
        session.add(row)
    row.svalue = value


async def set_many(session: AsyncSession, values: dict[str, str]) -> None:
    for key, value in values.items():
        await set_value(session, key, value)
