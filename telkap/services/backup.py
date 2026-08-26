"""پشتیبان‌گیری خودکار از دیتابیس، روی سرور و بیرون از آن.

فایل دیتابیس شامل سشن رمزنگاری‌شده‌ی کاربران و همه‌ی تنظیمات است؛
از دست رفتنش یعنی همه باید دوباره وارد شوند.

نسخه‌ی محلی به‌تنهایی محافظت واقعی نیست: اگر خودِ سرور از دست برود،
پشتیبان هم با آن می‌رود. برای همین هر نسخه فشرده و به یک کانال خصوصی
تلگرام فرستاده می‌شود — بدون هزینه، بدون سرویس اضافه، و از دسترس هر
اتفاقی که برای VPS بیفتد بیرون.
"""
from __future__ import annotations

import asyncio
import gzip
import logging
import shutil
from pathlib import Path

from telkap.config import BASE_DIR, get_settings
from telkap.models import utcnow

log = logging.getLogger(__name__)

KEEP_BACKUPS = 14
SQLITE_PREFIX = "sqlite+aiosqlite:///"

# سقف آپلود سند در Bot API؛ بالاتر از این تلگرام رد می‌کند
MAX_UPLOAD_BYTES = 50 * 1024 * 1024


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


def compress(source: Path) -> Path | None:
    """نسخه را فشرده می‌کند. SQLite معمولاً چند برابر کوچک می‌شود."""
    target = source.with_suffix(source.suffix + ".gz")
    try:
        with open(source, "rb") as raw, gzip.open(target, "wb", compresslevel=6) as packed:
            shutil.copyfileobj(raw, packed)
    except Exception:
        log.exception("فشرده‌سازی نسخه‌ی پشتیبان ناموفق بود")
        return None
    return target


async def send_offsite(bot, path: Path, *, chat_id: str | int | None = None) -> bool:
    """نسخه را به کانال پشتیبان می‌فرستد تا بیرون از سرور هم باشد."""
    target = chat_id if chat_id is not None else get_settings().backup_chat_id
    if not target:
        return False

    size = path.stat().st_size if path.exists() else 0
    if size == 0:
        return False
    if size > MAX_UPLOAD_BYTES:
        log.error(
            "نسخه‌ی پشتیبان %s بایت است و از سقف آپلود تلگرام بزرگ‌تر؛ ارسال نشد",
            size,
        )
        return False

    from aiogram.types import FSInputFile

    try:
        destination = int(target) if str(target).lstrip("-").isdigit() else str(target)
        await bot.send_document(
            destination,
            FSInputFile(path),
            caption=(
                f"💾 نسخه‌ی پشتیبان\n"
                f"{utcnow():%Y-%m-%d %H:%M} UTC · {size // 1024} کیلوبایت"
            ),
            disable_notification=True,
        )
    except Exception:
        log.exception("ارسال نسخه‌ی پشتیبان به کانال ناموفق بود")
        return False
    log.info("نسخه‌ی پشتیبان به کانال ارسال شد (%s کیلوبایت)", size // 1024)
    return True


async def run_once(bot=None) -> tuple[Path | None, bool]:
    """یک چرخه‌ی کامل: ساخت نسخه، فشرده‌سازی و ارسال بیرون از سرور.

    خروجی: (مسیر نسخه‌ی محلی، آیا بیرون از سرور هم ذخیره شد).
    """
    local = await asyncio.to_thread(make_backup)
    if local is None:
        return None, False
    if bot is None or not get_settings().backup_chat_id:
        return local, False

    packed = await asyncio.to_thread(compress, local)
    sent = await send_offsite(bot, packed or local)
    if packed is not None:
        packed.unlink(missing_ok=True)      # فشرده فقط برای ارسال لازم بود
    return local, sent


async def run_forever(bot=None) -> None:
    interval = max(1, get_settings().backup_hours) * 3600
    while True:
        try:
            await asyncio.sleep(interval)
            await run_once(bot)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("چرخه‌ی پشتیبان‌گیری با خطا مواجه شد")
