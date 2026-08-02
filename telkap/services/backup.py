"""پشتیبان‌گیری خودکار از دیتابیس.

فایل دیتابیس شامل سشن رمزنگاری‌شده‌ی کاربران و همه‌ی تنظیمات است؛
از دست رفتنش یعنی همه باید دوباره وارد شوند.
"""
from __future__ import annotations

import asyncio
import logging
import shutil
from pathlib import Path

from telkap.config import BASE_DIR, get_settings
from telkap.models import utcnow

log = logging.getLogger(__name__)

KEEP_BACKUPS = 14
SQLITE_PREFIX = "sqlite+aiosqlite:///"


def database_path() -> Path | None:
    """مسیر فایل دیتابیس، اگر SQLite باشد."""
    url = get_settings().database_url
    if not url.startswith(SQLITE_PREFIX):
        return None
    path = Path(url[len(SQLITE_PREFIX):])
    return path if path.is_absolute() else BASE_DIR / path


def backup_dir() -> Path:
    return BASE_DIR / "data" / "backups"


def make_backup() -> Path | None:
    """یک نسخه‌ی پشتیبان می‌سازد و مسیرش را برمی‌گرداند."""
    source = database_path()
    if source is None:
        log.info("دیتابیس SQLite نیست؛ پشتیبان‌گیری خودکار انجام نمی‌شود")
        return None
    if not source.exists():
        return None

    target_dir = backup_dir()
    target_dir.mkdir(parents=True, exist_ok=True)
    stamp = utcnow().strftime("%Y%m%d-%H%M%S")
    target = target_dir / f"telkap-{stamp}.db"

    try:
        # کپی ساده کافی است چون SQLite در حالت عادی فایل را یکپارچه نگه می‌دارد
        shutil.copy2(source, target)
    except Exception:
        log.exception("ساخت نسخه‌ی پشتیبان ناموفق بود")
        return None

    _prune(target_dir)
    log.info("نسخه‌ی پشتیبان ساخته شد: %s", target.name)
    return target


def _prune(target_dir: Path) -> None:
    backups = sorted(target_dir.glob("telkap-*.db"), key=lambda p: p.name, reverse=True)
    for stale in backups[KEEP_BACKUPS:]:
        stale.unlink(missing_ok=True)


async def run_forever() -> None:
    interval = max(1, get_settings().backup_hours) * 3600
    while True:
        try:
            await asyncio.sleep(interval)
            await asyncio.to_thread(make_backup)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("چرخه‌ی پشتیبان‌گیری با خطا مواجه شد")
