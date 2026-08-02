"""کپی پیام‌های گذشته‌ی یک کانال (backfill).

کاربر تعداد پیام را مشخص می‌کند و ربات پیام‌های قبلی کانال مبدا را با
همان تنظیمات و فیلترهای کار، از قدیمی به جدید در مقصد منتشر می‌کند.
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field

from telethon.errors import FloodWaitError

from telkap.db import get_session, log_activity
from telkap.models import Task

log = logging.getLogger(__name__)


@dataclass
class BackfillJob:
    user_id: int
    task_id: int
    total: int
    done: int = 0
    copied: int = 0
    skipped: int = 0
    cancelled: bool = False
    finished: bool = False
    error: str = ""
    task_handle: asyncio.Task | None = field(default=None, repr=False)

    @property
    def progress_percent(self) -> int:
        if not self.total:
            return 0
        return min(100, int(self.done * 100 / self.total))


class HistoryCopier:
    """اجرای کارهای backfill به‌صورت پس‌زمینه، حداکثر یکی به ازای هر کاربر."""

    def __init__(self, manager, copier, notifier=None) -> None:
        self.manager = manager
        self.copier = copier
        self.notifier = notifier
        self._jobs: dict[int, BackfillJob] = {}

    def job_for(self, user_id: int) -> BackfillJob | None:
        return self._jobs.get(user_id)

    def is_running(self, user_id: int) -> bool:
        job = self._jobs.get(user_id)
        return bool(job and not job.finished)

    async def start(self, user_id: int, task_id: int, limit: int) -> BackfillJob:
        if self.is_running(user_id):
            raise RuntimeError("یک کپی گذشته در حال اجراست. ابتدا آن را متوقف کنید.")
        job = BackfillJob(user_id=user_id, task_id=task_id, total=limit)
        self._jobs[user_id] = job
        job.task_handle = asyncio.create_task(self._run(job))
        return job

    def cancel(self, user_id: int) -> bool:
        job = self._jobs.get(user_id)
        if not job or job.finished:
            return False
        job.cancelled = True
        return True

    async def _run(self, job: BackfillJob) -> None:
        try:
            await self._copy_history(job)
        except asyncio.CancelledError:
            job.cancelled = True
            raise
        except Exception as exc:
            job.error = str(exc)
            log.exception("کپی پیام‌های گذشته برای کار %s شکست خورد", job.task_id)
        finally:
            job.finished = True
            await self._report(job)

    async def _copy_history(self, job: BackfillJob) -> None:
        async with get_session() as db:
            task = await db.get(Task, job.task_id)
            if task is None:
                raise RuntimeError("کار موردنظر پیدا نشد")
            source = task.source_id or task.source_ref

        client = await self.manager.ensure_client(job.user_id)
        if client is None:
            raise RuntimeError("اکانت کاربری متصل نیست")

        entity = await self.manager.resolve_entity(client, str(source))
        if entity is None:
            raise RuntimeError("کانال مبدا در دسترس نیست")

        # از جدید به قدیم می‌خوانیم، سپس معکوس می‌فرستیم تا ترتیب کانال حفظ شود
        collected: list = []
        async for message in client.iter_messages(entity, limit=job.total):
            collected.append(message)

        # گروه‌بندی آلبوم‌ها تا مثل مبدا کنار هم ارسال شوند
        groups: list[list] = []
        album_index: dict[int, int] = {}
        for message in reversed(collected):
            gid = getattr(message, "grouped_id", None)
            if gid is not None and gid in album_index:
                groups[album_index[gid]].append(message)
                continue
            groups.append([message])
            if gid is not None:
                album_index[gid] = len(groups) - 1

        for group in groups:
            if job.cancelled:
                break
            try:
                sent = await self.copier.process(job.user_id, job.task_id, group)
                if sent:
                    job.copied += 1
                else:
                    job.skipped += 1
            except FloodWaitError as exc:
                log.warning("FloodWait %s ثانیه در کپی گذشته", exc.seconds)
                await asyncio.sleep(min(exc.seconds, 300))
                job.skipped += 1
            except Exception:
                log.exception("ارسال یکی از پیام‌های گذشته ناموفق بود")
                job.skipped += 1
            job.done += len(group)
            # فاصله‌ی کوتاه بین ارسال‌ها، مکمل محدودکننده‌ی نرخ
            await asyncio.sleep(0.5)

        await log_activity(
            user_id=job.user_id,
            task_id=job.task_id,
            event="backfill",
            detail=f"{job.copied} کپی، {job.skipped} رد شد",
        )

    async def _report(self, job: BackfillJob) -> None:
        if not self.notifier:
            return
        if job.error:
            text = f"❌ کپی پیام‌های گذشته متوقف شد.\nعلت: {job.error}"
        elif job.cancelled:
            text = (
                f"⏹ کپی پیام‌های گذشته لغو شد.\n"
                f"کپی‌شده: {job.copied} | رد‌شده: {job.skipped}"
            )
        else:
            text = (
                f"✅ کپی پیام‌های گذشته تمام شد.\n"
                f"کپی‌شده: {job.copied} | رد‌شده: {job.skipped}"
            )
        try:
            await self.notifier(job.user_id, text)
        except Exception:
            log.debug("ارسال گزارش backfill ناموفق بود", exc_info=True)
