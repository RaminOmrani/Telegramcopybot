"""انتخاب مخاطب برای پیام همگانی.

پیام همگانیِ بی‌هدف هم بی‌اثر است هم آزاردهنده: کسی که همین دیروز خرید
کرده، نباید تخفیف «برگرد» بگیرد. اینجا چند گروه از پیش تعریف‌شده هست که
هرکدام یک پرسش تجاری واقعی را جواب می‌دهد.
"""
from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import func, select

from telkap.db import get_session
from telkap.models import PaymentRequest, Subscription, Task, User, utcnow

ALL = "all"
ACTIVE = "active"
EXPIRED = "expired"
NEVER = "never"
LINKED = "linked"
IDLE = "idle"


@dataclass(frozen=True, slots=True)
class Segment:
    code: str
    title: str
    hint: str


SEGMENTS: tuple[Segment, ...] = (
    Segment(ALL, "👥 همه", "هر کاربری که ربات را استارت کرده"),
    Segment(ACTIVE, "✅ مشترکان فعال", "اشتراکشان هنوز تمام نشده"),
    Segment(EXPIRED, "⌛️ منقضی‌شده‌ها", "قبلاً خرید کرده‌اند ولی الان اشتراک ندارند"),
    Segment(NEVER, "🆕 هرگز نخریده‌ها", "استارت کرده‌اند ولی هیچ خریدی نداشته‌اند"),
    Segment(LINKED, "🔗 اکانت متصل", "اکانتشان وصل است — یعنی جدی‌اند"),
    Segment(IDLE, "😴 بدون کار", "اکانت وصل کرده‌اند ولی هیچ کاری نساخته‌اند"),
)

BY_CODE = {segment.code: segment for segment in SEGMENTS}


def _active_users():
    return select(Subscription.user_id).where(Subscription.expires_at > utcnow())


def _ever_paid():
    return select(PaymentRequest.user_id).where(
        PaymentRequest.status == PaymentRequest.STATUS_APPROVED
    )


def _with_tasks():
    return select(Task.user_id)


def _filtered(stmt, code: str):
    """کاربران مسدود هرگز پیام همگانی نمی‌گیرند."""
    stmt = stmt.where(User.is_banned.is_(False))
    if code == ACTIVE:
        return stmt.where(User.id.in_(_active_users()))
    if code == EXPIRED:
        return stmt.where(User.id.not_in(_active_users()), User.id.in_(_ever_paid()))
    if code == NEVER:
        # اشتراک هدیه یا آزمایشی هم «الان مشترک است» حساب می‌شود: فرستادن
        # پیام «بخر» به کسی که اشتراک فعال دارد، دقیقاً همان بدفهمی‌ای
        # است که گروه‌بندی برای جلوگیری از آن ساخته شده.
        return stmt.where(
            User.id.not_in(_ever_paid()), User.id.not_in(_active_users())
        )
    if code == LINKED:
        return stmt.where(User.session_enc.is_not(None))
    if code == IDLE:
        return stmt.where(
            User.session_enc.is_not(None), User.id.not_in(_with_tasks())
        )
    return stmt


async def size(code: str) -> int:
    async with get_session() as db:
        return int(await db.scalar(_filtered(select(func.count(User.id)), code)) or 0)


async def members(code: str) -> list[int]:
    async with get_session() as db:
        rows = await db.execute(_filtered(select(User.id), code))
        return list(rows.scalars())


async def sizes() -> dict[str, int]:
    """اندازه‌ی همه‌ی گروه‌ها، برای نمایش کنار دکمه‌ها."""
    return {segment.code: await size(segment.code) for segment in SEGMENTS}
