"""پویشگرِ مبدأهای عمومی — موتورِ حالت ساده.

<b>چرا پویش و نه آپدیت.</b> برای کانالی که عضوش نیستیم، تلگرام آپدیتِ
لحظه‌ای نمی‌فرستد؛ اصلاً نمی‌داند ما به آن علاقه داریم. پس خودمان سر
می‌زنیم. سنجیدیم: هر مبدأ حدود ۰٫۰۶ ثانیه طول می‌کشد، یعنی پویش ارزان
است و تنگنا زمان نیست — نرخِ درخواست است.

<b>و چرا این با «جارو» یکی نشد.</b> جارو تورِ ایمنیِ مسیرِ اکانتِ
مشتری است: فرض می‌کند آپدیت‌ها می‌آیند و فقط جاماندگی را جمع می‌کند.
اینجا آپدیتی در کار نیست و پویش <b>خودِ مسیر</b> است. قاطی کردنشان
یعنی یک ماژول که دو قرارداد متفاوت دارد و هر تغییری در یکی، دیگری را
بی‌صدا خراب می‌کند.

<b>اولین بار هرگز آرشیو نمی‌ریزد.</b> وقتی مبدأیی تازه سپرده می‌شود،
فقط نشانه‌اش روی آخرین پست گذاشته می‌شود و هیچ‌چیز فرستاده نمی‌شود.
بدون این، اولین اجرای هر کارِ تازه سی پستِ قدیمی را در کانال مشتری
خالی می‌کند — خرابی‌ای که برگرداندنش دستی است و اعتماد را همان‌جا
می‌برد.
"""
from __future__ import annotations

import asyncio
import logging

from sqlalchemy import select

from telkap.db import get_session
from telkap.models import DeliveryTiming, MessageMap, SourceLease, Task

log = logging.getLogger(__name__)

# هر دقیقه. از جارو (سه دقیقه) کوتاه‌تر است چون اینجا راه دیگری نیست:
# این تنها مسیرِ رسیدنِ پست است، نه تورِ ایمنیِ یک مسیرِ دیگر.
POLL_INTERVAL = 60

# از هر مبدأ در هر دور حداکثر این تعداد پست. کانالی که در یک دقیقه
# بیشتر از این بگذارد، بقیه را دور بعد می‌گیرد.
LOOKBACK = 25

# مکث بین مبدأها. تنگنا زمان نیست، نرخ است: بدون این، یک اکانت با چند
# ده مبدأ خودش را به محدودیت می‌رساند.
BREATH = 0.5


async def _simple_sources() -> dict[int, list[tuple[int, int]]]:
    """مبدأهای فعالِ حالت ساده → [(کار، صاحبش)].

    صاحبِ کار همین‌جا خوانده می‌شود و نه از یک کشِ جداگانه: موتور کپی
    برای سهمیه و گزارش لازمش دارد، و کشی که باید دستی تازه شود دیر یا
    زود کهنه می‌ماند — آن‌وقت پست‌ها به حسابِ کاربرِ اشتباه می‌خورند.
    """
    async with get_session() as db:
        rows = list(
            (
                await db.execute(
                    select(Task.id, Task.user_id, Task.source_id).where(
                        Task.enabled.is_(True),
                        Task.mode == Task.MODE_SIMPLE,
                        Task.source_kind == Task.SOURCE_TELEGRAM,
                        Task.source_id.is_not(None),
                    )
                )
            ).all()
        )
    grouped: dict[int, list[tuple[int, int]]] = {}
    for task_id, user_id, source_id in rows:
        grouped.setdefault(int(source_id), []).append((int(task_id), int(user_id)))
    return grouped


async def _already_sent(task_id: int, src_msg_id: int) -> bool:
    async with get_session() as db:
        found = await db.scalar(
            select(MessageMap.id).where(
                MessageMap.task_id == task_id,
                MessageMap.src_msg_id == src_msg_id,
            )
        )
    return found is not None


def _group_albums(messages: list) -> list[list]:
    """آلبوم‌ها کنار هم می‌مانند، وگرنه تک‌تک و بی‌ربط فرستاده می‌شوند."""
    groups: list[list] = []
    index: dict[int, int] = {}
    for message in messages:
        gid = getattr(message, "grouped_id", None)
        if gid is not None and gid in index:
            groups[index[gid]].append(message)
            continue
        groups.append([message])
        if gid is not None:
            index[gid] = len(groups) - 1
    return groups


