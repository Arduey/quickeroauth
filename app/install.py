"""安装向导。

未安装时唯一可访问的页面，全程浏览器操作，不碰命令行。
安装状态存在进程内存里——安装是单个人的一次性操作，不需要持久化，
也不需要处理并发。
"""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path
from typing import Any, Optional

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import insert, select, text, update
from sqlalchemy.ext.asyncio import create_async_engine

from . import config, security, timeutil
from .models import AdminUser, Setting
from .settings import DEFAULTS as SETTING_DEFAULTS
from .settings import REMARKS as SETTING_REMARKS

router = APIRouter()

TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

SCHEMA_PATH = config.BASE_DIR / "sql" / "schema.sql"
REQUIRED_MODULES = (
    "fastapi",
    "starlette",
    "sqlalchemy",
    "aiomysql",
    "sqladmin",
    "bcrypt",
    "jinja2",
    "httpx",
)

STATE: dict[str, Any] = {
    "db": {
        "host": "127.0.0.1",
        "port": "3306",
        "user": "",
        "password": "",
        "database": "license",
    },
    "autocreate": True,
    "admin_username": "",
    "admin_password_hash": "",
    "site": {
        "site_domain": "",
        "trial_days": "1",
        "platform_base_url": SETTING_DEFAULTS["platform_base_url"],
        "platform_email": "",
        "admin_path": "/admin",
        "session_hours": "8",
    },
    "error": "",
}


# ---------------------------------------------------------------- 工具


def _reset_error() -> str:
    message = STATE.get("error") or ""
    STATE["error"] = ""
    return message


def env_checks() -> list[dict]:
    checks: list[dict] = []

    version = "{}.{}.{}".format(*sys.version_info[:3])
    checks.append(
        {
            "name": "Python 版本",
            "ok": sys.version_info >= (3, 10),
            "detail": "{}（需要 3.10 或更高）".format(version),
        }
    )

    writable = os.access(str(config.BASE_DIR), os.W_OK)
    checks.append(
        {
            "name": "项目目录可写",
            "ok": writable,
            "detail": str(config.BASE_DIR),
        }
    )

    for module in REQUIRED_MODULES:
        try:
            __import__(module)
            checks.append({"name": "依赖 " + module, "ok": True, "detail": "已安装"})
        except Exception as exc:  # noqa: BLE001
            checks.append(
                {"name": "依赖 " + module, "ok": False, "detail": str(exc)}
            )

    return checks


def friendly_db_error(exc: Exception) -> str:
    raw = str(exc)
    low = raw.lower()
    if "access denied" in low:
        return "数据库用户名或密码不对"
    if "unknown database" in low:
        return "数据库不存在。可以勾选「库不存在时自动创建」再试一次"
    if (
        "can't connect" in low
        or "connection refused" in low
        or "2003" in raw
        or "cannot connect" in low
    ):
        return "连不上数据库。检查主机和端口，以及 MySQL 是否在运行"
    if "timeout" in low or "timed out" in low:
        return "连接数据库超时，检查主机和端口"
    return raw[:300]


def split_sql(sql: str) -> list[str]:
    """把 schema.sql 拆成一条条语句。跳过注释行和 SET NAMES。"""
    kept: list[str] = []
    for line in sql.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("--"):
            continue
        if stripped.upper().startswith("SET NAMES"):
            continue
        kept.append(line)
    body = "\n".join(kept)
    return [chunk.strip() for chunk in body.split(";") if chunk.strip()]


def db_config_from_state() -> dict:
    return dict(STATE["db"])


def _server_level_url(db_conf: dict) -> str:
    """连到 MySQL 服务器本身（不指定库），用来建库。"""
    loose = dict(db_conf)
    loose["database"] = ""
    return config.database_url({"database": loose})


async def ensure_database(db_conf: dict) -> tuple[bool, str]:
    name = str(db_conf.get("database") or "")
    if not re.fullmatch(r"[A-Za-z0-9_]{1,64}", name):
        return False, "数据库名只能包含字母、数字和下划线"

    engine = create_async_engine(_server_level_url(db_conf))
    try:
        async with engine.begin() as conn:
            await conn.execute(
                text(
                    "CREATE DATABASE IF NOT EXISTS `{}` "
                    "CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci".format(name)
                )
            )
    except Exception as exc:  # noqa: BLE001
        return False, friendly_db_error(exc)
    finally:
        await engine.dispose()
    return True, ""


async def test_connection(db_conf: dict) -> tuple[bool, str]:
    engine = create_async_engine(config.database_url({"database": db_conf}))
    try:
        async with engine.connect() as conn:
            result = await conn.execute(text("SELECT VERSION()"))
            version = result.scalar()
        return True, "MySQL {}".format(version)
    except Exception as exc:  # noqa: BLE001
        return False, friendly_db_error(exc)
    finally:
        await engine.dispose()


