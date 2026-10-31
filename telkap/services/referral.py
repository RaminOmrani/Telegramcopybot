"""دعوت دوستان: بستن معرف، محاسبه‌ی پاداش و ضدتقلب.

قاعده‌ی بنیادی: <b>پاداش هنگام ثبت‌نام پرداخت نمی‌شود.</b> فقط وقتی خریدِ
کاربرِ دعوت‌شده به تأیید برسد. در بازاری که اکانت مجازی ارزان است، هر
پاداشی که به «ثبت‌نام» گره بخورد ظرف چند روز فارم می‌شود.

قفل‌های دیگر: معرف را نمی‌شود عوض کرد، کسی نمی‌تواند معرف خودش شود،
زنجیره‌ی دوطرفه بسته است، و سقف پاداش هم برای هر کاربر و هم برای هر ماه
قابل تعیین است.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import timedelta

from sqlalchemy import func, select

from telkap.db import get_session, log_activity
from telkap.models import (
    AppSetting,
    PaymentRequest,
    ReferralReward,
    User,
    WalletEntry,
    utcnow,
)
from telkap.plans import toman
from telkap.services import wallet

log = logging.getLogger(__name__)

SETTING_KEY = "referral"
LINK_PREFIX = "ref_"

DEFAULTS: dict[str, object] = {
    "enabled": True,
    "mode": "percent",              # percent | fixed
    "value": 15,                    # درصد، یا مبلغ ثابت به تومان
    "first_purchase_only": False,   # فقط اولین خرید هر دعوت‌شده پاداش دارد؟
    "min_purchase_toman": 100_000,  # زیر این مبلغ پاداشی تعلق نمی‌گیرد
    "max_per_referral_toman": 0,    # سقف پاداش از یک دعوت‌شده؛ ۰ = بی‌سقف
    "monthly_cap_toman": 0,         # سقف پاداش هر معرف در ۳۰ روز؛ ۰ = بی‌سقف
    "reward_on_credits": False,     # خرید اعتبار هم پاداش دارد؟
    "invitee_discount_percent": 0,  # تخفیف اولین خرید خودِ دعوت‌شده
}

# کلید → (عنوان، نوع، توضیح) برای ساخت خودکار پنل ادمین
FIELDS: tuple[tuple[str, str, str, str], ...] = (
    ("enabled", "🎁 وضعیت برنامه", "bool", "خاموش کنید تا هیچ پاداش تازه‌ای تعلق نگیرد."),
    ("mode", "🧮 نوع پاداش", "mode", "درصدی از مبلغ خرید، یا مبلغ ثابت."),
    ("value", "💰 مقدار پاداش", "int", "اگر درصدی است عدد ۰ تا ۱۰۰، وگرنه مبلغ به تومان."),
    ("first_purchase_only", "1️⃣ فقط اولین خرید", "bool",
     "روشن: فقط خرید اول هر دعوت‌شده. خاموش: همه‌ی خریدهایش."),
    ("min_purchase_toman", "⬇️ حداقل مبلغ خرید", "int",
     "زیر این مبلغ پاداشی تعلق نمی‌گیرد؛ جلوی خریدهای خرد برای فارم را می‌گیرد."),
    ("max_per_referral_toman", "🔒 سقف از هر دعوت‌شده", "int", "۰ یعنی بی‌سقف."),
    ("monthly_cap_toman", "📆 سقف ماهانه‌ی هر معرف", "int", "۰ یعنی بی‌سقف."),
    ("reward_on_credits", "🎫 پاداش روی خرید اعتبار", "bool",
     "معمولاً خاموش؛ حاشیه‌ی سود اعتبار کمتر است."),
    ("invitee_discount_percent", "🎉 تخفیف خودِ دعوت‌شده", "int",
     "درصد تخفیف اولین خرید کسی که با لینک آمده. ۰ یعنی بدون تخفیف."),
)


@dataclass(slots=True)
class Stats:
    """آمار دعوت یک کاربر، برای نمایش در صفحه‌ی خودش."""

    invited: int = 0
    buyers: int = 0
    earned: int = 0

    @property
    def conversion(self) -> int:
        return round(self.buyers * 100 / self.invited) if self.invited else 0


# ------------------------------------------------------------ تنظیمات
_cache: dict[str, object] | None = None


async def settings() -> dict:
    """تنظیمات زنده‌ی برنامه‌ی دعوت (با پیش‌فرض‌ها پرشده)."""
    global _cache
    if _cache is not None:
        return dict(_cache)
    stored: dict = {}
    try:
        async with get_session() as db:
            row = await db.get(AppSetting, SETTING_KEY)
            if row is not None and isinstance(row.value, dict):
                stored = row.value
    except Exception:
        log.exception("خواندن تنظیمات دعوت ناموفق بود؛ پیش‌فرض‌ها به کار می‌روند")
    merged = {**DEFAULTS, **stored}
    _cache = merged
    return dict(merged)


async def set_value(key: str, value, *, admin_id: int | None = None) -> dict | None:
    """یک تنظیم را عوض می‌کند و تنظیمات تازه را برمی‌گرداند."""
    global _cache
    if key not in DEFAULTS:
        return None
    current = await settings()
    default = DEFAULTS[key]

    if isinstance(default, bool):
        clean = bool(value)
    elif key == "mode":
        if value not in {"percent", "fixed"}:
            return None
        clean = value
    else:
        try:
            clean = max(0, int(value))
        except (TypeError, ValueError):
            return None
        if key in {"value", "invitee_discount_percent"} and current.get("mode") == "percent":
            clean = min(clean, 100)

    current[key] = clean
    async with get_session() as db:
        row = await db.get(AppSetting, SETTING_KEY)
        if row is None:
            row = AppSetting(key=SETTING_KEY)
            db.add(row)
        row.value = current
        row.updated_by = admin_id
        row.updated_at = utcnow()
        await db.commit()

    _cache = dict(current)
    await log_activity(user_id=admin_id, event="referral_setting", detail=f"{key} = {clean}")
    return dict(current)


def invalidate() -> None:
    global _cache
    _cache = None


# --------------------------------------------------------- بستن معرف
def parse_payload(payload: str) -> int | None:
    """`ref_12345` را به شناسه‌ی معرف تبدیل می‌کند."""
    raw = (payload or "").strip()
    if not raw.startswith(LINK_PREFIX):
        return None
    digits = raw[len(LINK_PREFIX):]
    return int(digits) if digits.isdigit() else None


def link_for(bot_username: str, user_id: int) -> str:
    return f"https://t.me/{bot_username.lstrip('@')}?start={LINK_PREFIX}{user_id}"


async def bind(user_id: int, referrer_id: int) -> bool:
    """معرف را ثبت می‌کند؛ فقط اگر هنوز معرفی نداشته باشد.

    رد می‌شود اگر: خودش باشد، معرف وجود نداشته باشد، قبلاً معرف داشته
    باشد، یا معرف خودش دعوت‌شده‌ی همین کاربر باشد (زنجیره‌ی دوطرفه).
    """
    if user_id == referrer_id:
        return False

    async with get_session() as db:
        user = await db.get(User, user_id)
        referrer = await db.get(User, referrer_id)
        if user is None or referrer is None:
            return False
        if user.referred_by is not None:
            return False                     # هرگز بازنویسی نمی‌شود
        if referrer.referred_by == user_id:
            return False                     # حلقه‌ی دوطرفه
        user.referred_by = referrer_id
        await db.commit()

    await log_activity(
        user_id=user_id, event="referral_bind", detail=f"معرف: {referrer_id}"
    )
    return True


# ------------------------------------------------------ محاسبه‌ی پاداش
async def _paid_last_30_days(referrer_id: int) -> int:
    since = utcnow() - timedelta(days=30)
    async with get_session() as db:
        total = await db.scalar(
            select(func.coalesce(func.sum(ReferralReward.amount_toman), 0)).where(
                ReferralReward.referrer_id == referrer_id,
                ReferralReward.status == ReferralReward.STATUS_PAID,
                ReferralReward.created_at >= since,
            )
        )
    return int(total or 0)


async def _paid_from(referrer_id: int, referred_id: int) -> int:
    async with get_session() as db:
        total = await db.scalar(
            select(func.coalesce(func.sum(ReferralReward.amount_toman), 0)).where(
                ReferralReward.referrer_id == referrer_id,
                ReferralReward.referred_id == referred_id,
                ReferralReward.status == ReferralReward.STATUS_PAID,
            )
        )
    return int(total or 0)


async def _already_rewarded(referred_id: int) -> bool:
    async with get_session() as db:
        row = await db.scalar(
            select(ReferralReward.id).where(
                ReferralReward.referred_id == referred_id,
                ReferralReward.status == ReferralReward.STATUS_PAID,
            ).limit(1)
        )
    return row is not None


async def on_payment_approved(request: PaymentRequest) -> ReferralReward | None:
    """پس از تأیید یک رسید، پاداش معرف را حساب و واریز می‌کند.

    خروجی None یعنی پاداشی تعلق نگرفت (معرفی نبود، یا شرطی برقرار نشد).
    """
    cfg = await settings()
    if not cfg.get("enabled"):
        return None

    async with get_session() as db:
        user = await db.get(User, request.user_id)
        referrer_id = user.referred_by if user is not None else None
    if not referrer_id:
        return None

    basis = int(request.amount_toman or 0)
    is_credit = request.kind == PaymentRequest.KIND_CREDIT

    def void(note: str) -> ReferralReward:
        return ReferralReward(
            referrer_id=referrer_id,
            referred_id=request.user_id,
            payment_id=request.id,
            basis_toman=basis,
            amount_toman=0,
            status=ReferralReward.STATUS_VOID,
            note=note[:255],
        )

    # --- شرط‌ها؛ هر رد شدن هم ثبت می‌شود تا بعداً قابل توضیح باشد ---
    if is_credit and not cfg.get("reward_on_credits"):
        reward = void("خرید اعتبار پاداش ندارد")
    elif basis < int(cfg.get("min_purchase_toman") or 0):
        reward = void("زیر حداقل مبلغ خرید")
    elif cfg.get("first_purchase_only") and await _already_rewarded(request.user_id):
        reward = void("فقط اولین خرید پاداش دارد")
    else:
        amount = (
            basis * int(cfg.get("value") or 0) // 100
            if cfg.get("mode") == "percent"
            else int(cfg.get("value") or 0)
        )
        per_cap = int(cfg.get("max_per_referral_toman") or 0)
        if per_cap:
            amount = min(amount, max(0, per_cap - await _paid_from(referrer_id, request.user_id)))
        month_cap = int(cfg.get("monthly_cap_toman") or 0)
        if month_cap:
            amount = min(amount, max(0, month_cap - await _paid_last_30_days(referrer_id)))
        reward = (
            void("سقف پاداش پر شده است")
            if amount <= 0
            else ReferralReward(
                referrer_id=referrer_id,
                referred_id=request.user_id,
                payment_id=request.id,
                basis_toman=basis,
                amount_toman=amount,
                status=ReferralReward.STATUS_PAID,
            )
        )

    async with get_session() as db:
        db.add(reward)
        try:
            await db.commit()
            await db.refresh(reward)
        except Exception:
            # قید یکتای payment_id: این رسید قبلاً حساب شده
            await db.rollback()
            log.info("پاداش دعوت برای رسید %s قبلاً ثبت شده بود", request.id)
            return None

    if reward.status != ReferralReward.STATUS_PAID:
        return reward

    await wallet.credit(
        referrer_id,
        reward.amount_toman,
        reason=WalletEntry.REASON_REFERRAL,
        note=f"دعوت کاربر {request.user_id} — رسید #{request.id}",
        ref_id=reward.id,
    )
    await log_activity(
        user_id=referrer_id,
        event="referral_reward",
        detail=f"{toman(reward.amount_toman)} بابت خرید کاربر {request.user_id}",
    )
    return reward


# ------------------------------------------------------------- آمار
async def stats(user_id: int) -> Stats:
    async with get_session() as db:
        invited = await db.scalar(
            select(func.count(User.id)).where(User.referred_by == user_id)
        )
        buyers = await db.scalar(
            select(func.count(func.distinct(ReferralReward.referred_id))).where(
                ReferralReward.referrer_id == user_id,
                ReferralReward.status == ReferralReward.STATUS_PAID,
            )
        )
        earned = await db.scalar(
            select(func.coalesce(func.sum(ReferralReward.amount_toman), 0)).where(
                ReferralReward.referrer_id == user_id,
                ReferralReward.status == ReferralReward.STATUS_PAID,
            )
        )
    return Stats(invited=int(invited or 0), buyers=int(buyers or 0), earned=int(earned or 0))


def describe(cfg: dict) -> str:
    """یک جمله که به کاربر می‌گوید چقدر گیرش می‌آید."""
    if not cfg.get("enabled"):
        return "برنامه‌ی دعوت فعلاً غیرفعال است."
    value = int(cfg.get("value") or 0)
    if cfg.get("mode") == "percent":
        return f"{value}٪ از هر خرید کسی که دعوت می‌کنید"
    return f"{toman(value)} بابت هر خرید کسی که دعوت می‌کنید"
