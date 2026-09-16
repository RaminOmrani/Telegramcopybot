"""شماره کارت و نام صاحب حساب — قابل تغییر از پنل، نه از فایل.

<b>چرا این فایل هست.</b> شماره کارت تا امروز فقط در <code>.env</code>
بود. یعنی عوض کردنش یعنی ورود به سرور، ویرایش فایل و ری‌استارت ربات —
و اگر کسی این را بلد نبود، شماره‌ای هم ثبت نمی‌شد و ربات به مشتری
می‌گفت «با پشتیبانی تماس بگیرید». یعنی فروش، به‌جای اینکه انجام شود،
تبدیل به یک پیام دستی می‌شد.

نشانی ولت تتر و نرخش از اول در پنل بودند؛ کارت جا مانده بود. حالا هر
سه یک‌جا و یک‌شکل‌اند.

<b>مقدار <code>.env</code> دور ریخته نمی‌شود.</b> اگر چیزی در پنل ثبت
نشده باشد، همان <code>CARD_NUMBER</code> قبلی خوانده می‌شود — پس
نصب‌های موجود با آپدیت چیزی از دست نمی‌دهند.
"""
from __future__ import annotations

import re

from telkap.db import get_session
from telkap.models import AppSetting, utcnow

NUMBER_KEY = "card_number"
HOLDER_KEY = "card_holder"

# کارت‌های بانکی ایران ۱۶ رقم‌اند. کاربر معمولاً با فاصله یا خط تیره
# می‌نویسد، پس پیش از سنجیدن پاک می‌شود.
_DIGITS = re.compile(r"\D+")
CARD_LENGTH = 16


def normalize(value: str) -> str:
    """شماره را به ۱۶ رقم خالص تبدیل می‌کند. خالی یعنی معتبر نبود.

    ارقام فارسی هم پذیرفته می‌شوند: کاربری که با کیبورد فارسی تایپ
    می‌کند نباید با «شماره نامعتبر» روبه‌رو شود.
    """
    text = (value or "").strip().translate(
        str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789")
    )
    digits = _DIGITS.sub("", text)
    return digits if len(digits) == CARD_LENGTH else ""


def pretty(number: str) -> str:
    """۶۰۳۷۹۹۱۲۳۴۵۶۷۸۹۰ → ۶۰۳۷ ۹۹۱۲ ۳۴۵۶ ۷۸۹۰

    خواندن و مقایسه‌ی شانزده رقمِ پیوسته سخت است و اشتباه تایپی را
    پنهان می‌کند.
    """
    clean = normalize(number)
    if not clean:
        return number or ""
    return " ".join(clean[i:i + 4] for i in range(0, CARD_LENGTH, 4))


async def _read(key: str) -> str:
    async with get_session() as db:
        row = await db.get(AppSetting, key)
    return str(row.value).strip() if row and row.value else ""


async def _write(key: str, value: str, admin_id: int | None) -> None:
    async with get_session() as db:
        row = await db.get(AppSetting, key)
        if row is None:
            row = AppSetting(key=key)
            db.add(row)
        row.value = value
        row.updated_by = admin_id
        row.updated_at = utcnow()
        await db.commit()


def _from_env(attr: str) -> str:
    from telkap import config

    return str(getattr(config.settings, attr, "") or "").strip()


async def number() -> str:
    """شماره‌ی ثبت‌شده در پنل، وگرنه همان مقدار .env ."""
    return await _read(NUMBER_KEY) or _from_env("card_number")


async def holder() -> str:
    return await _read(HOLDER_KEY) or _from_env("card_holder")


async def set_number(value: str, *, admin_id: int | None = None) -> str | None:
    """None یعنی شماره پذیرفته نشد."""
    clean = normalize(value)
    if not clean:
        return None
    await _write(NUMBER_KEY, clean, admin_id)
    return clean


async def set_holder(value: str, *, admin_id: int | None = None) -> str | None:
    clean = (value or "").strip()[:64]
    if not clean:
        return None
    await _write(HOLDER_KEY, clean, admin_id)
    return clean


async def available() -> bool:
    """آیا کارت‌به‌کارت آماده‌ی استفاده است.

    نام صاحب حساب لازم نیست: بدون آن هم می‌شود واریز کرد. ولی بودنش
    اعتماد می‌سازد، چون مشتری می‌بیند پول به کجا می‌رود.
    """
    return bool(await number())
