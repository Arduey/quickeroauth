"""三个业务接口。

    POST /api/trial   试用申请
    POST /api/verify  在线校验
    POST /api/redeem  订单核销

统一响应包 {code, message, data}；业务结果一律 HTTP 200，靠 code 区分。
message 文案刻意沿用旧系统，这样现有客户端不用改。
"""

from __future__ import annotations

import re
from typing import Any, AsyncIterator, Optional, Tuple

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from . import db, platform
from . import settings as site_settings
from . import timeutil
from .models import LicenseOrder, LicenseUser

router = APIRouter()

TYPEKEY_MAX_LEN = 64


# ---------------------------------------------------------------- 出入参


class TrialIn(BaseModel):
    typekey: str = ""
    user: Optional[str] = None
    email: Optional[str] = None


class VerifyIn(BaseModel):
    typekey: str = ""
    user: Optional[str] = None
    email: Optional[str] = None


class RedeemIn(BaseModel):
    typekey: str = ""
    ordernumber: str = ""


# ---------------------------------------------------------------- 工具


def envelope(code: str, message: str, data: Any = None) -> dict:
    return {"code": code, "message": message, "data": data}


def clean_typekey(raw: Optional[str]) -> Optional[str]:
    value = (raw or "").strip()
    if not value or len(value) > TYPEKEY_MAX_LEN:
        return None
    return value


def clean_optional(raw: Optional[str]) -> Optional[str]:
    """空字符串按未传处理——不覆盖库里已有的值。"""
    value = (raw or "").strip()
    return value or None


def parse_sku(sku: str) -> Tuple[Optional[int], bool]:
    """把规格文本解析成 (月数, 是否永久)。

    数字部分就是数量，中文部分决定单位：
      含「永久」→ 永久
      含「年」  → 数字 × 12
      含「月」  → 数字
    都不含 → 无法识别，返回 (None, False)
    """
    text = (sku or "").strip()
    if not text:
        return None, False

    digits = re.sub(r"[^0-9]", "", text)
    chinese = re.sub(r"[0-9A-Za-z]", "", text)
    number = int(digits) if digits else 0

    if "永久" in chinese:
        return 0, True
    if "年" in chinese:
        return number * 12, False
    if "月" in chinese:
        return number, False
    return None, False


async def get_session() -> AsyncIterator[AsyncSession]:
    if not db.is_ready():
        raise HTTPException(status_code=503, detail="系统尚未安装")
    factory = db.session_factory()
    async with factory() as session:
        yield session


def user_summary(row: LicenseUser) -> dict:
    return {
        "typekey": row.typekey,
        "addtime": timeutil.fmt(row.addtime),
        "exptime": timeutil.fmt(row.exptime),
    }


# ---------------------------------------------------------------- 1. 试用申请


@router.post("/trial")
async def trial(payload: TrialIn, session: AsyncSession = Depends(get_session)):
    typekey = clean_typekey(payload.typekey)
    if typekey is None:
        return envelope("INVALID_KEY", "授权标识无效")

    # 一个 typekey 终身只能领一次——这是试用的全部规则，不做 IP 限流
    existing = await session.get(LicenseUser, typekey)
    if existing is not None:
        return envelope("TRIAL_ALREADY_USED", "不支持多次试用", user_summary(existing))

    trial_days = await site_settings.get_int(session, "trial_days", 1)
    if trial_days <= 0:
        trial_days = 1

    now = timeutil.now()
    row = LicenseUser(
        typekey=typekey,
        addtime=now,
        exptime=timeutil.add_days(now, trial_days),
        status="active",
        user=clean_optional(payload.user),
        email=clean_optional(payload.email),
        count=0,
    )
    session.add(row)
    try:
        await session.commit()
    except IntegrityError:
        # 并发下另一个请求抢先建了号，退回到「已领过」
        await session.rollback()
        existing = await session.get(LicenseUser, typekey)
        data = user_summary(existing) if existing is not None else None
        return envelope("TRIAL_ALREADY_USED", "不支持多次试用", data)

    return envelope("TRIAL_GRANTED", "申请已通过", user_summary(row))


# ---------------------------------------------------------------- 2. 在线校验


