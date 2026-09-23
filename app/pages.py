"""对外的静态页面：首页、接口说明。

这两个页面以访客身份渲染（没有后台导航），顶栏只留一个「控制台」入口。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from . import config

router = APIRouter()
templates = Jinja2Templates(
    directory=str(Path(__file__).resolve().parent / "templates")
)

# 接口说明页的内容。改动接口时要一起改这里 —— 页面上的错误码和
# app/api.py 里的 envelope() 调用是一一对应的。
API_DOCS: list[dict] = [
    {
        "index": 1,
        "name": "试用申请",
        "method": "POST",
        "path": "/api/trial",
        "summary": "给一个新的授权标识建号，有效期一天。同一个标识终身只能领一次。",
        "request": '{\n  "typekey": "Pro-8f3a91c2",\n  "user": "张三",          // 可为空\n  "email": "a@b.com"      // 可为空\n}',
        "response": '{\n  "code": "TRIAL_GRANTED",\n  "message": "申请已通过",\n  "data": {\n    "typekey": "Pro-8f3a91c2",\n    "addtime": "2026-03-01 13:05:22",\n    "exptime": "2026-03-02 13:05:22"\n  }\n}',
        "codes": [
            ("TRIAL_GRANTED", "申请已通过", "新建成功，有效期从此刻起一天"),
            ("TRIAL_ALREADY_USED", "不支持多次试用", "该标识已存在；不改动任何字段，原样返回原有时间"),
            ("INVALID_KEY", "授权标识无效", "为空或超过 64 个字符"),
        ],
    },
    {
        "index": 2,
        "name": "在线校验",
        "method": "POST",
        "path": "/api/verify",
        "summary": "确认授权当前是否有效。只有有效时才会计数并更新「上次使用时间」。",
        "request": '{\n  "typekey": "Pro-8f3a91c2",\n  "user": "张三",          // 可选，非空才覆盖\n  "email": "a@b.com"      // 可选，非空才覆盖\n}',
        "response": '{\n  "code": "ACTIVE",\n  "message": "未过期",\n  "data": {\n    "typekey": "Pro-8f3a91c2",\n    "addtime": "2026-03-01 13:05:22",\n    "exptime": "2027-03-01 13:05:22",\n    "last_time": "2026-03-02 09:12:00",\n    "count": 13\n  }\n}',
        "codes": [
            ("ACTIVE", "未过期", "有效；已计数并更新 last_time"),
            ("EXPIRED", "已过期", "到期时间早于或等于当前时间"),
            ("DISABLED", "账户已被禁用", "被后台封禁，即使没到期也拒绝"),
            ("NOT_FOUND", "不存在", "账户不存在；不写库"),
            ("INVALID_KEY", "授权标识无效", "为空或超过 64 个字符"),
        ],
    },
    {
        "index": 3,
        "name": "订单核销",
        "method": "POST",
        "path": "/api/redeem",
        "summary": (
            "凭订单号延长有效期。订单真伪由服务端向发卡平台核对，客户端只传订单号。"
            "如果这个标识还不存在，服务端会自动建号，先给一天垫底再加上订单时长 —— "
            "这样「先付款、后安装」的用户也能直接用。"
        ),
        "request": '{\n  "typekey": "Pro-8f3a91c2",\n  "ordernumber": "AFD2026021400001"\n}',
        "response": '{\n  "code": "REDEEMED",\n  "message": "订单使用成功，有效期至 2027-03-01 13:05:22",\n  "data": {\n    "typekey": "Pro-8f3a91c2",\n    "ordernumber": "AFD2026021400001",\n    "exptime": "2027-03-01 13:05:22",\n    "permanent": false,\n    "added_months": 12,\n    "created": false      // 本次是否顺带新建了账户\n  }\n}',
        "codes": [
            ("REDEEMED", "订单使用成功，有效期至 …", "核销成功。data.created 为 true 表示本次顺带新建了账户，客户端应提醒用户核对标识"),
            ("ORDER_ALREADY_USED", "订单已于 … 使用过", "该订单号已核销过，一张订单只能用一次"),
            ("ORDER_NOT_FOUND", "订单不存在", "平台查不到，或该订单尚未支付"),
            ("PLATFORM_UNAVAILABLE", "订单查询失败", "平台暂时不可用，稍后可重试"),
            ("PLATFORM_CONFIG", "发卡平台的商户邮箱配置有误", "服务端的商户邮箱设置不对，请联系管理员"),
            ("PRODUCT_MISMATCH", "输入的订单非{前缀}系列订单", "订单不属于该标识所在的产品线，或规格无法识别"),
            ("INVALID_KEY", "授权标识无效", "为空或超过 64 个字符"),
        ],
    },
]


def _page(request: Request, template: str, **extra: Any) -> HTMLResponse:
    payload: dict[str, Any] = {
        "admin_base": config.admin_path(),
        "active": request.url.path,
        "current": None,       # 访客
        "show_console": True,  # 顶栏只显示「控制台」入口
        "partial": False,
        "ok": "",
        "error": "",
        # 反代场景下 Host 已被中间件修正过，所以这里拿到的是真实域名
        "site_url": str(request.base_url).rstrip("/"),
    }
    payload.update(extra)
    return templates.TemplateResponse(request, template, payload)


@router.get("/", response_class=HTMLResponse)
async def home(request: Request):
    """根路径的介绍页。未安装时会被中间件先重定向到 /install。"""
    return _page(request, "home.html")


@router.get("/api-docs", response_class=HTMLResponse)
async def api_docs(request: Request):
    """三个业务接口的使用说明。"""
    return _page(request, "apidoc.html", apis=API_DOCS)
