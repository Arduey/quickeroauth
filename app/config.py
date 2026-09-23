"""config.json 的读写。

这个文件只存「进程启动就必须知道」的东西：
会话密钥、数据库连接、后台路径。其余业务配置都在数据库的 setting 表里
（见 settings.py），因为那些要能在管理后台随时改。
"""

from __future__ import annotations

import json
import os
import secrets
from pathlib import Path
from typing import Optional
from urllib.parse import quote_plus

BASE_DIR = Path(__file__).resolve().parent.parent
CONFIG_PATH = BASE_DIR / "config.json"

_config: Optional[dict] = None


def is_installed() -> bool:
    return CONFIG_PATH.exists()


def load() -> Optional[dict]:
    """读入 config.json。文件不存在时返回 None（即未安装）。"""
    global _config
    if not CONFIG_PATH.exists():
        _config = None
        return None
    try:
        _config = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        _config = None
    return _config


def get() -> Optional[dict]:
    return _config


def save(data: dict) -> None:
    CONFIG_PATH.write_text(
        json.dumps(data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    try:
        os.chmod(CONFIG_PATH, 0o600)
    except OSError:
        pass
    global _config
    _config = data


def new_secret_key() -> str:
    return secrets.token_urlsafe(48)


def database_url(cfg: dict) -> str:
    db = cfg.get("database") or {}
    user = quote_plus(str(db.get("user") or ""))
    password = quote_plus(str(db.get("password") or ""))
    host = db.get("host") or "127.0.0.1"
    port = int(db.get("port") or 3306)
    name = db.get("database") or "license"
    return (
        f"mysql+aiomysql://{user}:{password}@{host}:{port}/{name}"
        f"?charset=utf8mb4"
    )


def admin_path() -> str:
    path = str(((_config or {}).get("app") or {}).get("admin_path") or "/admin")
    if not path.startswith("/"):
        path = "/" + path
    return path.rstrip("/") or "/admin"


def session_hours() -> int:
    try:
        return int(((_config or {}).get("app") or {}).get("session_hours") or 8)
    except (TypeError, ValueError):
        return 8
