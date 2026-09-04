"""یادآوری انقضای اشتراک، پیش از آنکه کارهای کاربر بخوابند."""
from __future__ import annotations

import asyncio
import logging
from datetime import UTC

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from telkap.db import get_session
from telkap.models import ReminderState, Subscription, utcnow
from telkap.plans import get_plan, toman
from telkap.texts import fa_num

log = logging.getLogger(__name__)

CHECK_INTERVAL = 3600  # هر ساعت
# روزهای باقی‌مانده‌ای که برایشان یادآوری می‌رود
THRESHOLDS = (3, 1)


def _aware(value):
    return value if value.tzinfo else value.replace(tzinfo=UTC)


async def _already_sent(user_id: int, kind: str, sub_id: int) -> bool:
    async with get_session() as db:
        row = await db.execute(
            select(ReminderState.id).where(
                ReminderState.user_id == user_id,
                ReminderState.kind == kind,
                ReminderState.sub_id == sub_id,
            )
        )
        return row.scalar_one_or_none() is not None


async def _mark_sent(user_id: int, kind: str, sub_id: int) -> None:
    async with get_session() as db:
        db.add(ReminderState(user_id=user_id, kind=kind, sub_id=sub_id))
        try:
            await db.commit()
        except IntegrityError:
            await db.rollback()


WINBACK_KEY = "winback_coupon"


async def winback_note() -> str:
    """اگر ادمین کد تخفیف بازگشت گذاشته باشد، به یادآوری پیوست می‌شود.

    یادآوری خالی فقط می‌گوید «تمام شد»؛ یک کد تخفیف همان پیام را به یک
    دلیل برای برگشتن تبدیل می‌کند.
    """
    from telkap.models import AppSetting
    from telkap.services import coupons

    try:
        async with get_session() as db:
            row = await db.get(AppSetting, WINBACK_KEY)
        code = (row.value or {}).get("code", "") if row is not None else ""
        if not code:
            return ""
        coupon = await coupons.find(code)
        if coupon is None or not coupon.enabled:
            return ""
        offer = (
            f"{coupon.value}٪"
            if coupon.kind == coupon.KIND_PERCENT
            else toman(coupon.value)
        )
        return (
            f"\n\n🎟 <b>کد تخفیف برای شما:</b> <code>{coupon.code}</code>\n"
            f"با این کد {offer} تخفیف می‌گیرید."
        )
    except Exception:
        log.debug("خواندن کد تخفیف بازگشت ناموفق بود", exc_info=True)
        return ""


async def set_winback(code: str, *, admin_id: int | None = None) -> str:
    """کد تخفیفی که به یادآوری‌های انقضا پیوست می‌شود. خالی = هیچ."""
    from telkap.models import AppSetting, utcnow
    from telkap.services import coupons

    cleaned = coupons.normalize(code)
    async with get_session() as db:
        row = await db.get(AppSetting, WINBACK_KEY)
        if row is None:
            row = AppSetting(key=WINBACK_KEY)
            db.add(row)
        row.value = {"code": cleaned}
        row.updated_by = admin_id
        row.updated_at = utcnow()
        await db.commit()
    return cleaned


async def current_winback() -> str:
    from telkap.models import AppSetting

    async with get_session() as db:
        row = await db.get(AppSetting, WINBACK_KEY)
    return (row.value or {}).get("code", "") if row is not None else ""


