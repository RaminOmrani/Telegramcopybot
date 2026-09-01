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
from telkap.services import coins

log = logging.getLogger(__name__)

RATE_KEY = "usdt_rate"          # تومان به ازای هر ۱ تتر
ADDRESS_KEY = "usdt_address"    # نشانی ولت TRC20

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
        before = row.value if row else None
        if row is None:
            row = AppSetting(key=key)
            db.add(row)
        row.value = value
        row.updated_by = admin_id
        row.updated_at = utcnow()
        await db.commit()

    if before != value:
        await _announce(key, before, value, admin_id)


async def _announce(key: str, before, after, admin_id: int | None) -> None:
    """تغییر نشانی یا نرخ را به همه‌ی ادمین‌های مالی خبر می‌دهد.

    <b>چرا این لازم است.</b> کلید خصوصی ولت هیچ‌وقت روی سرور نیست، پس
    حتی هکِ کاملِ سرور هم پول موجود را نمی‌برد. ولی کسی که به یک حساب
    ادمین برسد می‌تواند <b>نشانی را عوض کند</b> و درآمد آینده را به ولت
    خودش ببرد — و این تغییر تا وقتی کسی لاگ را نخواند بی‌صدا می‌ماند.

    یک پیام فوری به بقیه‌ی ادمین‌ها، همان بی‌صدا بودن را از بین می‌برد.
    """
    from telkap.services import alerts, roles

    labels = {ADDRESS_KEY: "نشانی ولت تتر", RATE_KEY: "نرخ تتر"}
    label = labels.get(key)
    if label is None:
        return

    # <b>نرخِ خودکار خبر نمی‌دهد، نشانی همیشه خبر می‌دهد.</b>
    #
    # نرخ هر ربع ساعت از بازار به‌روز می‌شود؛ هشدار برای هرکدام یعنی
    # روزی ده‌ها پیام — و هشداری که هر روز می‌آید، روزی که واقعاً مهم
    # است هم نادیده گرفته می‌شود.
    #
    # نشانی فرق دارد: هیچ فرآیند خودکاری عوضش نمی‌کند، پس هر تغییرش
    # کار یک نفر است و باید دیده شود.
    if key == RATE_KEY and admin_id is None:
        return

    who = f"<code>{admin_id}</code>" if admin_id else "سیستم"
    await alerts.send(
        f"🔐 <b>{label} عوض شد</b>\n\n"
        f"از: <code>{before or '—'}</code>\n"
        f"به: <code>{after}</code>\n"
        f"توسط: {who}\n\n"
        "<i>اگر این کار شما نبود، همین حالا برش گردانید و رمز حساب‌های "
        "ادمین را عوض کنید — پرداخت‌های بعدی به همین نشانی می‌روند.</i>",
        cap=roles.CAP_MONEY,
        # هشدار امنیتی هیچ‌وقت خفه نمی‌شود. کلیدِ یکتا یعنی throttle
        # هرگز جلویش را نمی‌گیرد، حتی اگر پشت سر هم عوض شود.
        key=f"walletchange:{key}:{after}",
        cooldown=0,
    )


def _rate_key(coin: str) -> str:
    """کلید نرخ این ارز در تنظیمات.

    ارز ناشناخته به تتر برمی‌گردد، نه اینکه خطا بدهد: صدازننده‌های
    قدیمی که هیچ ارزی نمی‌دهند باید مثل قبل کار کنند.
    """
    found = coins.get(coin)
    return found.rate_key if found else RATE_KEY


async def rate(coin: str = coins.USDT) -> int:
    """تومان به ازای هر واحد این ارز. صفر یعنی هنوز تنظیم نشده."""
    stored = await _read(_rate_key(coin))
    try:
        value = int(stored or 0)
    except (TypeError, ValueError):
        return 0
    return value if value >= MIN_RATE else 0


async def set_rate(value, *, coin: str = coins.USDT, admin_id: int | None = None):
    """نرخ تازه را ذخیره می‌کند. None یعنی مقدار پذیرفته نشد."""
    try:
        number = int(str(value).strip().replace(",", "").replace("،", ""))
    except (TypeError, ValueError):
        return None
    if number < MIN_RATE:
        return None
    await _write(_rate_key(coin), number, admin_id)
    return number


