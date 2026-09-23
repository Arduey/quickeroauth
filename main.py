"""应用入口。

给宝塔「网站 → Python 项目」用的启动命令：

    uvicorn main:app --host 127.0.0.1 --port 8000

注意 host 是 127.0.0.1，不对外暴露端口，外部流量一律经宝塔生成的站点进来。
"""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, RedirectResponse

from app import api, config, db
from app import install as install_module


@asynccontextmanager
async def lifespan(_app: FastAPI):
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


@app.middleware("http")
async def installation_gate(request: Request, call_next):
    """没安装好之前，除了安装向导什么都不给走。"""
    if config.is_installed():
        return await call_next(request)

    path = request.url.path
    if path.startswith("/install"):
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


# 已安装才挂管理后台——未安装时不需要 sqladmin，也就不导入它，
# 这样万一 sqladmin 没装上，安装向导本身还是打得开的。
_cfg = config.load()
if _cfg:
    from app import admin as admin_module  # noqa: E402

    db.init(config.database_url(_cfg))
    admin_module.build_admin(app, db.engine)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("main:app", host="127.0.0.1", port=8000, reload=False)
