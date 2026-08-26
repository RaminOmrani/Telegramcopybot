"""هشدار فوری به ادمین + دیده‌بانِ سلامت سرویس.

دو مسئله را حل می‌کند:

۱) اطلاع‌رسانی پراکنده بود — هر بخش خودش حلقه‌ای روی `admin_ids` می‌زد و
   همه‌ی ادمین‌ها همه‌چیز را می‌گرفتند. حالا هشدار به «کسی که آن دسترسی
   را دارد» می‌رود؛ پشتیبانی خبرِ رسید نمی‌گیرد.

۲) خرابی‌ها بی‌صدا بودند. صف تلاش مجدد می‌توانست پُر شود، اکانت‌ها بن
   شوند و رسیدها روی زمین بمانند، بدون اینکه کسی بفهمد. دیده‌بان هر ربع
   نگاه می‌کند و فقط وقتی چیزی از حد گذشت خبر می‌دهد.

هر هشدار «کلید» دارد و تا `cooldown` ثانیه دوباره فرستاده نمی‌شود؛ وگرنه
یک اشکال ماندگار، هر ربع یک پیام می‌ساخت و ادمین آن را بی‌صدا می‌کرد.
"""
from __future__ import annotations

import asyncio
import logging
import time
from datetime import timedelta

from sqlalchemy import func, select

from telkap.db import get_session
from telkap.models import PaymentRequest, RetryItem, User, utcnow
from telkap.services import health, roles

log = logging.getLogger(__name__)

CHECK_SECONDS = 900          # هر ربع ساعت
DEFAULT_COOLDOWN = 6 * 3600  # همان هشدار زودتر از ۶ ساعت تکرار نمی‌شود

# حدهایی که رد شدن از آن‌ها یعنی «یک نفر باید نگاه کند»
RETRY_QUEUE_LIMIT = 200      # صف تلاش مجدد
PENDING_PAY_HOURS = 12       # رسید بررسی‌نشده
PENDING_PAY_LIMIT = 3

_last_sent: dict[str, float] = {}

# ربات یک بار در راه‌اندازی وصل می‌شود تا بخش‌هایی مثل موتور کپی — که
# دسترسی به Bot ندارند — بتوانند هشدار بدهند.
_bot = None


def bind(bot) -> None:
    global _bot
    _bot = bot


def reset() -> None:
    """پاک کردن حافظه‌ی ضد تکرار (برای تست‌ها و راه‌اندازی دوباره)."""
    _last_sent.clear()


def _throttled(key: str, cooldown: int) -> bool:
    if not key or cooldown <= 0:
        return False
    now = time.monotonic()
    previous = _last_sent.get(key)
    if previous is not None and now - previous < cooldown:
        return True
    _last_sent[key] = now
    return False


async def send(
    text: str,
    *,
    bot=None,
    cap: str | None = None,
    key: str = "",
    cooldown: int = DEFAULT_COOLDOWN,
    markup=None,
) -> int:
    """ارسال هشدار به ادمین‌های دارای دسترسی `cap`. خروجی: تعداد ارسال موفق."""
    bot = bot or _bot
    if bot is None:
        return 0
    if _throttled(key, cooldown):
        return 0
    sent = 0
    for admin_id in await roles.staff_ids(cap):
        try:
            await bot.send_message(admin_id, text, reply_markup=markup)
            sent += 1
        except Exception:
            log.debug("ارسال هشدار به ادمین %s ناموفق بود", admin_id, exc_info=True)
    return sent


# ------------------------------------------------------------- دیده‌بان
async def _retry_backlog() -> str:
    async with get_session() as db:
        queued = await db.scalar(select(func.count(RetryItem.id)))
    queued = int(queued or 0)
    if queued < RETRY_QUEUE_LIMIT:
        return ""
    return (
        "🔁 <b>صف تلاش مجدد پر شده است</b>\n\n"
        f"{queued} پست در صف مانده. معمولاً یعنی یک کانال مقصد مشکل دارد "
        "یا اکانتی محدود شده است.\n"
        "پنل مدیریت ← 🔁 صف تلاش مجدد"
    )