class PublicPoller:
    def __init__(self, copier) -> None:
        self.copier = copier

    async def run_forever(self) -> None:
        while True:
            try:
                await asyncio.sleep(POLL_INTERVAL)
                await self.run_once()
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("چرخه‌ی پویش مبدأهای عمومی با خطا مواجه شد")

    async def run_once(self) -> int:
        sent = 0
        for source_id, jobs in (await _simple_sources()).items():
            try:
                sent += await self._poll(source_id, jobs)
            except Exception:
                log.exception("پویش مبدأ عمومی %s نشد", source_id)
            await asyncio.sleep(BREATH)
        return sent

    async def _poll(self, source_id: int, jobs: list[tuple[int, int]]) -> int:
        from telethon.errors import FloodWaitError

        from telkap.services import pool

        async with get_session() as db:
            task = await db.get(Task, jobs[0][0])
            source_ref = task.source_ref if task is not None else ""

        try:
            account = await pool.lease(source_id, source_ref)
        except pool.NoAccount:
            # <b>بلند، نه بی‌صدا.</b> بدون خواننده هیچ کارِ ساده‌ای اجرا
            # نمی‌شود — و چون سمت مشتری هیچ خطایی دیده نمی‌شود، از بیرون
            # شبیه «سرویس کند است» به نظر می‌رسد، نه شبیه «زیرساخت
            # نداریم». لاگ کافی نیست؛ کسی دنبال لاگ نمی‌گردد وقتی
            # نمی‌داند چیزی خراب است.
            from telkap.services import alerts

            log.error(
                "هیچ اکانت سرویسی برای مبدأ %s نیست؛ %d کار ساکت‌اند",
                source_id, len(jobs),
            )
            await alerts.send(
                "👥 <b>هیچ اکانت سرویسِ سالمی نداریم.</b>\n\n"
                "کارهای «حالت ساده» خواننده‌ای ندارند و هیچ پستی برایشان "
                "نمی‌رود. مشتری خطایی نمی‌بیند — فقط کانالش خالی می‌ماند.\n\n"
                "در ربات: <code>/pool</code> ← افزودن اکانت.",
                key="pool-empty",
            )
            return 0

        client = await pool.client_for(account)
        if client is None:
            return 0

        async with get_session() as db:
            lease = await db.scalar(
                select(SourceLease).where(SourceLease.source_id == source_id)
            )
            seen = int(getattr(lease, "seen_msg_id", 0) or 0)

        try:
            fresh = []
            async for message in client.iter_messages(source_id, limit=LOOKBACK):
                if seen and message.id <= seen:
                    break
                fresh.append(message)
        except FloodWaitError as exc:
            await pool.mark_flood(account.id, int(exc.seconds))
            return 0
        except Exception as exc:
            if _is_dead_account(exc):
                await pool.mark_banned(account.id, str(exc)[:200])
                return 0
            raise

        if not fresh:
            return 0

        newest = max(m.id for m in fresh)

        if not seen:
            # <b>اولین دیدار.</b> فقط نشانه می‌گذاریم. ریختنِ آرشیو در
            # کانال مشتری، خرابی‌ای است که پاک کردنش دستی است.
            await _remember(source_id, newest)
            log.info(
                "مبدأ عمومی %s برای اولین بار دیده شد؛ نشانه روی %s، چیزی فرستاده نشد",
                source_id, newest,
            )
            return 0

        count = 0
        for group in _group_albums(list(reversed(fresh))):
            for task_id, user_id in jobs:
                if await _already_sent(task_id, group[0].id):
                    continue
                try:
                    if await self.copier.process(
                        user_id, task_id, group, via=DeliveryTiming.VIA_SIMPLE,
                    ):
                        count += 1
                except Exception:
                    log.exception(
                        "ارسال پست %s برای کار ساده‌ی %s نشد", group[0].id, task_id
                    )
            await asyncio.sleep(BREATH)

        # نشانه <b>بعد از</b> تلاش برای ارسال جابه‌جا می‌شود. اگر قبلش
        # بود، پستی که ارسالش شکست خورده دیگر هیچ‌وقت دیده نمی‌شد.
        await _remember(source_id, newest)
        return count


def _is_dead_account(exc: BaseException) -> bool:
    name = type(exc).__name__
    return name in {
        "AuthKeyUnregisteredError",
        "AuthKeyInvalidError",
        "SessionRevokedError",
        "SessionExpiredError",
        "UserDeactivatedError",
        "UserDeactivatedBanError",
    }


async def _remember(source_id: int, newest: int) -> None:
    async with get_session() as db:
        lease = await db.scalar(
            select(SourceLease).where(SourceLease.source_id == source_id)
        )
        if lease is not None and newest > int(lease.seen_msg_id or 0):
            lease.seen_msg_id = newest
            await db.commit()
