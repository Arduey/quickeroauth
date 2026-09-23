"""向发卡平台（爱发电铺）核对订单。

接口：GET {base_url}/api/order/query?email={商户邮箱}&order_no={订单号}
公开接口，没有鉴权；凭证就是「商户邮箱 + 订单号」，而且只返回已支付订单。
"""

from __future__ import annotations

from typing import Any

import httpx

REQUEST_TIMEOUT = 5.0

# 平台返回的字段含义（已在设计阶段确认）：
#   order_id   订单号
#   creat_time 付款时间
#   type       typekey 的前缀
#   sku        增加多少有效期（年 / 月 / 永久）
#   total      金额
#   title      忽略
#   seller     忽略


class PlatformError(Exception):
    """kind: unavailable（可重试） | not_found（订单不存在或未支付）"""

    def __init__(self, kind: str, message: str) -> None:
        super().__init__(message)
        self.kind = kind
        self.message = message


async def query_order(base_url: str, email: str, order_no: str) -> dict[str, Any]:
    if not base_url or not email:
        raise PlatformError("unavailable", "平台地址或商户邮箱未配置")

    url = base_url.rstrip("/") + "/api/order/query"
    try:
        async with httpx.AsyncClient(
            timeout=REQUEST_TIMEOUT, follow_redirects=True
        ) as client:
            response = await client.get(
                url, params={"email": email, "order_no": order_no}
            )
    except httpx.HTTPError:
        raise PlatformError("unavailable", "订单查询失败")

    if response.status_code >= 500:
        raise PlatformError("unavailable", "订单查询失败")

    try:
        payload = response.json()
    except ValueError:
        raise PlatformError("not_found", "订单不存在")

    if not isinstance(payload, dict):
        raise PlatformError("not_found", "订单不存在")

    if response.status_code != 200 or not payload.get("ok"):
        # 把平台自己的说明带出去，便于区分「订单不存在」和「配置不对」
        detail = str(payload.get("message") or "").strip()
        raise PlatformError("not_found", detail or "订单不存在")

    data = payload.get("data")
    if not isinstance(data, dict) or not data.get("order_id"):
        raise PlatformError("not_found", "订单不存在")

    return data