async def run_schema(db_conf: dict) -> tuple[bool, str]:
    if not SCHEMA_PATH.exists():
        return False, "找不到建表脚本 sql/schema.sql"

    statements = split_sql(SCHEMA_PATH.read_text(encoding="utf-8"))
    engine = create_async_engine(config.database_url({"database": db_conf}))
    try:
        async with engine.begin() as conn:
            for statement in statements:
                await conn.execute(text(statement))
    except Exception as exc:  # noqa: BLE001
        return False, friendly_db_error(exc)
    finally:
        await engine.dispose()
    return True, ""


async def create_admin_user(db_conf: dict, username: str, password_hash: str) -> tuple[bool, str]:
    engine = create_async_engine(config.database_url({"database": db_conf}))
    try:
        async with engine.begin() as conn:
            exists = (
                await conn.execute(
                    select(AdminUser.id).where(AdminUser.username == username)
                )
            ).scalar_one_or_none()
            if exists is not None:
                await conn.execute(
                    update(AdminUser)
                    .where(AdminUser.username == username)
                    .values(password_hash=password_hash)
                )
            else:
                await conn.execute(
                    insert(AdminUser).values(
                        username=username,
                        password_hash=password_hash,
                        created_at=timeutil.now(),
                    )
                )
    except Exception as exc:  # noqa: BLE001
        return False, friendly_db_error(exc)
    finally:
        await engine.dispose()
    return True, ""


async def write_settings(db_conf: dict, values: dict) -> tuple[bool, str]:
    engine = create_async_engine(config.database_url({"database": db_conf}))
    try:
        async with engine.begin() as conn:
            for key, value in values.items():
                exists = (
                    await conn.execute(
                        select(Setting.skey).where(Setting.skey == key)
                    )
                ).scalar_one_or_none()
                if exists is None:
                    await conn.execute(
                        insert(Setting).values(
                            skey=key,
                            svalue=value,
                            remark=SETTING_REMARKS.get(key),
                            updatetime=timeutil.now(),
                        )
                    )
                else:
                    await conn.execute(
                        update(Setting)
                        .where(Setting.skey == key)
                        .values(svalue=value, updatetime=timeutil.now())
                    )
    except Exception as exc:  # noqa: BLE001
        return False, friendly_db_error(exc)
    finally:
        await engine.dispose()
    return True, ""


# ---------------------------------------------------------------- 渲染


def page(request: Request, step: int, **extra) -> HTMLResponse:
    context: dict[str, Any] = {
        "step": step,
        "error": extra.pop("error", ""),
        "db": STATE["db"],
        "site": STATE["site"],
        "autocreate": STATE["autocreate"],
    }
    context.update(extra)
    return templates.TemplateResponse(
        request=request, name="install.html", context=context
    )


def installed_guard() -> Optional[RedirectResponse]:
    if config.is_installed():
        return RedirectResponse(url="/install/done", status_code=303)
    return None


# ---------------------------------------------------------------- 路由


@router.get("/install")
async def install_home(request: Request):
    if config.is_installed():
        return page(request, 6, already=True, admin_path=config.admin_path())

    checks = env_checks()
    return page(
        request,
        1,
        checks=checks,
        all_ok=all(item["ok"] for item in checks),
    )


@router.get("/install/db")
async def install_db_form(request: Request):
    guard = installed_guard()
    if guard:
        return guard
    return page(request, 2, error=_reset_error())


@router.post("/install/db")
async def install_db_submit(
    request: Request,
    host: str = Form("127.0.0.1"),
    port: str = Form("3306"),
    user: str = Form(""),
    password: str = Form(""),
    database: str = Form("license"),
    autocreate: str = Form(""),
):
    guard = installed_guard()
    if guard:
        return guard

    STATE["db"] = {
        "host": (host or "127.0.0.1").strip(),
        "port": (port or "3306").strip(),
        "user": (user or "").strip(),
        "password": password or "",
        "database": (database or "license").strip(),
    }
    STATE["autocreate"] = bool(autocreate)

    if not STATE["db"]["user"]:
        return page(request, 2, error="数据库用户名不能不填")

    db_conf = db_config_from_state()

    if STATE["autocreate"]:
        created, message = await ensure_database(db_conf)
        if not created:
            return page(request, 2, error=message)

    connected, message = await test_connection(db_conf)
    if not connected:
        return page(request, 2, error=message)

    STATE["db_version"] = message
    return RedirectResponse(url="/install/schema", status_code=303)


@router.get("/install/schema")
async def install_schema_form(request: Request):
    guard = installed_guard()
    if guard:
        return guard
    if not STATE["db"]["user"]:
        return RedirectResponse(url="/install/db", status_code=303)

    sql_text = (
        SCHEMA_PATH.read_text(encoding="utf-8") if SCHEMA_PATH.exists() else ""
    )
    return page(
        request,
        3,
        error=_reset_error(),
        schema_sql=sql_text,
        db_version=STATE.get("db_version", ""),
    )


