"""نگهداری دوره‌ای دیتابیس.

جدول لاگ فعالیت‌ها به ازای هر پست کپی‌شده یک ردیف می‌گیرد. با ده‌ها کاربر
فعال این جدول سریع‌ترین بخش رشد دیتابیس است و اگر هرس نشود، هم فایل بزرگ
می‌شود و هم صفحه‌ی «گزارش فعالیت» کند.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import timedelta

from sqlalchemy import delete, func, select

from telkap.config import get_settings
from telkap.db import get_session
from telkap.models import ActivityLog, ReminderState, utcnow

log = logging.getLogger(__name__)

# فاصله‌ی اجرای نگهداری
INTERVAL_SECONDS = 6 * 3600


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


async def run_forever() -> None:
    while True:
        try:
            await asyncio.sleep(INTERVAL_SECONDS)
            await run_once()
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("چرخه‌ی نگهداری دیتابیس با خطا مواجه شد")
