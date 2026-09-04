"""خواندن منابع غیرتلگرامی: فید RSS و Atom.

<b>چرا این هست.</b> تا امروز مبدا فقط کانال تلگرام بود. با این، هر
سایتی که فید دارد — خبرگزاری، وبلاگ، فروشگاه — می‌تواند مبدا باشد و
پست‌هایش با همان قواعد فعلی (پاک‌سازی، امضا، فیلتر، مقصدهای چندگانه)
منتشر شود.

<b>سه خطری که در «یک URL را بخوان» پنهان است</b>، و هر سه اینجا
بسته شده‌اند:

۱. <b>SSRF.</b> کاربر آدرس را خودش می‌نویسد. اگر
   <code>http://127.0.0.1:8080/</code> بنویسد، سرور <b>پنل وب
   خودمان</b> را می‌خواند و جوابش را در کانال او منتشر می‌کند. پس
   آدرس‌هایی که به شبکه‌ی داخلی می‌رسند رد می‌شوند — و چون DNS
   می‌تواند دروغ بگوید، بررسی روی <b>آی‌پیِ حل‌شده</b> انجام
   می‌شود نه روی نام.

۲. <b>حجم.</b> فایل بی‌انتها حافظه‌ی سرور را می‌خورد. سقف سخت‌گیرانه
   است و خواندن با رسیدن به آن قطع می‌شود، نه بعد از دانلود کامل.

۳. <b>XML.</b> پارسر پایتون entity خارجی را باز نمی‌کند، ولی
   سقف حجم لایه‌ی دوم است در برابر فایلی که با تودرتویی حافظه را
   منفجر می‌کند.
"""
from __future__ import annotations

import asyncio
import hashlib
import html
import ipaddress
import logging
import re
import socket
from dataclasses import dataclass
from datetime import UTC, datetime
from urllib.parse import urljoin, urlparse
from xml.etree import ElementTree

import aiohttp

log = logging.getLogger(__name__)

MAX_BYTES = 2 * 1024 * 1024      # فید معمولی چند ده کیلوبایت است
TIMEOUT_SECONDS = 20
MAX_ITEMS = 60                   # بیشتر از این را هیچ چرخه‌ای لازم ندارد
MAX_REDIRECTS = 3

# اگر فید فارسی نباشد و سرور encoding را اشتباه بگوید، متن خراب می‌شود.
# پارسر XML خودش encoding را از خط اول فایل می‌خواند، پس bytes خام به
# آن داده می‌شود نه رشته‌ی از پیش دیکدشده.
_NS = {
    "atom": "http://www.w3.org/2005/Atom",
    "content": "http://purl.org/rss/1.0/modules/content/",
    "media": "http://search.yahoo.com/mrss/",
    "dc": "http://purl.org/dc/elements/1.1/",
    "rss1": "http://purl.org/rss/1.0/",
}

_TAG_RE = re.compile(r"<[^>]+>")
_SPACE_RE = re.compile(r"[ \t\xa0]+")
_BLANK_RE = re.compile(r"\n{3,}")
# پاراگراف با خط خالی جدا می‌شود ولی شکستن خط فقط یک خط — همان‌طور که
# در مرورگر دیده می‌شود. اگر هر دو یکی حساب شوند، یا متن به هم می‌چسبد
# یا فهرست‌ها بی‌خود کش می‌آیند.
_PARA_RE = re.compile(r"(?i)</p>|</div>|</h[1-6]>")
_BR_RE = re.compile(r"(?i)<br\s*/?>|</li>|</tr>")


class FeedError(Exception):
    """خطایی که پیامش مستقیم به کاربر نشان داده می‌شود، پس فارسی است."""


@dataclass(frozen=True)
class FeedItem:
    """یک آیتم فید، مستقل از اینکه RSS بود یا Atom."""

    guid: str
    title: str
    summary: str
    link: str
    image: str
    published: datetime | None

    @property
    def key(self) -> int:
        """شناسه‌ی عددیِ پایدار برای این آیتم.

        جدول <code>message_map</code> که تکراری‌ها را می‌شناسد
        <code>src_msg_id</code> عددی می‌خواهد، ولی فید شناسه‌ی رشته‌ای
        دارد. هش، پل بین این دو است — و چون از guid ساخته می‌شود،
        همان آیتم در هر بار خواندن همان عدد را می‌گیرد.

        ۶۳ بیت، چون ستون BIGINT علامت‌دار است.
        """
        digest = hashlib.sha256(self.guid.encode("utf-8")).digest()
        return int.from_bytes(digest[:8], "big") >> 1


