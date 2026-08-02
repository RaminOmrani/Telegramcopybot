"""مدیریت اشتراک کاربران."""
from __future__ import annotations

from datetime import UTC, timedelta

from sqlalchemy import select

from telkap.config import get_settings
from telkap.db import get_session, log_activity
from telkap.models import Subscription, User, utcnow
from telkap.plans import TRIAL, Plan, get_plan


async def active_subscription(user_id: int) -> Subscription | None:
    """آخرین اشتراک معتبر کاربر."""
    now = utcnow()
    async with get_session() as db:
        rows = await db.execute(
            select(Subscription)
            .where(Subscription.user_id == user_id, Subscription.expires_at > now)
            .order_by(Subscription.expires_at.desc())
            .limit(1)
        )
        return rows.scalar_one_or_none()


async def active_plan_for(user_id: int) -> Plan | None:
    sub = await active_subscription(user_id)
    if sub is None:
        return None
    return get_plan(sub.plan_code)


async def grant(
    user_id: int,
    plan_code: str,
    *,
    granted_by: int | None = None,
    note: str = "",
) -> Subscription | None:
    """اشتراک می‌دهد. اگر اشتراک فعالی باشد، از انتهای آن تمدید می‌شود."""
    plan = get_plan(plan_code)
    if plan is None:
        return None
    current = await active_subscription(user_id)
    start = current.expires_at if current else utcnow()
    if start.tzinfo is None:

        start = start.replace(tzinfo=UTC)
    async with get_session() as db:
        sub = Subscription(
            user_id=user_id,
            plan_code=plan.code,
            starts_at=utcnow(),
            expires_at=start + timedelta(days=plan.days),
            granted_by=granted_by,
            note=note[:255],
        )
        db.add(sub)
        await db.commit()
        await db.refresh(sub)
    await log_activity(
        user_id=user_id,
        event="subscription",
        detail=f"پلن {plan.code} تا {sub.expires_at:%Y-%m-%d} فعال شد",
    )
    return sub


async def grant_trial_if_new(user: User) -> Subscription | None:
    """به کاربر تازه‌وارد اشتراک آزمایشی می‌دهد (اگر در .env فعال باشد)."""
    days = get_settings().trial_days
    if days <= 0:
        return None
    async with get_session() as db:
        rows = await db.execute(
            select(Subscription.id).where(Subscription.user_id == user.id).limit(1)
        )
        if rows.scalar_one_or_none() is not None:
            return None
    return await grant(user.id, TRIAL.code, note="اشتراک آزمایشی خودکار")


async def remaining_days(user_id: int) -> int:
    sub = await active_subscription(user_id)
    if sub is None:
        return 0
    expires = sub.expires_at
    if expires.tzinfo is None:

        expires = expires.replace(tzinfo=UTC)
    delta = expires - utcnow()
    return max(0, delta.days + (1 if delta.seconds else 0))
