"""数据库引擎与会话。

时间口径：所有连接建立后统一执行 SET time_zone = '+08:00'，
这样 CURRENT_TIMESTAMP 之类的库端默认值落下来就是北京时间，
且不依赖服务器/容器本身的时区设置。
"""

from __future__ import annotations

from typing import Optional

from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

BEIJING_TZ = "+08:00"

engine: Optional[AsyncEngine] = None
SessionLocal: Optional[async_sessionmaker[AsyncSession]] = None


def _build_engine(url: str) -> AsyncEngine:
    eng = create_async_engine(
        url,
        pool_pre_ping=True,
        pool_recycle=3600,
        echo=False,
    )

    @event.listens_for(eng.sync_engine, "connect")
    def _set_session_timezone(dbapi_conn, _connection_record):  # pragma: no cover
        cursor = dbapi_conn.cursor()
        try:
            cursor.execute("SET time_zone = '%s'" % BEIJING_TZ)
        finally:
            cursor.close()

    return eng


def init(url: str) -> AsyncEngine:
    global engine, SessionLocal
    engine = _build_engine(url)
    SessionLocal = async_sessionmaker(engine, expire_on_commit=False)
    return engine


async def dispose() -> None:
    global engine, SessionLocal
    if engine is not None:
        await engine.dispose()
    engine = None
    SessionLocal = None


# 表结构变更。新装的库由 schema.sql 直接建好，已经跑着的库靠这里跟上，
# 免得每次改一个字段都要人工去动表。
SCHEMA_ADDITIONS: list[tuple[str, str, str]] = [
    (
        "license_user",
        "status",
        "ALTER TABLE `license_user` ADD COLUMN `status` VARCHAR(16) "
        "DEFAULT 'active' COMMENT '账户状态：active / disabled' AFTER `exptime`",
    ),
]

# 删列是不可逆的，执行前想清楚
SCHEMA_REMOVALS: list[tuple[str, str, str]] = [
    (
        "license_order",
        "count",
        "ALTER TABLE `license_order` DROP COLUMN `count`",
    ),
]


async def ensure_schema() -> list[str]:
    """把表结构对齐到最新，返回实际执行过的语句。"""
    applied: list[str] = []
    if SessionLocal is None:
        return applied

    async with SessionLocal() as session:
        for table, column, ddl in SCHEMA_ADDITIONS:
            exists = (
                await session.execute(
                    text("SHOW COLUMNS FROM `%s` LIKE '%s'" % (table, column))
                )
            ).first()
            if exists is None:
                await session.execute(text(ddl))
                await session.commit()
                applied.append(ddl)

        for table, column, ddl in SCHEMA_REMOVALS:
            exists = (
                await session.execute(
                    text("SHOW COLUMNS FROM `%s` LIKE '%s'" % (table, column))
                )
            ).first()
            if exists is not None:
                await session.execute(text(ddl))
                await session.commit()
                applied.append(ddl)

    return applied


def is_ready() -> bool:
    return SessionLocal is not None


def session_factory() -> async_sessionmaker[AsyncSession]:
    if SessionLocal is None:
        raise RuntimeError("数据库尚未初始化")
    return SessionLocal
