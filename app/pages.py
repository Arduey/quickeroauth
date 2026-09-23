"""对外的介绍页。"""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from . import config

router = APIRouter()
templates = Jinja2Templates(
    directory=str(Path(__file__).resolve().parent / "templates")
)


@router.get("/", response_class=HTMLResponse)
async def home(request: Request):
    """根路径的介绍页。未安装时会被中间件先重定向到 /install。"""
    return templates.TemplateResponse(
        request,
        "home.html",
        {
            "admin_base": config.admin_path(),
            "active": "/",
            "current": None,   # 访客，不显示后台导航
            "partial": False,
            "ok": "",
            "error": "",
        },
    )