@router.post("/verify")
async def verify(payload: VerifyIn, session: AsyncSession = Depends(get_session)):
    typekey = clean_typekey(payload.typekey)
    if typekey is None:
        return envelope("INVALID_KEY", "授权标识无效")

    row = await session.get(LicenseUser, typekey)
    if row is None:
        # 不存在的账户不写库
        return envelope("NOT_FOUND", "不存在")

    if (row.status or "active") != "active":
        # 被封禁的账户即使时间没到也必须拒绝
        return envelope("DISABLED", "账户已被禁用")

    now = timeutil.now()
    active = row.exptime > now

    if active:
        row.last_time = now
        row.count = (row.count or 0) + 1
        new_user = clean_optional(payload.user)
        new_email = clean_optional(payload.email)
        if new_user:
            row.user = new_user
        if new_email:
            row.email = new_email
        await session.commit()
    # 已过期：不计数、不更新 last_time、也不接受 user / email 覆盖

    data = {
        "typekey": row.typekey,
        "addtime": timeutil.fmt(row.addtime),
        "exptime": timeutil.fmt(row.exptime),
        "last_time": timeutil.fmt(row.last_time),
        "count": row.count,
    }
    if active:
        return envelope("ACTIVE", "未过期", data)
    return envelope("EXPIRED", "已过期", data)


# ---------------------------------------------------------------- 3. 订单核销


@router.post("/redeem")
async def redeem(payload: RedeemIn, session: AsyncSession = Depends(get_session)):
    typekey = clean_typekey(payload.typekey)
    if typekey is None:
        return envelope("INVALID_KEY", "授权标识无效")

    order_no = (payload.ordernumber or "").strip()
    if not order_no:
        return envelope("ORDER_NOT_FOUND", "订单不存在")

    prefix = typekey.split("-")[0]

    # 幂等：这张单已经核销过
    used = await session.get(LicenseOrder, order_no)
    if used is not None:
        return envelope(
            "ORDER_ALREADY_USED",
            "订单已于{}使用过".format(timeutil.fmt(used.usetime) or ""),
        )

    base_url = await site_settings.get(session, "platform_base_url", "") or ""
    shop_email = await site_settings.get(session, "platform_email", "") or ""

    try:
        order = await platform.query_order(base_url, shop_email, order_no)
    except platform.PlatformError as exc:
        if exc.kind == "unavailable":
            return envelope("PLATFORM_UNAVAILABLE", "订单查询失败")
        if "商户" in (exc.message or ""):
            # 平台回的是「商户不存在」，说明填的商户邮箱不对，不是这张订单的问题
            return envelope("PLATFORM_CONFIG", "发卡平台的商户邮箱配置有误")
        return envelope("ORDER_NOT_FOUND", "订单不存在")

    # 订单归属：平台返回的 type 就是 typekey 的前缀
    order_type = str(order.get("type") or "").strip()
    if order_type != prefix:
        return envelope("PRODUCT_MISMATCH", "输入的订单非{}系列订单".format(prefix))

    months, is_permanent = parse_sku(str(order.get("sku") or ""))
    if months is None:
        return envelope("PRODUCT_MISMATCH", "输入的订单非{}系列订单".format(prefix))

    now = timeutil.now()

    row = await session.get(LicenseUser, typekey)
    if row is None:
        # 订单比账户先到：先建号给 1 天，再在这 1 天之上叠加
        row = LicenseUser(
            typekey=typekey,
            addtime=now,
            exptime=timeutil.add_days(now, 1),
            count=0,
        )
        session.add(row)
        await session.flush()
        base_expiry = row.exptime
    else:
        base_expiry = row.exptime

    if is_permanent:
        new_expiry = timeutil.PERMANENT
    else:
        new_expiry = timeutil.add_months(
            timeutil.later_of(base_expiry, now), months
        )
    row.exptime = new_expiry

    session.add(
        LicenseOrder(
            ordernumber=order_no,
            typekey=typekey,
            ordertime=timeutil.parse(str(order.get("creat_time") or "")),
            usetime=now,
            money=(
                str(order.get("total"))
                if order.get("total") is not None
                else None
            ),
            # title 只做留档，不参与任何判断
            name=str(order.get("title") or "") or None,
            sku=str(order.get("sku") or "") or None,
            count=1,
        )
    )

    try:
        await session.commit()
    except IntegrityError:
        # 两个请求同时核销同一单，靠主键冲突兜底
        await session.rollback()
        used = await session.get(LicenseOrder, order_no)
        used_at = timeutil.fmt(used.usetime) if used is not None else ""
        return envelope(
            "ORDER_ALREADY_USED", "订单已于{}使用过".format(used_at or "")
        )

    return envelope(
        "REDEEMED",
        "订单使用成功，有效期至 {}".format(timeutil.fmt(new_expiry)),
        {
            "typekey": typekey,
            "ordernumber": order_no,
            "exptime": timeutil.fmt(new_expiry),
            "permanent": is_permanent,
            "added_months": None if is_permanent else months,
        },
    )
