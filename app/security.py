"""密码哈希与登录失败限流。

密码用 bcrypt 直接调用，不绕 passlib——passlib 1.7.4 配 bcrypt 4.x 会往
日志里刷兼容错误，虽然能跑，但很脏。

限流做在应用进程的内存里，不依赖 Nginx 配置。单进程部署下够用；
将来要多进程的话把这里换成共享存储即可。
"""

from __future__ import annotations

import time

import bcrypt

# ---------------------------------------------------------------- 密码

BCRYPT_MAX_BYTES = 72


def hash_password(raw: str) -> str:
    data = raw.encode("utf-8")[:BCRYPT_MAX_BYTES]
    return bcrypt.hashpw(data, bcrypt.gensalt()).decode("utf-8")


def verify_password(raw: str, hashed: str) -> bool:
    if not raw or not hashed:
        return False
    try:
        data = raw.encode("utf-8")[:BCRYPT_MAX_BYTES]
        return bcrypt.checkpw(data, hashed.encode("utf-8"))
    except (ValueError, TypeError):
        return False


# ---------------------------------------------------------------- 登录限流

WINDOW_SECONDS = 900.0
MAX_FAILURES = 5

_failures: dict[str, list[float]] = {}


def _prune(bucket: list[float], now_ts: float) -> list[float]:
    return [t for t in bucket if now_ts - t < WINDOW_SECONDS]


def is_locked(ip: str) -> bool:
    now_ts = time.monotonic()
    bucket = _failures.get(ip)
    if not bucket:
        return False
    bucket = _prune(bucket, now_ts)
    if bucket:
        _failures[ip] = bucket
        return len(bucket) >= MAX_FAILURES
    _failures.pop(ip, None)
    return False


def remaining_lock_seconds(ip: str) -> int:
    bucket = _failures.get(ip)
    if not bucket:
        return 0
    now_ts = time.monotonic()
    oldest = min(bucket)
    left = WINDOW_SECONDS - (now_ts - oldest)
    return int(left) if left > 0 else 0


def record_failure(ip: str) -> None:
    now_ts = time.monotonic()
    bucket = _prune(_failures.get(ip, []), now_ts)
    bucket.append(now_ts)
    _failures[ip] = bucket


def clear_failures(ip: str) -> None:
    _failures.pop(ip, None)
