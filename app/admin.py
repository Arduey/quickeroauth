"""管理后台。

自己写的，不依赖任何第三方 admin 库。登录用签名 cookie（itsdangerous），
密钥取 config.json 里的 secret_key；页面服务端渲染，不引任何前端框架和 CDN。
"""

from __future__ import annotations

import csv
import io
import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, AsyncIterator, Optional

from fastapi import APIRouter, Depends, File, Form, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from sqlalchemy import delete, func, or_, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from . import config, db, security, timeutil
from . import settings as site_settings
from .models import AdminUser, LicenseOrder, LicenseUser, Setting

router = APIRouter()
templates = Jinja2Templates(
    directory=str(Path(__file__).resolve().parent / "templates")
)

ADMIN_BASE = config.admin_path()
COOKIE_NAME = "admin_session"
PAGE_SIZE = 50
SETTING_KEYS = ("site_domain", "trial_days", "platform_base_url", "platform_email")


# ---------------------------------------------------------------- 会话


def _serializer() -> URLSafeTimedSerializer:
    secret = str((config.get() or {}).get("secret_key") or "not-installed")
    return URLSafeTimedSerializer(secret, salt="quickeroauth-admin")


def _session_max_age() -> int:
    return max(600, config.session_hours() * 3600)


def current_admin(request: Request) -> Optional[str]:
    raw = request.cookies.get(COOKIE_NAME)
    if not raw:
        return None
    try:
        data = _serializer().loads(raw, max_age=_session_max_age())
    except (BadSignature, SignatureExpired):
        # 正常的「没登录 / 会话过期 / cookie 被改过」
        return None
    # 这里刻意不吞别的异常：签名密钥读不到、itsdangerous 没装上，
    # 都会让登录静默失败、谁都进不了后台，必须让它炸在日志里。
    if isinstance(data, dict):
        name = data.get("username")
        if name:
            return str(name)
    return None


def set_session(response: RedirectResponse, username: str) -> None:
    token = _serializer().dumps({"username": username})
    response.set_cookie(
        COOKIE_NAME,
        token,
        max_age=_session_max_age(),
        httponly=True,
        samesite="lax",
        secure=True,
        path="/",
    )


def clear_session(response: RedirectResponse) -> None:
    try:
        response.delete_cookie(COOKIE_NAME, path="/")
    except Exception:  # noqa: BLE001
        pass


def guard(request: Request) -> Optional[RedirectResponse]:
    if current_admin(request) is None:
        return RedirectResponse(url=ADMIN_BASE + "/login", status_code=303)
    return None


def client_ip(request: Request) -> str:
    """真实来源 IP。前面可能套了 Cloudflare + Nginx。"""
    cf = request.headers.get("cf-connecting-ip")
    if cf and cf.strip():
        return cf.strip()
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        first = forwarded.split(",")[0].strip()
        if first:
            return first
    return request.client.host if request.client else "unknown"


# ---------------------------------------------------------------- 基础


async def db_session() -> AsyncIterator[AsyncSession]:
    factory = db.session_factory()
    async with factory() as session:
        yield session


def render(request: Request, template: str, **context: Any) -> HTMLResponse:
    payload: dict[str, Any] = {
        "admin_base": ADMIN_BASE,
        "current": current_admin(request),
        "ok": request.query_params.get("ok") or "",
        "error": context.pop("error", "") or request.query_params.get("error") or "",
    }
    payload.update(context)
    return templates.TemplateResponse(request, template, payload)


def redirect(path: str, ok: str = "", error: str = "") -> RedirectResponse:
    url = ADMIN_BASE + path
    query = []
    if ok:
        query.append("ok=" + _q(ok))
    if error:
        query.append("error=" + _q(error))
    if query:
        url += ("&" if "?" in url else "?") + "&".join(query)
    return RedirectResponse(url=url, status_code=303)


def _q(value: str) -> str:
    from urllib.parse import quote_plus

    return quote_plus(value)


