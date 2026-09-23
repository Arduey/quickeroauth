"""ORM 模型。表结构以 sql/schema.sql 为准，这里只做映射，不负责建表。"""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import BigInteger, DateTime, Integer, String, Text, text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class LicenseUser(Base):
    """授权账户表。"""

    __tablename__ = "license_user"

    typekey: Mapped[str] = mapped_column(String(64), primary_key=True)
    addtime: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=text("CURRENT_TIMESTAMP")
    )
    exptime: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    last_time: Mapped[Optional[datetime]] = mapped_column(DateTime)
    user: Mapped[Optional[str]] = mapped_column(String(64))
    email: Mapped[Optional[str]] = mapped_column(String(128))
    count: Mapped[Optional[int]] = mapped_column(
        BigInteger, server_default=text("0")
    )


class LicenseOrder(Base):
    """订单核销记录表。"""

    __tablename__ = "license_order"

    ordernumber: Mapped[str] = mapped_column(String(64), primary_key=True)
    typekey: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    ordertime: Mapped[Optional[datetime]] = mapped_column(DateTime)
    usetime: Mapped[Optional[datetime]] = mapped_column(
        DateTime, server_default=text("CURRENT_TIMESTAMP")
    )
    money: Mapped[Optional[str]] = mapped_column(String(32))
    name: Mapped[Optional[str]] = mapped_column(String(255))
    sku: Mapped[Optional[str]] = mapped_column(String(255))
    count: Mapped[Optional[int]] = mapped_column(Integer)


class AdminUser(Base):
    """管理后台账号表。"""

    __tablename__ = "admin_user"

    id: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, autoincrement=True
    )
    username: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=text("CURRENT_TIMESTAMP")
    )
    last_login: Mapped[Optional[datetime]] = mapped_column(DateTime)


class Setting(Base):
    """站点设置表，键值对。"""

    __tablename__ = "setting"

    skey: Mapped[str] = mapped_column(String(64), primary_key=True)
    svalue: Mapped[Optional[str]] = mapped_column(Text)
    remark: Mapped[Optional[str]] = mapped_column(String(255))
    updatetime: Mapped[Optional[datetime]] = mapped_column(
        DateTime, server_default=text("CURRENT_TIMESTAMP")
    )
