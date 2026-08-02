"""تلاش مجدد برای پست‌هایی که ارسالشان شکست خورده است.

خود پیام ذخیره نمی‌شود؛ فقط نشانی‌اش. هنگام تلاش مجدد پیام از کانال مبدا
دوباره خوانده می‌شود تا نسخه‌ی به‌روز ارسال شود. اگر پست در مبدا حذف شده
باشد، آیتم از صف پاک می‌گردد.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import timedelta

from sqlalchemy import select

from telkap.db import get_session, log_activity
from telkap.models import RetryItem, Task, utcnow
from telkap.services.copier import RETRY_BACKOFF

log = logging.getLogger(__name__)

CHECK_INTERVAL = 60  # هر دقیقه صف بررسی می‌شود
MAX_ATTEMPTS = len(RETRY_BACKOFF)


class RetryWorker:
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
                log.exception("چرخه‌ی تلاش مجدد با خطا مواجه شد")

    async def run_once(self) -> int:
        """آیتم‌های سررسیدشده را دوباره تلاش می‌کند و تعداد موفق‌ها را برمی‌گرداند."""
        async with get_session() as db:
            rows = await db.execute(
                select(RetryItem).where(RetryItem.next_try_at <= utcnow()).limit(50)
            )
            items = list(rows.scalars())

        succeeded = 0
        for item in items:
            try:
                if await self._attempt(item):
                    succeeded += 1
            except Exception:
                log.exception("تلاش مجدد برای آیتم %s ناموفق بود", item.id)
        return succeeded

    async def _attempt(self, item: RetryItem) -> bool:
        async with get_session() as db:
            task = await db.get(Task, item.task_id)
        if task is None or not task.enabled:
            await self._drop(item.id)
            return False

        client = await self.manager.ensure_client(item.user_id)
        if client is None:
            await self._reschedule(item, "اکانت کاربری متصل نیست")
            return False

        try:
            messages = await client.get_messages(item.src_chat_id, ids=item.message_ids)
        except Exception as exc:
            await self._reschedule(item, f"خواندن پیام مبدا ناموفق بود: {exc}")
            return False

        messages = [m for m in (messages or []) if m is not None]
        if not messages:
            # پست در مبدا حذف شده؛ دیگر چیزی برای ارسال نیست
            await self._drop(item.id)
            await log_activity(
                user_id=item.user_id,
                task_id=item.task_id,
                event="retry_dropped",
                detail="پیام در کانال مبدا دیگر وجود ندارد",
            )
            return False

        try:
            sent = await self.copier.process(item.user_id, item.task_id, messages)
        except Exception as exc:
            await self._reschedule(item, str(exc))
            return False

        if sent:
            await self._drop(item.id)
            await log_activity(
                user_id=item.user_id,
                task_id=item.task_id,
                event="retry_ok",
                detail=f"ارسال مجدد موفق پس از {item.attempts + 1} تلاش",
            )
            return True

        # فیلترها یا تکراری بودن جلویش را گرفت؛ تلاش دوباره فایده ندارد
        await self._drop(item.id)
        return False

    async def _reschedule(self, item: RetryItem, error: str) -> None:
        async with get_session() as db:
            row = await db.get(RetryItem, item.id)
            if row is None:
                return
            row.attempts += 1
            row.last_error = error[:400]
            if row.attempts >= MAX_ATTEMPTS:
                user_id, task_id, attempts = row.user_id, row.task_id, row.attempts
                await db.delete(row)
                await db.commit()
                await log_activity(
                    user_id=user_id,
                    task_id=task_id,
                    event="retry_failed",
                    detail=f"پس از {attempts} تلاش ناموفق رها شد: {error}"[:600],
                    level="error",
                )
                if self.notifier:
                    await self.notifier(
                        user_id,
                        f"⚠️ یک پست پس از {attempts} تلاش ارسال نشد.\nعلت: {error}",
                    )
                return
            delay = RETRY_BACKOFF[min(row.attempts, len(RETRY_BACKOFF) - 1)]
            row.next_try_at = utcnow() + timedelta(seconds=delay)
            await db.commit()

    async def _drop(self, item_id: int) -> None:
        async with get_session() as db:
            row = await db.get(RetryItem, item_id)
            if row is not None:
                await db.delete(row)
                await db.commit()
