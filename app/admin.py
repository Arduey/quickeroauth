"""管理后台。

用 sqladmin：注册模型就有增删改查、搜索、排序、导出。
登录用 sqladmin 自带的 AuthenticationBackend（内部基于 Starlette 的
SessionMiddleware，Cookie 是 HttpOnly + SameSite=Lax）。
登录失败限流做在本进程内存里，不依赖 Nginx。
"""

from __future__ import annotations

from typing import Optional

from sqladmin import Admin, ModelView
from sqladmin.authentication import AuthenticationBackend
from sqlalchemy import select
from starlette.middleware import Middleware
from starlette.middleware.sessions import SessionMiddleware
from starlette.requests import Request

from . import config, db, security, timeutil
from .models import AdminUser, LicenseOrder, LicenseUser, Setting


def client_ip(request: Request) -> str:
    """取真实来源 IP。

    前面可能套了两层：Cloudflare + Nginx。Cloudflare 会把真实访客地址放在
    CF-Connecting-IP 里，优先用它；否则退回到 X-Forwarded-For 的第一段。
    如果取错了（比如取到 Cloudflare 自己的地址），登录限流会把所有访客算成
    同一个人，一个人试错就把大家全锁上。
    """
    cf = request.headers.get("cf-connecting-ip")
    if cf and cf.strip():
        return cf.strip()

    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        first = forwarded.split(",")[0].strip()
        if first:
            return first

    return request.client.host if request.client else "unknown"


class AdminAuth(AuthenticationBackend):
    def __init__(self, secret_key: str, max_age: Optional[int] = None) -> None:
        super().__init__(secret_key=secret_key)
        # 自己装会话中间件，不用 sqladmin 转发的 kwargs。
        # 各版本这里行为不一致：有的认 session_max_age，有的把它原样丢给
        # Starlette 的 SessionMiddleware，而后者只认 max_age，于是启动后在
        # 构建中间件时直接崩。与其猜参数名，不如以 Starlette 的口径装。
        self.middlewares = [
            Middleware(SessionMiddleware, secret_key=secret_key, max_age=max_age)
        ]

    async def login(self, request: Request) -> bool:
        ip = client_ip(request)

        if security.is_locked(ip):
            return False

        form = await request.form()
        username = str(form.get("username") or "").strip()
        password = str(form.get("password") or "")

        if not username or not password:
            security.record_failure(ip)
            return False

        factory = db.session_factory()
        async with factory() as session:
            found = (
                await session.execute(
                    select(AdminUser).where(AdminUser.username == username)
                )
            ).scalar_one_or_none()

            if found is None or not security.verify_password(
                password, found.password_hash
            ):
                security.record_failure(ip)
                return False

            found.last_login = timeutil.now()
            await session.commit()

        security.clear_failures(ip)
        request.session.update({"admin_user": username})
        return True

    async def logout(self, request: Request) -> bool:
        request.session.clear()
        return True

    async def authenticate(self, request: Request) -> bool:
        session = getattr(request, "session", None) or {}
        return bool(session.get("admin_user"))


# ---------------------------------------------------------------- 视图


class LicenseUserAdmin(ModelView, model=LicenseUser):
    name = "账户"
    name_plural = "授权账户"
    icon = "fa-solid fa-user"
    column_list = [
        LicenseUser.typekey,
        LicenseUser.user,
        LicenseUser.email,
        LicenseUser.exptime,
        LicenseUser.last_time,
        LicenseUser.count,
        LicenseUser.addtime,
    ]
    column_labels = {
        LicenseUser.typekey: "账户标识",
        LicenseUser.user: "用户名",
        LicenseUser.email: "邮箱",
        LicenseUser.exptime: "过期时间",
        LicenseUser.last_time: "上次使用",
        LicenseUser.count: "使用次数",
        LicenseUser.addtime: "创建时间",
    }
    column_searchable_list = [LicenseUser.typekey, LicenseUser.user, LicenseUser.email]
    column_sortable_list = [
        LicenseUser.exptime,
        LicenseUser.last_time,
        LicenseUser.count,
        LicenseUser.addtime,
    ]
    column_default_sort = (LicenseUser.addtime, True)
    page_size = 50
    can_export = True


class LicenseOrderAdmin(ModelView, model=LicenseOrder):
    name = "订单"
    name_plural = "订单核销记录"
    icon = "fa-solid fa-receipt"
    column_list = [
        LicenseOrder.ordernumber,
        LicenseOrder.typekey,
        LicenseOrder.name,
        LicenseOrder.sku,
        LicenseOrder.money,
        LicenseOrder.ordertime,
        LicenseOrder.usetime,
    ]
    column_labels = {
        LicenseOrder.ordernumber: "订单号",
        LicenseOrder.typekey: "账户标识",
        LicenseOrder.name: "商品标题",
        LicenseOrder.sku: "规格",
        LicenseOrder.money: "金额",
        LicenseOrder.ordertime: "付款时间",
        LicenseOrder.usetime: "核销时间",
    }
    column_searchable_list = [
        LicenseOrder.ordernumber,
        LicenseOrder.typekey,
        LicenseOrder.name,
    ]
    column_sortable_list = [LicenseOrder.ordertime, LicenseOrder.usetime]
    column_default_sort = (LicenseOrder.usetime, True)
    page_size = 50
    can_export = True


class AdminUserAdmin(ModelView, model=AdminUser):
    name = "管理员"
    name_plural = "管理员账号"
    icon = "fa-solid fa-user-shield"
    column_list = [
        AdminUser.id,
        AdminUser.username,
        AdminUser.created_at,
        AdminUser.last_login,
    ]
    column_labels = {
        AdminUser.id: "ID",
        AdminUser.username: "登录名",
        AdminUser.password_hash: "密码",
        AdminUser.created_at: "创建时间",
        AdminUser.last_login: "上次登录",
    }
    column_searchable_list = [AdminUser.username]
    can_export = False


class SettingAdmin(ModelView, model=Setting):
    name = "设置项"
    name_plural = "站点设置"
    icon = "fa-solid fa-gear"
    column_list = [
        Setting.skey,
        Setting.svalue,
        Setting.remark,
        Setting.updatetime,
    ]
    column_labels = {
        Setting.skey: "配置项",
        Setting.svalue: "配置值",
        Setting.remark: "说明",
        Setting.updatetime: "更新时间",
    }
    column_searchable_list = [Setting.skey]
    # 设置项由安装向导写入，这里只允许改值，不允许增删
    can_create = False
    can_delete = False
    can_export = False


def build_admin(app, engine) -> Admin:
    secret_key = str((config.get() or {}).get("secret_key") or "")
    backend = AdminAuth(secret_key=secret_key, max_age=config.session_hours() * 3600)

    admin = Admin(
        app=app,
        engine=engine,
        authentication_backend=backend,
        base_url=config.admin_path(),
        title="授权管理后台",
    )
    admin.add_view(LicenseUserAdmin)
    admin.add_view(LicenseOrderAdmin)
    admin.add_view(AdminUserAdmin)
    admin.add_view(SettingAdmin)
    return admin
