"""پرداخت با تتر (USDT) روی شبکه‌ی ترون.

<b>کنار پرداخت کارت بانکی، نه به‌جایش.</b> کاربر سر صفحه‌ی پرداخت انتخاب
می‌کند از کدام راه می‌خواهد بپردازد؛ هر دو به همان درخواستِ پرداخت و همان
تأیید ادمین می‌رسند، پس منطق اشتراک و صورتحساب یکی می‌ماند.

<b>نرخ تبدیل در دیتابیس است نه در `.env`.</b> قیمت دلار روزانه عوض
می‌شود و اگر در فایل تنظیمات بنشیند، هر تغییرش یعنی ری‌استارت ربات. از
پنل ادمین عوض می‌شود و همان لحظه اثر می‌کند.

<b>مبلغ در لحظه‌ی ساخت درخواست قفل می‌شود.</b> اگر هر بار از نو حساب
می‌شد، کاربری که ده دقیقه بعد واریز می‌کند مبلغ دیگری می‌دید و رسیدش با
درخواست نمی‌خواند.
"""
from __future__ import annotations

import logging
import re
from decimal import ROUND_UP, Decimal

from telkap.db import get_session
from telkap.models import AppSetting, utcnow

log = logging.getLogger(__name__)

RATE_KEY = "usdt_rate"          # تومان به ازای هر ۱ تتر
ADDRESS_KEY = "usdt_address"    # نشانی ولت TRC20

METHOD_CARD = "card"
METHOD_USDT = "usdt"
METHODS = (METHOD_CARD, METHOD_USDT)

METHOD_LABELS = {
    METHOD_CARD: "💳 کارت بانکی",
    METHOD_USDT: "₮ تتر (TRC20)",
}

# نشانی ترون همیشه با T شروع می‌شود و ۳۴ نویسه‌ی Base58 است. این فقط
# غلط تایپی را می‌گیرد نه نشانی جعلی را — ولی همان هم ارزش دارد، چون
# نشانی اشتباه یعنی پولِ رفته.
ADDRESS_RE = re.compile(r"^T[1-9A-HJ-NP-Za-km-z]{33}$")

# هش تراکنش ترون: ۶۴ نویسه‌ی هگز، گاهی کاربر با 0x می‌چسباند.
TX_RE = re.compile(r"^(?:0x)?[0-9a-fA-F]{64}$")

# کمترین نرخ معقول، تا صفر یا عدد اشتباه ثبت نشود و مبلغ‌ها بی‌معنی نشوند
MIN_RATE = 1_000


def valid_address(value: str) -> bool:
    return bool(ADDRESS_RE.match((value or "").strip()))


def normalize_tx(value: str) -> str:
    """هش را یکدست می‌کند. رشته‌ی خالی یعنی معتبر نبود."""
    cleaned = (value or "").strip().lower()
    if not TX_RE.match(cleaned):
        return ""
    return cleaned.removeprefix("0x")


async def _read(key: str):
    async with get_session() as db:
        row = await db.get(AppSetting, key)
        return row.value if row else None


async def _write(key: str, value, admin_id: int | None) -> None:
    async with get_session() as db:
        row = await db.get(AppSetting, key)
        if row is None:
            row = AppSetting(key=key)
            db.add(row)
        row.value = value
        row.updated_by = admin_id
        row.updated_at = utcnow()
        await db.commit()


async def rate() -> int:
    """تومان به ازای هر تتر. صفر یعنی هنوز تنظیم نشده."""
    stored = await _read(RATE_KEY)
    try:
        value = int(stored or 0)
    except (TypeError, ValueError):
        return 0
    return value if value >= MIN_RATE else 0


async def set_rate(value, *, admin_id: int | None = None) -> int | None:
    """نرخ تازه را ذخیره می‌کند. None یعنی مقدار پذیرفته نشد."""
    try:
        number = int(str(value).strip().replace(",", "").replace("،", ""))
    except (TypeError, ValueError):
        return None
    if number < MIN_RATE:
        return None
    await _write(RATE_KEY, number, admin_id)
    return number


async def address() -> str:
    return str(await _read(ADDRESS_KEY) or "").strip()


async def set_address(value: str, *, admin_id: int | None = None) -> str | None:
    cleaned = (value or "").strip()
    if not valid_address(cleaned):
        return None
    await _write(ADDRESS_KEY, cleaned, admin_id)
    return cleaned


async def available() -> bool:
    """آیا این راه پرداخت آماده‌ی استفاده است؟

    هر دو لازم‌اند: نشانیِ بدون نرخ یعنی نمی‌دانیم چقدر بخواهیم، و نرخِ
    بدون نشانی یعنی جایی برای واریز نیست.
    """
    return bool(await address()) and await rate() > 0


def to_usdt(amount_toman: int, rate_toman: int) -> Decimal:
    """تومان را به تتر تبدیل می‌کند، با دو رقم اعشار و رو به بالا.

    رو به بالا گرد می‌شود چون کم‌تر بودنِ مبلغ یعنی پرداخت ناقص و یک
    رفت‌وبرگشت اضافه با پشتیبانی؛ چند سنتِ بیشتر این دردسر را ندارد.
    """
    if amount_toman <= 0 or rate_toman <= 0:
        return Decimal("0")
    raw = Decimal(amount_toman) / Decimal(rate_toman)
    return raw.quantize(Decimal("0.01"), rounding=ROUND_UP)


def format_usdt(value: Decimal) -> str:
    """بدون صفرهای انتهاییِ بی‌فایده: 12.50 → 12.5 و 12.00 → 12"""
    text = f"{value:.2f}".rstrip("0").rstrip(".")
    return text or "0"


async def quote(amount_toman: int) -> dict | None:
    """مبلغ تتری و نشانی مقصد برای یک پرداخت.

    None یعنی این راه پرداخت آماده نیست؛ صدازننده باید فقط کارت را نشان
    بدهد.
    """
    wallet = await address()
    rate_toman = await rate()
    if not wallet or rate_toman <= 0:
        return None

    usdt = to_usdt(amount_toman, rate_toman)
    if usdt <= 0:
        return None
    return {
        "address": wallet,
        "rate": rate_toman,
        "usdt": usdt,
        "usdt_text": format_usdt(usdt),
    }
