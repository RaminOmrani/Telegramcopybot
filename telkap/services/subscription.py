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


async def _user_limits(user_id: int) -> dict:
    """سقف‌های اختصاصی‌ای که ادمین برای این کاربر گذاشته است."""
    async with get_session() as db:
        user = await db.get(User, user_id)
        return dict(user.limits or {}) if user is not None else {}


async def effective_plan(plan_code: str, user_id: int) -> Plan | None:
    """طرح، پس از اعمال تغییرهای اختصاصی همان کاربر."""
    from telkap.services import limits

    base = get_plan(plan_code)
    if base is None:
        return None
    return limits.apply(base, await _user_limits(user_id))


async def active_plan_for(user_id: int) -> Plan | None:
    sub = await active_subscription(user_id)
    if sub is None:
        return None
    return await effective_plan(sub.plan_code, user_id)


async def active_entitlement(user_id: int) -> tuple[Plan | None, int | None]:
    """طرح فعال و شناسه‌ی اشتراکش.

    سهمیه‌ها به اشتراک گره خورده‌اند، پس مصرف‌کننده‌ها به شناسه هم نیاز دارند.
    """
    sub = await active_subscription(user_id)
    if sub is None:
        return None, None
    return await effective_plan(sub.plan_code, user_id), sub.id


async def remaining_value(user_id: int) -> int:
    """ارزش ریالی روزهای باقی‌مانده‌ی اشتراک فعلی.

    اگر کسی روز پنجم از یک طرح ۳۰ روزه‌ی ۴۲۹ هزار تومانی بخواهد ارتقا
    بدهد، منصفانه نیست پول ۲۵ روز استفاده‌نشده‌اش بسوزد.
    """
    sub = await active_subscription(user_id)
    if sub is None:
        return 0
    plan = get_plan(sub.plan_code)
    if plan is None or plan.price_toman <= 0 or plan.days <= 0:
        return 0

    expires = sub.expires_at
    if expires.tzinfo is None:
        expires = expires.replace(tzinfo=UTC)
    left_days = max(0, (expires - utcnow()).days)
    # سقف روی مدت خود طرح، تا تمدیدهای انباشته ارزش غیرواقعی نسازند
    left_days = min(left_days, plan.days)
    return (plan.price_toman * left_days) // plan.days


async def upgrade_quote(user_id: int, plan_code: str) -> tuple[int, bool]:
    """(اعتبار قابل کسر، آیا این خرید ارتقا است).

    ارتقا فقط وقتی معنی دارد که طرح تازه گران‌تر از طرح فعلی باشد؛ در
    غیر این صورت خرید مثل همیشه از انتهای اشتراک فعلی تمدید می‌شود.
    """
    target = get_plan(plan_code)
    if target is None:
        return 0, False
    sub = await active_subscription(user_id)
    if sub is None:
        return 0, False
    current = get_plan(sub.plan_code)
    if current is None or target.price_toman <= current.price_toman:
        return 0, False

    credit = await remaining_value(user_id)
    # اعتبار هرگز از قیمت طرح تازه بیشتر نمی‌شود
    return min(credit, target.price_toman), credit > 0


async def grant(
    user_id: int,
    plan_code: str,
    *,
    granted_by: int | None = None,
    note: str = "",
    replace: bool = False,
) -> Subscription | None:
    """اشتراک می‌دهد. اگر اشتراک فعالی باشد، از انتهای آن تمدید می‌شود.

    `replace=True` برای ارتقا: طرح تازه همین حالا شروع می‌شود و اشتراک
    قبلی بسته می‌گردد (ارزش باقی‌مانده‌اش قبلاً از قیمت کسر شده است).
    """
    plan = get_plan(plan_code)
    if plan is None:
        return None
    current = await active_subscription(user_id)
    start = utcnow() if (replace or current is None) else current.expires_at
    if start.tzinfo is None:
        start = start.replace(tzinfo=UTC)

    if replace and current is not None:
        # وگرنه اشتراک قدیمی که انقضای دورتری دارد همچنان برنده می‌شود
        async with get_session() as db:
            old = await db.get(Subscription, current.id)
            if old is not None:
                old.expires_at = utcnow()
                await db.commit()

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
    from telkap.services import cache

    cache.invalidate_user(user_id)
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


async def adjust_days(user_id: int, days: int, *, admin_id: int | None = None):
    """روز به اشتراک فعال اضافه یا از آن کم می‌کند.

    عدد منفی یعنی کم کردن. اگر اشتراک فعالی نباشد، None برمی‌گردد —
    در آن حالت ادمین باید اول یک پلن بدهد تا سطح دسترسی مشخص شود.
    """
    sub = await active_subscription(user_id)
    if sub is None:
        return None

    async with get_session() as db:
        row = await db.get(Subscription, sub.id)
        if row is None:
            return None
        expires = row.expires_at
        if expires.tzinfo is None:
            expires = expires.replace(tzinfo=UTC)
        new_expiry = expires + timedelta(days=days)
        # کم کردن بیش از حد، اشتراک را همین حالا تمام می‌کند
        row.expires_at = max(new_expiry, utcnow())
        await db.commit()
        await db.refresh(row)

    from telkap.services import cache

    cache.invalidate_user(user_id)
    await log_activity(
        user_id=user_id,
        event="subscription_adjust",
        detail=f"{days:+d} روز توسط ادمین {admin_id or '—'}",
    )
    return row


async def revoke(user_id: int, *, admin_id: int | None = None) -> int:
    """همه‌ی اشتراک‌های فعال کاربر را همین حالا تمام می‌کند."""
    now = utcnow()
    async with get_session() as db:
        rows = await db.execute(
            select(Subscription).where(
                Subscription.user_id == user_id, Subscription.expires_at > now
            )
        )
        subs = list(rows.scalars())
        for sub in subs:
            sub.expires_at = now
        await db.commit()

    from telkap.services import cache

    cache.invalidate_user(user_id)
    if subs:
        await log_activity(
            user_id=user_id,
            event="subscription_revoke",
            detail=f"{len(subs)} اشتراک توسط ادمین {admin_id or '—'} لغو شد",
            level="warning",
        )
    return len(subs)
