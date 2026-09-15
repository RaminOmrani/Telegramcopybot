"""گزارش‌های تجاری: درآمد، قیف تبدیل، و دلیل ریزش.

قیف عمداً از روی جدول‌های موجود ساخته می‌شود، نه از رویدادنگاری تازه.
اینطور آمار برای کاربران قدیمی هم درست درمی‌آید — رویدادنگاری فقط از
لحظه‌ی نصب به بعد را می‌دید و ماه‌ها طول می‌کشید تا قابل استناد شود.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import UTC, timedelta

from sqlalchemy import func, select

from telkap.db import get_session
from telkap.models import (
    ChurnFeedback,
    PaymentRequest,
    ResellerSale,
    Subscription,
    Task,
    User,
    utcnow,
)

log = logging.getLogger(__name__)


@dataclass(slots=True)
class Revenue:
    """درآمد در یک بازه، تفکیک‌شده به منبع."""

    plans: int = 0
    credits: int = 0
    reseller: int = 0
    payers: int = 0

    @property
    def total(self) -> int:
        return self.plans + self.credits + self.reseller

    @property
    def per_payer(self) -> int:
        return self.total // self.payers if self.payers else 0


@dataclass(slots=True)
class Funnel:
    """از استارت تا خرید — هر پله و افت بین آن‌ها."""

    started: int = 0
    connected: int = 0
    built_task: int = 0
    paid: int = 0

    def rate(self, part: int) -> int:
        return round(part * 100 / self.started) if self.started else 0

    @property
    def steps(self) -> list[tuple[str, int, int]]:
        """(عنوان، تعداد، درصد از کل) برای نمایش."""
        return [
            ("ربات را استارت کردند", self.started, 100 if self.started else 0),
            ("اکانت وصل کردند", self.connected, self.rate(self.connected)),
            ("کار کپی ساختند", self.built_task, self.rate(self.built_task)),
            ("خرید کردند", self.paid, self.rate(self.paid)),
        ]

    @property
    def biggest_drop(self) -> tuple[str, int]:
        """بزرگ‌ترین افت بین دو پله — همان‌جا که باید کار کرد."""
        pairs = [
            ("استارت ← اتصال اکانت", self.started - self.connected),
            ("اتصال اکانت ← ساخت کار", self.connected - self.built_task),
            ("ساخت کار ← خرید", self.built_task - self.paid),
        ]
        return max(pairs, key=lambda item: item[1])


@dataclass(slots=True)
class Retention:
    once: int = 0        # فقط یک بار خرید کرده‌اند
    repeat: int = 0      # دو بار یا بیشتر
    active_subs: int = 0
    expired_users: int = 0

    @property
    def repeat_rate(self) -> int:
        total = self.once + self.repeat
        return round(self.repeat * 100 / total) if total else 0


@dataclass(slots=True)
class Dashboard:
    this_month: Revenue = field(default_factory=Revenue)
    last_month: Revenue = field(default_factory=Revenue)
    all_time: Revenue = field(default_factory=Revenue)
    funnel: Funnel = field(default_factory=Funnel)
    retention: Retention = field(default_factory=Retention)

    @property
    def growth(self) -> int:
        """درصد رشد نسبت به ماه قبل. ۰ اگر ماه قبل صفر بوده."""
        previous = self.last_month.total
        if not previous:
            return 0
        return round((self.this_month.total - previous) * 100 / previous)


def _month_start(offset: int = 0):
    """ابتدای ماه جاری یا ماه‌های قبل (بر مبنای ۳۰ روز)."""
    return utcnow() - timedelta(days=30 * (offset + 1))


def _aware(value):
    return value if value.tzinfo else value.replace(tzinfo=UTC)


async def revenue(since=None, until=None) -> Revenue:
    """درآمد تأییدشده در یک بازه."""
    async with get_session() as db:
        approved = [PaymentRequest.status == PaymentRequest.STATUS_APPROVED]
        if since is not None:
            approved.append(PaymentRequest.created_at >= since)
        if until is not None:
            approved.append(PaymentRequest.created_at < until)

        plans = await db.scalar(
            select(func.coalesce(func.sum(PaymentRequest.amount_toman), 0)).where(
                *approved, PaymentRequest.kind == PaymentRequest.KIND_PLAN
            )
        )
        credits = await db.scalar(
            select(func.coalesce(func.sum(PaymentRequest.amount_toman), 0)).where(
                *approved, PaymentRequest.kind == PaymentRequest.KIND_CREDIT
            )
        )
        payers = await db.scalar(
            select(func.count(func.distinct(PaymentRequest.user_id))).where(*approved)
        )

        sale_filters = []
        if since is not None:
            sale_filters.append(ResellerSale.created_at >= since)
        if until is not None:
            sale_filters.append(ResellerSale.created_at < until)
        reseller_total = await db.scalar(
            select(func.coalesce(func.sum(ResellerSale.paid_toman), 0)).where(
                *sale_filters
            )
            if sale_filters
            else select(func.coalesce(func.sum(ResellerSale.paid_toman), 0))
        )

    return Revenue(
        plans=int(plans or 0),
        credits=int(credits or 0),
        reseller=int(reseller_total or 0),
        payers=int(payers or 0),
    )


async def funnel(days: int = 0) -> Funnel:
    """قیف تبدیل. `days=0` یعنی از ابتدا."""
    since = utcnow() - timedelta(days=days) if days else None

    async with get_session() as db:
        user_filter = [User.created_at >= since] if since else []
        started = await db.scalar(select(func.count(User.id)).where(*user_filter))
        connected = await db.scalar(
            select(func.count(User.id)).where(
                User.session_enc.is_not(None), *user_filter
            )
        )

        task_users = select(func.distinct(Task.user_id))
        built = await db.scalar(
            select(func.count(User.id)).where(User.id.in_(task_users), *user_filter)
        )

        paid_users = select(func.distinct(PaymentRequest.user_id)).where(
            PaymentRequest.status == PaymentRequest.STATUS_APPROVED
        )
        paid = await db.scalar(
            select(func.count(User.id)).where(User.id.in_(paid_users), *user_filter)
        )

    return Funnel(
        started=int(started or 0),
        connected=int(connected or 0),
        built_task=int(built or 0),
        paid=int(paid or 0),
    )


async def retention() -> Retention:
    async with get_session() as db:
        rows = await db.execute(
            select(PaymentRequest.user_id, func.count(PaymentRequest.id))
            .where(
                PaymentRequest.status == PaymentRequest.STATUS_APPROVED,
                PaymentRequest.kind == PaymentRequest.KIND_PLAN,
            )
            .group_by(PaymentRequest.user_id)
        )
        counts = [int(count) for _uid, count in rows.all()]

        active = await db.scalar(
            select(func.count(func.distinct(Subscription.user_id))).where(
                Subscription.expires_at > utcnow()
            )
        )
        ever = await db.scalar(
            select(func.count(func.distinct(Subscription.user_id)))
        )

    return Retention(
        once=sum(1 for c in counts if c == 1),
        repeat=sum(1 for c in counts if c > 1),
        active_subs=int(active or 0),
        expired_users=max(0, int(ever or 0) - int(active or 0)),
    )


async def dashboard() -> Dashboard:
    this_start = _month_start(0)
    last_start = _month_start(1)
    return Dashboard(
        this_month=await revenue(since=this_start),
        last_month=await revenue(since=last_start, until=this_start),
        all_time=await revenue(),
        funnel=await funnel(),
        retention=await retention(),
    )


# ------------------------------------------------------- دلیل ریزش
REASONS: dict[str, str] = {
    "price": "💸 گران بود",
    "unused": "🤷 به کارم نیامد",
    "broken": "🐞 مشکل فنی داشتم",
    "paused": "⏸ فعلاً لازم ندارم",
    "other": "✍️ دلیل دیگر",
}


async def record_churn(user_id: int, sub_id: int, reason: str, note: str = "") -> bool:
    """پاسخ کاربر به «چرا تمدید نکردید؟». هر اشتراک فقط یک بار."""
    if reason not in REASONS:
        return False
    async with get_session() as db:
        db.add(
            ChurnFeedback(
                user_id=user_id, sub_id=sub_id, reason=reason, note=note[:400]
            )
        )
        try:
            await db.commit()
        except Exception:
            await db.rollback()      # قید یکتا: قبلاً جواب داده است
            return False
    return True


async def update_churn_note(user_id: int, sub_id: int, note: str) -> bool:
    """متن تشریحی را به پاسخِ قبلاً ثبت‌شده اضافه می‌کند."""
    async with get_session() as db:
        rows = await db.execute(
            select(ChurnFeedback).where(
                ChurnFeedback.user_id == user_id, ChurnFeedback.sub_id == sub_id
            )
        )
        row = rows.scalar_one_or_none()
        if row is None:
            return False
        row.note = note[:400]
        await db.commit()
    return True


async def churn_summary(days: int = 90) -> list[tuple[str, int]]:
    """(دلیل، تعداد) به ترتیب فراوانی."""
    since = utcnow() - timedelta(days=days)
    async with get_session() as db:
        rows = await db.execute(
            select(ChurnFeedback.reason, func.count(ChurnFeedback.id))
            .where(ChurnFeedback.created_at >= since)
            .group_by(ChurnFeedback.reason)
            .order_by(func.count(ChurnFeedback.id).desc())
        )
        return [(reason, int(count)) for reason, count in rows.all()]


async def churn_notes(limit: int = 10) -> list[ChurnFeedback]:
    """پاسخ‌های تشریحی — معمولاً مفیدترین بخش."""
    async with get_session() as db:
        rows = await db.execute(
            select(ChurnFeedback)
            .where(ChurnFeedback.note != "")
            .order_by(ChurnFeedback.id.desc())
            .limit(limit)
        )
        return list(rows.scalars())
