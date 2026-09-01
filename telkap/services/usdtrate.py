"""نرخ خودکار تتر به تومان.

<b>مسئله‌ای که حل می‌کند.</b> نرخ دلار روزانه — گاهی ساعتی — عوض
می‌شود. نرخِ دستی یعنی هر ساعتی که یادتان برود، یا ضرر می‌کنید یا
مشتری گران می‌خرد. هیچ‌کدام قابل قبول نیست.

<b>حاشیه‌ی اطمینان رو به پایین است، نه بالا.</b> مبلغ تتری از تقسیم
به‌دست می‌آید: <code>usdt = تومان ÷ نرخ</code>. پس نرخِ <b>بالاتر</b>
یعنی مشتری تتر <b>کمتری</b> می‌دهد. برای اینکه در فروش ضرر نکنید،
نرخ باید کمی <b>پایین‌تر</b> از بازار باشد تا تتری که می‌گیرید
دست‌کم به اندازه‌ی قیمت تومانی بیرزد.

<b>سه محافظ، چون این عدد مستقیم قیمت است.</b> یک پاسخ خراب از
صرافی می‌تواند اشتراک را مفت کند:

۱. عدد باید در بازه‌ی معقول باشد. چیزی خارج از آن یعنی پاسخ را
   اشتباه خوانده‌ایم، نه اینکه بازار تکان خورده.
۲. جهش بزرگ نسبت به نرخ فعلی پذیرفته نمی‌شود و به ادمین خبر داده
   می‌شود. بازار در چند دقیقه سی درصد تکان نمی‌خورد؛ چنین چیزی
   یعنی خطا.
۳. اگر خواندن شکست بخورد، نرخ <b>قبلی سر جایش می‌ماند</b>. صفر
   کردنش یعنی خاموش شدن پرداخت تتری.
"""
from __future__ import annotations

import asyncio
import logging
from decimal import Decimal, InvalidOperation

import aiohttp

from telkap.db import get_session
from telkap.models import AppSetting, utcnow
from telkap.services import crypto

log = logging.getLogger(__name__)

AUTO_KEY = "usdt_rate_auto"       # روشن/خاموش
MARGIN_KEY = "usdt_rate_margin"   # درصد پایین‌تر از بازار

INTERVAL_SECONDS = 900            # هر ۱۵ دقیقه
TIMEOUT_SECONDS = 15

DEFAULT_MARGIN = 2                # درصد
MAX_MARGIN = 20

# بازه‌ی معقول برای «تومان به ازای هر تتر». اگر عددی بیرون از این
# بیاید، یعنی پاسخ را اشتباه خوانده‌ایم — مثلاً ریال را تومان گرفته‌ایم
# یا ساختار پاسخ عوض شده.
MIN_SANE = 10_000
MAX_SANE = 10_000_000

# بیشترین تغییر مجاز در یک دور. بازار در پانزده دقیقه این‌قدر تکان
# نمی‌خورد؛ چنین جهشی یعنی خطا، و خطا نباید بی‌صدا قیمت را عوض کند.
MAX_JUMP_PERCENT = 25


class RateError(Exception):
    """نرخ خوانده نشد. نرخ قبلی دست نمی‌خورد."""


# ── خواندن از صرافی ──────────────────────────────────────────────────


def _first_number(payload, keys) -> Decimal | None:
    """اولین کلیدی که عدد معتبر بدهد.

    ساختار پاسخ صرافی‌ها بدون اطلاع عوض می‌شود. امتحان کردن چند نام،
    ارزان‌تر از شکستنِ بی‌صدای قیمت‌گذاری است.
    """
    if not isinstance(payload, dict):
        return None
    for key in keys:
        raw = payload.get(key)
        if raw is None:
            continue
        try:
            value = Decimal(str(raw))
        except (InvalidOperation, ValueError, TypeError):
            continue
        if value > 0:
            return value
    return None