@router.post("/install/schema")
async def install_schema_submit(request: Request):
    guard = installed_guard()
    if guard:
        return guard

    ok, message = await run_schema(db_config_from_state())
    if not ok:
        sql_text = (
            SCHEMA_PATH.read_text(encoding="utf-8") if SCHEMA_PATH.exists() else ""
        )
        return page(request, 3, error=message, schema_sql=sql_text)

    return RedirectResponse(url="/install/admin", status_code=303)


@router.get("/install/admin")
async def install_admin_form(request: Request):
    guard = installed_guard()
    if guard:
        return guard
    if not STATE["db"]["user"]:
        return RedirectResponse(url="/install/db", status_code=303)
    return page(request, 4, error=_reset_error())


@router.post("/install/admin")
async def install_admin_submit(
    request: Request,
    username: str = Form(""),
    password: str = Form(""),
    password2: str = Form(""),
):
    guard = installed_guard()
    if guard:
        return guard

    username = (username or "").strip()
    if len(username) < 3:
        return page(request, 4, error="登录名至少 3 个字符")
    if len(password or "") < 8:
        return page(request, 4, error="密码至少 8 个字符")
    if password != password2:
        return page(request, 4, error="两次输入的密码不一致")

    STATE["admin_username"] = username
    STATE["admin_password_hash"] = security.hash_password(password)
    return RedirectResponse(url="/install/site", status_code=303)


@router.get("/install/site")
async def install_site_form(request: Request):
    guard = installed_guard()
    if guard:
        return guard
    if not STATE["admin_username"]:
        return RedirectResponse(url="/install/admin", status_code=303)
    return page(request, 5, error=_reset_error())


@router.post("/install/site")
async def install_site_submit(
    request: Request,
    site_domain: str = Form(""),
    trial_days: str = Form("1"),
    platform_base_url: str = Form(SETTING_DEFAULTS["platform_base_url"]),
    platform_email: str = Form(""),
    admin_path: str = Form("/admin"),
    session_hours: str = Form("8"),
):
    guard = installed_guard()
    if guard:
        return guard

    try:
        trial_days_value = int(trial_days or "1")
    except ValueError:
        return page(request, 5, error="试用天数必须是数字")
    if trial_days_value < 1:
        return page(request, 5, error="试用天数至少为 1")

    try:
        session_hours_value = int(session_hours or "8")
    except ValueError:
        return page(request, 5, error="会话有效期必须是数字")
    if session_hours_value < 1:
        return page(request, 5, error="会话有效期至少为 1 小时")

    normalized_admin_path = (admin_path or "/admin").strip()
    if not normalized_admin_path.startswith("/"):
        normalized_admin_path = "/" + normalized_admin_path
    if len(normalized_admin_path) < 2:
        return page(request, 5, error="后台路径不能是单个斜杠")

    platform_url = (platform_base_url or "").strip()
    if platform_url and not platform_url.startswith(("http://", "https://")):
        return page(request, 5, error="平台地址要以 http:// 或 https:// 开头")

    STATE["site"] = {
        "site_domain": (site_domain or "").strip(),
        "trial_days": str(trial_days_value),
        "platform_base_url": platform_url,
        "platform_email": (platform_email or "").strip(),
        "admin_path": normalized_admin_path,
        "session_hours": str(session_hours_value),
    }

    db_conf = db_config_from_state()

    ok, message = await create_admin_user(
        db_conf, STATE["admin_username"], STATE["admin_password_hash"]
    )
    if not ok:
        return page(request, 5, error="创建管理员失败：" + message)

    setting_values = {
        key: STATE["site"][key]
        for key in (
            "site_domain",
            "trial_days",
            "platform_base_url",
            "platform_email",
        )
    }
    ok, message = await write_settings(db_conf, setting_values)
    if not ok:
        return page(request, 5, error="写入站点设置失败：" + message)

    config.save(
        {
            "installed_at": timeutil.fmt(timeutil.now()),
            "secret_key": config.new_secret_key(),
            "database": db_conf,
            "app": {
                "admin_path": STATE["site"]["admin_path"],
                "session_hours": session_hours_value,
            },
        }
    )

    return RedirectResponse(url="/install/done", status_code=303)


@router.get("/install/done")
async def install_done(request: Request):
    if not config.is_installed():
        return RedirectResponse(url="/install", status_code=303)

    domain = STATE["site"].get("site_domain") or "（未填写域名）"
    admin_path = config.admin_path()
    return page(
        request,
        6,
        done=True,
        admin_path=admin_path,
        site_domain=domain,
    )