# ── امنیت آدرس ───────────────────────────────────────────────────────


def _is_private(address: str) -> bool:
    try:
        ip = ipaddress.ip_address(address)
    except ValueError:
        return True     # چیزی که آی‌پی نیست، قابل اعتماد هم نیست
    return (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_reserved
        or ip.is_multicast
        or ip.is_unspecified
    )


async def _resolve(host: str) -> list[str]:
    loop = asyncio.get_running_loop()
    try:
        infos = await loop.getaddrinfo(host, None, proto=socket.IPPROTO_TCP)
    except OSError as exc:
        raise FeedError("این دامنه پیدا نشد. آدرس را دوباره ببینید.") from exc
    return [info[4][0] for info in infos]


async def check_url(url: str) -> str:
    """آدرس را می‌سنجد و شکل تمیزشده‌اش را برمی‌گرداند.

    جدا از fetch است تا هنگام <b>ساختن کار</b> هم بشود صداش زد و
    خطای روشن به کاربر داد، به‌جای اینکه ساعت‌ها بعد در لاگ بماند.
    """
    url = (url or "").strip()
    if not url:
        raise FeedError("آدرس فید خالی است.")
    if "://" not in url:
        url = f"https://{url}"

    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"}:
        raise FeedError("فقط آدرس http و https پذیرفته می‌شود.")
    if not parsed.hostname:
        raise FeedError("این آدرس دامنه ندارد.")

    # نامی که خودش آی‌پی داخلی است، پیش از DNS رد می‌شود
    for address in await _resolve(parsed.hostname):
        if _is_private(address):
            raise FeedError("این آدرس به شبکه‌ی داخلی اشاره می‌کند و پذیرفته نیست.")

    return url


# ── دریافت ───────────────────────────────────────────────────────────


async def _read_capped(response) -> bytes:
    """خواندن با سقف حجم — پیش از پر شدن حافظه قطع می‌شود."""
    chunks: list[bytes] = []
    size = 0
    async for chunk in response.content.iter_chunked(64 * 1024):
        size += len(chunk)
        if size > MAX_BYTES:
            raise FeedError("این فید بیش از حد بزرگ است.")
        chunks.append(chunk)
    return b"".join(chunks)


async def fetch(url: str, *, session: aiohttp.ClientSession | None = None) -> list[FeedItem]:
    """فید را می‌گیرد و آیتم‌هایش را برمی‌گرداند (تازه‌ترین اول)."""
    url = await check_url(url)

    owned = session is None
    session = session or aiohttp.ClientSession()
    try:
        async with session.get(
            url,
            timeout=aiohttp.ClientTimeout(total=TIMEOUT_SECONDS),
            max_redirects=MAX_REDIRECTS,
            headers={"User-Agent": "Mozilla/5.0 (compatible; FeedReader/1.0)"},
            allow_redirects=True,
        ) as response:
            if response.status != 200:
                raise FeedError(f"سرور فید پاسخ {response.status} داد.")
            # تغییر مسیر می‌تواند به شبکه‌ی داخلی برساند؛ مقصد نهایی
            # همان‌قدر باید سنجیده شود که آدرس اول
            final = str(response.url)
            if final != url:
                await check_url(final)
            raw = await _read_capped(response)
    except FeedError:
        raise
    except TimeoutError as exc:
        raise FeedError("سرور فید در زمان مقرر پاسخ نداد.") from exc
    except aiohttp.ClientError as exc:
        raise FeedError("اتصال به سرور فید ممکن نشد.") from exc
    finally:
        if owned:
            await session.close()

    return parse(raw, base_url=url)


# ── پارس ─────────────────────────────────────────────────────────────


def _text(node) -> str:
    if node is None:
        return ""
    # Atom می‌تواند محتوا را به‌صورت HTML تودرتو بدهد؛ itertext همه را
    # جمع می‌کند، در حالی که .text فقط تا اولین تگ را می‌گیرد
    return "".join(node.itertext())


def clean_html(raw: str) -> str:
    """توضیحِ HTML دار را به متن ساده‌ی خوانا تبدیل می‌کند.

    شکستن خط پیش از حذف تگ‌ها انجام می‌شود، وگرنه پاراگراف‌ها به هم
    می‌چسبند و یک دیوار متن می‌شود.
    """
    if not raw:
        return ""
    text = _PARA_RE.sub("\n\n", raw)
    text = _BR_RE.sub("\n", text)
    text = _TAG_RE.sub("", text)
    text = html.unescape(text)
    text = _SPACE_RE.sub(" ", text)
    text = "\n".join(line.strip() for line in text.split("\n"))
    return _BLANK_RE.sub("\n\n", text).strip()