async def market_toman(*, session: aiohttp.ClientSession | None = None) -> int:
    """نرخ بازار تتر به <b>تومان</b>.

    نوبیتکس قیمت را به <b>ریال</b> می‌دهد. تقسیم بر ده جایی است که
    اشتباهش گران تمام می‌شود: ده برابر خطا یعنی اشتراکِ یک‌دهم قیمت.
    """
    owned = session is None
    session = session or aiohttp.ClientSession()
    url = "https://api.nobitex.ir/v2/orderbook/USDTIRT"

    try:
        async with session.get(
            url, timeout=aiohttp.ClientTimeout(total=TIMEOUT_SECONDS)
        ) as response:
            if response.status != 200:
                raise RateError(f"صرافی پاسخ {response.status} داد")
            body = await response.json(content_type=None)
    except RateError:
        raise
    except TimeoutError as exc:
        raise RateError("صرافی در زمان مقرر پاسخ نداد") from exc
    except aiohttp.ClientError as exc:
        raise RateError("اتصال به صرافی ممکن نشد") from exc
    except (ValueError, TypeError) as exc:
        raise RateError("پاسخ صرافی قابل خواندن نبود") from exc
    finally:
        if owned:
            await session.close()

    rial = _first_number(body, ("lastTradePrice", "last", "latest"))
    if rial is None:
        raise RateError("قیمتی در پاسخ صرافی پیدا نشد")

    toman = int(rial / 10)
    if not MIN_SANE <= toman <= MAX_SANE:
        raise RateError(f"نرخ خوانده‌شده معقول نیست: {toman}")
    return toman


# ── تنظیمات ──────────────────────────────────────────────────────────


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


async def is_auto() -> bool:
    return bool(await _read(AUTO_KEY))


async def set_auto(value: bool, *, admin_id: int | None = None) -> bool:
    await _write(AUTO_KEY, bool(value), admin_id)
    return bool(value)


async def margin() -> int:
    try:
        value = int(await _read(MARGIN_KEY) or DEFAULT_MARGIN)
    except (TypeError, ValueError):
        return DEFAULT_MARGIN
    return value if 0 <= value <= MAX_MARGIN else DEFAULT_MARGIN


async def set_margin(value, *, admin_id: int | None = None) -> int | None:
    try:
        number = int(str(value).strip())
    except (TypeError, ValueError):
        return None
    if not 0 <= number <= MAX_MARGIN:
        return None
    await _write(MARGIN_KEY, number, admin_id)
    return number


def with_margin(market: int, percent: int) -> int:
    """نرخ فروش = نرخ بازار منهای حاشیه.

    پایین‌تر، چون مبلغ تتری از تقسیم می‌آید: نرخِ کمتر یعنی مشتری تتر
    بیشتری می‌دهد، و همان چیزی است که جلوی ضرر را می‌گیرد.
    """
    if market <= 0:
        return 0
    percent = max(0, min(int(percent), MAX_MARGIN))
    return int(market * (100 - percent) / 100)


def jumped(old: int, new: int) -> bool:
    """آیا تغییر آن‌قدر بزرگ است که به خطا شبیه‌تر باشد تا به بازار."""
    if old <= 0:
        return False
    return abs(new - old) * 100 / old > MAX_JUMP_PERCENT


# ── چرخه ─────────────────────────────────────────────────────────────


async def refresh(*, force: bool = False) -> int:
    """یک بار نرخ را از بازار می‌گیرد و ثبت می‌کند.

    خروجی نرخ تازه است، یا صفر اگر چیزی عوض نشد. <code>force</code>
    محافظِ جهش را کنار می‌گذارد — فقط برای وقتی که ادمین خودش
    خواسته.
    """
    if not force and not await is_auto():
        return 0

    try:
        market = await market_toman()
    except RateError as exc:
        # نرخ قبلی سر جایش می‌ماند؛ خاموش شدن پرداخت بدتر از نرخِ
        # کمی قدیمی است
        log.warning("خواندن نرخ تتر ناموفق بود: %s", exc)
        return 0

    fresh = with_margin(market, await margin())
    current = await crypto.rate()

    if fresh == current:
        return 0

    if not force and jumped(current, fresh):
        from telkap.services import alerts, roles

        log.warning("جهش نرخ تتر نادیده گرفته شد: %s → %s", current, fresh)
        await alerts.send(
            "⚠️ <b>نرخ خودکار تتر اعمال نشد</b>\n\n"
            f"نرخ فعلی: <b>{current:,}</b> تومان\n"
            f"نرخ بازار: <b>{fresh:,}</b> تومان\n\n"
            "اختلاف بیش از حد انتظار است، پس تغییری داده نشد.\n"
            "<i>اگر بازار واقعاً این‌قدر تکان خورده، از پنل ادمین دستی "
            "ثبتش کنید.</i>",
            cap=roles.CAP_MONEY,
            key=f"ratejump:{fresh}",
            cooldown=0,
        )
        return 0

    await crypto.set_rate(fresh, admin_id=None)
    log.info("نرخ تتر به‌روز شد: %s تومان (بازار %s)", fresh, market)
    return fresh


async def run_forever() -> None:
    while True:
        try:
            await asyncio.sleep(INTERVAL_SECONDS)
            await refresh()
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("چرخه‌ی نرخ خودکار تتر با خطا مواجه شد")
