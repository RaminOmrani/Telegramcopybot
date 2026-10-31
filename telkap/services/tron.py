"""خواندن تراکنش‌های تتر از بلاک‌چین ترون.

<b>چرا این هست.</b> تا امروز هر پرداخت تتری را باید ادمین دستی
می‌دید و تأیید می‌کرد: کاربر هش تراکنش را می‌فرستاد و تا وقتی کسی
بیدار بود و نگاه می‌کرد، اشتراکش فعال نمی‌شد. این ماژول همان نگاه
کردن را خودکار می‌کند.

<b>چرا بر اساس نشانی می‌پرسیم، نه بر اساس هش.</b> ساده‌ترین راه این
بود که هشِ فرستاده‌شده را مستقیم از شبکه بپرسیم. ولی آن‌وقت باید
جداگانه بررسی می‌کردیم که مقصد واقعاً <b>ولت ماست</b> — و اگر یک
جا از قلم می‌افتاد، هر کسی می‌توانست هش تراکنشِ شخص دیگری را
بفرستد و اشتراک بگیرد. اینجا برعکس عمل می‌شود: <b>فهرست واریزهای
ولتِ خودمان</b> خوانده می‌شود و هشِ کاربر در آن جست‌وجو می‌گردد.
تراکنشی که به ما نرسیده باشد اصلاً در فهرست نیست، پس آن اشتباه
ممکن نیست.

<b>وابستگی به یک سرویس بیرونی.</b> TronGrid درگاه عمومی شبکه‌ی
ترون است. بدون کلید هم جواب می‌دهد ولی سقف نرخ سخت‌گیرانه‌تری دارد؛
با TRON_API_KEY در .env سقف بالاتر می‌رود. اگر از دسترس خارج شود،
هیچ پرداختی گم نمی‌شود — فقط تأیید خودکار انجام نمی‌شود و همان
مسیر دستیِ ادمین سر جایش است.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from decimal import Decimal

import aiohttp

from telkap.services import coins

log = logging.getLogger(__name__)

API_BASE = "https://api.trongrid.io"

# قرارداد رسمی USDT روی شبکه‌ی ترون، از coins.py — یک جا تعریف
# می‌شود تا اضافه کردن ارز تازه یعنی ویرایش یک فایل، نه چند تا.
USDT_CONTRACT = coins.USDT_CONTRACT

# ۱ TRX = ۱٬۰۰۰٬۰۰۰ سان. خواندن عدد خام یعنی مبلغ را یک میلیون برابر
# دیدن — همان اشتباهی که برای تتر هم ممکن بود.
SUN = 1_000_000

TIMEOUT_SECONDS = 20
MAX_PAGE = 200          # سقف خودِ TronGrid
MAX_PAGES = 5           # بیشتر از این یعنی چیزی غیرعادی است، نه یک صف پرداخت


class TronError(Exception):
    """خطای شبکه یا پاسخ نامفهوم. پرداخت رد نمی‌شود، فقط عقب می‌افتد."""


@dataclass(frozen=True)
class Transfer:
    """یک واریز TRC20 به ولت ما."""

    tx_id: str
    sender: str
    to: str
    contract: str
    amount: Decimal          # به واحد خودِ توکن، نه واحد خام
    symbol: str
    timestamp_ms: int

    @property
    def is_usdt(self) -> bool:
        return self.contract == USDT_CONTRACT


def _amount(raw, decimals) -> Decimal:
    """مقدار خام را به واحد توکن تبدیل می‌کند.

    تتر روی ترون شش رقم اعشار دارد، پس ۱۲٬۵۰۰٬۰۰۰ یعنی ۱۲٫۵ تتر.
    خواندن مستقیمِ عدد خام یعنی مبلغ را یک میلیون برابر دیدن.
    """
    try:
        places = int(decimals)
        value = Decimal(str(raw))
    except (TypeError, ValueError, ArithmeticError):
        return Decimal("0")
    if places < 0 or places > 30:
        return Decimal("0")
    return value / (Decimal(10) ** places)


def _parse(row: dict) -> Transfer | None:
    """یک ردیف از پاسخ TronGrid را به Transfer تبدیل می‌کند.

    ردیفی که شکلش را نمی‌فهمیم کنار گذاشته می‌شود؛ حدس زدن روی
    داده‌ی پولی بدترین کار ممکن است.
    """
    if not isinstance(row, dict):
        return None
    token = row.get("token_info") or {}
    tx_id = str(row.get("transaction_id") or "").lower()
    if len(tx_id) != 64:
        return None

    amount = _amount(row.get("value"), token.get("decimals"))
    if amount <= 0:
        return None

    return Transfer(
        tx_id=tx_id,
        sender=str(row.get("from") or ""),
        to=str(row.get("to") or ""),
        contract=str(token.get("address") or ""),
        amount=amount,
        symbol=str(token.get("symbol") or ""),
        timestamp_ms=int(row.get("block_timestamp") or 0),
    )


def _headers(api_key: str = "") -> dict:
    headers = {"Accept": "application/json"}
    if api_key:
        headers["TRON-PRO-API-KEY"] = api_key
    return headers


async def incoming_usdt(
    address: str,
    *,
    since_ms: int = 0,
    api_key: str = "",
    session: aiohttp.ClientSession | None = None,
) -> dict[str, Transfer]:
    """واریزهای تتر به این ولت، کلیدشده با هش تراکنش.

    <code>since_ms</code> جست‌وجو را به بازه‌ی مورد نیاز محدود می‌کند —
    بدون آن، هر بار کل تاریخچه‌ی ولت خوانده می‌شود.

    فقط واریز برمی‌گردد نه برداشت: پارامتر <code>only_to</code> این را
    از سمت خودِ سرویس تضمین می‌کند، پس تراکنش‌های خروجی اصلاً نمی‌آیند.
    """
    address = (address or "").strip()
    if not address:
        return {}

    params = {
        "only_to": "true",
        "only_confirmed": "true",      # تراکنش تأییدنشده ممکن است برگردد
        "contract_address": USDT_CONTRACT,
        "limit": str(MAX_PAGE),
    }
    if since_ms > 0:
        params["min_timestamp"] = str(since_ms)

    owned = session is None
    session = session or aiohttp.ClientSession()
    found: dict[str, Transfer] = {}
    url = f"{API_BASE}/v1/accounts/{address}/transactions/trc20"
    # نشانیِ صفحه‌ی بعد پارامترها را داخل خودش دارد. اگر دوباره
    # params بدهیم، بعضی‌شان تکراری می‌شوند و صفحه‌بندی می‌شکند.
    query: dict | None = params

    try:
        for _ in range(MAX_PAGES):
            async with session.get(
                url,
                params=query,
                headers=_headers(api_key),
                timeout=aiohttp.ClientTimeout(total=TIMEOUT_SECONDS),
            ) as response:
                if response.status == 429:
                    raise TronError("سقف درخواست TronGrid پر شده است")
                if response.status != 200:
                    raise TronError(f"TronGrid پاسخ {response.status} داد")
                body = await response.json(content_type=None)

            if not isinstance(body, dict):
                raise TronError("پاسخ TronGrid قابل خواندن نبود")

            for row in body.get("data") or ():
                transfer = _parse(row)
                if transfer is not None and transfer.is_usdt:
                    found[transfer.tx_id] = transfer

            url = ((body.get("meta") or {}).get("links") or {}).get("next") or ""
            if not url:
                break
            query = None
    except TronError:
        raise
    except TimeoutError as exc:
        raise TronError("TronGrid در زمان مقرر پاسخ نداد") from exc
    except aiohttp.ClientError as exc:
        raise TronError("اتصال به TronGrid ممکن نشد") from exc
    except (ValueError, TypeError) as exc:
        raise TronError("پاسخ TronGrid قابل خواندن نبود") from exc
    finally:
        if owned:
            await session.close()

    return found


# ── ترون، ارز بومی شبکه ─────────────────────────────────────────────
#
# <b>چرا مسیرش کاملاً جداست.</b> تتر توکن است: انتقالش یک رویداد روی
# قرارداد ثبت می‌کند و سرویس آن را در فهرست trc20 می‌دهد. ترون خودِ
# ارز شبکه است و انتقالش یک TransferContract داخل تراکنش است، نه
# رویداد. پس نه همان نشانی سرویس جواب می‌دهد نه همان شکل پاسخ.


def _tron_amount(raw) -> Decimal:
    try:
        return Decimal(str(raw)) / SUN
    except (TypeError, ValueError, ArithmeticError):
        return Decimal("0")


def _parse_native(row: dict) -> Transfer | None:
    """یک تراکنش خام ترون را به Transfer تبدیل می‌کند.

    فقط انتقال ساده و <b>موفق</b> پذیرفته می‌شود. تراکنشی که روی
    شبکه شکست خورده در فهرست هست ولی پولی جابه‌جا نکرده — پذیرفتنش
    یعنی اشتراک رایگان.
    """
    if not isinstance(row, dict):
        return None

    tx_id = str(row.get("txID") or "").lower()
    if len(tx_id) != 64:
        return None

    results = row.get("ret") or []
    if not any(
        (item or {}).get("contractRet") == "SUCCESS"
        for item in results
        if isinstance(item, dict)
    ):
        return None

    contracts = ((row.get("raw_data") or {}).get("contract")) or []
    for item in contracts:
        if not isinstance(item, dict) or item.get("type") != "TransferContract":
            continue
        value = ((item.get("parameter") or {}).get("value")) or {}
        amount = _tron_amount(value.get("amount"))
        if amount <= 0:
            continue
        return Transfer(
            tx_id=tx_id,
            sender=str(value.get("owner_address") or ""),
            to=str(value.get("to_address") or ""),
            contract="",                     # ارز بومی، قرارداد ندارد
            amount=amount,
            symbol="TRX",
            timestamp_ms=int(row.get("block_timestamp") or 0),
        )
    return None


async def incoming_trx(
    address: str,
    *,
    since_ms: int = 0,
    api_key: str = "",
    session: aiohttp.ClientSession | None = None,
) -> dict[str, Transfer]:
    """واریزهای ترون به این ولت، کلیدشده با هش تراکنش.

    مثل incoming_usdt فقط واریز برمی‌گرداند نه برداشت، و همان دلیل
    امنیتی را دارد: فهرست از <b>ولت خودمان</b> پرسیده می‌شود، پس
    تراکنشی که به ما نرسیده اصلاً در آن نیست.
    """
    address = (address or "").strip()
    if not address:
        return {}

    params = {
        "only_to": "true",
        "only_confirmed": "true",
        "limit": str(MAX_PAGE),
    }
    if since_ms > 0:
        params["min_timestamp"] = str(since_ms)

    owned = session is None
    session = session or aiohttp.ClientSession()
    found: dict[str, Transfer] = {}
    url = f"{API_BASE}/v1/accounts/{address}/transactions"
    query: dict | None = params

    try:
        for _ in range(MAX_PAGES):
            async with session.get(
                url,
                params=query,
                headers=_headers(api_key),
                timeout=aiohttp.ClientTimeout(total=TIMEOUT_SECONDS),
            ) as response:
                if response.status == 429:
                    raise TronError("سقف درخواست TronGrid پر شده است")
                if response.status != 200:
                    raise TronError(f"TronGrid پاسخ {response.status} داد")
                body = await response.json(content_type=None)

            if not isinstance(body, dict):
                raise TronError("پاسخ TronGrid قابل خواندن نبود")

            for row in body.get("data") or ():
                transfer = _parse_native(row)
                if transfer is not None:
                    found[transfer.tx_id] = transfer

            url = ((body.get("meta") or {}).get("links") or {}).get("next") or ""
            if not url:
                break
            query = None
    except TronError:
        raise
    except TimeoutError as exc:
        raise TronError("TronGrid در زمان مقرر پاسخ نداد") from exc
    except aiohttp.ClientError as exc:
        raise TronError("اتصال به TronGrid ممکن نشد") from exc
    except (ValueError, TypeError) as exc:
        raise TronError("پاسخ TronGrid قابل خواندن نبود") from exc
    finally:
        if owned:
            await session.close()

    return found


async def incoming(
    coin: str,
    address: str,
    *,
    since_ms: int = 0,
    api_key: str = "",
    session: aiohttp.ClientSession | None = None,
) -> dict[str, Transfer]:
    """واریزهای این ارز — مسیر درست را بر اساس نوع ارز انتخاب می‌کند."""
    spec = coins.get(coin)
    if spec is None:
        return {}
    reader = incoming_trx if spec.is_native else incoming_usdt
    return await reader(
        address, since_ms=since_ms, api_key=api_key, session=session
    )
