"""把中文词条注入 sqladmin。

sqladmin 支持的界面语言是硬编码在它自己源码里的（en / de / az / ru / tr / ja），
没有中文，也没有留出注册新语言的公开接口。所以这里在启动时做两件事：

1. 把项目里的 admin.po 编译成 gettext 目录（Babel 在内存里完成，不落文件）
2. 把 "zh" 塞进 sqladmin 的语言表

必须在构造 Admin 之前调用，因为语言中间件是在那时候装的。
"""

from __future__ import annotations

import io
from pathlib import Path

LOCALE_DIR = Path(__file__).resolve().parent / "locale"
LOCALE_CODE = "zh"
DOMAIN = "admin"


_installed: bool | None = None


def install() -> bool:
    """注入中文。返回是否成功；不满足条件就安静地退回英文界面，不报错。

    可以重复调用，第一次的结果会被记住。
    """
    global _installed
    if _installed is None:
        _installed = _do_install()
    return _installed


def _do_install() -> bool:
    try:
        import sqladmin.i18n as sq_i18n
        from babel.messages.mofile import write_mo
        from babel.messages.pofile import read_po
        from babel.support import Translations
    except ImportError:
        return False

    po_path = LOCALE_DIR / LOCALE_CODE / "LC_MESSAGES" / (DOMAIN + ".po")
    if not po_path.exists():
        return False

    try:
        with po_path.open(encoding="utf-8") as handle:
            catalog = read_po(handle)

        buffer = io.BytesIO()
        write_mo(buffer, catalog)
        buffer.seek(0)
        compiled = Translations(buffer)
    except Exception:  # noqa: BLE001
        return False

    if LOCALE_CODE not in sq_i18n.SUPPORTED_LOCALES:
        sq_i18n.SUPPORTED_LOCALES.append(LOCALE_CODE)

    catalog_map = getattr(sq_i18n, "translations", None)
    if isinstance(catalog_map, dict):
        catalog_map[LOCALE_CODE] = compiled

    return True
