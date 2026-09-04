"""نگهداری: هرس دوره‌ای دیتابیس + حالت «در دست تعمیر».

جدول لاگ فعالیت‌ها به ازای هر پست کپی‌شده یک ردیف می‌گیرد. با ده‌ها کاربر
فعال این جدول سریع‌ترین بخش رشد دیتابیس است و اگر هرس نشود، هم فایل بزرگ
می‌شود و هم صفحه‌ی «گزارش فعالیت» کند.

حالت تعمیر هم اینجاست: وقتی می‌خواهید ربات را به‌روز کنید یا دیتابیس را
جابه‌جا کنید، به‌جای اینکه کاربر خطای عجیب بگیرد، یک پیام روشن می‌بیند.
ادمین‌ها همچنان کار می‌کنند تا بتوانند نتیجه را ببینند و حالت را بردارند.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import timedelta

from sqlalchemy import delete, func, select

from telkap.config import get_settings
from telkap.db import get_session
from telkap.models import ActivityLog, AppSetting, ReminderState, utcnow

log = logging.getLogger(__name__)

# فاصله‌ی اجرای نگهداری
INTERVAL_SECONDS = 6 * 3600

# ------------------------------------------------- حالت «در دست تعمیر»
MODE_KEY = "maintenance_mode"
DEFAULT_NOTE = "ربات برای به‌روزرسانی موقتاً در دسترس نیست. تا دقایقی دیگر برمی‌گردیم 🙏"

# در هر پیام خوانده می‌شود؛ پس یک بار از دیتابیس می‌آید و با تغییر، تازه می‌شود
_mode: tuple[bool, str] | None = None


def invalidate_mode() -> None:
    global _mode
    _mode = None


async def mode() -> tuple[bool, str]:
    """(روشن است؟، متن اعلام‌شده به کاربر)."""
    global _mode
    if _mode is None:
        async with get_session() as db:
            row = await db.get(AppSetting, MODE_KEY)
        data = row.value if row and isinstance(row.value, dict) else {}
        _mode = (bool(data.get("on")), str(data.get("note") or DEFAULT_NOTE))
    return _mode


async def set_mode(on: bool, *, note: str = "", by: int | None = None) -> tuple[bool, str]:
    async with get_session() as db:
        row = await db.get(AppSetting, MODE_KEY)
        if row is None:
            row = AppSetting(key=MODE_KEY)
            db.add(row)
        row.value = {"on": bool(on), "note": (note or DEFAULT_NOTE)[:400]}
        row.updated_by = by
        row.updated_at = utcnow()
        await db.commit()
    invalidate_mode()
    return await mode()


async def prune_activity_log(days: int | None = None) -> int:
    """لاگ‌های قدیمی‌تر از N روز را حذف می‌کند و تعدادشان را برمی‌گرداند."""
    days = days if days is not None else get_settings().log_retention_days
    if days <= 0:
        return 0  # ۰ یعنی نگه‌داشتن همه‌چیز
    cutoff = utcnow() - timedelta(days=days)
    async with get_session() as db:
        stale = await db.scalar(
            select(func.count(ActivityLog.id)).where(ActivityLog.created_at < cutoff)
        )
        if not stale:
            return 0
        await db.execute(delete(ActivityLog).where(ActivityLog.created_at < cutoff))
        await db.commit()
    log.info("هرس لاگ فعالیت: %s ردیف قدیمی‌تر از %s روز حذف شد", stale, days)
    return int(stale)


async def prune_reminder_state(days: int = 90) -> int:
    """نشانه‌های یادآوری اشتراک‌های خیلی قدیمی دیگر به کار نمی‌آیند."""
    cutoff = utcnow() - timedelta(days=days)
    async with get_session() as db:
        stale = await db.scalar(
            select(func.count(ReminderState.id)).where(ReminderState.sent_at < cutoff)
        )
        if not stale:
            return 0
        await db.execute(delete(ReminderState).where(ReminderState.sent_at < cutoff))
        await db.commit()
    return int(stale)


async def run_once() -> None:
    await prune_activity_log()
    await prune_reminder_state()
    # آمار سرعت انتشار هم کهنه می‌شود؛ بدون این، جدولش بی‌انتها
    # رشد می‌کند.
    from telkap.services import timings

    await timings.prune()


async def run_forever() -> None:
    while True:
        try:
            await asyncio.sleep(INTERVAL_SECONDS)
            await run_once()
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("چرخه‌ی نگهداری دیتابیس با خطا مواجه شد")