async def run_once(notify) -> int:
    """اشتراک‌های نزدیک به انقضا را پیدا و یادآوری می‌فرستد."""
    now = utcnow()
    async with get_session() as db:
        rows = await db.execute(
            select(Subscription).where(Subscription.expires_at > now)
        )
        subs = list(rows.scalars())

    # فقط آخرین اشتراک هر کاربر ملاک است
    latest: dict[int, Subscription] = {}
    for sub in subs:
        current = latest.get(sub.user_id)
        if current is None or _aware(sub.expires_at) > _aware(current.expires_at):
            latest[sub.user_id] = sub

    sent = 0
    for user_id, sub in latest.items():
        remaining = _aware(sub.expires_at) - now
        days_left = remaining.days
        for threshold in THRESHOLDS:
            if days_left != threshold - 1:
                continue
            kind = f"expiry_{threshold}"
            if await _already_sent(user_id, kind, sub.id):
                continue
            plan = get_plan(sub.plan_code)
            hours = int(remaining.total_seconds() // 3600)
            try:
                await notify(
                    user_id,
                    f"⏳ اشتراک <b>{plan.title if plan else sub.plan_code}</b> شما "
                    f"تا حدود {fa_num(hours)} ساعت دیگر تمام می‌شود.\n\n"
                    "پس از انقضا، کارهای کپی خودکار متوقف می‌شوند.\n"
                    "برای تمدید، «💳 خرید اشتراک» را بزنید."
                    + await winback_note(),
                )
                sent += 1
            except Exception:
                log.debug("ارسال یادآوری به %s ناموفق بود", user_id, exc_info=True)
            await _mark_sent(user_id, kind, sub.id)
            break
    return sent


# ------------------------------------------------- پرسش پس از انقضا
# پنجره‌ی پرسیدن «چرا تمدید نکردید؟». زودتر از این، کاربر شاید همان
# ساعت تمدید کند؛ دیرتر، دیگر یادش نیست چرا رفته.
CHURN_AFTER_HOURS = 6
CHURN_BEFORE_HOURS = 96


def _churn_markup(sub_id: int):
    from aiogram.types import InlineKeyboardButton
    from aiogram.utils.keyboard import InlineKeyboardBuilder

    from telkap.services.analytics import REASONS

    kb = InlineKeyboardBuilder()
    for key, label in REASONS.items():
        kb.row(
            InlineKeyboardButton(text=label, callback_data=f"churn:{sub_id}:{key}")
        )
    return kb.as_markup()


async def ask_churn(notify) -> int:
    """از کاربرانی که اشتراکشان تازه تمام شده می‌پرسد چرا تمدید نکردند.

    فقط از کسانی که اشتراک فعال دیگری ندارند؛ وگرنه از کسی که همان روز
    تمدید کرده می‌پرسیدیم چرا نرفته — که هم بی‌معنی است و هم آزاردهنده.
    """
    now = utcnow()
    async with get_session() as db:
        rows = await db.execute(select(Subscription))
        subs = list(rows.scalars())

    active_users = {
        sub.user_id for sub in subs if _aware(sub.expires_at) > now
    }
    asked = 0
    for sub in subs:
        if sub.user_id in active_users:
            continue
        gone = (now - _aware(sub.expires_at)).total_seconds() / 3600
        if not CHURN_AFTER_HOURS <= gone <= CHURN_BEFORE_HOURS:
            continue
        if await _already_sent(sub.user_id, "churn_ask", sub.id):
            continue
        try:
            await notify(
                sub.user_id,
                "🙏 <b>یک سؤال کوتاه</b>\n\n"
                "اشتراک شما تمام شد و تمدید نکردید. اگر یک دکمه بزنید و "
                "بگویید چرا، خیلی کمک می‌کند تا ربات را بهتر کنیم.",
                _churn_markup(sub.id),
            )
            asked += 1
        except Exception:
            log.debug("پرسش ریزش به %s نرسید", sub.user_id, exc_info=True)
        await _mark_sent(sub.user_id, "churn_ask", sub.id)
    return asked


async def run_forever(notify) -> None:
    while True:
        try:
            await asyncio.sleep(CHECK_INTERVAL)
            count = await run_once(notify)
            if count:
                log.info("%d یادآوری انقضا ارسال شد", count)
            asked = await ask_churn(notify)
            if asked:
                log.info("%d پرسش دلیل ریزش ارسال شد", asked)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("چرخه‌ی یادآوری با خطا مواجه شد")
