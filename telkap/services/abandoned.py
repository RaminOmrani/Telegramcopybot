"""پست‌هایی که دیگر نباید فرستاده شوند — تا جارو دوباره سراغشان نرود.

سه راه به اینجا می‌رسد: پستِ «نرفتنی» (ایراد محتوا، نه شبکه)، پستی که
صفِ تلاش مجدد پس از آخرین تلاش رهایش کرد، و پستی که کاربر در صفِ تأیید
ردش کرد. هر سه از نگاهِ جارو «نرفته»اند و بدون این ثبت، هر سه دقیقه
دوباره فرستاده می‌شدند — و پستِ ردشده دوباره در صف تأیید می‌نشست.
"""
from __future__ import annotations

import logging
from datetime import timedelta

from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError

from telkap.db import get_session
from telkap.models import AbandonedPost, utcnow

log = logging.getLogger(__name__)

# جارو فقط ۳۰ پستِ آخر را می‌بیند؛ یک هفته برای پرکارترین کانال هم کافی است
KEEP_DAYS = 7


async def mark(task_id: int, src_msg_id: int, reason: str) -> None:
    """ثبت می‌کند؛ اگر از قبل ثبت شده باشد، کاری نمی‌کند. هرگز خطا نمی‌دهد.

    «هرگز» چون صدازننده معمولاً وسطِ رسیدگی به یک شکست است و خطای این
    ثبت نباید جای آن را بگیرد.
    """
    try:
        async with get_session() as db:
            db.add(
                AbandonedPost(
                    task_id=int(task_id), src_msg_id=int(src_msg_id), reason=reason
                )
            )
            try:
                await db.commit()
            except IntegrityError:
                await db.rollback()
    except Exception:
        log.warning(
            "ثبتِ رهاشدنِ پست %s کار %s نشد", src_msg_id, task_id, exc_info=True
        )


async def is_marked(task_id: int, src_msg_id: int) -> bool:
    async with get_session() as db:
        found = await db.scalar(
            select(AbandonedPost.id).where(
                AbandonedPost.task_id == int(task_id),
                AbandonedPost.src_msg_id == int(src_msg_id),
            ).limit(1)
        )
    return found is not None


async def prune(days: int = KEEP_DAYS) -> int:
    cutoff = utcnow() - timedelta(days=days)
    async with get_session() as db:
        result = await db.execute(
            delete(AbandonedPost).where(AbandonedPost.created_at < cutoff)
        )
        await db.commit()
    return int(result.rowcount or 0)
