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
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

import aiohttp

from telkap.db import get_session
from telkap.models import AppSetting, utcnow
from telkap.services import coins, crypto, dnsfix

log = logging.getLogger(__name__)

# روشن/خاموش و حاشیه، هر کدام برای همه‌ی ارزها مشترک‌اند: کسی که
# نرخ خودکار می‌خواهد، برای هر دو می‌خواهد. فقط حاشیه‌ی پیش‌فرض هر
# ارز فرق دارد، چون نوسانشان یکی نیست.
AUTO_KEY = "usdt_rate_auto"
MARGIN_KEY = "usdt_rate_margin"

INTERVAL_SECONDS = 900            # هر ۱۵ دقیقه
TIMEOUT_SECONDS = 15

DEFAULT_MARGIN = 2                # درصد
MAX_MARGIN = 20

# بازه‌ی معقول برای «تومان به ازای هر تتر». اگر عددی بیرون از این
# بیاید، یعنی پاسخ را اشتباه خوانده‌ایم — مثلاً ریال را تومان گرفته‌ایم
# یا ساختار پاسخ عوض شده.
MIN_SANE = 10_000
MAX_SANE = 10_000_000

# بازه‌ی معقول برای هر ارز جدا. عددی بیرون از این یعنی پاسخ را اشتباه
# خوانده‌ایم — مثلاً ریال را تومان گرفته‌ایم یا بازار عوضی را پرسیده‌ایم.
SANE_RANGE = {
    coins.USDT: (10_000, 10_000_000),
    coins.TRX: (100, 500_000),
}

# بیشترین تغییر مجاز در یک دور. بازار در پانزده دقیقه این‌قدر تکان
# نمی‌خورد؛ چنین جهشی یعنی خطا، و خطا نباید بی‌صدا قیمت را عوض کند.
MAX_JUMP_PERCENT = 25


class RateError(Exception):
    """نرخ خوانده نشد. نرخ قبلی دست نمی‌خورد."""


# ── خواندن از صرافی ──────────────────────────────────────────────────


def _dig(payload, path: tuple[str, ...]):
    """مسیر تودرتو را دنبال می‌کند. None یعنی نبود."""
    for step in path:
        if not isinstance(payload, dict):
            return None
        payload = payload.get(step)
    return payload


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


@dataclass(frozen=True)
class Source:
    """یک راه گرفتن قیمت.

    <b>واحد در خودِ منبع نوشته می‌شود، نه یک جای مشترک.</b> بعضی
    سرویس‌ها ریال می‌دهند و بعضی تومان؛ اگر تقسیم بر ده یک جای ثابت
    باشد، اضافه کردن منبعِ تومانی یعنی ده برابر خطا در قیمت — و آن خطا
    بی‌صدا است، چون عددش هنوز «یک عدد معقول» به نظر می‌رسد.
    """

    name: str
    url: str
    keys: tuple[str, ...]
    divisor: int                      # ۱۰ برای ریال، ۱ برای تومان
    method: str = "GET"
    path: tuple[str, ...] = ()        # مسیر تودرتو تا خودِ قیمت
    payload: dict | None = None       # برای POST
    params: dict | None = None        # پارامترهای نشانی


def _sources(spec) -> tuple[Source, ...]:
    """منبع‌های <b>تومانی</b> این ارز، به ترتیبِ امتحان شدن.

    هر دو از نوبیتکس‌اند و هر دو ریال می‌دهند، پس عددی که برمی‌گردد
    یکی است — فقط راهِ رسیدن به آن دو تاست.

    <b>روی سرور خارج از ایران هیچ‌کدام کار نمی‌کنند.</b> نامِ
    <code>api.nobitex.ir</code> از بیرون ایران اصلاً به IP ترجمه
    نمی‌شود — نه با DNS سرور، نه از راه DoH. اینجا می‌مانند چون اگر
    روزی ربات روی سرور ایرانی برود یا نوبیتکس نظرش عوض شود، این
    مسیر <b>بهترین</b> است: قیمت ریالی مستقیم از بازار، بدون هیچ ضرب
    و تبدیلی.
    """
    return (
        Source(
            name="نوبیتکس (سفارش‌ها)",
            url=f"https://api.nobitex.ir/v2/orderbook/{spec.market}",
            keys=("lastTradePrice", "last", "latest"),
            divisor=10,
        ),
        Source(
            name="نوبیتکس (آمار بازار)",
            url="https://api.nobitex.ir/market/stats",
            method="POST",
            payload={"srcCurrency": spec.symbol.lower(), "dstCurrency": "rls"},
            path=("stats", f"{spec.symbol.lower()}-rls"),
            keys=("latest", "last", "lastTradePrice"),
            divisor=10,
        ),
    )


