"""وقتی نامِ صرافی روی این سرور ترجمه نمی‌شود.

<b>مسئله‌ی واقعی که دیدیم.</b> سرور به اینترنت وصل است — تلگرام و
گیت‌هاب کار می‌کنند — ولی <code>api.nobitex.ir</code> اصلاً به IP
ترجمه نمی‌شود. خطا <code>ClientConnectorDNSError</code> است، یعنی
حتی به مرحله‌ی اتصال نمی‌رسیم. صرافی ما را رد نکرده؛ ما اصلاً پیدایش
نمی‌کنیم.

<b>چرا این اتفاق می‌افتد.</b> بعضی سرویس‌های ایرانی برای درخواست‌های
خارج از ایران پاسخ DNS نمی‌دهند، و بعضی ارائه‌دهنده‌های خارجی هم
دامنه‌های <code>.ir</code> را کامل ترجمه نمی‌کنند. در هر دو حالت
اشکال در <b>ترجمه‌ی نام</b> است، نه در خودِ اتصال.

<b>راه‌حل.</b> نام را از یک سرویس DNS-over-HTTPS می‌پرسیم — یعنی یک
درخواست HTTPS معمولی به کلادفلر یا گوگل که از همه‌جا کار می‌کند — و
IP به‌دست‌آمده را به aiohttp می‌دهیم. اتصال بعدی مستقیم به همان IP
است، با همان نام در SNI و سرصفحه‌ی Host، پس گواهی TLS هم درست
بررسی می‌شود.

<b>اول همیشه DNS خودِ سیستم.</b> این مسیر فقط وقتی به کار می‌آید که
راه معمولی شکست بخورد؛ جایگزین کردنش یعنی یک وابستگی تازه به سرویسی
بیرونی، برای مسئله‌ای که اغلب وجود ندارد.
"""
from __future__ import annotations

import logging
import socket
import time

import aiohttp
from aiohttp.abc import AbstractResolver

log = logging.getLogger(__name__)

# دو سرویس، چون یکی‌شان هم ممکن است در دسترس نباشد. هر دو پاسخ JSON
# با همان شکل می‌دهند.
DOH_ENDPOINTS = (
    "https://cloudflare-dns.com/dns-query",
    "https://dns.google/resolve",
)

TIMEOUT_SECONDS = 10
CACHE_SECONDS = 300        # پنج دقیقه؛ IP صرافی هر لحظه عوض نمی‌شود

_cache: dict[str, tuple[float, list[str]]] = {}


def forget() -> None:
    """حافظه را خالی می‌کند. برای تست."""
    _cache.clear()


async def over_https(host: str, *, session: aiohttp.ClientSession | None = None):
    """IPv4 این نام را از راه DNS-over-HTTPS می‌پرسد.

    فهرست خالی یعنی هیچ‌کدام از سرویس‌ها جوابی ندادند.
    """
    hit = _cache.get(host)
    if hit and time.monotonic() - hit[0] < CACHE_SECONDS:
        return hit[1]

    owned = session is None
    session = session or aiohttp.ClientSession()
    found: list[str] = []

    try:
        for endpoint in DOH_ENDPOINTS:
            try:
                async with session.get(
                    endpoint,
                    params={"name": host, "type": "A"},
                    headers={"Accept": "application/dns-json"},
                    timeout=aiohttp.ClientTimeout(total=TIMEOUT_SECONDS),
                ) as response:
                    if response.status != 200:
                        continue
                    body = await response.json(content_type=None)
            except Exception:                      # noqa: BLE001
                # هر خرابی اینجا فقط یعنی «این سرویس نه»؛ بعدی را
                # امتحان می‌کنیم و در نهایت فهرست خالی برمی‌گردد
                log.debug("پرسش DoH از %s ناموفق بود", endpoint, exc_info=True)
                continue

            # نوع ۱ یعنی رکورد A. بقیه‌ی رکوردها (CNAME و…) IP نیستند
            # و دادنشان به aiohttp یعنی خطای مبهم در لایه‌ی بعد.
            for answer in (body or {}).get("Answer") or ():
                if isinstance(answer, dict) and answer.get("type") == 1:
                    address = str(answer.get("data") or "").strip()
                    if address and address not in found:
                        found.append(address)
            if found:
                break
    finally:
        if owned:
            await session.close()

    if found:
        _cache[host] = (time.monotonic(), found)
    return found


class Resolver(AbstractResolver):
    """DNS سیستم، و اگر نشد از راه HTTPS.

    <b>ترتیب مهم است.</b> راه معمولی سریع‌تر است، به سرویس بیرونی
    وابسته نیست، و در ۹۹ درصد موارد کار می‌کند. این کلاس فقط جایی
    وارد می‌شود که آن راه شکست بخورد.
    """

    def __init__(self) -> None:
        self._session: aiohttp.ClientSession | None = None

    async def resolve(self, host: str, port: int = 0, family=socket.AF_INET):
        try:
            infos = await _system(host, port, family)
            if infos:
                return infos
        except OSError as exc:
            log.info("ترجمه‌ی %s با DNS سیستم نشد (%s)؛ از HTTPS می‌پرسیم", host, exc)

        if self._session is None or self._session.closed:
            # این نشست عمداً از resolver پیش‌فرض استفاده می‌کند: نامِ
            # خودِ سرویس DoH با DNS معمولی ترجمه می‌شود، وگرنه دور
            # می‌افتیم.
            self._session = aiohttp.ClientSession()

        addresses = await over_https(host, session=self._session)
        if not addresses:
            raise OSError(f"نام {host} نه با DNS سیستم ترجمه شد نه با DoH")

        log.info("%s از راه DoH ترجمه شد: %s", host, addresses[0])
        return [
            {
                "hostname": host,
                "host": address,
                "port": port,
                "family": socket.AF_INET,
                "proto": 0,
                "flags": socket.AI_NUMERICHOST,
            }
            for address in addresses
        ]

    async def close(self) -> None:
        if self._session is not None and not self._session.closed:
            await self._session.close()
        self._session = None


async def _system(host: str, port: int, family):
    """همان کاری که aiohttp به‌طور پیش‌فرض می‌کند."""
    import asyncio

    loop = asyncio.get_running_loop()
    infos = await loop.getaddrinfo(
        host, port, family=family, type=socket.SOCK_STREAM, flags=socket.AI_ADDRCONFIG
    )
    return [
        {
            "hostname": host,
            "host": address[0],
            "port": address[1],
            "family": info_family,
            "proto": proto,
            "flags": socket.AI_NUMERICHOST,
        }
        for info_family, _type, proto, _canon, address in infos
    ]


def session(**kwargs) -> aiohttp.ClientSession:
    """یک ClientSession که نامِ ترجمه‌نشدنی را از راه HTTPS پیدا می‌کند."""
    return aiohttp.ClientSession(
        connector=aiohttp.TCPConnector(resolver=Resolver()), **kwargs
    )