def account_state(row: LicenseUser, now=None) -> str:
    if (row.status or "active") != "active":
        return "disabled"
    moment = now or timeutil.now()
    return "active" if row.exptime and row.exptime > moment else "expired"


# ---------------------------------------------------------------- 登录


@router.get(ADMIN_BASE + "/login")
async def login_page(request: Request):
    if current_admin(request) is not None:
        return redirect("/")
    return render(request, "admin/login.html")


@router.post(ADMIN_BASE + "/login")
async def login_submit(
    request: Request,
    username: str = Form(""),
    password: str = Form(""),
    session: AsyncSession = Depends(db_session),
):
    ip = client_ip(request)

    if security.is_locked(ip):
        return render(
            request, "admin/login.html", error="登录失败次数过多，请过一会儿再试"
        )

    name = (username or "").strip()
    if not name or not password:
        security.record_failure(ip)
        return render(request, "admin/login.html", error="用户名和密码都要填")

    found = (
        await session.execute(select(AdminUser).where(AdminUser.username == name))
    ).scalar_one_or_none()

    if found is None or not security.verify_password(password, found.password_hash):
        security.record_failure(ip)
        return render(request, "admin/login.html", error="用户名或密码不对")

    security.clear_failures(ip)
    found.last_login = timeutil.now()
    await session.commit()

    response = redirect("/")
    set_session(response, name)
    return response


@router.get(ADMIN_BASE + "/logout")
async def logout():
    response = redirect("/login")
    clear_session(response)
    return response


# ---------------------------------------------------------------- 概览


@router.get(ADMIN_BASE)
@router.get(ADMIN_BASE + "/")
async def dashboard(request: Request, session: AsyncSession = Depends(db_session)):
    if (response := guard(request)) is not None:
        return response

    now = timeutil.now()
    today = now.replace(hour=0, minute=0, second=0, microsecond=0)
    week_ago = timeutil.add_days(now, -7)

    total = await session.scalar(select(func.count()).select_from(LicenseUser))
    alive = await session.scalar(
        select(func.count())
        .select_from(LicenseUser)
        .where(LicenseUser.exptime > now, LicenseUser.status == "active")
    )
    disabled = await session.scalar(
        select(func.count())
        .select_from(LicenseUser)
        .where(LicenseUser.status != "active")
    )
    new_today = await session.scalar(
        select(func.count())
        .select_from(LicenseUser)
        .where(LicenseUser.addtime >= today)
    )
    active_week = await session.scalar(
        select(func.count())
        .select_from(LicenseUser)
        .where(LicenseUser.last_time >= week_ago)
    )
    orders_today = await session.scalar(
        select(func.count())
        .select_from(LicenseOrder)
        .where(LicenseOrder.usetime >= today)
    )

    recent_orders = (
        (
            await session.execute(
                select(LicenseOrder)
                .order_by(LicenseOrder.usetime.desc())
                .limit(10)
            )
        )
        .scalars()
        .all()
    )

    expiring = (
        (
            await session.execute(
                select(LicenseUser)
                .where(LicenseUser.exptime > now)
                .order_by(LicenseUser.exptime.asc())
                .limit(10)
            )
        )
        .scalars()
        .all()
    )

    return render(
        request,
        "admin/dashboard.html",
        stats={
            "total": total or 0,
            "alive": alive or 0,
            "disabled": disabled or 0,
            "new_today": new_today or 0,
            "active_week": active_week or 0,
            "orders_today": orders_today or 0,
        },
        recent_orders=recent_orders,
        expiring=expiring,
        now=now,
        account_state=account_state,
    )


# ---------------------------------------------------------------- 账户


def _account_filters(q: str, state: str, now):
    conditions = []
    keyword = (q or "").strip()
    if keyword:
        like = "%" + keyword + "%"
        conditions.append(
            or_(
                LicenseUser.typekey.like(like),
                LicenseUser.user.like(like),
                LicenseUser.email.like(like),
            )
        )
    if state == "active":
        conditions.extend([LicenseUser.status == "active", LicenseUser.exptime > now])
    elif state == "expired":
        conditions.extend([LicenseUser.status == "active", LicenseUser.exptime <= now])
    elif state == "disabled":
        conditions.append(LicenseUser.status != "active")
    return conditions