# ── مسیر دوم: قیمت جهانی ─────────────────────────────────────────────
#
# <b>چرا این مسیر لازم شد.</b> صرافی ایرانی از این سرور در دسترس نیست.
# ولی قیمت <b>جهانی</b> ترون از هر جای دنیا خوانده می‌شود، و تتر عملاً
# همان دلار است — پس:
#
#     تومانِ ترون  =  (ترون به تتر)  ×  (تومانِ تتر)
#
# <b>و این تقسیم کار، درست همان‌جایی می‌افتد که باید.</b> ترون روزی ده
# درصد تکان می‌خورد و تتر تقریباً ثابت است. با این مسیر، چیزی که
# دستی می‌ماند همان عددِ کندِ تتر است و چیزی که خودکار می‌شود همان
# عددِ تندِ ترون — یعنی جایی که فراموش کردنش واقعاً ضرر می‌زند.


def _global_sources(spec) -> tuple[Source, ...]:
    """قیمت این ارز بر حسب <b>تتر</b>، از صرافی‌های جهانی."""
    return (
        Source(
            name="بایننس",
            url="https://api.binance.com/api/v3/ticker/price",
            params={"symbol": f"{spec.symbol}USDT"},
            keys=("price",),
            divisor=1,
        ),
        Source(
            name="کوکوین",
            url="https://api.kucoin.com/api/v1/market/orderbook/level1",
            params={"symbol": f"{spec.symbol}-USDT"},
            path=("data",),
            keys=("price",),
            divisor=1,
        ),
    )


# قیمت دلاریِ معقول برای هر ارز. اینجا سخت‌گیرانه‌تر از بازه‌ی تومانی
# است چون قیمت دلاری تورم ندارد: ترون هیچ‌وقت یک دلار نبوده و اگر
# روزی چنین عددی برگردد، پاسخ را اشتباه خوانده‌ایم.
SANE_USDT_PRICE = {
    coins.TRX: (Decimal("0.005"), Decimal("5")),
}


async def usdt_price(
    coin: str, *, session: aiohttp.ClientSession | None = None
) -> Decimal:
    """قیمت این ارز به <b>تتر</b>، از صرافی‌های جهانی."""
    spec = coins.get(coin)
    if spec is None:
        raise RateError(f"ارز ناشناخته: {coin}")
    if spec.code == coins.USDT:
        return Decimal(1)

    low, high = SANE_USDT_PRICE.get(spec.code, (Decimal("0.000001"), Decimal("1000")))

    owned = session is None
    session = session or dnsfix.session()
    problems: list[str] = []

    try:
        for source in _global_sources(spec):
            try:
                price = await _fetch_decimal(source, session)
            except RateError as exc:
                problems.append(f"{source.name}: {exc}")
                continue
            if not low <= price <= high:
                problems.append(f"{source.name}: قیمت نامعقول ({price})")
                continue
            return price
    finally:
        if owned:
            await session.close()

    raise RateError(" — ".join(problems) or "هیچ صرافی جهانی پاسخ نداد")


async def _fetch_decimal(source: Source, session: aiohttp.ClientSession) -> Decimal:
    """یک منبع را می‌خواند و عددِ خام (پس از تقسیم بر واحد) می‌دهد."""
    timeout = aiohttp.ClientTimeout(total=TIMEOUT_SECONDS)
    try:
        if source.method == "POST":
            call = session.post(
                source.url, json=source.payload, params=source.params, timeout=timeout
            )
        else:
            call = session.get(source.url, params=source.params, timeout=timeout)
        async with call as response:
            if response.status != 200:
                raise RateError(f"پاسخ {response.status}")
            body = await response.json(content_type=None)
    except RateError:
        raise
    except TimeoutError as exc:
        raise RateError(f"در {TIMEOUT_SECONDS} ثانیه پاسخ نداد") from exc
    except aiohttp.ClientError as exc:
        # پیام خودِ کتابخانه معمولاً علت واقعی را دارد — DNS، اتصال
        # رد شده، گواهی. بدون آن، ادمین فقط «نشد» می‌بیند.
        raise RateError(f"اتصال ممکن نشد ({type(exc).__name__})") from exc
    except (ValueError, TypeError) as exc:
        raise RateError("پاسخ قابل خواندن نبود") from exc

    price = _first_number(_dig(body, source.path) if source.path else body, source.keys)
    if price is None:
        raise RateError("قیمتی در پاسخ نبود")
    return price / source.divisor


async def _fetch(source: Source, session: aiohttp.ClientSession) -> int:
    """همان، ولی تومانِ گرد‌شده — برای منبع‌های تومانی."""
    return int(await _fetch_decimal(source, session))


