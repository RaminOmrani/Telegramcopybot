"""تمدید خودکار اشتراک از موجودی کیف پول.

بیشترین ریزش مشترکان عمدی نیست — کسی که راضی است هم ممکن است یادش برود
تمدید کند و کارهایش بخوابد. این ماژول همان را می‌بندد.

سه قاعده‌ی محافظه‌کارانه:
۱. پیش‌فرض خاموش است. برداشت خودکار پول باید انتخاب صریح کاربر باشد.
۲. فقط از کیف پول برداشت می‌شود؛ هیچ‌جای دیگری به آن دسترسی ندارد.
۳. اگر موجودی کافی نباشد هیچ برداشت جزئی انجام نمی‌شود — فقط یک بار به
   کاربر خبر می‌رود که شارژ کند.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import UTC

from sqlalchemy import select

from telkap.db import get_session, log_activity
from telkap.models import ReminderState, Subscription, User, WalletEntry, utcnow
from telkap.plans import get_plan, toman
from telkap.services import subscription, wallet

log = logging.getLogger(__name__)

CHECK_INTERVAL = 3600            # هر ساعت
# چند ساعت مانده به انقضا تمدید انجام شود
RENEW_WITHIN_HOURS = 24
KIND_RENEWED = "auto_renewed"
KIND_SHORT = "auto_renew_short"


def _aware(value):
    """SQLite تاریخ را بدون منطقه‌ی زمانی برمی‌گرداند؛ UTC فرض می‌شود."""
    return value if value.tzinfo else value.replace(tzinfo=UTC)


async def _already(user_id: int, kind: str, sub_id: int) -> bool:
    async with get_session() as db:
        row = await db.scalar(
            select(ReminderState.id).where(
                ReminderState.user_id == user_id,
                ReminderState.kind == kind,
                ReminderState.sub_id == sub_id,
            )
        )
    return row is not None


async def _mark(user_id: int, kind: str, sub_id: int) -> None:
    async with get_session() as db:
        db.add(ReminderState(user_id=user_id, kind=kind, sub_id=sub_id))
        try:
            await db.commit()
        except Exception:
            await db.rollback()


async def is_on(user_id: int) -> bool:
    async with get_session() as db:
        return bool(await db.scalar(select(User.auto_renew).where(User.id == user_id)))


async def toggle(user_id: int) -> bool | None:
    async with get_session() as db:
        user = await db.get(User, user_id)
        if user is None:
            return None
        user.auto_renew = not user.auto_renew
        await db.commit()
        state = user.auto_renew
    await log_activity(
        user_id=user_id,
        event="auto_renew",
        detail="روشن شد" if state else "خاموش شد",
    )
    return state


async def try_renew(sub: Subscription, notify=None) -> bool:
    """تلاش برای تمدید یک اشتراک. True یعنی تمدید شد."""
    user_id = sub.user_id
    plan = get_plan(sub.plan_code)
    if plan is None or plan.price_toman <= 0:
        return False
    if await _already(user_id, KIND_RENEWED, sub.id):
        return False

    price = plan.price_toman
    balance = await wallet.balance(user_id)
    if balance < price:
        if not await _already(user_id, KIND_SHORT, sub.id):
            await _mark(user_id, KIND_SHORT, sub.id)
            if notify is not None:
                await notify(
                    user_id,
                    "⏳ <b>تمدید خودکار انجام نشد</b>\n\n"
                    f"اشتراک <b>{plan.title}</b> شما به‌زودی تمام می‌شود و "
                    "موجودی کیف پولتان برای تمدید کافی نیست.\n\n"
                    f"لازم: <b>{toman(price)}</b>\n"
                    f"موجودی: <b>{toman(balance)}</b>\n\n"
                    "کیف پولتان را شارژ کنید یا از «💳 خرید اشتراک» دستی "
                    "تمدید کنید.",
                )
        return False

    if await wallet.debit(
        user_id,
        price,
        reason=WalletEntry.REASON_PURCHASE,
        note=f"تمدید خودکار {plan.title}",
    ) is None:
        return False

    fresh = await subscription.grant(
        user_id, sub.plan_code, note="تمدید خودکار از کیف پول"
    )
    if fresh is None:
        # برداشت انجام شده ولی اشتراکی نگرفت؛ همان لحظه اصلاح می‌شود
        await wallet.credit(
            user_id,
            price,
            reason=WalletEntry.REASON_REFUND,
            note="تمدید خودکار ناموفق بود",
        )
        return False

    await _mark(user_id, KIND_RENEWED, sub.id)
    if notify is not None:
        left = await wallet.balance(user_id)
        await notify(
            user_id,
            f"🔄 <b>اشتراک شما خودکار تمدید شد.</b>\n\n"
            f"طرح: <b>{plan.title}</b>\n"
            f"پرداخت از کیف پول: <b>{toman(price)}</b>\n"
            f"موجودی باقی‌مانده: <b>{toman(left)}</b>\n\n"
            "<i>برای خاموش کردن تمدید خودکار، «👤 حساب کاربری» را ببینید.</i>",
        )
    await log_activity(
        user_id=user_id, event="auto_renew", detail=f"{plan.title} — {toman(price)}"
    )
    return True


async def run_once(notify=None) -> int:
    """اشتراک‌های نزدیک به انقضای کاربرانِ تمدید-خودکار را تمدید می‌کند."""
    now = utcnow()
    async with get_session() as db:
        rows = await db.execute(
            select(Subscription)
            .join(User, User.id == Subscription.user_id)
            .where(
                Subscription.expires_at > now,
                User.auto_renew.is_(True),
                User.is_banned.is_(False),
            )
        )
        subs = list(rows.scalars())

    # فقط دورترین اشتراک هر کاربر ملاک است
    latest: dict[int, Subscription] = {}
    for sub in subs:
        current = latest.get(sub.user_id)
        if current is None or _aware(sub.expires_at) > _aware(current.expires_at):
            latest[sub.user_id] = sub

    renewed = 0
    for sub in latest.values():
        hours_left = (_aware(sub.expires_at) - now).total_seconds() / 3600
        if hours_left > RENEW_WITHIN_HOURS:
            continue
        try:
            if await try_renew(sub, notify):
                renewed += 1
        except Exception:
            log.exception("تمدید خودکار برای کاربر %s ناموفق بود", sub.user_id)
    return renewed


async def run_forever(notify=None) -> None:
    while True:
        try:
            await asyncio.sleep(CHECK_INTERVAL)
            await run_once(notify)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("چرخه‌ی تمدید خودکار با خطا مواجه شد")