def _parse_date(raw: str) -> datetime | None:
    raw = (raw or "").strip()
    if not raw:
        return None

    from email.utils import parsedate_to_datetime

    for parse_one in (parsedate_to_datetime, datetime.fromisoformat):
        try:
            value = parse_one(raw.replace("Z", "+00:00"))
        except (TypeError, ValueError):
            continue
        if value.tzinfo is None:
            value = value.replace(tzinfo=UTC)
        return value
    return None


def _first(node, *paths) -> str:
    for path in paths:
        found = node.find(path, _NS)
        if found is not None:
            value = _text(found).strip()
            if value:
                return value
    return ""


def _link_of(node, base_url: str) -> str:
    """لینک آیتم — RSS آن را متن می‌گذارد، Atom در attribute."""
    direct = _first(node, "link", "rss1:link")
    if direct:
        return urljoin(base_url, direct)

    for element in node.findall("atom:link", _NS) + node.findall("link"):
        rel = element.get("rel") or "alternate"
        href = element.get("href")
        if href and rel == "alternate":
            return urljoin(base_url, href)
    return ""


def _image_of(node, base_url: str) -> str:
    """تصویر آیتم، اگر فید داده باشد."""
    for element in node.findall("enclosure"):
        kind = (element.get("type") or "").lower()
        href = element.get("url")
        if href and kind.startswith("image/"):
            return urljoin(base_url, href)

    for path in ("media:content", "media:thumbnail"):
        for element in node.findall(path, _NS):
            kind = (element.get("type") or element.get("medium") or "").lower()
            href = element.get("url")
            if href and (kind.startswith("image") or not kind):
                return urljoin(base_url, href)
    return ""


def _entries(root) -> list:
    """آیتم‌های فید، از هر سه قالب رایج."""
    for path in ("channel/item", "item", "rss1:item", "atom:entry", "entry"):
        found = root.findall(path, _NS)
        if found:
            return found
    return []


def parse(raw: bytes | str, *, base_url: str = "") -> list[FeedItem]:
    """XML فید را به آیتم‌ها تبدیل می‌کند.

    آیتمی که نه عنوان دارد و نه متن، کنار گذاشته می‌شود — پست خالی
    در کانال کاربر بدتر از نبودنش است.
    """
    if isinstance(raw, str):
        raw = raw.encode("utf-8")
    try:
        root = ElementTree.fromstring(raw)
    except ElementTree.ParseError as exc:
        raise FeedError("این آدرس فید معتبر RSS یا Atom نیست.") from exc

    entries = _entries(root)
    if not entries:
        raise FeedError("در این فید هیچ مطلبی پیدا نشد.")

    items: list[FeedItem] = []
    for node in entries[:MAX_ITEMS]:
        title = clean_html(_first(node, "title", "atom:title", "rss1:title"))
        body = clean_html(
            _first(
                node,
                "content:encoded",
                "description",
                "atom:content",
                "atom:summary",
                "rss1:description",
            )
        )
        link = _link_of(node, base_url)
        if not title and not body:
            continue

        # guid ترجیح اول است چون سایت آن را برای همین گذاشته. بدون آن،
        # لینک؛ و بدون لینک، خودِ متن — تا آیتمِ بی‌شناسه هم دو بار
        # منتشر نشود.
        guid = (
            _first(node, "guid", "atom:id", "rss1:link")
            or link
            or f"{title}\n{body}"[:400]
        )

        items.append(
            FeedItem(
                guid=guid,
                title=title,
                summary=body,
                link=link,
                image=_image_of(node, base_url),
                published=_parse_date(
                    _first(node, "pubDate", "atom:published", "atom:updated", "dc:date")
                ),
            )
        )

    if not items:
        raise FeedError("در این فید هیچ مطلبی پیدا نشد.")
    return items


def feed_title(raw: bytes | str) -> str:
    """نام فید، برای اینکه کار عنوان معنادار بگیرد نه خودِ URL."""
    if isinstance(raw, str):
        raw = raw.encode("utf-8")
    try:
        root = ElementTree.fromstring(raw)
    except ElementTree.ParseError:
        return ""
    for path in ("channel/title", "atom:title", "title", "rss1:channel/rss1:title"):
        found = root.find(path, _NS)
        if found is not None:
            return clean_html(_text(found))[:120]
    return ""