async def market_toman(
    coin: str = coins.USDT, *, session: aiohttp.ClientSession | None = None
) -> int:
    """نرخ بازار این ارز به <b>تومان</b>.

    هر دو ارز از یک جا می‌آیند — نوبیتکس، بازار USDTIRT برای تتر و
    TRXIRT برای ترون. یعنی نرخ تومانی ترون <b>مستقیم</b> از بازار
    خوانده می‌شود، نه از ضرب قیمت دلاری در نرخ دلار: یک مرحله کمتر،
    یک جای کمترِ خطا.

    نوبیتکس قیمت را به <b>ریال</b> می‌دهد. تقسیم بر ده جایی است که
    اشتباهش گران تمام می‌شود: ده برابر خطا یعنی اشتراکِ یک‌دهم قیمت.

    اگر منبع اول نشد، دومی امتحان می‌شود. خطای <b>همه‌ی</b> منبع‌ها در
    پیام می‌آید، چون وقتی هیچ‌کدام کار نمی‌کند، دانستنِ اینکه هرکدام
    چه گفت تنها راه فهمیدن علت است.
    """
    spec = coins.get(coin)
    if spec is None:
        raise RateError(f"ارز ناشناخته: {coin}")

    low, high = SANE_RANGE.get(spec.code, (MIN_SANE, MAX_SANE))

    owned = session is None
    # نشستی که اگر نامِ صرافی با DNS سیستم ترجمه نشد، از راه HTTPS
    # بپرسد. روی سرور خارج از ایران، همین یک قدم فرق بین «قیمت داریم»
    # و «هیچ منبعی جواب نداد» است.
    session = session or dnsfix.session()
    problems: list[str] = []

    try:
        for source in _sources(spec):
            try:
                toman = await _fetch(source, session)
            except RateError as exc:
                problems.append(f"{source.name}: {exc}")
                continue

            # بازه بر حسب ارز فرق دارد: یک تتر ده‌ها هزار تومان است و
            # یک ترون چند هزار تومان. یک بازه‌ی مشترک یا خیلی گشاد
            # می‌شد یا یکی از دو ارز را رد می‌کرد.
            if not low <= toman <= high:
                problems.append(f"{source.name}: عدد نامعقول ({toman:,})")
                continue

            if problems:
                log.info("نرخ %s از منبع پشتیبان گرفته شد: %s", spec.symbol, source.name)
            return toman

        # هیچ منبع تومانی نشد. اگر این ارز از روی تتر قابل حساب کردن
        # است، همان مسیر — وگرنه خطا، و نرخ قبلی سر جایش می‌ماند.
        if spec.code != coins.USDT:
            try:
                return await _from_usdt(spec, session, low, high)
            except RateError as exc:
                problems.append(str(exc))
    finally:
        if owned:
            await session.close()

    raise RateError(" — ".join(problems) or "هیچ منبعی پاسخ نداد")


async def _from_usdt(spec, session, low: int, high: int) -> int:
    """تومانِ این ارز، از قیمت جهانی‌اش ضربدر نرخ تتر.

    <b>نرخ تتر از دیتابیس خودمان می‌آید</b>، نه از بازار — چون بازار
    ایرانی از این سرور در دسترس نیست. یعنی این مسیر روی عددی تکیه
    دارد که ادمین گذاشته، و اگر آن عدد کهنه باشد، این هم کهنه است.
    برای همین سنِ نرخ تتر در پنل دیده می‌شود.
    """
    anchor = await crypto.rate(coins.USDT)
    if anchor <= 0:
        raise RateError(
            "نرخ تتر تنظیم نشده، پس قیمت ترون هم قابل حساب کردن نیست"
        )

    price = await usdt_price(spec.code, session=session)
    toman = int(price * anchor)
    if not low <= toman <= high:
        raise RateError(f"نرخ محاسبه‌شده معقول نیست: {toman:,}")

    log.info(
        "نرخ %s از قیمت جهانی حساب شد: %s × %s تومان = %s",
        spec.symbol, price, anchor, toman,
    )
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


async def margin(coin: str = coins.USDT) -> int:
    """حاشیه‌ی این ارز.

    اگر ادمین چیزی تنظیم نکرده باشد، پیش‌فرضِ خودِ ارز به کار می‌رود —
    ترون بیشتر، چون در فاصله‌ی ساخت درخواست تا پرداخت واقعاً تکان
    می‌خورد.
    """
    spec = coins.get(coin)
    fallback = spec.default_margin if spec else DEFAULT_MARGIN
    stored = await _read(f"{MARGIN_KEY}:{coin}")
    if stored is None:
        stored = await _read(MARGIN_KEY)      # مقدار مشترکِ قدیمی
    if stored is None:
        return fallback
    try:
        value = int(stored)
    except (TypeError, ValueError):
        return fallback
    return value if 0 <= value <= MAX_MARGIN else fallback


