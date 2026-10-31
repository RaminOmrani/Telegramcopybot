"""درگاه پرداخت زرین‌پال (REST v4).

سومین راه پرداخت، کنار کارت‌به‌کارت و تتر. تفاوت مهمش این است که
<b>دخالت ادمین لازم ندارد</b>: کاربر به درگاه می‌رود، پرداخت می‌کند،
برمی‌گردد، و اشتراکش همان لحظه فعال می‌شود.

قرارداد API از نمونه‌ی رسمی خودِ زرین‌پال گرفته شده نه از حافظه:
https://github.com/ZarinPal-Lab/Zarinpal-RestAPI-Sample-php

    درخواست  POST https://api.zarinpal.com/pg/v4/payment/request.json
             {merchant_id, amount, currency, callback_url, description}
             موفق: data.code == 100 و data.authority

    هدایت    https://www.zarinpal.com/pg/StartPay/{authority}

    بازگشت   callback_url?Authority=…&Status=OK|NOK

    تأیید    POST https://api.zarinpal.com/pg/v4/payment/verify.json
             {merchant_id, amount, authority}
             موفق: data.code == 100 (تازه) یا 101 (قبلاً تأیید شده)

<b>مبلغ به تومان فرستاده می‌شود</b> با `currency: "IRT"`. زرین‌پال ریال
را هم می‌پذیرد، ولی قیمت‌های ما تومان‌اند و هر تبدیلی یک جای اشتباه
کردن اضافه می‌کند. ارز صریح فرستاده می‌شود تا به پیش‌فرضِ سرویس تکیه
نکنیم.

<b>کد ۱۰۱ هم موفق است.</b> یعنی «قبلاً تأیید شده». اگر آن را شکست حساب
کنیم، یک تلاش دوباره‌ی بی‌ضرر (قطعی لحظه‌ای شبکه، دوبار باز شدن صفحه‌ی
بازگشت) کاربری را که واقعاً پول داده رد می‌کند.
"""
from __future__ import annotations

import logging
import re

import aiohttp

from telkap.config import get_settings
from telkap.db import get_session
from telkap.models import AppSetting, utcnow

log = logging.getLogger(__name__)

REQUEST_URL = "https://api.zarinpal.com/pg/v4/payment/request.json"
VERIFY_URL = "https://api.zarinpal.com/pg/v4/payment/verify.json"
START_PAY = "https://www.zarinpal.com/pg/StartPay/{authority}"

CURRENCY_TOMAN = "IRT"

CODE_OK = 100
CODE_ALREADY_VERIFIED = 101

TIMEOUT_SECONDS = 25

# مسیر بازگشت روی همان وب‌سروری که پنل مدیریت رویش است
CALLBACK_PATH = "/pay/zarinpal"


# کد پذیرنده در پنل ذخیره می‌شود، با .env به‌عنوان پیش‌فرض. تا امروز
# فقط در .env بود، یعنی روشن کردن درگاه به SSH نیاز داشت — همان
# مشکلی که شماره کارت داشت و فروش را می‌خواباند.
MERCHANT_KEY = "zarinpal_merchant"

# کد پذیرنده یک UUID است. این بررسی کدِ جعلی را نمی‌گیرد، ولی غلط
# تایپی و «کپی ناقص» را می‌گیرد — و آن دو، تنها نشانه‌شان این است که
# هر خرید بی‌دلیل شکست می‌خورد.
MERCHANT_RE = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
    r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)


def valid_merchant(value: str) -> bool:
    return bool(MERCHANT_RE.match((value or "").strip()))


async def merchant() -> str:
    """کد پذیرنده — از پنل، وگرنه از .env."""
    async with get_session() as db:
        row = await db.get(AppSetting, MERCHANT_KEY)
    stored = str((row.value if row else None) or "").strip()
    return stored or (get_settings().zarinpal_merchant or "").strip()


async def set_merchant(value: str, *, admin_id: int | None = None) -> str | None:
    """کد پذیرنده‌ی تازه. None یعنی پذیرفته نشد."""
    cleaned = (value or "").strip().lower()
    if not valid_merchant(cleaned):
        return None
    async with get_session() as db:
        row = await db.get(AppSetting, MERCHANT_KEY)
        if row is None:
            row = AppSetting(key=MERCHANT_KEY)
            db.add(row)
        row.value = cleaned
        row.updated_by = admin_id
        row.updated_at = utcnow()
        await db.commit()
    return cleaned


