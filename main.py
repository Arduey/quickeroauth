"""应用入口。

给宝塔「网站 → Python 项目」用的启动命令：

    uvicorn main:app --host 127.0.0.1 --port 8000

注意 host 是 127.0.0.1，不对外暴露端口，外部流量一律经宝塔生成的站点进来。
"""

from __future__ import annotations

import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from app import api, config, db
from app import install as install_module
from app import settings as site_settings

STATIC_DIR = Path(__file__).resolve().parent / "app" / "static"

# ---------------------------------------------------------------- 反代修正

# 这些 host 说明请求是经反向代理转进来的，不是用户直接访问的地址
INTERNAL_HOSTS = {"127.0.0.1", "localhost", "0.0.0.0", "::1", "[::1]"}

DOMAIN_CACHE_SECONDS = 60.0
_domain_cache: dict[str, Any] = {"value": "", "at": 0.0}


def host_is_internal(value: str) -> bool:
    raw = (value or "").strip().lower()
    if not raw:
        return False
    host = raw
    if host.startswith("["):
        end = host.find("]")
        host = host[: end + 1] if end > 0 else host
    elif host.count(":") == 1:
        host = host.split(":")[0]
    return host in INTERNAL_HOSTS


def set_scope_header(scope: dict, name: bytes, value: str) -> None:
    headers = [(k, v) for (k, v) in scope.get("headers", []) if k.lower() != name]
    headers.append((name, value.encode("latin-1", "ignore")))
    scope["headers"] = headers


async def load_site_domain() -> str:
    """站点域名存在数据库里，带一个短缓存，避免每个请求都查一次。"""
    now = time.monotonic()
    if _domain_cache["value"] and now - _domain_cache["at"] < DOMAIN_CACHE_SECONDS:
        return _domain_cache["value"]
    if not db.is_ready():
        return ""
    try:
        factory = db.session_factory()
        async with factory() as session:
            value = await site_settings.get(session, "site_domain", "") or ""
    except Exception:  # noqa: BLE001
        return _domain_cache["value"]
    _domain_cache["value"] = value.strip()
    _domain_cache["at"] = now
    return _domain_cache["value"]


# ---------------------------------------------------------------- 生命周期


@asynccontextmanager
async def lifespan(_app: FastAPI):
    # 补上后加的列，这样已经跑着的库也能跟着升级，不用人工改表
    if db.is_ready():
        try:
            await db.ensure_schema()
        except Exception:  # noqa: BLE001
            pass
    yield
    await db.dispose()


app = FastAPI(
    title="授权管理系统",
    # 生产系统不暴露接口文档页
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
    lifespan=lifespan,
)

app.include_router(api.router, prefix="/api")
app.include_router(install_module.router)
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


@app.middleware("http")
async def installation_gate(request: Request, call_next):
    """没安装好之前，除了安装向导什么都不给走。"""
    if config.is_installed():
        return await call_next(request)

    path = request.url.path
    if path.startswith("/install") or path.startswith("/static"):
        return await call_next(request)

    if path.startswith("/api"):
        return JSONResponse(
            status_code=503,
            content={
                "code": "NOT_INSTALLED",
                "message": "系统尚未安装",
                "data": None,
            },
        )

    return RedirectResponse(url="/install", status_code=302)


@app.middleware("http")
async def reverse_proxy_fix(request: Request, call_next):
    """把反向代理丢掉的信息补回来。

    宝塔自动生成的 Nginx 配置经常不传 `proxy_set_header Host $host`，后端于是
    把自己当成 127.0.0.1，所有跳转地址都指向 127.0.0.1，浏览器当然连不上。

    这里的做法是：只在这种「经过反代」的情况下（Host 是内网地址）才修正——
    优先用 X-Forwarded-Host，其次用安装时填的站点域名。这样部署时不需要为了
    让它能跑而去手工编辑 Nginx 配置。
    """
    scope = request.scope
    current_host = request.headers.get("host") or ""

    if not host_is_internal(current_host):
        return await call_next(request)

    forwarded_host = (request.headers.get("x-forwarded-host") or "").strip()
    real_host = forwarded_host or await load_site_domain()

    if real_host:
        set_scope_header(scope, b"host", real_host)
        proto = (request.headers.get("x-forwarded-proto") or "").split(",")[0].strip()
        scope["scheme"] = proto or "https"
        return await call_next(request)

    # 连站点域名都没配：至少把跳转地址变成相对路径，不把用户丢到 127.0.0.1
    response = await call_next(request)
    location = response.headers.get("location")
    if location:
        parts = urlsplit(location)
        if parts.netloc and host_is_internal(parts.netloc):
            response.headers["location"] = urlunsplit(
                ("", "", parts.path or "/", parts.query, parts.fragment)
            )
    return response


# 已安装才挂管理后台和数据库
_cfg = config.load()
if _cfg:
    from app import admin as admin_module  # noqa: E402

    db.init(config.database_url(_cfg))
    app.include_router(admin_module.router)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("main:app", host="127.0.0.1", port=8000, reload=False)
