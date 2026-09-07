"""تورِ ایمنی: هر چند دقیقه، مبدأها را خودمان نگاه می‌کنیم.

<b>چرا این لازم شد — و چرا دیر لازمش دانستیم.</b>

کل سیستم روی یک فرض بنا شده بود: تلگرام هر پست تازه را به‌صورت
«آپدیت» به ما می‌فرستد. آن فرض معمولاً درست است، ولی <b>وقتی درست
نباشد هیچ نشانه‌ای ندارد</b>: اتصال برقرار است، هندلرها ثبت‌اند،
تماس‌های خروجی جواب می‌دهند — و هیچ آپدیتی نمی‌آید. از بیرون دقیقاً
همان چیزی دیده می‌شود که همیشه: مقصد خالی.

همین امروز دقیقاً همین اتفاق افتاد. گزارش سلامت توانست آخرین پستِ هر
شش مبدأ را <b>از خودِ تلگرام بخواند</b> — یعنی مسیر خروجی سالم بود —
ولی پنج کار از شش‌تا ساعت‌ها عقب بودند. خواندن کار می‌کرد، شنیدن نه.

<b>پس دیگر فقط گوش نمی‌دهیم.</b> این ماژول هر چند دقیقه سراغ هر مبدأ
می‌رود و می‌پرسد «از آخرین چیزی که من دیدم، چیز تازه‌ای هست؟» — و اگر
باشد، همان مسیر همیشگیِ کپی را طی می‌کند. آپدیت‌ها هنوز راه اصلی‌اند
(چون بی‌درنگ‌اند)؛ این یکی فقط نمی‌گذارد سکوتشان بی‌صدا بماند.

<b>چرا دوباره‌کاری نمی‌شود.</b> پیش از هر ارسال، جدول نگاشت پرسیده
می‌شود: «این پستِ مبدا برای این کار قبلاً رفته؟» اگر رفته باشد رد
می‌شود. همان جدولی که همگام‌سازیِ ویرایش و حذف از آن می‌خواند، پس
چیز تازه‌ای هم اضافه نشد.
"""
from __future__ import annotations

import asyncio
import logging

from sqlalchemy import func, select

from telkap.db import get_session
from telkap.models import MessageMap, Task

log = logging.getLogger(__name__)

# هر سه دقیقه. آن‌قدر کوتاه که تأخیرش به چشم نیاید، و آن‌قدر بلند که
# برای هر مبدأ روزی چندصد تماس اضافه نسازد.
SWEEP_INTERVAL = 180

# از هر مبدأ حداکثر این تعداد پستِ اخیر نگاه می‌شود. اگر کانالی در سه
# دقیقه بیشتر از این پست بگذارد، بقیه در دور بعد گرفته می‌شوند.
LOOKBACK = 30

# مکث کوتاه بین مبدأها، تا جاروی دوره‌ای خودش به محدودیت نرخ نخورد
BREATH = 0.4


async def _already_sent(task_id: int, src_msg_id: int) -> bool:
    async with get_session() as db:
        found = await db.scalar(
            select(MessageMap.id).where(
                MessageMap.task_id == task_id,
                MessageMap.src_msg_id == src_msg_id,
            )
        )
    return found is not None


async def _watermarks(task_ids: list[int]) -> dict[int, int]:
    """بزرگ‌ترین آیدیِ پستِ مبدأ، <b>برای هر کار جداگانه</b>.

    <b>چرا جداگانه و نه یکی برای کل مبدأ.</b> نسخه‌ی اول یک نشانه برای
    همه‌ی کارهای یک مبدأ می‌گرفت — بزرگ‌ترینشان. ولی وقتی دو کار روی
    یک مبدأ باشند و یکی‌شان عقب مانده باشد، نشانه‌ی مشترک همان کارِ
    جلوتر است و کارِ عقب‌مانده <b>برای همیشه</b> نادیده گرفته می‌شود —
    دقیقاً همان کاری که این ماژول قرار بود جلویش را بگیرد.
    """
    if not task_ids:
        return {}
    async with get_session() as db:
        rows = await db.execute(
            select(MessageMap.task_id, func.max(MessageMap.src_msg_id))
            .where(MessageMap.task_id.in_(task_ids))
            .group_by(MessageMap.task_id)
        )
        found = {int(task_id): int(top or 0) for task_id, top in rows.all()}
    return {task_id: found.get(task_id, 0) for task_id in task_ids}


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


class Sweeper:
    def __init__(self, manager, copier) -> None:
        self.manager = manager
        self.copier = copier

    async def run_forever(self) -> None:
        while True:
            try:
                await asyncio.sleep(SWEEP_INTERVAL)
                await self.run_once()
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("چرخه‌ی جاروی مبدأها با خطا مواجه شد")

    async def run_once(self) -> int:
        """تعداد پست‌هایی که آپدیتشان نرسیده بود و اینجا گرفته شدند."""
        picked = 0
        for user_id in list(getattr(self.manager, "_runtimes", {})):
            for chat_id in self.manager.listening_chats(user_id):
                try:
                    picked += await self._sweep(user_id, chat_id)
                except Exception:
                    log.exception("جاروی مبدا %s کاربر %s نشد", chat_id, user_id)
                await asyncio.sleep(BREATH)
        return picked

    async def _sweep(self, user_id: int, chat_id: int) -> int:
        task_ids = self.manager.tasks_for_chat(user_id, chat_id)
        if not task_ids:
            return 0

        # کارِ خاموش‌شده هنوز ممکن است در نقشه باشد تا بارگذاری بعدی
        async with get_session() as db:
            live = [
                row
                for row in (
                    await db.execute(
                        select(Task.id).where(
                            Task.id.in_(task_ids), Task.enabled.is_(True)
                        )
                    )
                ).scalars()
            ]
        if not live:
            return 0

        marks = await _watermarks(live)
        # کاری که هیچ‌وقت چیزی نفرستاده کنار گذاشته می‌شود. <b>عمداً</b>:
        # نمی‌دانیم از کجایش «تازه» است و ریختنِ آرشیو یک کانال در
        # مقصد، بدترین کاری است که می‌شود کرد. کپی گذشته کارِ خودش را
        # دارد و کاربر خودش شروعش می‌کند.
        started = {task_id: top for task_id, top in marks.items() if top > 0}
        if not started:
            return 0

        # از پایین‌ترین نشانه می‌خوانیم تا عقب‌مانده‌ترین کار هم پوشش
        # داده شود؛ تصمیمِ فرستادن بعداً برای هر کار جدا گرفته می‌شود.
        low = min(started.values())

        client = await self.manager.ensure_client(user_id)
        if client is None:
            return 0

        fresh = []
        async for message in client.iter_messages(chat_id, limit=LOOKBACK):
            if message.id <= low:
                break
            fresh.append(message)
        if not fresh:
            return 0

        fresh.reverse()      # از قدیم به جدید، مثل ترتیب خودِ کانال
        log.warning(
            "جارو: %d پستِ جامانده در مبدا %s کاربر %s پیدا شد "
            "— آپدیتشان نرسیده بود",
            len(fresh), chat_id, user_id,
        )

        sent = 0
        for group in _group_albums(fresh):
            for task_id, top in started.items():
                if group[0].id <= top:
                    continue
                if await _already_sent(task_id, group[0].id):
                    continue
                try:
                    if await self.copier.process(user_id, task_id, group):
                        sent += 1
                except Exception:
                    log.exception(
                        "ارسال پستِ جامانده‌ی %s برای کار %s نشد",
                        group[0].id, task_id,
                    )
            await asyncio.sleep(BREATH)
        return sent