async def set_margin(
    value, *, coin: str = coins.USDT, admin_id: int | None = None
) -> int | None:
    try:
        number = int(str(value).strip())
    except (TypeError, ValueError):
        return None
    if not 0 <= number <= MAX_MARGIN:
        return None
    await _write(f"{MARGIN_KEY}:{coin}", number, admin_id)
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


@dataclass(frozen=True)
class Outcome:
    """نتیجه‌ی یک تلاش برای گرفتن نرخ.

    <b>چرا فقط یک عدد کافی نبود.</b> قبلاً خروجی صفر هم یعنی «بازار
    تکان نخورده» بود، هم «صرافی جواب نداد»، هم «جهش مشکوک بود». ادمین
    در پنل پیام «نرخی عوض نشد یا خواندن ناموفق بود» می‌دید و هیچ راهی
    نداشت بفهمد کدام‌یک — یعنی وقتی قیمت‌گذاری می‌خوابید، تشخیصش فقط
    از روی لاگ سرور ممکن بود.
    """

    coin: str
    rate: int = 0        # نرخ تازه‌ی ثبت‌شده، یا صفر
    market: int = 0      # نرخ بازار پیش از حاشیه
    error: str = ""      # چرا خوانده نشد
    note: str = ""       # خوانده شد ولی ثبت نشد — و چرا

    @property
    def changed(self) -> bool:
        return self.rate > 0


async def refresh(coin: str = coins.USDT, *, force: bool = False) -> Outcome:
    """یک بار نرخ این ارز را از بازار می‌گیرد و ثبت می‌کند.

    <code>force</code> محافظِ جهش را کنار می‌گذارد — فقط برای وقتی که
    ادمین خودش خواسته.
    """
    if not force and not await is_auto():
        return Outcome(coin=coin, note="نرخ خودکار خاموش است")

    spec = coins.get(coin)
    name = spec.symbol if spec else coin

    try:
        market = await market_toman(coin)
    except RateError as exc:
        # نرخ قبلی سر جایش می‌ماند؛ خاموش شدن پرداخت بدتر از نرخِ
        # کمی قدیمی است
        log.warning("خواندن نرخ %s ناموفق بود: %s", name, exc)
        return Outcome(coin=coin, error=str(exc))

    fresh = with_margin(market, await margin(coin))
    current = await crypto.rate(coin)

    if fresh == current:
        return Outcome(coin=coin, market=market, note="تغییری نداشت")

    if not force and jumped(current, fresh):
        from telkap.services import alerts, roles

        log.warning("جهش نرخ %s نادیده گرفته شد: %s → %s", name, current, fresh)
        await alerts.send(
            f"⚠️ <b>نرخ خودکار {name} اعمال نشد</b>\n\n"
            f"نرخ فعلی: <b>{current:,}</b> تومان\n"
            f"نرخ بازار: <b>{fresh:,}</b> تومان\n\n"
            "اختلاف بیش از حد انتظار است، پس تغییری داده نشد.\n"
            "<i>اگر بازار واقعاً این‌قدر تکان خورده، از پنل ادمین دستی "
            "ثبتش کنید.</i>",
            cap=roles.CAP_MONEY,
            key=f"ratejump:{coin}:{fresh}",
            cooldown=0,
        )
        return Outcome(
            coin=coin,
            market=market,
            note=f"جهش مشکوک ({current:,} ← {fresh:,})؛ اعمال نشد",
        )

    await crypto.set_rate(fresh, coin=coin, admin_id=None)
    log.info("نرخ %s به‌روز شد: %s تومان (بازار %s)", name, fresh, market)
    return Outcome(coin=coin, rate=fresh, market=market)


async def refresh_all(*, force: bool = False) -> dict[str, Outcome]:
    """همه‌ی ارزها. خرابیِ یکی نباید جلوی بقیه را بگیرد."""
    result: dict[str, Outcome] = {}
    for code in coins.all_codes():
        try:
            result[code] = await refresh(code, force=force)
        except Exception as exc:
            log.exception("به‌روزرسانی نرخ %s شکست خورد", code)
            result[code] = Outcome(coin=code, error=f"خطای غیرمنتظره: {exc}")
    return result


async def run_forever() -> None:
    while True:
        try:
            await asyncio.sleep(INTERVAL_SECONDS)
            await refresh_all()
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("چرخه‌ی نرخ خودکار تتر با خطا مواجه شد")
