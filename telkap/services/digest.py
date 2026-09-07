"""خلاصه‌ی روزانه‌ی کارها برای کاربر.

کاربری که ربات را تنظیم کرده و رفته، هیچ‌وقت نمی‌فهمد دیروز چه شد — نه
اینکه چند پست رفت، نه اینکه کاری خطا خورده و خوابیده. یک پیام کوتاه در
روز، هم اطمینان می‌دهد و هم مشکل را زود لو می‌دهد.

پیش‌فرض خاموش است؛ پیام روزانه‌ی ناخواسته آزاردهنده است.
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import timedelta

from sqlalchemy import func, select

from telkap.config import get_settings
from telkap.db import get_session
from telkap.models import DailyStat, PendingPost, Task, User, utcnow
from telkap.services.subscription import remaining_days
from telkap.texts import fa_num

log = logging.getLogger(__name__)

# ساعت محلیِ ارسال خلاصه. صبح، تا اگر مشکلی هست کاربر همان روز برسد.
SEND_HOUR = 9
CHECK_INTERVAL = 1800   # هر نیم ساعت، تا ساعت هدف از دست نرود


def yesterday_key(offset_hours: float | None = None) -> str:
    if offset_hours is None:
        offset_hours = get_settings().timezone_offset
    return (utcnow() + timedelta(hours=offset_hours) - timedelta(days=1)).strftime(
        "%Y-%m-%d"
    )


@dataclass(slots=True)
class Summary:
    copied: int = 0
    skipped: int = 0
    failed: int = 0
    waiting: int = 0
    days_left: int = 0
    stopped: list[str] = field(default_factory=list)

    @property
    def worth_sending(self) -> bool:
        """روزی که هیچ اتفاقی نیفتاده ارزش پیام ندارد.

        مگر اینکه خبر بدی باشد — کار خوابیده یا اشتراک رو به پایان.
        """
        return bool(
            self.copied or self.failed or self.waiting or self.stopped
            or 0 < self.days_left <= 3
        )


async def build(user_id: int, day: str) -> Summary:
    async with get_session() as db:
        row = (
            await db.execute(
                select(
                    func.coalesce(func.sum(DailyStat.copied), 0),
                    func.coalesce(func.sum(DailyStat.skipped), 0),
                    func.coalesce(func.sum(DailyStat.failed), 0),
                ).where(DailyStat.user_id == user_id, DailyStat.day == day)
            )
        ).one()
        waiting = await db.scalar(
            select(func.count(PendingPost.id)).where(
                PendingPost.user_id == user_id,
                PendingPost.reason == PendingPost.REASON_APPROVAL,
            )
        )
        broken = await db.execute(
            select(Task.title, Task.source_ref).where(
                Task.user_id == user_id,
                Task.enabled.is_(False),
                Task.last_error.is_not(None),
            )
        )
        stopped = [(title or ref) for title, ref in broken.all()]

    return Summary(
        copied=int(row[0] or 0),
        skipped=int(row[1] or 0),
        failed=int(row[2] or 0),
        waiting=int(waiting or 0),
        days_left=await remaining_days(user_id),
        stopped=stopped[:5],
    )


def render(summary: Summary) -> str:
    lines = ["📬 <b>خلاصه‌ی دیروز</b>\n"]
    lines.append(f"✅ کپی‌شده: <b>{fa_num(summary.copied)}</b>")
    if summary.skipped:
        lines.append(f"⏭ رد‌شده با فیلترها: {fa_num(summary.skipped)}")
    if summary.failed:
        lines.append(f"⚠️ ناموفق: {fa_num(summary.failed)}")
    if summary.waiting:
        lines.append(f"⏳ منتظر تأیید شما: <b>{fa_num(summary.waiting)}</b>")

    if summary.stopped:
        lines.append("\n🔴 <b>کارهای متوقف‌شده</b>")
        lines.extend(f"• {title}" for title in summary.stopped)
        lines.append("<i>در «📋 کارهای کپی» دلیلش را ببینید.</i>")

    if 0 < summary.days_left <= 3:
        lines.append(
            f"\n⏳ <b>{fa_num(summary.days_left)} روز</b> تا پایان اشتراک شما."
        )

    lines.append("\n<i>خاموش کردن: «👤 حساب کاربری» ← «📬 خلاصه‌ی روزانه»</i>")
    return "\n".join(lines)


async def run_once(notify, *, day: str | None = None) -> int:
    """برای هر کاربرِ مشترکِ خلاصه یک پیام می‌فرستد. خروجی: تعداد ارسال."""
    target_day = day or yesterday_key()
    async with get_session() as db:
        rows = await db.execute(
            select(User.id).where(
                User.daily_digest.is_(True), User.is_banned.is_(False)
            )
        )
        user_ids = list(rows.scalars())

    sent = 0
    for user_id in user_ids:
        try:
            summary = await build(user_id, target_day)
            if not summary.worth_sending:
                continue
            await notify(user_id, render(summary))
            sent += 1
        except Exception:
            log.debug("ارسال خلاصه‌ی روزانه به %s ناموفق بود", user_id, exc_info=True)
        await asyncio.sleep(0.05)   # نرخ امن Bot API
    return sent


async def run_forever(notify) -> None:
    """در ساعت مقرر، یک بار در روز خلاصه می‌فرستد."""
    last_sent = ""
    while True:
        try:
            await asyncio.sleep(CHECK_INTERVAL)
            offset = get_settings().timezone_offset
            local = utcnow() + timedelta(hours=offset)
            today = local.strftime("%Y-%m-%d")
            if local.hour < SEND_HOUR or last_sent == today:
                continue
            last_sent = today
            count = await run_once(notify)
            if count:
                log.info("%d خلاصه‌ی روزانه ارسال شد", count)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("چرخه‌ی خلاصه‌ی روزانه با خطا مواجه شد")