async def _sick_accounts() -> str:
    states = await health.summary()
    bad = {
        state: count
        for state, count in states.items()
        if state in health.FATAL_STATES and count
    }
    if not bad:
        return ""
    lines = "\n".join(
        f"• {health.STATE_LABELS.get(state, state)}: {count}"
        for state, count in sorted(bad.items(), key=lambda kv: -kv[1])
    )
    return (
        "🚨 <b>اکانت‌هایی از کار افتاده‌اند</b>\n\n"
        f"{lines}\n\n"
        "کارهای این کاربران متوقف شده تا اوضاع بدتر نشود."
    )


async def _stale_receipts() -> str:
    cutoff = utcnow() - timedelta(hours=PENDING_PAY_HOURS)
    async with get_session() as db:
        old = await db.scalar(
            select(func.count(PaymentRequest.id)).where(
                PaymentRequest.status == PaymentRequest.STATUS_PENDING,
                PaymentRequest.receipt_file_id.is_not(None),
                PaymentRequest.created_at < cutoff,
            )
        )
    old = int(old or 0)
    if old < PENDING_PAY_LIMIT:
        return ""
    return (
        "🧾 <b>رسیدهای بررسی‌نشده</b>\n\n"
        f"{old} رسید بیش از {PENDING_PAY_HOURS} ساعت است منتظر مانده. "
        "کاربری که پول داده و جواب نگرفته، سریع‌ترین راه از دست دادن اوست.\n"
        "پنل مدیریت ← 🧾 رسیدهای در انتظار"
    )


CHECKS: tuple[tuple[str, str, object], ...] = (
    ("retry_backlog", roles.CAP_SYSTEM, _retry_backlog),
    ("sick_accounts", roles.CAP_SYSTEM, _sick_accounts),
    ("stale_receipts", roles.CAP_MONEY, _stale_receipts),
)


async def run_checks(bot=None) -> list[str]:
    """همه‌ی بررسی‌ها را اجرا و هشدارهای لازم را می‌فرستد.

    خروجی: کلید بررسی‌هایی که هشدار دادند — برای تست و لاگ.
    """
    fired: list[str] = []
    for key, cap, probe in CHECKS:
        try:
            text = await probe()
        except Exception:
            log.exception("بررسی سلامت «%s» با خطا مواجه شد", key)
            continue
        if not text:
            _last_sent.pop(key, None)  # مشکل رفع شده؛ دفعه‌ی بعد فوراً خبر بده
            continue
        fired.append(key)
        await send(text, bot=bot, cap=cap, key=key)
    return fired


async def run_forever(bot=None) -> None:
    while True:
        try:
            await asyncio.sleep(CHECK_SECONDS)
            await run_checks(bot)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("چرخه‌ی دیده‌بانی با خطا مواجه شد")


# ------------------------------------------------- هشدارهای موردی
async def new_user_milestone(total: int, *, bot=None) -> None:
    """هر ۱۰۰ کاربر یک تبریک — تنها آماری که دیدنش انگیزه می‌دهد."""
    if total <= 0 or total % 100:
        return
    await send(
        f"🎉 <b>کاربر شماره {total}</b> وارد ربات شد.",
        bot=bot,
        cap=roles.CAP_REPORTS,
        key=f"milestone:{total}",
        cooldown=DEFAULT_COOLDOWN,
    )


async def account_failed(user_id: int, state: str, *, bot=None) -> None:
    """اکانت یک کاربر از کار افتاد — بلافاصله، نه در چرخه‌ی ربع‌ساعته."""
    if state not in health.FATAL_STATES:
        return
    async with get_session() as db:
        user = await db.get(User, user_id)
    name = (user.first_name if user else "") or str(user_id)
    await send(
        "⛔️ <b>اکانت یک کاربر از کار افتاد</b>\n\n"
        f"کاربر: {name} (<code>{user_id}</code>)\n"
        f"وضعیت: {health.STATE_LABELS.get(state, state)}\n\n"
        "کارهای کپی‌اش متوقف شد.",
        bot=bot,
        cap=roles.CAP_SYSTEM,
        key=f"account:{user_id}:{state}",
        cooldown=DEFAULT_COOLDOWN,
    )
