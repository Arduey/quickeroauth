"""数据库引擎与会话。

时间口径：所有连接建立后统一执行 SET time_zone = '+08:00'，
这样 CURRENT_TIMESTAMP 之类的库端默认值落下来就是北京时间，
且不依赖服务器/容器本身的时区设置。
"""

from __future__ import annotations

from typing import Optional

from sqlalchemy import event
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


def is_ready() -> bool:
    return SessionLocal is not None


def session_factory() -> async_sessionmaker[AsyncSession]:
    if SessionLocal is None:
        raise RuntimeError("数据库尚未初始化")
    return SessionLocal
