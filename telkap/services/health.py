"""تشخیص وضعیت اکانت کاربری و اطلاع‌رسانی به موقع.

تلگرام اکانت‌هایی را که رفتار خودکار دارند محدود می‌کند. تا امروز این
خطاها یا اصلاً گرفته نمی‌شدند یا در لاگ گم می‌شدند و کاربر فقط می‌دید
«ربات کار نمی‌کند». اینجا هر خطا به یک وضعیت روشن ترجمه می‌شود، روی
کاربر ثبت می‌گردد، و <b>یک بار</b> — نه در هر پست — به او خبر داده می‌شود.

سطح‌بندی مهم است: `FloodWait` عادی است و خودبه‌خود رفع می‌شود، ولی
`PeerFlood` و بن شدن یعنی باید کارها متوقف شوند تا اوضاع بدتر نشود.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

from sqlalchemy import func, select
from telethon.errors import (
    AuthKeyUnregisteredError,
    FloodWaitError,
    PeerFloodError,
    PhoneNumberBannedError,
    SessionExpiredError,
    SessionRevokedError,
    SlowModeWaitError,
    UserDeactivatedBanError,
    UserDeactivatedError,
    UserRestrictedError,
)

from telkap.db import get_session, log_activity
from telkap.models import User, utcnow

log = logging.getLogger(__name__)

STATE_OK = "ok"
STATE_FLOOD = "flood"            # موقت، خودبه‌خود رفع می‌شود
STATE_PEER_FLOOD = "peer_flood"  # محدودیت اسپم، چند روز
STATE_BANNED = "banned"          # اکانت بن یا حذف شده
STATE_REVOKED = "revoked"        # سشن باطل شده (خروج از دستگاه‌ها)
STATE_ERROR = "error"            # خطای ناشناخته‌ی پایدار

STATE_LABELS: dict[str, str] = {
    STATE_OK: "✅ سالم",
    STATE_FLOOD: "⏳ محدودیت موقت",
    STATE_PEER_FLOOD: "🚧 محدودیت اسپم",
    STATE_BANNED: "⛔️ بن شده",
    STATE_REVOKED: "🔌 سشن باطل",
    STATE_ERROR: "⚠️ خطا",
}

# وضعیت‌هایی که باید کارهای کاربر را متوقف کنند
FATAL_STATES = frozenset({STATE_PEER_FLOOD, STATE_BANNED, STATE_REVOKED})


@dataclass(frozen=True, slots=True)
class Diagnosis:
    """ترجمه‌ی یک خطای تلگرام به وضعیتی که هم ادمین و هم کاربر بفهمند."""

    state: str
    title: str          # کوتاه، برای پنل ادمین
    message: str        # پیام کامل فارسی برای خود کاربر
    retry_after: int = 0

    @property
    def fatal(self) -> bool:
        return self.state in FATAL_STATES


_HEALTHY = Diagnosis(STATE_OK, "سالم", "")


def _fa_duration(seconds: int) -> str:
    if seconds >= 3600:
        return f"حدود {seconds // 3600} ساعت"
    if seconds >= 60:
        return f"حدود {seconds // 60} دقیقه"
    return f"{seconds} ثانیه"


def classify(exc: BaseException) -> Diagnosis:
    """خطای Telethon را به یک تشخیص روشن تبدیل می‌کند."""
    if isinstance(exc, SlowModeWaitError | FloodWaitError):
        seconds = int(getattr(exc, "seconds", 0) or 0)
        return Diagnosis(
            STATE_FLOOD,
            f"صبر {seconds} ثانیه",
            "⏳ تلگرام موقتاً سرعت اکانت شما را کم کرده است.\n\n"
            f"ارسال بعدی بعد از {_fa_duration(seconds)} ادامه پیدا می‌کند. "
            "کاری لازم نیست بکنید؛ این حالت عادی است و خودبه‌خود رفع می‌شود.",
            retry_after=seconds,
        )

    if isinstance(exc, PeerFloodError):
        return Diagnosis(
            STATE_PEER_FLOOD,
            "محدودیت اسپم",
            "🚧 <b>اکانت شما موقتاً محدود شده است.</b>\n\n"
            "تلگرام تشخیص داده که این اکانت در زمان کوتاه پیام زیادی "
            "فرستاده و فعلاً اجازه‌ی ارسال به مقصدهای تازه را نمی‌دهد.\n\n"
            "<b>کارهای شما موقتاً متوقف شدند</b> تا وضعیت بدتر نشود.\n\n"
            "چه کنید؟\n"
            "• چند روز صبر کنید؛ معمولاً خودش برداشته می‌شود\n"
            "• به <code>@SpamBot</code> پیام بدهید و وضعیت را بپرسید\n"
            "• بعد از رفع، تعداد مقصدها یا سرعت ارسال را کمتر کنید",
        )

    if isinstance(exc, UserDeactivatedBanError | UserDeactivatedError | PhoneNumberBannedError):
        return Diagnosis(
            STATE_BANNED,
            "بن شده",
            "⛔️ <b>اکانت تلگرام شما مسدود یا حذف شده است.</b>\n\n"
            "متأسفانه از این اکانت دیگر نمی‌شود استفاده کرد و همه‌ی کارهای "
            "شما متوقف شدند.\n\n"
            "چه کنید؟\n"
            "• به <code>@SpamBot</code> پیام بدهید و درخواست بررسی بدهید\n"
            "• یا اکانت دیگری به ربات وصل کنید",
        )

    if isinstance(exc, UserRestrictedError):
        return Diagnosis(
            STATE_PEER_FLOOD,
            "محدود شده",
            "🚧 <b>اکانت شما محدود شده است.</b>\n\n"
            "تلگرام امکان ارسال را از این اکانت گرفته و کارهایتان متوقف "
            "شدند. برای بررسی به <code>@SpamBot</code> پیام بدهید.",
        )

    if isinstance(exc, AuthKeyUnregisteredError | SessionRevokedError | SessionExpiredError):
        return Diagnosis(
            STATE_REVOKED,
            "سشن باطل",
            "🔌 <b>اتصال اکانت شما قطع شد.</b>\n\n"
            "این معمولاً وقتی پیش می‌آید که از «دستگاه‌های متصل» در تلگرام، "
            "نشست ربات را خارج کرده باشید — یا رمز دو مرحله‌ای را عوض "
            "کرده‌اید.\n\n"
            "برای ادامه، از «👤 حساب کاربری» ← «🔐 اتصال اکانت» دوباره وارد شوید.",
        )

    return _HEALTHY


# ------------------------------------------------------------- ثبت وضعیت
async def record(user_id: int, diagnosis: Diagnosis, *, notifier=None) -> bool:
    """وضعیت را ذخیره می‌کند و در صورت تغییر، یک بار به کاربر خبر می‌دهد.

    خروجی True یعنی وضعیت عوض شد (و پیام رفت). اگر کاربر قبلاً در همان
    وضعیت بوده، دوباره پیام نمی‌گیرد — وگرنه در هر پست یک هشدار می‌آمد.
    """
    if diagnosis.state == STATE_OK:
        return False

    async with get_session() as db:
        user = await db.get(User, user_id)
        if user is None:
            return False
        changed = user.account_state != diagnosis.state
        user.account_state = diagnosis.state
        user.account_note = diagnosis.title[:120]
        user.account_checked_at = utcnow()
        await db.commit()

    if not changed:
        return False

    await log_activity(
        user_id=user_id,
        event="account_health",
        detail=f"{STATE_LABELS.get(diagnosis.state, diagnosis.state)} — {diagnosis.title}",
        level="error" if diagnosis.fatal else "warning",
    )
    # محدودیت موقت ارزش مزاحمت ندارد؛ فقط موارد جدی به کاربر گفته می‌شوند
    if notifier is not None and diagnosis.fatal and diagnosis.message:
        try:
            await notifier(user_id, diagnosis.message)
        except Exception:
            log.debug("اطلاع وضعیت اکانت به کاربر نرسید", exc_info=True)
    return True


async def clear(user_id: int) -> None:
    """اکانت دوباره سالم شد (مثلاً اتصال موفق بعد از خطا)."""
    async with get_session() as db:
        user = await db.get(User, user_id)
        if user is None or user.account_state == STATE_OK:
            return
        user.account_state = STATE_OK
        user.account_note = ""
        user.account_checked_at = utcnow()
        await db.commit()
    await log_activity(user_id=user_id, event="account_health", detail="اکانت دوباره سالم شد")


async def state_of(user_id: int) -> str:
    async with get_session() as db:
        value = await db.scalar(select(User.account_state).where(User.id == user_id))
    return value or STATE_OK


async def summary() -> dict[str, int]:
    """شمارش اکانت‌ها بر اساس وضعیت، برای پنل ادمین."""
    async with get_session() as db:
        rows = await db.execute(
            select(User.account_state, func.count(User.id))
            .where(User.session_enc.is_not(None))
            .group_by(User.account_state)
        )
        return {(state or STATE_OK): count for state, count in rows.all()}


async def unhealthy(limit: int = 20) -> list[User]:
    """اکانت‌هایی که مشکل دارند، برای رسیدگی ادمین."""
    async with get_session() as db:
        rows = await db.execute(
            select(User)
            .where(
                User.session_enc.is_not(None),
                User.account_state.is_not(None),
                User.account_state != STATE_OK,
            )
            .order_by(User.account_checked_at.desc())
            .limit(limit)
        )
        return list(rows.scalars())
