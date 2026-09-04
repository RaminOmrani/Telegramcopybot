"""صف پست‌های منتشرنشده: منتظر تأیید کاربر، یا منتظر ساعتشان.

هر دو حالت یک سازوکار دارند — پست نگه داشته می‌شود و بعداً از مبدا
دوباره خوانده و منتشر می‌گردد — و فقط شرط آزاد شدنشان فرق می‌کند.

نگه داشتنِ نشانی به‌جای خود پیام دو فایده دارد: دیتابیس بزرگ نمی‌شود، و
اگر پست در فاصله‌ی انتظار ویرایش یا حذف شود، نسخه‌ی درست منتشر می‌شود یا
اصلاً نمی‌رود.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import timedelta

from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError

from telkap.db import get_session, log_activity
from telkap.models import PendingPost, Task, utcnow

log = logging.getLogger(__name__)

CHECK_INTERVAL = 60          # هر دقیقه صف بررسی می‌شود
STALE_APPROVAL_HOURS = 72    # تأییدنشده‌های خیلی قدیمی دور ریخته می‌شوند
MAX_GAP_SECONDS = 24 * 3600  # سقف منطقی فاصله‌ی بین دو انتشار

REASON_LABELS = {
    PendingPost.REASON_APPROVAL: "⏳ منتظر تأیید شما",
    PendingPost.REASON_SCHEDULE: "🕐 منتظر زمان انتشار",
}


def summarize(text: str, media_kind: str) -> str:
    """خلاصه‌ای که کاربر در فهرست ببیند و بفهمد کدام پست است."""
    clean = " ".join((text or "").split())
    if clean:
        return clean[:160]
    labels = {
        "photo": "🖼 عکس بدون متن",
        "video": "🎬 ویدیو بدون متن",
        "animation": "🎞 گیف",
        "audio": "🎵 فایل صوتی",
        "voice": "🎤 ویس",
        "document": "📎 فایل",
        "sticker": "🩹 استیکر",
        "poll": "📊 نظرسنجی",
        "video_note": "⭕️ ویدیو-پیام",
    }
    return labels.get(media_kind, "پست بدون متن")


async def hold(
    *,
    task_id: int,
    user_id: int,
    src_chat_id: int,
    message_ids,
    reason: str,
    text: str = "",
    media_kind: str = "text",
    release_at=None,
) -> PendingPost | None:
    """یک پست را در صف می‌گذارد. اگر قبلاً در صف باشد، None برمی‌گرداند."""
    ids = ",".join(str(mid) for mid in message_ids)
    if not ids:
        return None
    row = PendingPost(
        task_id=task_id,
        user_id=user_id,
        src_chat_id=src_chat_id,
        src_msg_ids=ids[:400],
        reason=reason,
        preview=summarize(text, media_kind),
        media_kind=media_kind,
        release_at=release_at,
    )
    async with get_session() as db:
        db.add(row)
        try:
            await db.commit()
        except IntegrityError:
            await db.rollback()   # همین پست قبلاً در صف است
            return None
        await db.refresh(row)
    return row


async def get(item_id: int, user_id: int) -> PendingPost | None:
    """یک آیتم، فقط اگر مال همین کاربر باشد."""
    async with get_session() as db:
        row = await db.get(PendingPost, item_id)
    return row if row is not None and row.user_id == user_id else None


async def listing(
    user_id: int,
    *,
    reason: str | None = None,
    task_id: int | None = None,
    limit: int = 20,
) -> list[PendingPost]:
    async with get_session() as db:
        stmt = select(PendingPost).where(PendingPost.user_id == user_id)
        if reason is not None:
            stmt = stmt.where(PendingPost.reason == reason)
        if task_id is not None:
            stmt = stmt.where(PendingPost.task_id == task_id)
        rows = await db.execute(stmt.order_by(PendingPost.id).limit(limit))
        return list(rows.scalars())


async def waiting_count(
    user_id: int, *, reason: str | None = None, task_id: int | None = None
) -> int:
    async with get_session() as db:
        stmt = select(func.count(PendingPost.id)).where(PendingPost.user_id == user_id)
        if reason is not None:
            stmt = stmt.where(PendingPost.reason == reason)
        if task_id is not None:
            stmt = stmt.where(PendingPost.task_id == task_id)
        return int(await db.scalar(stmt) or 0)


async def drop(item_id: int) -> bool:
    async with get_session() as db:
        result = await db.execute(delete(PendingPost).where(PendingPost.id == item_id))
        await db.commit()
    return bool(result.rowcount)


async def drop_task(task_id: int, *, reason: str | None = None) -> int:
    """پاک کردن صف یک کار — وقتی کاربر تأیید یا زمان‌بندی را خاموش می‌کند.

    `reason` را بدهید تا فقط همان دسته پاک شود؛ وگرنه خاموش کردن تأیید
    دستی، پست‌های زمان‌بندی‌شده را هم با خودش می‌برد.
    """
    stmt = delete(PendingPost).where(PendingPost.task_id == task_id)
    if reason is not None:
        stmt = stmt.where(PendingPost.reason == reason)
    async with get_session() as db:
        result = await db.execute(stmt)
        await db.commit()
    return int(result.rowcount or 0)


async def due(limit: int = 50) -> list[PendingPost]:
    """پست‌های زمان‌بندی‌شده‌ای که وقتشان رسیده است."""
    async with get_session() as db:
        rows = await db.execute(
            select(PendingPost)
            .where(
                PendingPost.reason == PendingPost.REASON_SCHEDULE,
                PendingPost.release_at.is_not(None),
                PendingPost.release_at <= utcnow(),
            )
            .order_by(PendingPost.release_at)
            .limit(limit)
        )
        return list(rows.scalars())


async def next_slot(task_id: int, gap_seconds: int):
    """زودترین زمانی که این کار اجازه‌ی انتشار بعدی دارد.

    هم آخرین انتشار واقعی حساب می‌شود و هم پست‌هایی که جلوتر در صف
    نشسته‌اند؛ وگرنه ده پستِ همزمان همگی یک زمان می‌گرفتند و فاصله‌ای در
    کار نبود.
    """
    gap = max(0, min(int(gap_seconds or 0), MAX_GAP_SECONDS))
    now = utcnow()
    if not gap:
        return now

    async with get_session() as db:
        task = await db.get(Task, task_id)
        last_sent = task.last_copy_at if task is not None else None
        queued = await db.scalar(
            select(func.max(PendingPost.release_at)).where(
                PendingPost.task_id == task_id,
                PendingPost.reason == PendingPost.REASON_SCHEDULE,
            )
        )

    latest = now - timedelta(seconds=gap)   # یعنی «همین حالا مجاز است»
    for stamp in (last_sent, queued):
        if stamp is None:
            continue
        aware = stamp if stamp.tzinfo else stamp.replace(tzinfo=now.tzinfo)
        latest = max(latest, aware)
    return max(now, latest + timedelta(seconds=gap))


async def prune(hours: int = STALE_APPROVAL_HOURS) -> int:
    """تأییدنشده‌های خیلی قدیمی. پستِ سه‌روزه دیگر ارزش انتشار ندارد."""
    cutoff = utcnow() - timedelta(hours=hours)
    async with get_session() as db:
        rows = await db.execute(
            select(PendingPost).where(
                PendingPost.reason == PendingPost.REASON_APPROVAL,
                PendingPost.created_at < cutoff,
            )
        )
        stale = list(rows.scalars())
        if not stale:
            return 0
        for row in stale:
            await db.delete(row)
        await db.commit()
    log.info("%s پست تأییدنشده‌ی قدیمی از صف پاک شد", len(stale))
    return len(stale)


class ReleaseWorker:
    """پست‌های سررسیدشده را منتشر می‌کند.

    از همان مسیر موتور کپی رد می‌شوند — با `released` تا دوباره در همان
    صف نیفتند — پس همه‌ی فیلترها، سهمیه‌ها و واترمارک سر جایشان می‌مانند.
    ردیف پیش از پردازش پاک می‌شود تا پستی که مثلاً منتظر تعامل بوده،
    بتواند بعدش به صف تأیید برود.
    """

    def __init__(self, manager, copier, notifier=None) -> None:
        self.manager = manager
        self.copier = copier
        self.notifier = notifier

    async def run_forever(self) -> None:
        while True:
            try:
                await asyncio.sleep(CHECK_INTERVAL)
                await self.run_once()
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("چرخه‌ی انتشار زمان‌بندی‌شده با خطا مواجه شد")

    async def run_once(self) -> int:
        released = 0
        for item in await due():
            try:
                if await self.release(item):
                    released += 1
            except Exception:
                log.exception("انتشار پست صف‌شده‌ی %s ناموفق بود", item.id)
        await prune()
        return released

    async def release(self, item: PendingPost) -> bool:
        """یک آیتم را منتشر می‌کند و از صف برمی‌دارد."""
        async with get_session() as db:
            task = await db.get(Task, item.task_id)
        if task is None or not task.enabled:
            await drop(item.id)
            return False

        client = await self.manager.ensure_client(item.user_id)
        if client is None:
            return False      # اکانت وصل نیست؛ دفعه‌ی بعد دوباره تلاش می‌شود

        try:
            messages = await client.get_messages(
                item.src_chat_id, ids=item.message_ids
            )
        except Exception as exc:
            log.warning("خواندن پست صف‌شده‌ی %s ناموفق بود: %s", item.id, exc)
            return False

        messages = [m for m in (messages or []) if m is not None]
        if not messages:
            # در مبدا حذف شده؛ انتشارش دیگر درست نیست
            await drop(item.id)
            await log_activity(
                user_id=item.user_id,
                task_id=item.task_id,
                event="pending_dropped",
                detail="پست در کانال مبدا دیگر وجود ندارد",
            )
            return False

        await drop(item.id)
        sent = await self.copier.process(
            item.user_id, item.task_id, messages, released=item.reason
        )
        return bool(sent)