async def rate_updated_at(coin: str = coins.USDT):
    """کِی این نرخ آخرین بار نوشته شد. None یعنی هیچ‌وقت.

    <b>نرخِ کهنه بی‌صدا ضرر می‌دهد.</b> اگر خواندن از صرافی بخوابد،
    نرخ قبلی سر جایش می‌ماند و همه‌چیز سالم به نظر می‌رسد — فروش ادامه
    دارد، فقط با قیمتِ هفته‌ی پیش. تنها نشانه‌اش همین تاریخ است.
    """
    async with get_session() as db:
        row = await db.get(AppSetting, _rate_key(coin))
        return row.updated_at if row else None


async def address() -> str:
    return str(await _read(ADDRESS_KEY) or "").strip()


async def set_address(value: str, *, admin_id: int | None = None) -> str | None:
    cleaned = (value or "").strip()
    if not valid_address(cleaned):
        return None
    await _write(ADDRESS_KEY, cleaned, admin_id)
    return cleaned


async def available(coin: str = coins.USDT) -> bool:
    """آیا این راه پرداخت آماده‌ی استفاده است؟

    هر دو لازم‌اند: نشانیِ بدون نرخ یعنی نمی‌دانیم چقدر بخواهیم، و نرخِ
    بدون نشانی یعنی جایی برای واریز نیست.

    نشانی بین همه‌ی ارزها مشترک است — تتر و ترون هر دو روی شبکه‌ی
    ترون‌اند و به یک ولت واریز می‌شوند. فقط نرخ است که جدا تنظیم
    می‌شود.
    """
    return bool(await address()) and await rate(coin) > 0


async def ready_coins() -> tuple[str, ...]:
    """ارزهایی که همین حالا قابل پرداخت‌اند."""
    if not await address():
        return ()
    return tuple([code for code in coins.all_codes() if await rate(code) > 0])


def to_coin(amount_toman: int, rate_toman: int, places: str = "0.01") -> Decimal:
    """تومان را به واحد ارز تبدیل می‌کند، رو به بالا.

    رو به بالا گرد می‌شود چون کم‌تر بودنِ مبلغ یعنی پرداخت ناقص و یک
    رفت‌وبرگشت اضافه با پشتیبانی؛ چند سنتِ بیشتر این دردسر را ندارد.

    <code>places</code> برای هر ارز فرق دارد: ترون ارزان است و با دو
    رقم اعشار مبلغ‌ها گرد و بی‌دقت می‌شوند.
    """
    if amount_toman <= 0 or rate_toman <= 0:
        return Decimal("0")
    raw = Decimal(amount_toman) / Decimal(rate_toman)
    return raw.quantize(Decimal(places), rounding=ROUND_UP)


def to_usdt(amount_toman: int, rate_toman: int) -> Decimal:
    """همان to_coin با دقت تتر. برای صدازننده‌های قدیمی نگه داشته شده."""
    return to_coin(amount_toman, rate_toman, "0.01")


def format_amount(value: Decimal) -> str:
    """بدون صفرهای انتهاییِ بی‌فایده: 12.50 → 12.5 و 12.0000 → 12

    ترون تا چهار رقم اعشار دارد، پس قالب ثابتِ دو رقمی کافی نیست —
    ۱۲٫۳۴۵۶ را به ۱۲٫۳۵ گرد می‌کرد و مبلغِ گفته‌شده با مبلغِ خواسته‌شده
    نمی‌خواند.
    """
    text = format(value.normalize(), "f")
    return text or "0"


def format_usdt(value: Decimal) -> str:
    """نام قدیمی؛ نگه داشته شده تا صدازننده‌های موجود نشکنند."""
    return format_amount(value)


async def quote(amount_toman: int, coin: str = coins.USDT) -> dict | None:
    """مبلغ ارزی و نشانی مقصد برای یک پرداخت.

    None یعنی این راه پرداخت آماده نیست؛ صدازننده باید راه دیگری
    نشان بدهد.
    """
    spec = coins.get(coin)
    if spec is None:
        return None

    wallet = await address()
    rate_toman = await rate(coin)
    if not wallet or rate_toman <= 0:
        return None

    amount = to_coin(amount_toman, rate_toman, spec.quantize)
    if amount <= 0:
        return None
    text = format_amount(amount)
    return {
        "coin": spec.code,
        "symbol": spec.symbol,
        "label": spec.label,
        "address": wallet,
        "rate": rate_toman,
        "amount": amount,
        "amount_text": text,
        # نام‌های قدیمی، تا کدی که هنوز usdt_text می‌خواند نشکند
        "usdt": amount,
        "usdt_text": text,
    }