async def configured() -> bool:
    """آیا درگاه آماده‌ی استفاده است؟

    هر دو لازم‌اند: بدون کد پذیرنده درخواستی ساخته نمی‌شود، و بدون
    نشانی عمومی زرین‌پال جایی برای برگرداندن کاربر ندارد.
    """
    return bool(await merchant()) and bool(get_settings().web_base_url)


def callback_url() -> str:
    return get_settings().web_base_url.rstrip("/") + CALLBACK_PATH


def pay_url(authority: str) -> str:
    return START_PAY.format(authority=authority)


async def _post(url: str, payload: dict) -> dict | None:
    """یک تماس با زرین‌پال. None یعنی نشد — هرگز استثنا."""
    try:
        timeout = aiohttp.ClientTimeout(total=TIMEOUT_SECONDS)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(url, json=payload) as response:
                data = await response.json(content_type=None)
    except Exception:                       # noqa: BLE001 — پرداخت نباید بترکد
        log.exception("تماس با زرین‌پال ناموفق بود: %s", url)
        return None

    if not isinstance(data, dict):
        log.error("پاسخ زرین‌پال شکل مورد انتظار را نداشت: %s", str(data)[:300])
        return None
    return data


def _payload_errors(data: dict) -> str:
    """متن خطای زرین‌پال، برای لاگ.

    فیلد errors گاهی فهرست است و گاهی دیکشنری، پس به هر دو شکل نگاه
    می‌کنیم به‌جای اینکه یکی را فرض بگیریم.
    """
    errors = data.get("errors")
    if isinstance(errors, dict):
        return str(errors.get("message") or errors)[:200]
    if isinstance(errors, list) and errors:
        return str(errors[0])[:200]
    return ""


async def start(amount_toman: int, description: str, *, request_id: int) -> str | None:
    """پرداخت را باز می‌کند و نشانی درگاه را برمی‌گرداند.

    None یعنی درگاه درخواست را نپذیرفت؛ صدازننده باید راه دیگری پیشنهاد
    بدهد نه اینکه کاربر را با صفحه‌ی خطا تنها بگذارد.
    """
    code_id = await merchant()
    if not code_id or not get_settings().web_base_url or amount_toman <= 0:
        return None

    data = await _post(
        REQUEST_URL,
        {
            "merchant_id": code_id,
            "amount": int(amount_toman),
            "currency": CURRENCY_TOMAN,
            "callback_url": f"{callback_url()}?rid={request_id}",
            "description": description[:255],
        },
    )
    if data is None:
        return None

    # کد پیش از هر استفاده‌ای امن استخراج می‌شود. اگر data رشته یا None
    # باشد — که در خطاهای سرویس پیش می‌آید — خودِ خط لاگ نباید بترکد.
    body = data.get("data")
    code = body.get("code") if isinstance(body, dict) else None
    if code != CODE_OK:
        log.error(
            "زرین‌پال درخواست را رد کرد (کد %s): %s", code, _payload_errors(data)
        )
        return None

    authority = str(body.get("authority") or "")
    if not authority:
        log.error("زرین‌پال کد ۱۰۰ داد ولی authority نداشت")
        return None
    return authority


async def verify(authority: str, amount_toman: int) -> dict | None:
    """پرداخت را تأیید می‌کند.

    خروجی دیکشنریِ {ref_id, card_pan} است یا None اگر تأیید نشد.

    <b>هرگز به پارامترهای بازگشت اعتماد نمی‌شود.</b> مرورگر کاربر
    `Status=OK` را می‌آورد و هرکسی می‌تواند همان نشانی را دستی باز کند؛
    تنها چیزی که پرداخت را ثابت می‌کند همین تماس سمت سرور است.
    """
    code_id = await merchant()
    if not code_id or not authority or amount_toman <= 0:
        return None

    data = await _post(
        VERIFY_URL,
        {
            "merchant_id": code_id,
            "amount": int(amount_toman),
            "authority": authority,
        },
    )
    if data is None:
        return None

    body = data.get("data")
    code = body.get("code") if isinstance(body, dict) else None
    if code not in (CODE_OK, CODE_ALREADY_VERIFIED):
        log.warning(
            "تأیید زرین‌پال ناموفق (کد %s): %s", code, _payload_errors(data)
        )
        return None

    return {
        "ref_id": str(body.get("ref_id") or ""),
        "card_pan": str(body.get("card_pan") or ""),
        "already": code == CODE_ALREADY_VERIFIED,
    }