@router.get(ADMIN_BASE + "/accounts")
async def accounts(
    request: Request,
    q: str = "",
    state: str = "",
    page: int = 1,
    session: AsyncSession = Depends(db_session),
):
    if (response := guard(request)) is not None:
        return response

    now = timeutil.now()
    conditions = _account_filters(q, state, now)

    count_query = select(func.count()).select_from(LicenseUser)
    list_query = select(LicenseUser).order_by(LicenseUser.addtime.desc())
    for condition in conditions:
        count_query = count_query.where(condition)
        list_query = list_query.where(condition)

    total = await session.scalar(count_query) or 0
    page = max(1, page)
    rows = (
        (
            await session.execute(
                list_query.offset((page - 1) * PAGE_SIZE).limit(PAGE_SIZE)
            )
        )
        .scalars()
        .all()
    )

    pages = max(1, (int(total) + PAGE_SIZE - 1) // PAGE_SIZE)

    return render(
        request,
        "admin/accounts.html",
        rows=rows,
        total=int(total),
        page=page,
        pages=pages,
        q=q,
        state=state,
        now=now,
        account_state=account_state,
    )


@router.get(ADMIN_BASE + "/accounts/new")
async def account_new_page(request: Request):
    if (response := guard(request)) is not None:
        return response
    return render(request, "admin/account_new.html", days=1)


@router.post(ADMIN_BASE + "/accounts/new")
async def account_new_submit(
    request: Request,
    typekey: str = Form(""),
    user: str = Form(""),
    email: str = Form(""),
    days: int = Form(1),
    session: AsyncSession = Depends(db_session),
):
    if (response := guard(request)) is not None:
        return response

    key = (typekey or "").strip()
    if not key or len(key) > 64:
        return render(request, "admin/account_new.html", error="账户标识不能为空且不超过 64 个字符", days=days)

    if await session.get(LicenseUser, key) is not None:
        return render(request, "admin/account_new.html", error="这个账户标识已经存在了", days=days)

    now = timeutil.now()
    session.add(
        LicenseUser(
            typekey=key,
            addtime=now,
            exptime=timeutil.add_days(now, max(1, days)),
            status="active",
            user=(user or "").strip() or None,
            email=(email or "").strip() or None,
            count=0,
        )
    )
    await session.commit()
    return redirect("/accounts/" + _q(key), ok="账户已创建")


@router.get(ADMIN_BASE + "/accounts/{typekey}")
async def account_detail(
    request: Request, typekey: str, session: AsyncSession = Depends(db_session)
):
    if (response := guard(request)) is not None:
        return response

    row = await session.get(LicenseUser, typekey)
    if row is None:
        return redirect("/accounts", error="账户不存在：" + typekey)

    orders = (
        (
            await session.execute(
                select(LicenseOrder)
                .where(LicenseOrder.typekey == typekey)
                .order_by(LicenseOrder.usetime.desc())
                .limit(50)
            )
        )
        .scalars()
        .all()
    )

    return render(
        request,
        "admin/account_detail.html",
        row=row,
        orders=orders,
        now=timeutil.now(),
        state=account_state(row),
        exptime_text=timeutil.fmt(row.exptime) or "",
        addtime_text=timeutil.fmt(row.addtime) or "",
        last_time_text=timeutil.fmt(row.last_time) or "",
    )


@router.post(ADMIN_BASE + "/accounts/{typekey}")
async def account_save(
    request: Request,
    typekey: str,
    user: str = Form(""),
    email: str = Form(""),
    exptime: str = Form(""),
    count: str = Form(""),
    session: AsyncSession = Depends(db_session),
):
    if (response := guard(request)) is not None:
        return response

    row = await session.get(LicenseUser, typekey)
    if row is None:
        return redirect("/accounts", error="账户不存在：" + typekey)

    parsed = timeutil.parse(exptime)
    if parsed is None:
        return redirect("/accounts/" + _q(typekey), error="过期时间格式不对，应该像 2026-03-01 12:00:00")

    row.user = (user or "").strip() or None
    row.email = (email or "").strip() or None
    row.exptime = parsed
    try:
        row.count = int(count) if str(count).strip() != "" else row.count
    except ValueError:
        pass

    await session.commit()
    return redirect("/accounts/" + _q(typekey), ok="已保存")


@router.post(ADMIN_BASE + "/accounts/{typekey}/grant")
async def account_grant(
    request: Request,
    typekey: str,
    months: int = Form(0),
    days: int = Form(0),
    session: AsyncSession = Depends(db_session),
):
    if (response := guard(request)) is not None:
        return response

    row = await session.get(LicenseUser, typekey)
    if row is None:
        return redirect("/accounts", error="账户不存在：" + typekey)

    if months == 0 and days == 0:
        return redirect("/accounts/" + _q(typekey), error="加多少要填一个数")

    now = timeutil.now()
    # 未过期就从原到期日往后加，已过期就从现在起算
    base = timeutil.later_of(row.exptime, now)
    if months:
        base = timeutil.add_months(base, months)
    if days:
        base = timeutil.add_days(base, days)
    row.exptime = base
    row.status = "active"

    await session.commit()
    return redirect(
        "/accounts/" + _q(typekey),
        ok="已加时，新的到期时间 " + (timeutil.fmt(base) or ""),
    )


@router.post(ADMIN_BASE + "/accounts/{typekey}/status")
async def account_status(
    request: Request,
    typekey: str,
    status: str = Form("active"),
    session: AsyncSession = Depends(db_session),
):
    if (response := guard(request)) is not None:
        return response

    row = await session.get(LicenseUser, typekey)
    if row is None:
        return redirect("/accounts", error="账户不存在：" + typekey)

    row.status = "disabled" if status == "disabled" else "active"
    await session.commit()
    return redirect(
        "/accounts/" + _q(typekey),
        ok="已封禁" if row.status == "disabled" else "已解封",
    )


@router.post(ADMIN_BASE + "/accounts/{typekey}/delete")
async def account_delete(
    request: Request, typekey: str, session: AsyncSession = Depends(db_session)
):
    if (response := guard(request)) is not None:
        return response

    row = await session.get(LicenseUser, typekey)
    if row is None:
        return redirect("/accounts", error="账户不存在：" + typekey)

    await session.execute(delete(LicenseUser).where(LicenseUser.typekey == typekey))
    await session.commit()
    return redirect("/accounts", ok="账户已删除：" + typekey)


# ---------------------------------------------------------------- 订单


@router.get(ADMIN_BASE + "/orders")
async def orders(
    request: Request,
    q: str = "",
    page: int = 1,
    session: AsyncSession = Depends(db_session),
):
    if (response := guard(request)) is not None:
        return response

    count_query = select(func.count()).select_from(LicenseOrder)
    list_query = select(LicenseOrder).order_by(LicenseOrder.usetime.desc())

    keyword = (q or "").strip()
    if keyword:
        like = "%" + keyword + "%"
        condition = or_(
            LicenseOrder.ordernumber.like(like),
            LicenseOrder.typekey.like(like),
            LicenseOrder.name.like(like),
        )
        count_query = count_query.where(condition)
        list_query = list_query.where(condition)

    total = await session.scalar(count_query) or 0
    page = max(1, page)
    rows = (
        (
            await session.execute(
                list_query.offset((page - 1) * PAGE_SIZE).limit(PAGE_SIZE)
            )
        )
        .scalars()
        .all()
    )
    pages = max(1, (int(total) + PAGE_SIZE - 1) // PAGE_SIZE)

    return render(
        request,
        "admin/orders.html",
        rows=rows,
        total=int(total),
        page=page,
        pages=pages,
        q=q,
        usetime_text=lambda value: timeutil.fmt(value) or "",
        ordertime_text=lambda value: timeutil.fmt(value) or "",
    )


@router.post(ADMIN_BASE + "/orders/{ordernumber}/delete")
async def order_delete(
    request: Request, ordernumber: str, session: AsyncSession = Depends(db_session)
):
    if (response := guard(request)) is not None:
        return response

    row = await session.get(LicenseOrder, ordernumber)
    if row is None:
        return redirect("/orders", error="订单不存在：" + ordernumber)

    await session.execute(
        delete(LicenseOrder).where(LicenseOrder.ordernumber == ordernumber)
    )
    await session.commit()
    return redirect("/orders", ok="订单记录已删除：" + ordernumber)


# ---------------------------------------------------------------- 设置


@router.get(ADMIN_BASE + "/settings")
async def settings_page(request: Request, session: AsyncSession = Depends(db_session)):
    if (response := guard(request)) is not None:
        return response

    rows = (
        (await session.execute(select(Setting).order_by(Setting.skey.asc())))
        .scalars()
        .all()
    )
    values = {row.skey: (row.svalue or "") for row in rows}
    removed = [row for row in rows if row.skey not in SETTING_KEYS]

    return render(request, "admin/settings.html", values=values, removed=removed)


@router.post(ADMIN_BASE + "/settings")
async def settings_save(
    request: Request,
    site_domain: str = Form(""),
    trial_days: str = Form("1"),
    platform_base_url: str = Form(""),
    platform_email: str = Form(""),
    session: AsyncSession = Depends(db_session),
):
    if (response := guard(request)) is not None:
        return response

    try:
        trial_value = int(trial_days or "1")
    except ValueError:
        return redirect("/settings", error="试用天数必须是数字")
    if trial_value < 1:
        return redirect("/settings", error="试用天数至少为 1")

    domain = (site_domain or "").strip()
    for prefix in ("https://", "http://"):
        if domain.lower().startswith(prefix):
            domain = domain[len(prefix):]
    domain = domain.strip().strip("/")

    url = (platform_base_url or "").strip()
    if url and not url.startswith(("http://", "https://")):
        return redirect("/settings", error="平台地址要以 http:// 或 https:// 开头")

    values = {
        "site_domain": domain,
        "trial_days": str(trial_value),
        "platform_base_url": url,
        "platform_email": (platform_email or "").strip(),
    }
    await site_settings.set_many(session, values)
    await session.commit()
    return redirect("/settings", ok="设置已保存")


# ---------------------------------------------------------------- 管理员


@router.get(ADMIN_BASE + "/admins")
async def admins_page(request: Request, session: AsyncSession = Depends(db_session)):
    if (response := guard(request)) is not None:
        return response

    rows = (
        (await session.execute(select(AdminUser).order_by(AdminUser.id.asc())))
        .scalars()
        .all()
    )
    return render(
        request,
        "admin/admins.html",
        rows=rows,
        last_login=lambda value: timeutil.fmt(value) or "从未登录",
        created=lambda value: timeutil.fmt(value) or "",
    )


@router.post(ADMIN_BASE + "/admins/new")
async def admins_new(
    request: Request,
    username: str = Form(""),
    password: str = Form(""),
    password2: str = Form(""),
    session: AsyncSession = Depends(db_session),
):
    if (response := guard(request)) is not None:
        return response

    name = (username or "").strip()
    if len(name) < 3:
        return redirect("/admins", error="登录名至少 3 个字符")
    if len(password or "") < 8:
        return redirect("/admins", error="密码至少 8 个字符")
    if password != password2:
        return redirect("/admins", error="两次输入的密码不一致")

    exists = (
        await session.execute(select(AdminUser).where(AdminUser.username == name))
    ).scalar_one_or_none()
    if exists is not None:
        return redirect("/admins", error="这个登录名已经被占用了")

    session.add(
        AdminUser(
            username=name,
            password_hash=security.hash_password(password),
            created_at=timeutil.now(),
        )
    )
    await session.commit()
    return redirect("/admins", ok="管理员已添加：" + name)


@router.post(ADMIN_BASE + "/admins/password")
async def admins_password(
    request: Request,
    username: str = Form(""),
    password: str = Form(""),
    password2: str = Form(""),
    session: AsyncSession = Depends(db_session),
):
    if (response := guard(request)) is not None:
        return response

    if len(password or "") < 8:
        return redirect("/admins", error="密码至少 8 个字符")
    if password != password2:
        return redirect("/admins", error="两次输入的密码不一致")

    row = (
        await session.execute(select(AdminUser).where(AdminUser.username == username))
    ).scalar_one_or_none()
    if row is None:
        return redirect("/admins", error="没有这个管理员：" + username)

    row.password_hash = security.hash_password(password)
    await session.commit()
    return redirect("/admins", ok="密码已修改：" + username)


@router.post(ADMIN_BASE + "/admins/delete")
async def admins_delete(
    request: Request, username: str = Form(""), session: AsyncSession = Depends(db_session)
):
    if (response := guard(request)) is not None:
        return response

    me = current_admin(request)
    if me and username == me:
        return redirect("/admins", error="不能删掉自己正在用的账号")

    exists = (
        await session.execute(select(AdminUser).where(AdminUser.username == username))
    ).scalar_one_or_none()
    if exists is None:
        return redirect("/admins", error="没有这个管理员：" + username)

    await session.execute(delete(AdminUser).where(AdminUser.username == username))
    await session.commit()
    return redirect("/admins", ok="管理员已删除：" + username)


# ---------------------------------------------------------------- 导入旧数据


def _pick(row: dict, *names: str) -> str:
    """按列名取值，容忍大小写和空白的差异，列不存在就返回空串。"""
    wanted = {name.lower() for name in names}
    for key, value in row.items():
        if key and key.strip().lower() in wanted:
            if value is None:
                return ""
            return str(value).strip()
    return ""


def _parse_time(value: str) -> Optional[datetime]:
    if not value:
        return None
    raw = value.strip().replace("T", " ")
    if "." in raw:
        raw = raw.split(".")[0]
    if "+" in raw:
        raw = raw.split("+")[0].strip()
    return timeutil.parse(raw)


async def _bulk_import(session: AsyncSession, model: Any, rows: list) -> tuple:
    """返回 (提交行数, 实际新增行数)。已经存在的记录直接跳过，不覆盖。"""
    from sqlalchemy.dialects.mysql import insert as mysql_insert

    before = int(await session.scalar(select(func.count()).select_from(model)) or 0)
    statement = mysql_insert(model.__table__).prefix_with("IGNORE")
    for start in range(0, len(rows), 500):
        await session.execute(statement, rows[start : start + 500])
    await session.commit()
    after = int(await session.scalar(select(func.count()).select_from(model)) or 0)
    return len(rows), after - before


async def _read_rows(upload: UploadFile) -> list:
    """读上传的文件。CSV 和 JSON 都认——supabase 的 Table Editor 两种都能导。"""
    raw = await upload.read()
    text = None
    for encoding in ("utf-8-sig", "utf-8", "gbk"):
        try:
            text = raw.decode(encoding)
            break
        except UnicodeDecodeError:
            continue
    if text is None:
        raise ValueError("文件编码不认识，请另存为 UTF-8")

    name = (upload.filename or "").lower()
    stripped = text.lstrip()
    looks_like_json = name.endswith(".json") or stripped.startswith(("[", "{"))

    if looks_like_json:
        try:
            data = json.loads(text)
        except ValueError:
            raise ValueError("JSON 解析失败，检查一下文件有没有被截断")

        if isinstance(data, dict):
            for key in ("data", "rows", "records", "result"):
                if isinstance(data.get(key), list):
                    data = data[key]
                    break
            else:
                data = [data]

        if not isinstance(data, list):
            raise ValueError("JSON 结构不认识，期望是一个对象数组")

        return [row for row in data if isinstance(row, dict)]

    return [dict(row) for row in csv.DictReader(io.StringIO(text))]


@router.get(ADMIN_BASE + "/import")
async def import_page(request: Request):
    if (response := guard(request)) is not None:
        return response
    return render(request, "admin/import.html")


@router.post(ADMIN_BASE + "/import/accounts")
async def import_accounts(
    request: Request,
    file: UploadFile = File(...),
    shift_utc: str = Form(""),
    session: AsyncSession = Depends(db_session),
):
    if (response := guard(request)) is not None:
        return response

    try:
        raw_rows = await _read_rows(file)
    except ValueError as exc:
        return redirect("/import", error=str(exc))

    if not raw_rows:
        return redirect("/import", error="文件里没有数据")

    offset = timedelta(hours=8) if shift_utc else timedelta(0)
    rows = []
    skipped = 0

    for raw in raw_rows:
        typekey = _pick(raw, "typekey")
        if not typekey or len(typekey) > 64:
            skipped += 1
            continue

        addtime = _parse_time(_pick(raw, "addtime"))
        exptime = _parse_time(_pick(raw, "exptime"))
        if exptime is None:
            skipped += 1
            continue

        if offset:
            if addtime is not None:
                addtime = addtime + offset
            exptime = exptime + offset

        count_raw = _pick(raw, "count")
        try:
            count_value = int(count_raw) if count_raw else 0
        except ValueError:
            count_value = 0

        rows.append(
            {
                "typekey": typekey,
                "addtime": addtime or exptime,
                "exptime": exptime,
                "status": "active",
                "last_time": _parse_time(_pick(raw, "last_time")),
                "user": _pick(raw, "user") or None,
                "email": _pick(raw, "email") or None,
                "count": count_value,
            }
        )

    if not rows:
        return redirect("/import", error="没有一行能识别，检查一下列名对不对")

    submitted, inserted = await _bulk_import(session, LicenseUser, rows)
    message = "账户：读到 {} 行，新增 {} 条，已存在跳过 {} 条".format(
        submitted, inserted, submitted - inserted
    )
    if skipped:
        message += "，另有 {} 行格式不对被忽略".format(skipped)
    return redirect("/import", ok=message)


@router.post(ADMIN_BASE + "/import/orders")
async def import_orders(
    request: Request,
    file: UploadFile = File(...),
    session: AsyncSession = Depends(db_session),
):
    if (response := guard(request)) is not None:
        return response

    try:
        raw_rows = await _read_rows(file)
    except ValueError as exc:
        return redirect("/import", error=str(exc))

    if not raw_rows:
        return redirect("/import", error="文件里没有数据")

    rows = []
    skipped = 0

    for raw in raw_rows:
        ordernumber = _pick(raw, "ordernumber")
        typekey = _pick(raw, "typekey")
        if not ordernumber or len(ordernumber) > 64 or not typekey:
            skipped += 1
            continue

        rows.append(
            {
                "ordernumber": ordernumber,
                "typekey": typekey,
                "ordertime": _parse_time(_pick(raw, "ordertime")),
                "usetime": _parse_time(_pick(raw, "usetime")),
                "money": _pick(raw, "money") or None,
                "name": _pick(raw, "name") or None,
                "sku": _pick(raw, "sku") or None,
            }
        )

    if not rows:
        return redirect("/import", error="没有一行能识别，检查一下列名对不对")

    submitted, inserted = await _bulk_import(session, LicenseOrder, rows)
    message = "记录：读到 {} 行，新增 {} 条，已存在跳过 {} 条".format(
        submitted, inserted, submitted - inserted
    )
    if skipped:
        message += "，另有 {} 行格式不对被忽略".format(skipped)
    return redirect("/import", ok=message)
