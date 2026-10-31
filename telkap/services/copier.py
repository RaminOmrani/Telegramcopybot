"""موتور کپی: دریافت پیام از کانال مبدا، پردازش، و ارسال به کانال مقصد."""
from __future__ import annotations

import asyncio
import html
import logging
import random
import time
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError
from telethon.errors import (
    ChatWriteForbiddenError,
    FloodWaitError,
    MessageIdInvalidError,
    MessageNotModifiedError,
    PremiumAccountRequiredError,
)
from telethon.tl.custom import Button
from telethon.tl.types import (
    Document,
    DocumentAttributeAnimated,
    DocumentAttributeAudio,
    DocumentAttributeSticker,
    DocumentAttributeVideo,
    InputMediaPoll,
    KeyboardButtonUrl,
    MessageMediaDocument,
    MessageMediaPhoto,
    MessageMediaPoll,
    MessageMediaWebPage,
    Photo,
    Poll,
)

from telkap.config import get_settings
from telkap.db import get_session, log_activity
from telkap.models import (
    AbandonedPost,
    DailyStat,
    DeliveryTiming,
    Destination,
    MessageMap,
    PendingPost,
    RetryItem,
    Task,
    utcnow,
)
from telkap.plans import FEAT_MESSAGES, FEAT_WATERMARK
from telkap.services import (
    abandoned,
    aipass,
    alerts,
    cache,
    dedupe,
    docedit,
    entitlement,
    health,
    pending,
    richtext,
    roles,
    routing,
)
from telkap.services.filters import MessageFacts, content_hash, should_copy
from telkap.services.ratelimit import RateLimiter, daily_quota, hourly_quota
from telkap.services.transform import (
    apply_transforms,
    config_tag,
    drop_custom_emoji,
    remap_entities,
)
from telkap.services.watermark import apply_watermark, watermark_ready
from telkap.texts import fa_num

log = logging.getLogger(__name__)

# تایمر آلبومِ تلethon نیم ثانیه است. چهار ثانیه یعنی هم فرصت کافی
# برای رسیدن پیام‌های کندِ آلبوم، هم آن‌قدر کوتاه که کپی «لحظه‌ای»
# بماند. کمترش یعنی آلبوم دوتکه می‌شود، بیشترش یعنی تأخیر بی‌دلیل.
ALBUM_SAFETY_SECONDS = 4.0

# چقدر منتظر پر شدن یک جای رزروشده می‌مانیم. کمی بیشتر از تور ایمنی
# آلبوم، چون همان تور است که جا را آزاد می‌کند؛ این فقط برای وقتی است
# که خودِ تور هم به هر دلیلی نیامده باشد و صف نباید برای همیشه بایستد.
SLOT_WAIT_SECONDS = ALBUM_SAFETY_SECONDS + 2.0

# سه حالتِ ترتیب. توضیح کامل بالای _worker.
ORDER_STRICT = "strict"
ORDER_FAST = "fast"
ORDER_GRACE = "grace"

# سخت‌گیرترین حالت برنده است وقتی چند کار روی یک مبدا نظر متفاوت دارند
ORDER_RANK = {ORDER_FAST: 0, ORDER_GRACE: 1, ORDER_STRICT: 2}

# صف هر مبدا از این بلندتر شود یعنی چیزی گیر کرده — معمولاً یک فایل
# بزرگ. در لاگ هشدار می‌دهیم ولی چیزی دور ریخته نمی‌شود؛ دور ریختن،
# همان «پستِ گم‌شده»ای است که کل این کد برای نبودنش نوشته شده.
BUSY_QUEUE = 20

# کپی کندتر از این، در لاگ هشدار می‌گیرد. کاربر یک دقیقه را تحمل
# می‌کند؛ بیشترش یعنی چیزی درست کار نمی‌کند.
SLOW_COPY_SECONDS = 60

# فاصله‌ی تلاش‌های مجدد بر حسب ثانیه (۱ دقیقه، ۵ دقیقه، ۱۵ دقیقه، ۱ ساعت)
RETRY_BACKOFF = (60, 300, 900, 3600)

# <b>سقف صف تلاش مجدد برای هر کار.</b> اگر مقصدی واقعاً خراب باشد —
# دسترسی ادمین گرفته شده، کانال حذف شده — هر پستِ تازه‌ی مبدا یک آیتم
# تازه به صف اضافه می‌کند و صف تا بی‌نهایت بالا می‌رود. کاری که به این
# عدد برسد دیگر «کندِ موقت» نیست، خراب است: متوقف می‌شود و به کاربر
# گفته می‌شود، چون ادامه دادن فقط تلگرام را می‌کوبد.
MAX_RETRY_QUEUE = 60


class SendFailed(Exception):
    """ارسال شکست خورد و <b>خودش دوباره در صف گذاشته نشد</b>.

    فقط در مسیر تلاش مجدد پرتاب می‌شود تا کارگرِ صف بفهمد این تلاش
    ناموفق بوده و همان آیتمِ موجود را — با شمارنده‌ی یکی بیشتر —
    دوباره زمان‌بندی کند.
    """


class Unsendable(Exception):
    """این پست با <b>هیچ</b> تلاشِ دوباره‌ای نمی‌رود — رد کن، دوباره امتحان نکن.

    <b>چرا یک نوعِ جداگانه.</b> تا امروز هر خطای ارسال یک معنا داشت:
    «موقتی است، چهار بار دیگر امتحان کن». ولی پستی که رسانه‌اش اصلاً
    قابل دانلود نیست، یک ساعت و ربع بعد هم قابل دانلود نیست. نتیجه‌اش
    در ۳۱ اکتبر دیده شد: <b>یک</b> پست از نوعی که Telethonِ ما
    نمی‌شناسد، ۸۶ بار در صف رفت، حدود ۴۳۰ بار با اکانتِ مشتری ارسالش
    امتحان شد، و هر بار یک پیامِ «ارسال نشد» برای صاحب کانال آمد.

    `kind` نامِ نوعِ رسانه است تا هشدار به مشتری برای هر نوع فقط یک
    بار برود، نه برای هر پست.
    """

    def __init__(self, reason: str, *, kind: str = "") -> None:
        super().__init__(reason)
        self.kind = kind


# <b>رسانه‌هایی که فقط سرورِ تلگرام می‌تواند کپی‌شان کند.</b>
#
# برای اینها نه ارسال با مرجع کار می‌کند (Telethon برایشان ورودی
# نمی‌سازد) و نه دانلود (چیزی برای دانلود ندارند). تنها راه، فوروارد است
# که خودِ سرور انجامش می‌دهد بی‌آنکه ما لازم باشد محتوا را بفهمیم.
#
# MessageMediaUnsupported مهم‌ترینشان است: هر نوع پستی که تلگرام بعد از
# لایه‌ی Telethonِ ما (۱۹۳) اضافه کرده، به این شکل به ما می‌رسد. پس این
# فهرست با هر قابلیتِ تازه‌ی تلگرام خودبه‌خود بزرگ‌تر می‌شود بی‌آنکه
# لازم باشد اسمش را بدانیم.
#
# Dice هم اینجاست: ساختنِ دوباره‌اش عدد تازه‌ای می‌ریزد — یعنی «کپی»
# چیزی متفاوت با مبدأ می‌شود. فوروارد همان عدد را نگه می‌دارد.
_OPAQUE_MEDIA = frozenset({
    "MessageMediaUnsupported",
    "MessageMediaPaidMedia",
    "MessageMediaStory",
    "MessageMediaGiveaway",
    "MessageMediaGiveawayResults",
    "MessageMediaInvoice",
    "MessageMediaGame",
    "MessageMediaGeoLive",
    "MessageMediaDice",
})


def opaque_media(message) -> str:
    """نامِ نوعِ رسانه اگر فقط با فوروارد قابل کپی است؛ وگرنه رشته‌ی خالی.

    عکس یا فایلی که پوسته‌اش هست ولی محتوایش نه (منقضی‌شده، خالی) هم
    همین حکم را دارد: Telethon آن را «عکس» می‌شمارد ولی نه می‌فرستدش
    نه دانلودش می‌کند.
    """
    media = getattr(message, "media", None)
    if media is None:
        return ""
    name = type(media).__name__
    if name in _OPAQUE_MEDIA:
        return name
    if isinstance(media, MessageMediaPhoto) and not isinstance(media.photo, Photo):
        return name + "(empty)"
    if isinstance(media, MessageMediaDocument) and not isinstance(media.document, Document):
        return name + "(empty)"
    return ""


def _discard(paths) -> None:
    """فایل‌های موقت را پاک می‌کند — و <b>هرگز</b> خطا نمی‌دهد.

    <b>چرا «هرگز».</b> این تابع در finally بعد از یک ارسالِ <b>موفق</b>
    صدا می‌شود. خطایی که از finally بیرون بیاید، جای نتیجه‌ی ارسال را
    می‌گیرد: پستی که رفته «شکست» حساب می‌شود، دوباره فرستاده می‌شود، و
    مشتری همان پست را تا پنج بار در کانالش می‌بیند. دقیقاً همین اتفاق
    می‌افتاد: وقتی فقط یک فایل بود، فهرست به یک رشته تبدیل می‌شد و حلقه
    روی <b>حرف‌های</b> مسیر می‌چرخید تا به «/» برسد.

    پس اینجا فقط فهرست پذیرفته می‌شود، و هر خطای پاک کردن بلعیده
    می‌شود — یک فایلِ جامانده در پوشه‌ی موقت بی‌نهایت ارزان‌تر از یک پستِ
    پنج‌باره است.
    """
    if isinstance(paths, str | Path):
        paths = [paths]
    for path in paths or ():
        try:
            Path(path).unlink(missing_ok=True)
        except OSError:
            log.warning("فایل موقت %s پاک نشد", path, exc_info=True)


# خطاهایی که تکرارشان فرقی نمی‌کند: محتوا یا قالب پست ایراد دارد، نه
# شبکه یا سرعت. با نام مقایسه می‌شوند تا نبودنِ یکی در نسخه‌ای دیگر از
# Telethon کل ماژول را نشکند.
_PERMANENT_ERRORS = frozenset({
    "MediaEmptyError",
    "MediaInvalidError",
    "MessageEmptyError",
    "MessageTooLongError",
    "MediaCaptionTooLongError",
    "EntityBoundsInvalidError",
    "EntitiesTooLongError",
    "PhotoInvalidDimensionsError",
    "WebpageMediaEmptyError",
    "QuizCorrectAnswersEmptyError",
    "PollOptionInvalidError",
})


def is_permanent(exc: BaseException) -> bool:
    """آیا این خطا با تلاش دوباره درست می‌شود؟ False یعنی «شاید».

    TypeError هم دائمی است: Telethon وقتی نمی‌تواند از یک رسانه ورودی
    بسازد همین را می‌دهد («Cannot use … as file») — و اگر از ایرادِ کدِ
    خودمان باشد، تکرار هم درستش نمی‌کند.

    <b>ولی ValueError نه.</b> Telethon برای «این کانال را در کش ندارم»
    (Could not find the input entity) هم ValueError می‌دهد — همان خانواده‌ای
    که حالت ساده را بعد از هر ری‌استارت می‌خواباند — و آن با گرم شدنِ کش
    درست می‌شود. دائمی شمردنش یعنی دور ریختنِ پست‌هایی که چند دقیقه بعد
    می‌رفتند.
    """
    if isinstance(exc, Unsendable):
        return True
    if isinstance(exc, TypeError):
        return True
    return type(exc).__name__ in _PERMANENT_ERRORS


@dataclass
class _Outcome:
    """نتیجه‌ی ارسال به مقصدها، که `process` برای برگرداندنِ سهمیه می‌خواند."""

    sent: bool = False      # دست‌کم یک مقصد پستِ تازه گرفت
    already: bool = False   # تلاش مجدد دید که پست پیش‌تر رسیده بود
    queued: bool = False    # دست‌کم یک مقصد به صفِ تلاش مجدد رفت


class PartialForward(RuntimeError):
    """آلبومِ فورواردشده ناقص رسید. دائمی نیست؛ دوباره امتحان می‌شود."""


# فوروارد را تلگرام به خاطرِ <b>خودِ پست</b> نپذیرفت: کانالِ محافظت‌شده،
# پیامِ حذف‌شده. تکرار فرقی نمی‌کند.
_FORWARD_REFUSALS = frozenset({
    "ChatForwardsRestrictedError",
    "MessageIdInvalidError",
    "MessageIdsEmptyError",
})


def _forward_refused(exc: BaseException) -> bool:
    return type(exc).__name__ in _FORWARD_REFUSALS or is_permanent(exc)

# نامِ خواندنیِ هر مسیر. کدِ ذخیره‌شده انگلیسی و پایدار است تا با عوض
# شدنِ یک کلمه‌ی فارسی، آمارِ ماه‌های قبل بی‌معنا نشود.
PATH_LABELS = {
    DeliveryTiming.PATH_DIRECT: "مستقیم (بدون دانلود)",
    DeliveryTiming.PATH_TEXT: "فقط متن",
    DeliveryTiming.PATH_WATERMARK: "واترمارک",
    DeliveryTiming.PATH_REWRITE: "بازنویسی فایل",
    DeliveryTiming.PATH_REUPLOAD: "دانلود و آپلود مجدد",
    DeliveryTiming.PATH_FORWARD: "فوروارد بدون نام فرستنده",
}

VIA_LABELS = {
    DeliveryTiming.VIA_UPDATE: "آپدیت لحظه‌ای",
    DeliveryTiming.VIA_SWEEP: "جارو (آپدیتش نرسیده بود)",
    DeliveryTiming.VIA_RETRY: "تلاش مجدد",
    DeliveryTiming.VIA_QUEUE: "پس از صف انتظار",
    DeliveryTiming.VIA_HISTORY: "کپی آرشیو",
    DeliveryTiming.VIA_SIMPLE: "پویشِ حالت ساده",
    "unknown": "نامشخص (پیش از این اندازه‌گیری)",
}


class Slot:
    """یک جای رزروشده در صفِ یک مبدا.

    <b>چرا جا زودتر از محتوا رزرو می‌شود.</b> آلبوم پیام‌هایش جدا جدا
    می‌رسد و چند ثانیه طول می‌کشد تا کامل شود. اگر تازه آن‌وقت وارد صف
    می‌شد، پستِ متنیِ بعدی که فوراً آماده است از آن جلو می‌زد و ترتیبِ
    کانالِ مقصد با مبدا فرق می‌کرد. حالا جا همان لحظه‌ی دیدنِ اولین
    پیام گرفته می‌شود و صف همان‌جا منتظر پر شدنش می‌ماند.
    """

    __slots__ = ("messages", "ready")

    def __init__(self, messages=None, *, ready: bool = False) -> None:
        self.messages = list(messages or [])
        self.ready = asyncio.Event()
        if ready:
            self.ready.set()


def today_key(offset_hours: float | None = None) -> str:
    """کلید روز جاری به وقت محلی، برای جدول آمار روزانه."""
    if offset_hours is None:
        offset_hours = get_settings().timezone_offset
    return (utcnow() + timedelta(hours=offset_hours)).strftime("%Y-%m-%d")


def _as_target(dest_chat: str):
    """رشته‌ی ذخیره‌شده‌ی مقصد را به آیدی عددی یا یوزرنیم برمی‌گرداند."""
    return int(dest_chat) if dest_chat.lstrip("-").isdigit() else dest_chat


def classify_media(message) -> str:
    """نوع محتوای پیام را برای فیلترگذاری تشخیص می‌دهد."""
    media = getattr(message, "media", None)
    if media is None or isinstance(media, MessageMediaWebPage):
        return "text"
    if isinstance(media, MessageMediaPhoto):
        return "photo"
    if isinstance(media, MessageMediaPoll):
        return "poll"
    if isinstance(media, MessageMediaDocument):
        doc = media.document
        attrs = getattr(doc, "attributes", []) or []
        kinds = {type(a) for a in attrs}
        if DocumentAttributeSticker in kinds:
            return "sticker"
        if DocumentAttributeAnimated in kinds:
            return "animation"
        for attr in attrs:
            if isinstance(attr, DocumentAttributeVideo):
                return "video_note" if getattr(attr, "round_message", False) else "video"
            if isinstance(attr, DocumentAttributeAudio):
                return "voice" if getattr(attr, "voice", False) else "audio"
        return "document"
    return "document"


def media_size(message) -> int:
    doc = getattr(getattr(message, "media", None), "document", None)
    return int(getattr(doc, "size", 0) or 0)


def media_filename(message) -> str:
    """نام فایل پیوست، اگر داشته باشد."""
    doc = getattr(getattr(message, "media", None), "document", None)
    for attr in getattr(doc, "attributes", []) or []:
        name = getattr(attr, "file_name", None)
        if name:
            return str(name)
    return ""


def extract_buttons(message) -> list[list[Button]] | None:
    """دکمه‌های لینک‌دار پست را برای بازسازی در مقصد بیرون می‌کشد.

    فقط دکمه‌های URL قابل کپی‌اند؛ دکمه‌های callback به ربات مبدا وصل‌اند
    و در کانال دیگری کار نمی‌کنند، پس نادیده گرفته می‌شوند.
    """
    markup = getattr(message, "reply_markup", None)
    if markup is None:
        return None
    rows: list[list[Button]] = []
    for row in getattr(markup, "rows", []) or []:
        built = [
            Button.url(btn.text, btn.url)
            for btn in getattr(row, "buttons", []) or []
            if isinstance(btn, KeyboardButtonUrl)
        ]
        if built:
            rows.append(built)
    return rows or None


def local_hour(offset_hours: float | None = None) -> int:
    """ساعت جاری به وقت محلیِ تنظیم‌شده."""
    if offset_hours is None:
        offset_hours = get_settings().timezone_offset
    shifted = utcnow() + timedelta(hours=offset_hours)
    return shifted.hour


def within_active_hours(cfg: dict[str, Any], hour: int | None = None) -> bool:
    """آیا الان در بازه‌ی ساعتی فعال این کار هستیم؟

    شروع و پایان برابر یعنی ۲۴ ساعته. بازه می‌تواند از نیمه‌شب رد شود
    (مثلاً ۲۲ تا ۶).
    """
    start = int(cfg.get("active_from_hour") or 0)
    end = int(cfg.get("active_to_hour") or 0)
    if start == end:
        return True
    now = local_hour() if hour is None else hour
    if start < end:
        return start <= now < end
    return now >= start or now < end


def next_window_open(cfg: dict[str, Any]):
    """لحظه‌ای که بازه‌ی فعال دوباره باز می‌شود (UTC).

    برای پست‌هایی که خارج از ساعت کاری رسیده‌اند و به‌جای دور ریخته شدن
    نگه داشته می‌شوند.
    """
    start = int(cfg.get("active_from_hour") or 0)
    end = int(cfg.get("active_to_hour") or 0)
    now = utcnow()
    if start == end:
        return now      # ۲۴ ساعته؛ چیزی برای انتظار نیست

    offset = get_settings().timezone_offset
    local = now + timedelta(hours=offset)
    # ابتدای ساعتِ شروع، امروز به وقت محلی
    target = local.replace(hour=start, minute=0, second=0, microsecond=0)
    if target <= local:
        target += timedelta(days=1)
    return target - timedelta(hours=offset)


def engagement_of(message) -> tuple[int, int, int]:
    """(بازدید، مجموع واکنش‌ها، فوروارد) یک پست."""
    views = int(getattr(message, "views", 0) or 0)
    forwards = int(getattr(message, "forwards", 0) or 0)
    reactions = 0
    results = getattr(getattr(message, "reactions", None), "results", None) or []
    for item in results:
        reactions += int(getattr(item, "count", 0) or 0)
    return views, reactions, forwards


def _wants_engagement(cfg: dict[str, Any]) -> bool:
    return any(
        int(cfg.get(key) or 0)
        for key in ("min_views", "min_reactions", "min_forwards")
    )


def engagement_ok(message, cfg: dict[str, Any]) -> tuple[bool, str]:
    """آیا پست به حد نصاب تعامل رسیده؟ خروجی دوم دلیل رد شدن است."""
    need_views = int(cfg.get("min_views") or 0)
    need_reactions = int(cfg.get("min_reactions") or 0)
    need_forwards = int(cfg.get("min_forwards") or 0)
    if not (need_views or need_reactions or need_forwards):
        return True, ""

    views, reactions, forwards = engagement_of(message)
    if need_views and views < need_views:
        return False, f"بازدید کم بود ({views} از {need_views})"
    if need_reactions and reactions < need_reactions:
        return False, f"واکنش کم بود ({reactions} از {need_reactions})"
    if need_forwards and forwards < need_forwards:
        return False, f"فوروارد کم بود ({forwards} از {need_forwards})"
    return True, ""


def sender_is_bot(message) -> bool:
    """آیا فرستنده‌ی پیام ربات است؟ در کانال‌ها معمولاً فرستنده‌ای نیست."""
    sender = getattr(message, "sender", None)
    return bool(getattr(sender, "bot", False))


@dataclass(frozen=True, slots=True)
class Fingerprint:
    """سه سطح اثر انگشت یک پست، برای سه سطح سخت‌گیریِ تشخیص تکراری."""

    exact: str
    normalized: str
    fuzzy: int = 0


def fingerprint_of(facts: MessageFacts) -> Fingerprint:
    """اثر انگشت پست، در هر سه سطح.

    هر سه همیشه ساخته و ذخیره می‌شوند — هرکدام کمتر از یک میلی‌ثانیه —
    تا اگر کاربر بعداً سطح سخت‌گیری را عوض کرد، همان لحظه روی پست‌های
    قبلی هم کار کند، نه فقط از پستِ بعدی به بعد.
    """
    return Fingerprint(
        exact=content_hash(facts),
        normalized=dedupe.normalized_hash(facts.media_kind, facts.text),
        fuzzy=dedupe.simhash(facts.text),
    )


def with_own_entities(result: str, cfg: dict, entities):
    """قالب‌بندیِ امضا، هدر و فوتر کاربر را به پست نهایی برمی‌گرداند.

    این متن‌ها را خودِ کاربر در ربات نوشته و ممکن است ایموجی پریمیوم یا
    بولد داشته باشند. entity هایشان هنگام تنظیم ذخیره شده و اینجا روی
    جای تازه‌شان می‌نشینند.

    ترتیب مهم است: تلگرام entity ها را مرتب‌شده می‌خواهد.
    """
    extra = []
    for key, from_end in (("header", False), ("footer", True), ("signature", True)):
        spans = cfg.get(richtext.entities_key(key))
        if spans:
            extra.extend(richtext.place(result, cfg.get(key) or "", spans, from_end=from_end))

    if not extra:
        return entities
    merged = list(entities or []) + extra
    merged.sort(key=lambda e: getattr(e, "offset", 0))
    return merged


def build_facts(message) -> MessageFacts:
    return MessageFacts(
        text=message.message or "",
        media_kind=classify_media(message),
        is_forwarded=getattr(message, "fwd_from", None) is not None,
        has_buttons=getattr(message, "reply_markup", None) is not None,
        size_bytes=media_size(message),
        from_bot=sender_is_bot(message),
        is_reply=getattr(message, "reply_to", None) is not None,
    )


class Copier:
    """هسته‌ی کپی. یک نمونه برای کل ربات ساخته می‌شود."""

    def __init__(self, manager, notifier=None) -> None:
        self.manager = manager
        self.notifier = notifier  # callable(user_id, text) برای هشدار به کاربر
        self.limiter = RateLimiter(get_settings().rate_per_minute)
        # کارهایی که هشدار «اکانت پریمیوم نیست» برایشان رفته؛ تا تکرار نشود
        self._premium_warned: set[int] = set()
        # کاربرانی که هشدار پر شدن سقف روزانه برایشان رفته
        self._quota_warned: set[int] = set()
        self._quota_day: str = ""
        # کاربرانی که هشدار تمام شدن اعتبار واترمارک گرفته‌اند
        self._credit_warned: set[int] = set()
        # تور ایمنی آلبوم — پایین کلاس توضیح داده شده
        self._album_slots: dict[tuple, Slot] = {}
        self._album_timers: dict[tuple, asyncio.Task] = {}
        # یک صف و یک کارگر برای هر مبدا، تا ترتیب حفظ شود
        self._queues: dict[tuple, asyncio.Queue] = {}
        self._workers: dict[tuple, asyncio.Task] = {}
        # کدام مسیر برای آخرین ارسال به کار رفت — برای اندازه‌گیری تأخیر
        self._last_path: str = ""
        # (کار، نوع) ← زمانِ آخرین هشدار؛ برای هر نوع روزی یک پیام کافی است
        self._unsendable_warned: dict[tuple[int, str], float] = {}

    # ------------------------------------------------------------- هندلرها
    def make_new_message_handler(self, user_id: int):
        async def handler(event):
            message = event.message
            # از خودِ پیام خوانده می‌شود، نه از رویداد. تلethon صفت‌های
            # ناشناخته‌ی رویداد را به پیام واگذار می‌کند، ولی تکیه بر آن
            # رفتارِ ضمنی یعنی اگر روزی عوض شود، هر آلبوم دوباره فرستاده
            # می‌شود — بی‌آنکه چیزی خطا بدهد.
            group = getattr(message, "grouped_id", None)
            if group:
                # آلبوم را هندلر مخصوصش می‌گیرد — ولی اگر نگرفت، اینجا
                # می‌ماند. توضیح کامل بالای _album_release.
                await self._buffer_album(user_id, event.chat_id, group, message)
                return
            await self._enqueue(user_id, event.chat_id, Slot([message], ready=True))
        return handler

    def make_album_handler(self, user_id: int):
        async def handler(event):
            first = event.messages[0] if event.messages else None
            group = getattr(first, "grouped_id", None)
            if group is None:
                await self._enqueue(
                    user_id, event.chat_id, Slot(event.messages, ready=True)
                )
                return
            slot = await self._slot_for(user_id, event.chat_id, group)
            # فهرست هندلر آلبوم معتبرتر از چیزی است که خودمان جمع کرده‌ایم
            slot.messages = list(event.messages)
            slot.ready.set()
        return handler

    # ------------------------------------------------- صفِ ترتیب‌نگه‌دار
    #
    # <b>چرا صف.</b> تلethon هر به‌روزرسانی را در یک تسک جدا اجرا
    # می‌کند، یعنی دو پست هم‌زمان پردازش می‌شوند. پستی که ویدئوی
    # سنگین دارد باید دانلود و دوباره آپلود شود؛ پستِ متنیِ بعدی در
    # همان مدت تمام می‌شود و <b>زودتر</b> به مقصد می‌رسد. نتیجه‌اش
    # کانالی است که ترتیبش با مبدا فرق دارد.
    #
    # حالا هر مبدا یک صف و یک کارگر دارد: پست‌ها به همان ترتیبی که
    # رسیده‌اند فرستاده می‌شوند. هزینه‌اش صریح است — پستِ سنگین جلوی
    # پست‌های پشت سرش را می‌گیرد تا تمام شود. برای محصولی که وعده‌اش
    # «کپیِ درست» است، ترتیبِ درست از چند ثانیه زودتر رسیدن مهم‌تر
    # است.
    #
    # صف‌ها به ازای مبدا جدا هستند، پس یک کانالِ پرفایل کانال‌های دیگر
    # را کند نمی‌کند.

    async def _enqueue(self, user_id: int, chat_id: int, slot: Slot) -> None:
        key = (user_id, chat_id)
        queue = self._queues.get(key)
        if queue is None:
            queue = asyncio.Queue()
            self._queues[key] = queue
            self._workers[key] = asyncio.create_task(self._worker(key, queue))
        if queue.qsize() >= BUSY_QUEUE:
            # <b>صفی که پر می‌شود یعنی چیزی گیر کرده، و این تا امروز
            # فقط در لاگ می‌ماند.</b> از بیرون همان چیزی دیده می‌شود
            # که همیشه: مقصد خالی. پس علاوه بر لاگ، به ادمین هم خبر
            # می‌رود — با فاصله، تا خودش تبدیل به سیل نشود.
            log.warning(
                "صف مبدا %s برای کاربر %s به %d پست رسید؛ چیزی گیر کرده",
                chat_id, user_id, queue.qsize(),
            )
            await alerts.send(
                f"🐌 صف مبدا <code>{chat_id}</code> کاربر <code>{user_id}</code> "
                f"به {queue.qsize()} پست رسید — چیزی گیر کرده است.",
                cap=roles.CAP_USERS,
                key=f"queue-stuck-{user_id}-{chat_id}",
                cooldown=1800,
            )
        await queue.put(slot)

    async def _order_for(self, user_id: int, chat_id: int) -> tuple[str, float]:
        """حالت ترتیب برای یک مبدا، و مهلتش.

        <b>چرا از کارها خوانده می‌شود و نه یک تنظیم سراسری.</b> ترتیب،
        تصمیمِ صاحبِ کانال است: یکی خبر می‌گذارد و ترتیب برایش همه‌چیز
        است، دیگری کلیپ می‌گذارد و سرعت مهم‌تر است.

        و اگر چند کار روی یک مبدا نظر متفاوت داشتند، سخت‌گیرترین برنده
        می‌شود — چون بی‌ترتیبی برگشت‌پذیر نیست ولی کندی هست.
        """
        mode, grace = ORDER_GRACE, 300.0
        best = -1
        for task_id in self.manager.tasks_for_chat(user_id, chat_id):
            snapshot = await cache.get_task(task_id)
            if snapshot is None or not snapshot.enabled:
                continue
            wanted = str(snapshot.cfg.get("order_mode") or ORDER_GRACE)
            rank = ORDER_RANK.get(wanted, ORDER_RANK[ORDER_GRACE])
            if rank > best:
                best, mode = rank, wanted
                try:
                    grace = max(5.0, float(snapshot.cfg.get("order_grace_seconds") or 300))
                except (TypeError, ValueError):
                    grace = 300.0
        return mode, grace

    async def _worker(self, key: tuple, queue: asyncio.Queue) -> None:
        """صفِ یک مبدا، با سه حالتِ ترتیب.

        <b>strict</b> — همیشه به ترتیب مبدا. یک ویدئوی سنگین جلوی
        همه‌ی پست‌های پشت سرش را می‌گیرد تا تمام شود.

        <b>fast</b> — هرکدام زودتر آماده شد، همان اول. ترتیب تضمینی
        نیست.

        <b>grace</b> (پیش‌فرض) — به ترتیب، ولی بیش از مهلتِ تعیین‌شده
        پشت یک پستِ کند نمی‌ایستد: بعد از آن، پستِ کند در پس‌زمینه
        ادامه می‌دهد و بقیه جلو می‌روند. برای اغلب کانال‌ها یعنی ترتیبِ
        درست، بدون قفل شدنِ کانال پشت یک فایل بزرگ.
        """
        user_id, chat_id = key
        running: set[asyncio.Task] = set()

        while True:
            slot = await queue.get()
            try:
                if not slot.ready.is_set():
                    try:
                        await asyncio.wait_for(slot.ready.wait(), SLOT_WAIT_SECONDS)
                    except TimeoutError:
                        # جا هیچ‌وقت پر نشد. صف نباید برای همیشه بایستد؛
                        # با هرچه جمع شده ادامه می‌دهیم.
                        log.warning(
                            "جای رزروشده در صف مبدا %s پر نشد؛ با %d پیام ادامه",
                            chat_id, len(slot.messages),
                        )
                if not slot.messages:
                    continue

                slot.messages.sort(key=lambda m: m.id)
                mode, grace = await self._order_for(user_id, chat_id)

                if mode == ORDER_FAST:
                    # رها می‌شود و صف بی‌درنگ سراغ بعدی می‌رود
                    self._spawn(running, user_id, chat_id, slot.messages)
                    continue

                if mode == ORDER_STRICT:
                    await self._dispatch(user_id, chat_id, slot.messages)
                    continue

                # grace: تا مهلت منتظر می‌مانیم، بعد رهایش می‌کنیم
                job = self._spawn(running, user_id, chat_id, slot.messages)
                try:
                    await asyncio.wait_for(asyncio.shield(job), grace)
                except TimeoutError:
                    log.info(
                        "پستِ کند در مبدا %s بیش از %ds طول کشید؛ صف جلو رفت",
                        chat_id, int(grace),
                    )
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("کارگر صف مبدا %s با خطا مواجه شد", chat_id)
            finally:
                queue.task_done()

    def _spawn(self, running: set, user_id: int, chat_id: int, messages: list):
        """ارسال را در پس‌زمینه شروع می‌کند و مرجعش را نگه می‌دارد.

        بدون نگه داشتنِ مرجع، جمع‌آوریِ زباله می‌تواند تسکِ در حال اجرا
        را وسط کار از بین ببرد — و پست بی‌صدا گم می‌شود.
        """
        job = asyncio.create_task(self._dispatch(user_id, chat_id, messages))
        running.add(job)
        job.add_done_callback(running.discard)
        return job

    async def drain(self) -> None:
        """تا وقتی همه‌ی صف‌ها خالی شوند صبر می‌کند. برای تست."""
        for queue in list(self._queues.values()):
            await queue.join()

    def stop_user(self, user_id: int) -> None:
        """صف‌ها و کارگرهای یک کاربر را می‌بندد.

        وقتی اکانتی قطع می‌شود، کارگرش دیگر کاری ندارد؛ بدون این، هر
        وصل و قطع یک تسکِ بیکار جا می‌گذارد.
        """
        for key in [k for k in self._queues if k[0] == user_id]:
            worker = self._workers.pop(key, None)
            if worker is not None:
                worker.cancel()
            self._queues.pop(key, None)
        for key in [k for k in self._album_timers if k[0] == user_id]:
            timer = self._album_timers.pop(key, None)
            if timer is not None:
                timer.cancel()
            self._album_slots.pop(key, None)

    # ----------------------------------------------------- تورِ ایمنی آلبوم
    #
    # <b>چرا این هست.</b> پیام‌های یک آلبوم جدا جدا می‌رسند و تلethon
    # آن‌ها را با یک تایمر نیم‌ثانیه‌ای کنار هم می‌گذارد. خودِ نویسنده‌ی
    # تلethon در کد نوشته «این یک هک کثیف است»: کارِ تحویل با
    # <code>create_task</code> رها می‌شود و کلاینت را با weakref نگه
    # می‌دارد که خودش می‌گوید «ممکن است مرده باشد».
    #
    # تا امروز هندلر پیام تازه، هر پیامِ گروه‌دار را دور می‌ریخت. یعنی
    # برای آلبوم‌ها <b>یک راه</b> بیشتر نبود، و آن یک راه هم تضمینی
    # نداشت: قطعیِ لحظه‌ای، وصل شدن دوباره، یا حتی زمان‌بندی بد و کل
    # آلبوم برای همیشه گم می‌شد — بی‌هیچ خطایی، بی‌هیچ ردی در لاگ.
    #
    # برای محصولی که وعده‌اش «چیزی را از دست نمی‌دهید» است، این
    # پذیرفتنی نیست. حالا پیام‌های گروه‌دار در یک جای رزروشده جمع
    # می‌شوند: اگر هندلر آلبوم آمد، خودش پرش می‌کند؛ اگر نیامد، تور
    # ایمنی با هرچه جمع شده آزادش می‌کند.

    async def _slot_for(self, user_id: int, chat_id: int, group) -> Slot:
        """جای این آلبوم در صف — و اگر هنوز نبود، همین حالا رزروش می‌کند."""
        key = (user_id, chat_id, group)
        slot = self._album_slots.get(key)
        if slot is None:
            slot = Slot()
            self._album_slots[key] = slot
            await self._enqueue(user_id, chat_id, slot)
            self._album_timers[key] = asyncio.create_task(self._album_release(key))
        return slot

    async def _buffer_album(self, user_id: int, chat_id: int, group, message) -> None:
        slot = await self._slot_for(user_id, chat_id, group)
        if slot.ready.is_set():
            # هندلر آلبوم قبلاً فهرست کاملش را گذاشته؛ این پیام همان است
            return
        if not any(m.id == message.id for m in slot.messages):
            slot.messages.append(message)

    async def _album_release(self, key) -> None:
        """اگر هندلر آلبوم نیامد، جا را با هرچه جمع شده آزاد می‌کند."""
        try:
            await asyncio.sleep(ALBUM_SAFETY_SECONDS)
            slot = self._album_slots.get(key)
            if slot is not None and not slot.ready.is_set():
                _user_id, _chat_id, group = key
                log.warning(
                    "هندلر آلبوم برای گروه %s نیامد؛ %d پیام با تور ایمنی رفت",
                    group, len(slot.messages),
                )
                slot.ready.set()
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("تور ایمنی آلبوم با خطا مواجه شد")
        finally:
            self._album_timers.pop(key, None)
            self._album_slots.pop(key, None)

    def make_edit_handler(self, user_id: int):
        async def handler(event):
            for task_id in self.manager.tasks_for_chat(user_id, event.chat_id):
                try:
                    await self.sync_edit(user_id, task_id, event.message)
                except Exception:
                    log.exception("همگام‌سازی ویرایش برای کار %s ناموفق بود", task_id)
        return handler

    def make_delete_handler(self, user_id: int):
        async def handler(event):
            chat_id = getattr(event, "chat_id", None)
            if chat_id is None:
                return
            for task_id in self.manager.tasks_for_chat(user_id, chat_id):
                try:
                    await self._sync_delete(user_id, task_id, event.deleted_ids)
                except Exception:
                    log.exception("همگام‌سازی حذف برای کار %s ناموفق بود", task_id)
        return handler

    # -------------------------------------------------------------- ارسال
    async def _dispatch(self, user_id: int, chat_id: int, messages: Sequence) -> None:
        for task_id in self.manager.tasks_for_chat(user_id, chat_id):
            try:
                await self.process(user_id, task_id, messages)
            except FloodWaitError as exc:
                log.warning("FloodWait %s ثانیه برای کار %s", exc.seconds, task_id)
                await self._record_error(task_id, f"محدودیت تلگرام: {exc.seconds} ثانیه صبر")
                await health.record(user_id, health.classify(exc))
                await asyncio.sleep(min(exc.seconds, 300))
            except Exception as exc:
                if await self._handle_account_error(user_id, exc):
                    return          # اکانت محدود شد؛ ادامه‌ی کارها بی‌فایده است
                log.exception("پردازش کار %s ناموفق بود", task_id)
                await self._record_error(task_id, str(exc))

    async def _handle_account_error(self, user_id: int, exc: BaseException) -> bool:
        """اگر خطا یعنی اکانت محدود/بن شده، همه‌ی کارهای کاربر متوقف می‌شوند.

        ادامه دادن در این حالت فقط وضعیت را بدتر می‌کند، و کاربری که خبر
        ندارد فکر می‌کند ربات خراب است.
        """
        diagnosis = health.classify(exc)
        if not diagnosis.fatal:
            return False

        changed = await health.record(user_id, diagnosis, notifier=self.notifier)
        if changed:
            paused = await self._pause_all_tasks(user_id, diagnosis.title)
            log.warning(
                "اکانت کاربر %s محدود شد (%s)؛ %s کار متوقف شد",
                user_id, diagnosis.state, paused,
            )
            await alerts.account_failed(user_id, diagnosis.state)
        return True

    async def _pause_all_tasks(self, user_id: int, reason: str) -> int:
        """همه‌ی کارهای فعال کاربر را خاموش می‌کند و تعدادشان را می‌دهد."""
        async with get_session() as db:
            rows = await db.execute(
                select(Task).where(Task.user_id == user_id, Task.enabled.is_(True))
            )
            tasks = list(rows.scalars())
            for task in tasks:
                task.enabled = False
                task.last_error = reason[:400]
            await db.commit()
        for task in tasks:
            cache.invalidate_task(task.id)
        return len(tasks)

    async def _hold(
        self, snapshot, messages: Sequence, *, reason: str, release_at=None
    ) -> bool:
        """پست را در صف می‌گذارد. خروجی True یعنی نگه داشته شد.

        اگر نگه داشتن ممکن نباشد، False برمی‌گردد تا مسیر عادی ادامه
        پیدا کند — پستی نباید بی‌سروصدا گم شود.

        <b>ولی «از قبل در صف است» یعنی نگه داشته شده</b>، نه «نشد».
        تا امروز همین هم False بود: جارو پستِ منتظرِ تأیید را (که هنوز
        «نرفته» است) پیدا می‌کرد، اینجا «از قبل هست» می‌شنید، و پست
        <b>بی‌تأیید</b> منتشر می‌شد — تأیید دستی سه دقیقه دوام داشت.
        """
        first = messages[0]
        if await pending.is_waiting(snapshot.id, [m.id for m in messages]):
            return True
        item = await pending.hold(
            task_id=snapshot.id,
            user_id=snapshot.user_id,
            src_chat_id=snapshot.source_id,
            message_ids=[m.id for m in messages],
            reason=reason,
            text=first.message or "",
            media_kind=classify_media(first),
            release_at=release_at,
        )
        if item is None:
            return False

        await log_activity(
            user_id=snapshot.user_id,
            task_id=snapshot.id,
            event="pending",
            detail=pending.REASON_LABELS.get(reason, reason),
        )
        if reason == PendingPost.REASON_APPROVAL and self.notifier:
            waiting = await pending.waiting_count(
                snapshot.user_id, reason=PendingPost.REASON_APPROVAL
            )
            try:
                await self.notifier(
                    snapshot.user_id,
                    f"⏳ یک پست تازه از «{html.escape(snapshot.title or '')}» منتظر تأیید شماست.\n\n"
                    f"<i>{html.escape(item.preview or '')}</i>\n\n"
                    f"در صف: {waiting} پست — «📋 کارهای کپی» ← «⏳ در انتظار تأیید»",
                )
            except Exception:
                log.debug("اطلاع صف تأیید به کاربر نرسید", exc_info=True)
        return True

    async def process(
        self,
        user_id: int,
        task_id: int,
        messages: Sequence,
        *,
        released: str = "",
        retrying: bool = False,
        via: str = DeliveryTiming.VIA_UPDATE,
        only_target: str | None = None,
    ) -> bool:
        """یک پیام یا آلبوم را برای یک کار پردازش و به همه‌ی مقصدها ارسال می‌کند.

        `released` می‌گوید این پست از کدام صف آزاد شده — خالی یعنی تازه
        رسیده. اهمیتش در زنجیر شدن است: پستی که برای تعامل منتظر مانده،
        پس از آزاد شدن هنوز باید به صف تأیید برود، ولی پستی که خودِ صف
        تأیید آزادش کرده نباید دوباره همان‌جا بنشیند.

        <b>`retrying` جلوی یک حلقه‌ی بی‌پایان را می‌گیرد.</b> وقتی خودِ
        کارگرِ صفِ تلاش مجدد این را صدا می‌زند، شکستِ ارسال نباید آیتم
        <b>تازه‌ای</b> در صف بگذارد — چون آن آیتمِ تازه شمارنده‌ی
        تلاش‌هایش صفر است و آیتم قدیمی هم بلافاصله دور ریخته می‌شود.
        نتیجه‌اش صفی بود که هیچ‌وقت به سقفِ تلاش نمی‌رسید: هر دقیقه یک
        تلاشِ تازه، تا ابد، برای پستی که هرگز نمی‌رفت. با `retrying`،
        به‌جای آیتم تازه یک <code>SendFailed</code> پرتاب می‌شود تا
        همان آیتمِ موجود شمرده و در نهایت رها شود.

        <b>`via` می‌گوید چه چیزی خبر داد که این پست هست.</b> پیش‌فرضش
        آپدیتِ لحظه‌ای است چون راه اصلی همان است؛ جارو و صف‌ها خودشان
        مقدار دیگری می‌دهند. این تنها چیزی است که «تأخیرِ واقعی» را از
        «تأخیرِ جارو» جدا می‌کند، و بدون جدا کردنشان تور ایمنی، خرابیِ
        زیرش را پنهان می‌کند.

        <b>`only_target` یعنی «فقط همان مقصدی که شکست خورده».</b> صفِ
        تلاش مجدد برای هر مقصدِ ناموفق یک آیتم می‌سازد، ولی تا امروز هر
        تلاش دوباره را به <b>همه‌ی</b> مقصدها می‌فرستاد: کانالی که بار اول
        پست را گرفته بود، دوباره می‌گرفتش.

        <b>و در تلاش مجدد، دروازه‌ها دوباره بسته نمی‌شوند.</b> ساعت فعال،
        تأیید دستی، فاصله‌ی حداقل، سقف ساعتی، سهمیه‌ی پیام و تشخیص تکراری
        همه یک بار — سرِ ورودِ پست — سنجیده شده‌اند. تکرارشان هر بار یک
        واحد سهمیه می‌سوزاند (پستی که هرگز نرفت، پنج واحد) و اگر یکی
        «نه» می‌گفت، پست بی‌صدا دور ریخته می‌شد.
        """
        waited = bool(released)      # هر انتظاری که بوده، تمام شده
        # پستی که دوباره امتحان می‌شود، بار اول از همه‌ی دروازه‌ها گذشته
        admitted = retrying
        approved = released == PendingPost.REASON_APPROVAL
        snapshot = await cache.get_task(task_id)
        if snapshot is None or not snapshot.enabled:
            return False

        cfg = snapshot.cfg
        rules = snapshot.rules
        src_chat_id = snapshot.source_id
        # حالت ساده: فرستنده خودِ ربات است و مشتری اکانتی وصل نکرده
        simple = snapshot.mode == Task.MODE_SIMPLE

        if not admitted and not within_active_hours(cfg):
            # نگه داشتن، فقط اگر کاربر خواسته باشد؛ وگرنه رفتار قبلی
            if not waited and cfg.get("hold_outside_hours") and src_chat_id:
                if await self._hold(
                    snapshot, messages,
                    reason=PendingPost.REASON_SCHEDULE,
                    release_at=next_window_open(cfg),
                ):
                    return False
            await self._bump(task_id, user_id, skipped=True)
            await log_activity(
                user_id=user_id, task_id=task_id, event="skip", detail="خارج از ساعت فعال کار"
            )
            return False

        ent = await cache.get_entitlement(user_id)
        plan = ent.plan
        if plan is None:
            log.info("کاربر %s اشتراک فعال ندارد؛ کار %s متوقف شد", user_id, task_id)
            await self._pause_task(task_id, "اشتراک منقضی شده است")
            if self.notifier:
                await self.notifier(
                    user_id,
                    "⛔️ اشتراک شما به پایان رسیده و کارهای کپی متوقف شدند.\n"
                    "برای ادامه از بخش «خرید اشتراک» تمدید کنید.",
                )
            return False

        primary = messages[0]
        facts = build_facts(primary)
        text_source = primary
        # در آلبوم، متن معمولاً روی یکی از آیتم‌ها است
        if not facts.text:
            for msg in messages:
                if msg.message:
                    facts.text = msg.message
                    text_source = msg
                    break
        src_entities = getattr(text_source, "entities", None)

        decision = should_copy(facts, cfg, rules)
        if not decision.allowed:
            await self._bump(task_id, user_id, skipped=True)
            await log_activity(
                user_id=user_id, task_id=task_id, event="skip", detail=decision.reason
            )
            return False

        # فیلتر تعامل. پستِ تازه هنوز بازدید ندارد، پس اول منتظر می‌مانیم و
        # بعد از آنکه دوباره از مبدا خوانده شد (با آمار به‌روز) می‌سنجیم.
        if not admitted and _wants_engagement(cfg):
            wait = int(cfg.get("engagement_wait_minutes") or 0)
            if not waited and wait > 0 and src_chat_id:
                if await self._hold(
                    snapshot,
                    messages,
                    reason=PendingPost.REASON_SCHEDULE,
                    release_at=utcnow() + timedelta(minutes=min(wait, 1440)),
                ):
                    return False
            passed, why = engagement_ok(primary, cfg)
            if not passed:
                await self._bump(task_id, user_id, skipped=True)
                await log_activity(
                    user_id=user_id, task_id=task_id, event="skip", detail=why
                )
                return False

        # پست از فیلترها رد شده و نامزد انتشار است. اگر کاربر تأیید دستی
        # یا فاصله‌ی حداقلی خواسته، اینجا نگه داشته می‌شود — پیش از آنکه
        # سهمیه‌ای مصرف شود. هنگام انتشار همه‌ی بررسی‌ها دوباره اجرا
        # می‌شوند، پس چیزی از قلم نمی‌افتد.
        if src_chat_id and not admitted:
            if cfg.get("approval") and not approved:
                if await self._hold(
                    snapshot, messages, reason=PendingPost.REASON_APPROVAL
                ):
                    return False
            gap = int(cfg.get("min_gap_seconds") or 0)
            if gap > 0:
                slot = await pending.next_slot(task_id, gap)
                if slot > utcnow() and await self._hold(
                    snapshot,
                    messages,
                    reason=PendingPost.REASON_SCHEDULE,
                    release_at=slot,
                ):
                    return False

        if not admitted and not hourly_quota.allow(
            task_id, int(cfg.get("max_per_hour") or 0)
        ):
            await self._bump(task_id, user_id, skipped=True)
            await log_activity(
                user_id=user_id, task_id=task_id, event="skip", detail="سقف ساعتی پر شده است"
            )
            return False

        # تکراری پیش از سهمیه: پستی که قرار نیست برود نباید واحدی بسوزاند
        print_ = fingerprint_of(facts)
        if not admitted and cfg.get("skip_duplicates") and await self._seen_before(
            MessageMap.task_id == task_id, print_, cfg
        ):
            await self._bump(task_id, user_id, skipped=True)
            await log_activity(
                user_id=user_id, task_id=task_id, event="skip", detail="پست تکراری"
            )
            return False

        # واحدِ سهمیه‌ی پیام یک بار برای هر پست برداشته می‌شود؛ اگر پست
        # به هیچ مقصدی نرسید و در صف هم نرفت، برمی‌گردد — <b>از هر راهی که
        # بیرون برود</b>: بازگشتِ زودهنگام، FloodWait، یا خطای اکانت. برای
        # همین بقیه‌ی کار در try/finally است و نه یک release در ته تابع.
        message_grant = None
        if not admitted:
            allowed, message_grant = await self._message_quota_ok(user_id, task_id, ent)
            if not allowed:
                return False

        outcome = _Outcome()
        try:
            return await self._deliver(
                user_id, task_id, messages, snapshot, ent, facts, print_,
                src_entities, primary, outcome,
                simple=simple, admitted=admitted, retrying=retrying,
                via=via, only_target=only_target,
            )
        finally:
            if not (outcome.sent or outcome.already or outcome.queued):
                await entitlement.release(user_id, message_grant, ent.subscription_id)

    async def _deliver(
        self,
        user_id: int,
        task_id: int,
        messages: Sequence,
        snapshot,
        ent,
        facts,
        print_,
        src_entities,
        primary,
        outcome: _Outcome,
        *,
        simple: bool,
        admitted: bool,
        retrying: bool,
        via: str,
        only_target: str | None,
    ) -> bool:
        """نیمه‌ی دومِ `process`: از دروازه‌ها گذشته؛ حالا به مقصدها.

        `outcome` را صدازننده نگه می‌دارد تا سهمیه را — هر طور که این
        تابع تمام شود — درست برگرداند.
        """
        cfg = snapshot.cfg
        rules = snapshot.rules
        task_title = snapshot.title
        targets = list(snapshot.targets)
        src_ids = [m.id for m in messages]
        src_chat_id = snapshot.source_id
        plan = ent.plan

        # <b>در حالت ساده مشتری اصلاً اکانتی ندارد — و نباید داشته باشد.</b>
        #
        # این خط تا دیروز بی‌قید‌وشرط بود و همان یعنی: اولین کارِ حالت
        # ساده، سرِ اولین پست، با پیامِ «اکانت کاربری متصل نیست» متوقف
        # می‌شد. یعنی کلِ حالتی که برای «اکانت نمی‌خواهیم» ساخته شد،
        # دقیقاً به نبودنِ اکانت گیر می‌کرد.
        #
        # تست‌های خودم هم نگرفته بودندش، چون مدیرِ ساختگی همیشه یک
        # کلاینت برمی‌گرداند.
        client = None
        if not simple:
            client = await self.manager.ensure_client(user_id)
            if client is None:
                await self._pause_task(task_id, "اکانت کاربری متصل نیست")
                return False

        # مرحله‌ی هوش مصنوعی یک بار روی متن خام اجرا می‌شود و نتیجه‌اش
        # ورودی همه‌ی مقصدها می‌گردد؛ وگرنه کاری که یک بار لازم است به
        # تعداد مقصدها هزینه می‌برد بی‌آنکه نتیجه فرق کند.
        ai_pass = await aipass.enhance(facts.text, cfg, user_id)
        source_text = ai_pass.text
        if ai_pass.changed or ai_pass.out_of_credit:
            await log_activity(
                user_id=user_id, task_id=task_id, event="ai",
                detail=aipass.summary(ai_pass),
            )

        text = apply_transforms(source_text, cfg, rules)
        # فرمت‌ها و ایموجی پریمیوم فقط وقتی حفظ می‌شوند که متن اصلی
        # دست‌نخورده مانده باشد؛ در غیر این صورت آفست‌ها معتبر نیستند
        entities = with_own_entities(
            text, cfg, remap_entities(facts.text, text, src_entities)
        )
        # تأخیر کاملاً یکنواخت الگوی ماشینی می‌سازد، پس ±۱۵٪ پراکندگی
        # می‌گیرد. وقتی کاربر تأخیری نخواسته چیزی اضافه نمی‌شود: کپی باید
        # لحظه‌ای بماند، و فاصله‌گذاری واقعی را محدودکننده‌ی نرخ انجام
        # می‌دهد که بر حسب حجم کار می‌کند، نه بر حسب میلی‌ثانیه.
        delay = 0 if admitted else int(cfg.get("delay_seconds") or 0)
        if delay > 0:
            jitter = random.uniform(-0.15, 0.15) * delay
            await asyncio.sleep(max(0.0, min(delay + jitter, 3600)))

        # واترمارک: اول از سهمیه‌ی روزانه‌ی طرح، بعد از اعتبار خریداری‌شده.
        # هر تصویر یک واحد می‌برد و آلبوم چند تصویر دارد، پس به ازای هر
        # مقصد جداگانه برداشت می‌شود.
        wanted_watermark = watermark_ready(cfg) and not cfg.get("caption_only")
        wm_units = sum(1 for m in messages if classify_media(m) == "photo")
        want_wm = wanted_watermark and bool(wm_units)
        sub_id = ent.subscription_id
        watermark_open = True   # تا وقتی سهمیه و اعتبار تمام نشده
        routed_away = 0         # مقصدهایی که کلمه‌ی کلیدی‌شان نخورد
        cross_dupes = 0         # مقصدهایی که این محتوا را قبلاً گرفته بودند
        failures: list[str] = []   # فقط در مسیر تلاش مجدد پر می‌شود
        unsendable: list[tuple[str, Exception]] = []   # (مقصد، چرا)
        matched = False            # مقصدِ آیتمِ تلاش مجدد هنوز وجود دارد؟

        for spec in targets:
            target = spec.target
            if only_target and str(target) != only_target:
                continue
            matched = True
            if retrying and await self._delivered(task_id, src_ids, str(target)):
                # <b>یک تلاشِ قبلی در واقع رسیده بود.</b> مثلاً ارسال موفق
                # بود و خطایی بعدش رخ داد. فرستادنِ دوباره یعنی پستِ تکراری.
                # <b>ولی کپیِ تازه هم نیست</b>: آمار و تأخیر یک بار، همان
                # بار که واقعاً رفت، ثبت شده‌اند.
                outcome.already = True
                continue
            # مقصدی که امضا یا فوتر اختصاصی دارد، متنش جدا ساخته می‌شود
            if spec.overrides:
                dest_cfg = {**cfg, **spec.overrides}
                dest_text = apply_transforms(source_text, dest_cfg, rules)
                dest_entities = with_own_entities(
                    dest_text, dest_cfg,
                    remap_entities(facts.text, dest_text, src_entities),
                )
            else:
                dest_cfg, dest_text, dest_entities = cfg, text, entities

            # مسیریابی: این مقصد ممکن است فقط بعضی پست‌ها را بخواهد
            if not routing.wants(facts.text, dest_cfg):
                routed_away += 1
                continue

            # همین محتوا شاید از مبدای دیگری قبلاً به همین کانال رفته باشد
            if not admitted and dest_cfg.get("skip_cross_duplicates") and await self._seen_before(
                MessageMap.dest_chat == str(target), print_, dest_cfg
            ):
                cross_dupes += 1
                continue

            # سهمیه/اعتبار واترمارک پیش از ارسال کنار گذاشته می‌شود و اگر
            # ارسال شکست بخورد برمی‌گردد
            grant = None
            if want_wm and watermark_open:
                grant = await entitlement.reserve(
                    user_id, FEAT_WATERMARK, wm_units, plan, sub_id
                )
                if grant is None:
                    watermark_open = False
                    await self._warn_out_of_watermark(user_id, task_id)
            allow_watermark = grant is not None

            await self.limiter.acquire(str(target))
            try:
                try:
                    sent = await self._send(
                        client,
                        target,
                        messages,
                        dest_text,
                        dest_cfg,
                        allow_watermark=allow_watermark,
                        entities=dest_entities,
                        simple=simple,
                    )
                except PremiumAccountRequiredError:
                    # اکانت پریمیوم نیست؛ همان پیام بدون ایموجی پریمیوم می‌رود
                    plain = drop_custom_emoji(dest_entities)
                    if plain is None:
                        raise
                    await self._warn_no_premium(user_id, task_id)
                    sent = await self._send(
                        client,
                        target,
                        messages,
                        dest_text,
                        dest_cfg,
                        allow_watermark=allow_watermark,
                        entities=plain or None,
                        simple=simple,
                    )
            except ChatWriteForbiddenError:
                await entitlement.release(user_id, grant, sub_id)
                # فقط اگر مقصد اصلی مشکل دارد کار را متوقف می‌کنیم
                if spec is targets[0]:
                    await self._pause_task(task_id, "دسترسی ارسال به کانال مقصد وجود ندارد")
                    if self.notifier:
                        await self.notifier(
                            user_id,
                            f"⚠️ کار «{html.escape(task_title or '')}» متوقف شد: "
                            "اکانت شما اجازه‌ی ارسال در کانال مقصد را ندارد.",
                        )
                    return False
                await self._disable_destination(task_id, target)
                continue
            except FloodWaitError:
                await entitlement.release(user_id, grant, sub_id)
                raise
            except Exception as exc:
                await entitlement.release(user_id, grant, sub_id)
                # محدودیت یا بن شدن اکانت با تلاش مجدد درست نمی‌شود و فقط
                # اوضاع را بدتر می‌کند؛ بالاتر مرکزی رسیدگی می‌شود
                if health.classify(exc).fatal:
                    raise
                if is_permanent(exc):
                    # <b>دوباره امتحان نمی‌شود.</b> محتوا یا قالبِ پست ایراد
                    # دارد، نه شبکه؛ یک ساعت و ربع بعد هم همین است.
                    log.warning(
                        "پست %s برای مقصد %s فرستادنی نیست: %s",
                        src_ids[0], target, exc,
                    )
                    unsendable.append((str(target), exc))
                    continue
                if retrying:
                    # آیتمِ صف همین حالا وجود دارد؛ ساختنِ دومی یعنی
                    # همان حلقه‌ی بی‌پایان. فقط ثبتش می‌کنیم و ته کار
                    # به کارگر خبر می‌دهیم.
                    log.warning("تلاش مجدد به مقصد %s باز هم نشد: %s", target, exc)
                    failures.append(f"{target}: {exc}")
                    continue
                log.exception("ارسال به مقصد %s ناموفق بود؛ در صف تلاش مجدد", target)
                if await self._enqueue_retry(
                    task_id, user_id, src_chat_id, src_ids, str(target), str(exc)
                ):
                    outcome.queued = True
                continue

            if not sent:
                # چیزی ارسال نشد (مثلاً متن خالی)؛ سهمیه نباید سوخته شود
                await entitlement.release(user_id, grant, sub_id)

            if sent:
                outcome.sent = True
                await self._remember(task_id, src_ids, sent, print_, str(target))

        any_sent = outcome.sent
        delivered = outcome.sent or outcome.already

        if only_target and not matched:
            # مقصدی که این آیتم برایش بود، دیگر در کار نیست (حذف یا خاموش
            # شده). بی‌صدا دور ریختنش گزارش را گیج می‌کرد.
            await log_activity(
                user_id=user_id, task_id=task_id, event="retry_dropped",
                detail=f"{only_target}: مقصد دیگر در این کار نیست",
            )

        if unsendable:
            await self._give_up(
                user_id, task_id, task_title, src_ids, unsendable,
                # <b>برای هر مقصد یک شکست</b>، همان‌طور که صفِ تلاش مجدد
                # برای هر مقصد یکی می‌شمارد. در تلاش مجدد همان شکست سرِ
                # ورودِ پست شمرده شده.
                count_failure=not retrying,
                # پستی که به مقصدی رسیده، «نرفتنی» نیست
                remember=not delivered,
            )

        # (اگر نرفت و در صف هم نیست، `process` واحدِ سهمیه را برمی‌گرداند.
        # اگر در صف است، همان یک واحد بهای کلِ تلاش‌هایش است.)

        if retrying and failures and not delivered:
            # هیچ مقصدی نگرفت. کارگرِ صف باید همین آیتم را بشمارد، نه
            # اینکه فکر کند فیلترها جلویش را گرفته‌اند و دورش بیندازد.
            raise SendFailed("، ".join(failures)[:400])

        if any_sent:
            await self._bump(task_id, user_id, skipped=False)
            await self._record_latency(user_id, task_id, primary, via)
        elif outcome.already:
            pass
        elif routed_away or cross_dupes:
            # هیچ مقصدی این پست را نخواست — رد شدن است، نه خطا
            await self._bump(task_id, user_id, skipped=True)
            await log_activity(
                user_id=user_id,
                task_id=task_id,
                event="skip",
                detail=(
                    "این محتوا قبلاً در کانال مقصد منتشر شده بود"
                    if cross_dupes and not routed_away
                    else "کلمه‌ی کلیدی هیچ مقصدی نخورد"
                ),
            )
        return delivered

    async def _record_latency(
        self,
        user_id: int,
        task_id: int,
        message,
        via: str = DeliveryTiming.VIA_UPDATE,
    ) -> None:
        """چقدر طول کشید تا این پست منتشر شود.

        <b>چرا اندازه می‌گیریم.</b> «گاهی یک دقیقه، گاهی بیست دقیقه»
        چند علتِ ممکن دارد و از بیرون همه یک‌شکل‌اند. بدون عدد، هر
        تشخیصی حدس است — و حدس‌های قبلی‌مان درست از آب درنیامدند.

        فاصله از <b>زمان خودِ پست در مبدا</b> حساب می‌شود، نه از زمانی
        که ما دیدیمش؛ وگرنه دیر رسیدنِ خودِ رویداد — که یکی از
        مظنون‌هاست — اصلاً در عدد نمی‌افتد.

        <b>و `via` می‌گوید چه چیزی خبرمان کرد.</b> بدون آن، پستی که
        جارو دو دقیقه بعد پیدایش کرده از پستی که آپدیت در دو ثانیه
        آورده قابل تشخیص نیست — و آمارِ کلی فقط می‌گوید «دو دقیقه»،
        بی‌آنکه بگوید چرا.
        """
        posted = getattr(message, "date", None)
        if posted is None:
            return
        if posted.tzinfo is None:
            posted = posted.replace(tzinfo=UTC)
        seconds = int((utcnow() - posted).total_seconds())
        if seconds < 0:
            return

        path = self._last_path or DeliveryTiming.PATH_DIRECT
        kind = classify_media(message)
        size = media_size(message)

        # ردیفِ عددی، برای اینکه بعداً بشود پرسید «میانه‌ی این هفته چقدر
        # بود» و «کدام مسیر کندترین است». متنِ گزارش فعالیت برای خواندن
        # آدم است و برای جمع زدن به درد نمی‌خورد.
        async with get_session() as db:
            db.add(
                DeliveryTiming(
                    task_id=task_id,
                    user_id=user_id,
                    source_msg_id=int(getattr(message, "id", 0) or 0),
                    published_at=posted,
                    seconds=seconds,
                    path=path,
                    via=via or DeliveryTiming.VIA_UPDATE,
                    media_kind=kind,
                    size_bytes=size,
                )
            )
            await db.commit()

        await log_activity(
            user_id=user_id,
            task_id=task_id,
            event="copy",
            detail=f"{seconds}s · {PATH_LABELS.get(path, path)}",
        )
        if seconds >= SLOW_COPY_SECONDS:
            log.warning(
                "کپی کند: کار %s، پست %s پس از %d ثانیه منتشر شد (%s)",
                task_id, getattr(message, "id", "?"), seconds, path,
            )

    async def _send_by_bot(
        self,
        target,
        messages: Sequence,
        text: str,
        cfg: dict[str, Any],
        entities,
    ) -> list[int]:
        """مسیر حالت ساده: خودِ ربات در مقصد پست می‌گذارد.

        <b>رسانه از راهِ دانلود و آپلود می‌رود.</b> `file_id` در تلگرام
        به همان رباتی گره خورده که آن را دیده؛ فایلی که اکانت سرویس در
        مبدأ می‌بیند برای ربات ما شناسه‌ی قابل استفاده‌ای ندارد. پس
        چاره‌ای جز رد کردنِ خودِ بایت‌ها نیست.
        """
        from telkap.services import botsend

        bot = alerts.bot()
        if bot is None:
            raise RuntimeError("ربات در دسترس نیست؛ ارسال حالت ساده ممکن نشد")

        buttons = (
            botsend.to_bot_buttons(messages[0]) if cfg.get("copy_buttons") else None
        )
        media_kind = classify_media(messages[0])

        if media_kind == "text" or cfg.get("caption_only"):
            self._last_path = DeliveryTiming.PATH_TEXT
            result = await botsend.send_text(
                bot, target, text,
                entities=entities, buttons=buttons,
                silent=bool(cfg.get("silent")),
            )
            return result.ids

        if media_kind == "poll":
            # نظرسنجی محتوای فایلی ندارد و باید از نو ساخته شود، و ربات
            # هنوز این را نمی‌کند. <b>بلند رد می‌شود، نه بی‌صدا:</b> تا
            # امروز این پست بدون هیچ رویداد و شمارشی ناپدید می‌شد.
            raise Unsendable(
                "حالت ساده هنوز نظرسنجی را از نو نمی‌سازد", kind="simple-poll"
            )

        opaque = next((kind for m in messages if (kind := opaque_media(m))), "")
        if opaque:
            # در حالت کامل اینها را فوروارد می‌کنیم. اینجا ربات عضوِ مبدأ
            # نیست و فوروارد از آن ممکن نیست، و اکانت سرویس هم در کانالِ
            # مشتری ادمین نیست. پس راهی نمانده.
            raise Unsendable(
                f"این نوع پست ({opaque}) در حالت ساده کپی نمی‌شود — فقط با "
                "اکانتِ خودتان (حالت کامل) می‌شود فرستادش",
                kind=opaque,
            )

        client = await self._reader_for(messages)
        if client is None:
            raise RuntimeError("خواننده‌ای برای دانلود رسانه نبود")

        # <b>همان مسیرِ دانلودِ همیشگی.</b> تنها فرقش این است که با
        # کلاینتِ اکانت سرویس انجام می‌شود، نه اکانتِ مشتری.
        self._last_path = DeliveryTiming.PATH_REUPLOAD
        with_media = [m for m in messages if m.media is not None]
        files = await self._download_all(client, messages)
        if len(files) != len(with_media):
            _discard(files)
            # <b>نه نصفه.</b> فرستادنِ کپشنِ تنها به‌جای عکس — یا آلبومی
            # که یکی از عکس‌هایش جا مانده — پستی می‌سازد که بی‌معناست، و
            # بدتر، در نگاشت «فرستاده شد» ثبت می‌شود و دیگر درست نمی‌شود.
            raise Unsendable(
                f"فقط {len(files)} از {len(with_media)} رسانه‌ی پست دانلود شد",
                kind="simple-download",
            )

        try:
            result = await botsend.send_media(
                bot, target, media_kind, files,
                caption=text, entities=entities,
                # تصمیمِ «آلبوم دکمه نمی‌گیرد» یک‌جا گرفته می‌شود، داخل
                # botsend. تکرارش اینجا فقط این توهم را می‌سازد که
                # نگهبانِ واقعی همین‌جاست.
                buttons=buttons,
                silent=bool(cfg.get("silent")),
                # هر آیتمِ آلبوم نوعِ خودش را دارد. آلبومِ عکس و ویدیو
                # اگر همه با نوعِ اولی ساخته شود، ویدیو مثل عکس می‌رود.
                kinds=[classify_media(m) for m in with_media],
            )
        finally:
            _discard(files)
        return result.ids

    async def _reader_for(self, messages: Sequence):
        """کلاینتی که می‌تواند رسانه‌ی این پست را دانلود کند.

        جوابِ «چه کسی این مبدأ را می‌خواند» یک جا داده می‌شود — در
        `reader` — تا با اضافه شدنِ حالتِ تازه، هر جا جداگانه اشتباه
        نشود. همین سؤال در صف تلاش مجدد و صف تأیید هم پرسیده می‌شود و
        هر سه تا دیروز جوابِ قدیمی را می‌دادند.
        """
        from telkap.services import reader

        chat_id = getattr(messages[0], "chat_id", None)
        return await reader.for_task(
            SimpleNamespace(
                mode=Task.MODE_SIMPLE,
                source_id=int(chat_id) if chat_id else None,
                user_id=0,
            ),
            self.manager,
        )

    async def _send(
        self,
        client,
        target,
        messages: Sequence,
        text: str,
        cfg: dict[str, Any],
        *,
        allow_watermark: bool,
        entities=None,
        simple: bool = False,
    ) -> list[int]:
        """ارسال واقعی به مقصد؛ آیدی پیام‌های ارسالی را برمی‌گرداند.

        <b>`simple` یعنی فرستنده خودِ ربات است، نه اکانت مشتری.</b>

        درزِ دو مسیر عمداً <b>اینجا</b>ست و نه بالاتر: هرچه پیش از این
        نقطه است — قواعد، فیلترها، امضا، تشخیص تکراری — برای هر دو
        حالت یکی است، و هرچه بعدش می‌آید — نگاشت پیام، آمار، اندازه‌ی
        تأخیر — هم. اگر درز را بالاتر می‌گذاشتیم، حالت ساده کم‌کم
        نسخه‌ی دومی از همه‌ی آن‌ها می‌شد و هر امکانِ تازه باید دو بار
        نوشته می‌شد؛ همان‌جاست که دو مسیر از هم دور می‌افتند.
        """
        if simple:
            return await self._send_by_bot(target, messages, text, cfg, entities)

        # حالت فوروارد ساده: برچسب «فورواردشده از» حفظ می‌شود
        if cfg.get("mode") == "forward":
            result = await client.forward_messages(target, list(messages))
            result = result if isinstance(result, list) else [result]
            return [m.id for m in result if m]

        media_kind = classify_media(messages[0])
        buttons = extract_buttons(messages[0]) if cfg.get("copy_buttons") else None

        # نظرسنجی محتوای متنی ندارد؛ باید به‌صورت نظرسنجی تازه ساخته شود
        if media_kind == "poll" and not cfg.get("caption_only"):
            sent = await self._send_poll(client, target, messages[0])
            return [sent] if sent else []

        if cfg.get("caption_only") or media_kind == "text":
            if not text.strip():
                return []
            sent = await client.send_message(
                target,
                text,
                link_preview=False,
                buttons=buttons,
                formatting_entities=entities,
            )
            return [sent.id]

        self._last_path = DeliveryTiming.PATH_DIRECT

        # <b>اول: آیا اصلاً می‌شود این رسانه را خودمان فرستاد؟</b>
        #
        # برای بعضی نوع‌ها نه ارسال با مرجع کار می‌کند و نه دانلود. تا
        # امروز این را پنج بار امتحان می‌کردیم تا مطمئن شویم نمی‌شود —
        # و تهِ هر بار «Cannot use [] as file» بود. حالا همان اول تشخیص
        # داده می‌شود و یک‌راست سراغ تنها راهی می‌رویم که ممکن است جواب
        # بدهد.
        opaque = next((kind for m in messages if (kind := opaque_media(m))), "")
        if opaque:
            return await self._forward_opaque(
                client, target, messages, text, entities, kind=opaque
            )

        media_count = sum(1 for m in messages if m.media is not None)
        watermarking = allow_watermark and media_kind == "photo" and watermark_ready(cfg)
        # فایل کانفیگ باید دانلود، بازنویسی و دوباره آپلود شود؛ ارسال
        # مستقیمِ رسانه‌ی مبدا نسخه‌ی دست‌نخورده را می‌فرستد.
        rewriting = (
            bool(cfg.get("rewrite_files"))
            and media_kind == "document"
            and any(docedit.worth_opening(media_filename(m)) for m in messages)
        )

        if rewriting:
            files, changed = await self._rewritten_files(client, messages, cfg)
            if not changed or len(files) != media_count:
                # چیزی عوض نشد — یا همه‌ی فایل‌ها نیامدند؛ آلبومِ ناقص
                # نمی‌فرستیم. همان مسیر عادی، بدون آپلود اضافه.
                _discard(files)
                rewriting = False
            else:
                self._last_path = DeliveryTiming.PATH_REWRITE
                try:
                    sent = await client.send_file(
                        target,
                        files if len(files) > 1 else files[0],
                        caption=text or None,
                        buttons=buttons,
                        formatting_entities=entities,
                        force_document=True,
                    )
                finally:
                    _discard(files)
                sent = sent if isinstance(sent, list) else [sent]
                return [m.id for m in sent if m]

        if watermarking:
            self._last_path = DeliveryTiming.PATH_WATERMARK
            files = await self._watermarked_files(client, messages, cfg)
            try:
                if len(files) != media_count:
                    # <b>نه نصفه.</b> آلبومی که یکی از عکس‌هایش جا مانده،
                    # «فرستاده شد» ثبت می‌شود و هیچ‌وقت درست نمی‌شود.
                    raise Unsendable(
                        f"فقط {len(files)} از {media_count} عکس برای واترمارک "
                        "دانلود شد",
                        kind="watermark-download",
                    )
                sent = await client.send_file(
                    target,
                    # تک‌عکس باید تکی برود. فهرستِ یک‌عضوی در Telethon
                    # مسیر آلبوم را می‌رود، و آلبوم دکمه نمی‌گیرد.
                    files if len(files) > 1 else files[0],
                    caption=text or None,
                    buttons=buttons,
                    formatting_entities=entities,
                )
            finally:
                _discard(files)
        else:
            payload = [m.media for m in messages if m.media is not None]
            if not payload:
                if not text.strip():
                    return []
                sent = await client.send_message(
                    target,
                    text,
                    link_preview=False,
                    buttons=buttons,
                    formatting_entities=entities,
                )
                return [sent.id]
            try:
                sent = await client.send_file(
                    target,
                    # تک‌رسانه را تکی بفرست؛ فهرست یعنی آلبوم
                    payload if len(payload) > 1 else payload[0],
                    caption=text or None,
                    buttons=buttons,
                    formatting_entities=entities,
                )
            except (FloodWaitError, ChatWriteForbiddenError):
                raise  # این دو را لایه‌ی بالاتر مدیریت می‌کند
            except Exception as direct_error:
                # <b>گران‌ترین مسیر.</b> کانال‌هایی که «محافظت از محتوا»
                # دارند اجازه‌ی ارسال مجدد مرجعِ رسانه را نمی‌دهند، پس
                # هر پستِ رسانه‌ای اینجا می‌افتد: دانلود کامل و آپلود
                # دوباره. برای یک ویدیو یعنی چند دقیقه.
                #
                # <b>و دلیلِ اصلی ثبت می‌شود.</b> تا امروز فقط خطای مرحله‌ی
                # بعد ثبت می‌شد («Cannot use [] as file») و علتِ واقعی —
                # که مثلاً کدام نوع رسانه بود — هیچ‌جا نمی‌ماند.
                self._last_path = DeliveryTiming.PATH_REUPLOAD
                log.warning(
                    "ارسال مستقیم رسانه نشد (%s: %s)؛ دانلود و آپلود مجدد",
                    type(direct_error).__name__, direct_error,
                )
                files = await self._download_all(client, messages)
                if len(files) != media_count:
                    _discard(files)
                    # چیزی دانلود نشد، یا نه همه‌اش. آلبومِ ناقص نمی‌فرستیم
                    # و با فهرست خالی هم send_file صدا نمی‌زنیم؛ تنها راهِ
                    # باقی‌مانده فوروارد است.
                    return await self._forward_opaque(
                        client, target, messages, text, entities,
                        kind=f"download-failed:{type(direct_error).__name__}",
                    )
                try:
                    sent = await client.send_file(
                        target,
                        files if len(files) > 1 else files[0],
                        caption=text or None,
                        buttons=buttons,
                        formatting_entities=entities,
                    )
                finally:
                    _discard(files)

        sent = sent if isinstance(sent, list) else [sent]
        return [m.id for m in sent if m]

    async def _forward_opaque(
        self, client, target, messages: Sequence, text: str, entities, *, kind: str
    ) -> list[int]:
        """پستی که فقط سرورِ تلگرام می‌تواند کپی‌اش کند.

        <b>فوروارد، بی‌نام.</b> تا برچسبِ «فورواردشده از» نخورد و پست مثل
        یک کپی به نظر برسد. <b>کپشن پیش از فوروارد تصمیم گرفته می‌شود</b>،
        نه بعدش:

        - متنِ ما همان متنِ مبدأ است → کپشنِ مبدأ همراهش می‌آید و ویرایشی
          لازم نیست.
        - مبدأ کپشن نداشت (تاس، استوری، بازی، موقعیت زنده — اصلاً کپشن
          نمی‌گیرند) → بی‌ویرایش. ویرایش اینجا همیشه رد می‌شد و پست را
          بعد از انتشار پاک می‌کردیم؛ یعنی هر کاری که امضا یا فوتر داشت،
          هیچ‌کدام از این پست‌ها را نمی‌فرستاد. امضای ما روی این‌ها
          نمی‌نشیند — و پستی بی‌امضا بهتر از پستی است که نیامده.
        - کپشن عوض شده → بی‌کپشن فوروارد و کپشنِ <b>خودمان</b> جایش. کپشنِ
          مبدأ لینک و امضای کانالِ دیگری را دارد؛ همان که تبدیل‌ها پاک
          کرده‌اند.

        <b>نه نصفه.</b> اگر گذاشتنِ کپشن نشد، یا آلبوم ناقص رسید، پستِ
        فورواردشده پاک می‌شود. پستی که متنش جا مانده یا یکی از عکس‌هایش
        نیست، از پستی که نیامده بدتر است: آن یکی را مشتری می‌بیند.

        <b>فقط ایرادِ محتوا «نرفتنی» است.</b> کانالِ «محافظت‌شده» یا پیامِ
        حذف‌شده با تکرار درست نمی‌شود. ولی محدودیتِ سرعت، قطعی شبکه، یا بن
        شدنِ اکانت چیز دیگری‌اند و همان‌طور که هستند بالا می‌روند تا لایه‌ی
        بالاتر رسیدگی کند — اگر این‌ها را «نرفتنی» می‌نامیدیم، پست بی‌صدا
        رها می‌شد و محدودیتِ اکانت هم پنهان می‌ماند.
        """
        self._last_path = DeliveryTiming.PATH_FORWARD
        holder = next(
            (i for i, m in enumerate(messages) if (m.message or "").strip()), None
        )
        original = messages[holder].message if holder is not None else ""
        if text == original or (holder is None and not text.strip()):
            drop_captions, caption = False, ""
        elif holder is None:
            # مبدأ کپشن نداشت؛ متنِ ما فقط امضا/فوتر است
            log.info("پستِ %s (%s) کپشن نمی‌گیرد؛ بی‌امضا فوروارد شد", messages[0].id, kind)
            drop_captions, caption = False, ""
        else:
            drop_captions, caption = True, text

        try:
            forwarded = await self._forward_without_caption(
                client, target, messages, drop_captions=drop_captions
            )
        except Exception as exc:
            if _forward_refused(exc):
                raise Unsendable(
                    f"این نوع پست ({kind}) فقط با فوروارد کپی می‌شود و تلگرام "
                    f"فوروارد را نپذیرفت ({type(exc).__name__})",
                    kind=kind,
                ) from exc
            raise

        ids = [int(m.id) for m in forwarded if m]
        if not ids and not forwarded:
            raise Unsendable(
                f"فوروارد این نوع پست ({kind}) چیزی در مقصد نساخت", kind=kind
            )
        if len(ids) != len(messages):
            # آلبوم نصفه رسید. پاکش می‌کنیم و دوباره امتحان می‌شود —
            # این خطای محتوا نیست.
            await self._delete_quietly(client, target, ids)
            raise PartialForward(
                f"از {len(messages)} پیامِ آلبوم فقط {len(ids)} فوروارد شد"
            )

        if caption.strip():
            try:
                await client.edit_message(
                    target, ids[holder], caption, formatting_entities=entities,
                )
            except MessageNotModifiedError:
                pass     # همان متن از قبل رویش بود
            except Exception as exc:
                await self._delete_quietly(client, target, ids)
                if is_permanent(exc):
                    raise Unsendable(
                        f"این نوع پست ({kind}) فوروارد شد ولی کپشن رویش ننشست "
                        f"({type(exc).__name__}); پاکش کردیم تا نصفه نماند",
                        kind=kind,
                    ) from exc
                raise
        return ids

    @staticmethod
    async def _delete_quietly(client, target, ids: Sequence[int]) -> None:
        if not ids:
            return
        try:
            await client.delete_messages(target, list(ids))
        except Exception:
            log.warning("پستِ نصفه‌ی فورواردشده در %s پاک نشد", target, exc_info=True)

    @staticmethod
    async def _forward_without_caption(
        client, target, messages: Sequence, *, drop_captions: bool = True
    ) -> list:
        """فوروارد با `drop_author` و (در صورت خواست) `drop_media_captions`.

        `forward_messages` در Telethon فقط `drop_author` را می‌گیرد، پس
        درخواست خام ساخته می‌شود — دقیقاً همان‌طور که خودِ
        `forward_messages` می‌سازد — با یک پرچمِ بیشتر.

        خروجی <b>به ترتیبِ ورودی</b> است و جای پیامی که نرسید None است؛
        فیلتر کردنش کارِ صدازننده است، چون همان None یعنی «آلبوم ناقص».
        """
        from telethon.tl.functions.messages import ForwardMessagesRequest

        to_peer = await client.get_input_entity(target)
        from_peer = await client.get_input_entity(messages[0].peer_id)
        request = ForwardMessagesRequest(
            from_peer=from_peer,
            id=[m.id for m in messages],
            to_peer=to_peer,
            drop_author=True,
            drop_media_captions=drop_captions,
        )
        result = await client(request)
        # همان تابعی که forward_messages برای خواندنِ جواب صدا می‌زند
        found = client._get_response_message(request, result, to_peer)
        if found is None:
            return []
        return list(found) if isinstance(found, list) else [found]

    async def _send_poll(self, client, target, message) -> int | None:
        """نظرسنجی را به‌صورت یک نظرسنجی تازه در مقصد می‌سازد.

        تلگرام اجازه‌ی «کپی» نظرسنجی را نمی‌دهد، فقط ساخت دوباره‌ی آن را.
        """
        poll = getattr(message.media, "poll", None)
        if poll is None:
            raise Unsendable("نظرسنجی مبدأ خالی بود", kind="poll-empty")

        quiz = bool(getattr(poll, "quiz", False))
        correct: list[bytes] = []
        solution = None
        solution_entities = None
        if quiz:
            # <b>آزمون بدون جوابِ درست ساخته نمی‌شود.</b> تلگرام جوابِ
            # درست را فقط به کسی نشان می‌دهد که رأی داده — و اکانتِ ما
            # رأی نمی‌دهد. تا امروز آزمون بدون جواب فرستاده می‌شد، تلگرام
            # ردش می‌کرد، و پست بی‌هیچ ردی گم می‌شد.
            results = getattr(message.media, "results", None)
            correct = [
                r.option for r in (getattr(results, "results", None) or [])
                if getattr(r, "correct", False)
            ]
            if not correct:
                raise Unsendable(
                    "نظرسنجیِ آزمونی است و جوابِ درستش فقط به رأی‌دهنده‌ها "
                    "نشان داده می‌شود؛ بدون آن تلگرام آزمون نمی‌سازد",
                    kind="quiz-hidden-answer",
                )
            solution = getattr(results, "solution", None)
            solution_entities = getattr(results, "solution_entities", None)

        # سؤال و گزینه‌ها عیناً بازاستفاده می‌شوند تا با تغییر قالب متن در
        # نسخه‌های مختلف تلگرام سازگار بماند
        new_poll = InputMediaPoll(
            poll=Poll(
                id=0,
                question=poll.question,
                answers=list(poll.answers),
                multiple_choice=bool(getattr(poll, "multiple_choice", False)),
                public_voters=bool(getattr(poll, "public_voters", False)),
                quiz=quiz,
            ),
            correct_answers=correct or None,
            solution=solution,
            solution_entities=solution_entities,
        )
        try:
            sent = await client.send_file(target, new_poll)
        except (FloodWaitError, ChatWriteForbiddenError):
            raise
        except Exception as exc:
            # <b>بلند، نه بی‌صدا.</b> تا امروز اینجا None برمی‌گشت و پست
            # بدون هیچ رویداد و شمارشی ناپدید می‌شد.
            if not is_permanent(exc):
                raise
            raise Unsendable(
                f"ساخت دوباره‌ی نظرسنجی نشد ({type(exc).__name__})", kind="poll"
            ) from exc
        return sent.id

    async def _download_all(self, client, messages: Sequence) -> list[str]:
        """رسانه‌ی همه‌ی پیام‌ها. چیزی که دانلود نشد در خروجی نیست —
        صداکننده تعداد را با تعدادِ رسانه‌ها می‌سنجد.

        <b>اگر وسط کار خطا شد، دانلودشده‌ها پاک می‌شوند.</b> وگرنه هر
        آلبومی که آیتمِ سومش نیامد، دو فایلِ اولش را برای همیشه روی دیسک
        می‌گذاشت.
        """
        out_dir = get_settings().download_dir
        out_dir.mkdir(parents=True, exist_ok=True)
        paths: list[str] = []
        try:
            for msg in messages:
                if msg.media is None:
                    continue
                path = await client.download_media(msg, file=str(out_dir))
                if path:
                    paths.append(path)
        except BaseException:
            _discard(paths)
            raise
        return paths

    async def _watermarked_files(self, client, messages: Sequence, cfg: dict[str, Any]) -> list[str]:
        out_dir = get_settings().download_dir
        out_dir.mkdir(parents=True, exist_ok=True)
        results: list[str] = []
        try:
            for msg in messages:
                if msg.media is None:
                    continue
                src = await client.download_media(msg, file=str(out_dir))
                if not src:
                    continue
                src_path = Path(src)
                dest_path = src_path.with_name(f"wm_{src_path.name}").with_suffix(".jpg")
                try:
                    final = apply_watermark(src_path, dest_path, cfg)
                except BaseException:
                    _discard([src_path])
                    raise
                if final != src_path:
                    _discard([src_path])
                results.append(str(final))
        except BaseException:
            # همان دلیلِ _download_all: خطای وسطِ آلبوم نباید فایل جا بگذارد
            _discard(results)
            raise
        return results

    async def _rewritten_files(
        self, client, messages: Sequence, cfg: dict[str, Any]
    ) -> tuple[list[str], int]:
        """فایل‌های پیوست را دانلود و تگشان را بازنویسی می‌کند.

        خروجی: (مسیرها، تعداد کل تغییرها). تعداد ۰ یعنی هیچ فایلی قالب
        شناخته‌شده نداشت و باید مسیر عادی ارسال را رفت.
        """
        out_dir = get_settings().download_dir
        out_dir.mkdir(parents=True, exist_ok=True)
        tag = config_tag(cfg)
        pattern = cfg.get("file_rename") or "{tag}"

        paths: list[str] = []
        changed = 0
        try:
            for msg in messages:
                if msg.media is None:
                    continue
                src = await client.download_media(msg, file=str(out_dir))
                if not src:
                    continue
                src_path = Path(src)
                try:
                    final, count = await asyncio.to_thread(
                        docedit.rewrite_file, src_path, tag, rename=pattern
                    )
                except Exception:
                    log.exception(
                        "بازنویسی فایل %s ناموفق بود؛ نسخه‌ی اصلی می‌رود", src_path.name
                    )
                    final, count = src_path, 0
                if final != src_path:
                    _discard([src_path])
                changed += count
                paths.append(str(final))
        except BaseException:
            # همان دلیلِ _download_all: خطای وسطِ آلبوم نباید فایل جا بگذارد
            _discard(paths)
            raise
        return paths, changed

    # --------------------------------------------------------- همگام‌سازی
    async def sync_edit(self, user_id: int, task_id: int, message) -> bool:
        """نسخه‌ی مقصد را با نسخه‌ی تازه‌ی مبدا یکی می‌کند.

        <b>یک در برای هر دو حالت.</b> در مسیر اکانتِ مشتری، تلگرام خودش
        خبرِ ویرایش را می‌دهد و این تابع از هندلرِ آن صدا می‌شود. در
        حالت ساده خبری نمی‌آید — پویشگر هر دقیقه همان پست را دوباره
        می‌بیند و اینجا می‌پرسد «عوض شده؟». پس این تابع باید بتواند
        <b>بی‌دلیل هم صدا شود</b> و کاری نکند؛ نگهبانش اثر انگشت است.

        خروجی: آیا چیزی واقعاً ویرایش شد.
        """
        snapshot = await cache.get_task(task_id)
        if snapshot is None or not snapshot.enabled:
            return False
        cfg, rules = snapshot.cfg, snapshot.rules
        if not cfg.get("sync_edits"):
            return False
        async with get_session() as db:
            rows = await db.execute(
                select(MessageMap).where(
                    MessageMap.task_id == task_id, MessageMap.src_msg_id == message.id
                )
            )
            mappings = list(rows.scalars())
        if not mappings:
            return False

        # <b>اثر انگشت، نه تاریخِ ویرایش.</b> تلگرام برای هر تغییری —
        # عوض شدنِ تعداد بازدید هم — ممکن است پست را «ویرایش‌شده» نشان
        # دهد. اگر ملاکْ آن بود، پویشگر هر دقیقه همان متن را دوباره
        # می‌فرستاد و در عمل هر پستِ یک روزه صدها بار ویرایش می‌شد.
        print_ = fingerprint_of(build_facts(message))
        baseline = next((m.content_hash for m in mappings if m.content_hash), None)
        if baseline == print_.exact:
            return False

        original = message.message or ""
        text = apply_transforms(original, cfg, rules)
        entities = remap_entities(original, text, getattr(message, "entities", None))

        if snapshot.mode == Task.MODE_SIMPLE:
            settled = await self._edit_by_bot(mappings, message, text, entities, cfg)
        else:
            client = await self.manager.ensure_client(user_id)
            if client is None:
                return False
            settled = True
            for mapping in mappings:
                try:
                    await client.edit_message(
                        _as_target(mapping.dest_chat),
                        mapping.dst_msg_id,
                        text,
                        formatting_entities=entities,
                    )
                except (MessageIdInvalidError, ValueError):
                    log.debug("ویرایش پیام %s ممکن نبود", mapping.dst_msg_id)
                except FloodWaitError as exc:
                    settled = False
                    await asyncio.sleep(min(exc.seconds, 300))
                except Exception:
                    settled = False
                    log.debug(
                        "ویرایش در مقصد %s ناموفق بود", mapping.dest_chat, exc_info=True
                    )

        # <b>اثر انگشت فقط وقتی جلو می‌رود که کار تمام شده باشد.</b>
        # اگر شکستِ موقتی — قطعیِ شبکه، محدودیتِ نرخ — هم مهر بخورد،
        # دورِ بعد «عوض نشده» می‌گیریمش و آن پستِ نصفه برای همیشه نصفه
        # می‌ماند. دقیقاً همان خرابی‌ای که این کد برای رفعش نوشته شد.
        if not settled:
            return False
        await self._restamp(task_id, message.id, print_)
        await log_activity(
            user_id=user_id, task_id=task_id, event="edit", detail=f"#{message.id}"
        )
        return True

    async def _edit_by_bot(self, mappings, message, text, entities, cfg) -> bool:
        """همان ویرایش، ولی با خودِ ربات — چون مشتری اکانتی ندارد.

        خروجی: آیا پرونده بسته شد. «تلگرام گفت تغییری نکرده» هم بسته
        است — یعنی مقصد از قبل درست بوده.
        """
        from telkap.services import botsend

        bot = alerts.bot()
        if bot is None:
            log.warning("ربات در دسترس نیست؛ ویرایشِ حالت ساده انجام نشد")
            return False

        # پیامِ رسانه‌دار کپشن دارد نه متن، و تلگرام برای هرکدام تابع
        # جداگانه‌ای می‌خواهد. اشتباه گرفتنشان یعنی خطای «متنی برای
        # ویرایش نیست» و پستی که ناقص می‌ماند.
        as_caption = classify_media(message) != "text" and not cfg.get("caption_only")
        buttons = botsend.to_bot_buttons(message) if cfg.get("copy_buttons") else None

        settled = True
        for mapping in mappings:
            try:
                await botsend.edit(
                    bot,
                    _as_target(mapping.dest_chat),
                    mapping.dst_msg_id,
                    text,
                    entities=entities,
                    caption=as_caption,
                    buttons=buttons,
                )
            except Exception:
                settled = False
                log.debug(
                    "ویرایش با ربات در مقصد %s ناموفق بود",
                    mapping.dest_chat, exc_info=True,
                )
        return settled

    async def _restamp(self, task_id: int, src_msg_id: int, print_: Fingerprint) -> None:
        """اثر انگشتِ ذخیره‌شده را روی نسخه‌ی تازه می‌برد.

        بدون این، دفعه‌ی بعد که پویشگر همین پست را می‌بیند باز هم
        «عوض شده» می‌گیردش و تا ابد هر دقیقه یک ویرایش می‌فرستد.

        هر سه سطح با هم به‌روز می‌شوند، نه فقط آنکه مقایسه شد: تشخیصِ
        تکراری ممکن است روی هرکدامشان تنظیم باشد و جا ماندنِ دوتای دیگر
        یعنی پستِ بعدی با <b>متنِ قدیمی</b> سنجیده می‌شود.
        """
        async with get_session() as db:
            rows = await db.execute(
                select(MessageMap).where(
                    MessageMap.task_id == task_id,
                    MessageMap.src_msg_id == src_msg_id,
                )
            )
            for mapping in rows.scalars():
                mapping.content_hash = print_.exact
                mapping.norm_hash = print_.normalized
                mapping.simhash = print_.fuzzy
            await db.commit()

    async def _sync_delete(self, user_id: int, task_id: int, deleted_ids: Iterable[int]) -> None:
        snapshot = await cache.get_task(task_id)
        if snapshot is None:
            return
        if not snapshot.cfg.get("sync_deletes"):
            return
        async with get_session() as db:
            rows = await db.execute(
                select(MessageMap).where(
                    MessageMap.task_id == task_id, MessageMap.src_msg_id.in_(list(deleted_ids))
                )
            )
            mappings = list(rows.scalars())

        if not mappings:
            return
        client = await self.manager.ensure_client(user_id)
        if client is None:
            return

        by_dest: dict[str, list[int]] = {}
        for mapping in mappings:
            by_dest.setdefault(mapping.dest_chat, []).append(mapping.dst_msg_id)

        for dest_chat, msg_ids in by_dest.items():
            try:
                await client.delete_messages(_as_target(dest_chat), msg_ids)
            except Exception:
                log.debug("حذف در مقصد %s ناموفق بود", dest_chat, exc_info=True)
        await log_activity(
            user_id=user_id, task_id=task_id, event="delete", detail=f"{len(mappings)} پیام"
        )

    # -------------------------------------------------------------- کمکی
    async def _seen_before(self, scope, print_: Fingerprint, cfg: dict[str, Any]) -> bool:
        """آیا محتوایی با همین اثر انگشت قبلاً در این محدوده ثبت شده؟

        `scope` شرط SQLAlchemy است — یا «همین کار» یا «همین کانال مقصد».
        سطح سخت‌گیری از تنظیمات کاربر می‌آید.
        """
        mode = dedupe.mode_of(cfg)

        if mode == dedupe.MODE_SIMILAR:
            if not print_.fuzzy:
                return False
            async with get_session() as db:
                rows = await db.execute(
                    select(MessageMap.simhash)
                    .where(scope, MessageMap.simhash.is_not(None))
                    .order_by(MessageMap.id.desc())
                    .limit(dedupe.SIMILAR_WINDOW)
                )
                percent = int(cfg.get("similarity_percent") or 85)
                return any(
                    dedupe.looks_similar(print_.fuzzy, int(other), percent)
                    for other in rows.scalars()
                    if other
                )

        column, value = (
            (MessageMap.norm_hash, print_.normalized)
            if mode == dedupe.MODE_NORMALIZED
            else (MessageMap.content_hash, print_.exact)
        )
        if not value:
            return False
        async with get_session() as db:
            row = await db.execute(
                select(MessageMap.id).where(scope, column == value).limit(1)
            )
            return row.scalar_one_or_none() is not None

    async def _remember(
        self,
        task_id: int,
        src_ids: Sequence[int],
        dst_ids: Sequence[int],
        print_: Fingerprint,
        dest_chat: str,
    ) -> None:
        if not src_ids or not dst_ids:
            return
        async with get_session() as db:
            for index, src_id in enumerate(src_ids):
                dst_id = dst_ids[index] if index < len(dst_ids) else dst_ids[0]
                existing = await db.execute(
                    select(MessageMap).where(
                        MessageMap.task_id == task_id,
                        MessageMap.src_msg_id == src_id,
                        MessageMap.dest_chat == dest_chat,
                    )
                )
                if existing.scalar_one_or_none() is not None:
                    continue
                # اثر انگشت فقط روی اولین پیامِ آلبوم می‌نشیند؛ بقیه
                # همان محتوا را تکرار می‌کنند و شمردنشان تکراری‌سازی است
                first = index == 0
                db.add(
                    MessageMap(
                        task_id=task_id,
                        src_msg_id=src_id,
                        dst_msg_id=dst_id,
                        dest_chat=dest_chat,
                        content_hash=print_.exact if first else None,
                        norm_hash=print_.normalized if first else None,
                        simhash=print_.fuzzy if first else None,
                    )
                )
            await db.commit()
        await self._trim_history(task_id)

    async def _delivered(self, task_id: int, src_ids: Sequence[int], dest_chat: str) -> bool:
        """آیا این پست پیش‌تر به همین مقصد رسیده؟ — از روی آیدی، نه محتوا.

        تشخیصِ تکراریِ محتوایی در تلاش مجدد خاموش است (پست بار اول از آن
        گذشته)، پس این تنها چیزی است که جلوی فرستادنِ دوباره‌ی پستی را
        می‌گیرد که یک تلاشِ قبلی واقعاً رسانده بود.
        """
        if not src_ids:
            return False
        async with get_session() as db:
            found = await db.scalar(
                select(MessageMap.id).where(
                    MessageMap.task_id == task_id,
                    MessageMap.src_msg_id == src_ids[0],
                    MessageMap.dest_chat == dest_chat,
                ).limit(1)
            )
        return found is not None

    async def _give_up(
        self,
        user_id: int,
        task_id: int,
        task_title: str,
        src_ids: Sequence[int],
        unsendable: list[tuple[str, Exception]],
        *,
        count_failure: bool,
        remember: bool,
    ) -> None:
        """پستی که نمی‌رود: یک بار ثبت، یک بار شمرده، و به خاطر سپرده.

        <b>هشدار به مشتری برای هر نوع، روزی یک بار.</b> تا امروز برای هر
        پستِ رهاشده یک پیام می‌رفت — روزی که یک پست ۸۶ بار در صف رفت،
        یعنی ۷۷ پیامِ «ارسال نشد» با یک خطای انگلیسی. کانالی که مرتب
        استوری بازنشر می‌کند، با این قاعده روزی یک خبر می‌گیرد نه صد تا.
        """
        for dest, exc in unsendable:
            await log_activity(
                user_id=user_id,
                task_id=task_id,
                event="unsendable",
                detail=f"{dest}: {exc}"[:600],
                level="warning",
            )
        if count_failure:
            for _ in unsendable:
                await self._bump_daily(task_id, user_id, field="failed")
        if remember and src_ids:
            # در دیتابیس، نه حافظه: هر آپدیت یک ری‌استارت است و جارو بعدش
            # همه‌ی این پست‌ها را از نو امتحان می‌کرد.
            await abandoned.mark(task_id, src_ids[0], AbandonedPost.REASON_UNSENDABLE)

        _, first = unsendable[0]
        kind = getattr(first, "kind", "") or type(first).__name__
        key = (int(task_id), kind)
        now = time.monotonic()
        last = self._unsendable_warned.get(key)
        if self.notifier is None or (last is not None and now - last < 86_400):
            return
        self._unsendable_warned[key] = now
        await self.notifier(
            user_id,
            f"⚠️ یک پست از کار «{html.escape(task_title or '')}» کپی نشد.\n"
            f"علت: {html.escape(str(first))}\n\n"
            "<i>دوباره امتحانش نمی‌کنیم چون با تکرار درست نمی‌شود. برای همین "
            "نوع پست تا ۲۴ ساعت دوباره خبر نمی‌دهیم.</i>",
        )

    async def _enqueue_retry(
        self,
        task_id: int,
        user_id: int,
        src_chat_id: int | None,
        src_ids: Sequence[int],
        dest_chat: str,
        error: str,
    ) -> bool:
        """پستی که ارسالش شکست خورد را در صف تلاش مجدد می‌گذارد.

        خروجی True یعنی واقعاً در صف رفت — و واحدِ سهمیه‌اش نگه داشته
        می‌شود تا تلاش‌های بعدی هزینه‌ی تازه نداشته باشند.
        """
        if src_chat_id is None or not src_ids:
            # بدون نشانی مبدا نمی‌توان پیام را دوباره خواند
            await self._record_error(task_id, error)
            return False

        # <b>صفی که فقط بالا می‌رود یعنی مقصد خراب است، نه کند.</b>
        # وقتی دسترسی ادمین گرفته شده یا کانال حذف شده، هر پستِ تازه‌ی
        # مبدا یک آیتم دیگر اضافه می‌کند و هیچ‌کدام هرگز نمی‌روند.
        # ادامه دادن فقط تلگرام را می‌کوبد و اکانت را به محدودیت
        # می‌رساند — که آن‌وقت <b>کارهای سالمِ دیگر</b> را هم می‌خواباند.
        async with get_session() as db:
            waiting = await db.scalar(
                select(func.count(RetryItem.id)).where(RetryItem.task_id == task_id)
            )
        if (waiting or 0) >= MAX_RETRY_QUEUE:
            await self._pause_task(
                task_id, f"{MAX_RETRY_QUEUE} پست پشت سر هم ارسال نشد: {error}"[:400]
            )
            log.error(
                "کار %s متوقف شد: صف تلاش مجدد به %s رسید — %s",
                task_id, waiting, error,
            )
            if self.notifier:
                await self.notifier(
                    user_id,
                    "⛔️ <b>یک کار کپی متوقف شد.</b>\n\n"
                    f"{fa_num(MAX_RETRY_QUEUE)} پست پشت سر هم به کانال مقصد نرفتند، "
                    "پس ادامه ندادیم تا اکانتتان محدود نشود.\n\n"
                    f"<b>علت:</b> <code>{html.escape(error[:200])}</code>\n\n"
                    "معمولاً یعنی دسترسی ارسال در کانال مقصد گرفته شده. "
                    "با «🩺 بررسی سلامت کارها» دقیقاً می‌بینید کدام کار و چرا.",
                )
            await alerts.send(
                f"⛔️ کار <code>{task_id}</code> کاربر <code>{user_id}</code> "
                f"به‌دلیل {MAX_RETRY_QUEUE} شکست پیاپی متوقف شد.\n"
                f"<code>{html.escape(error[:200])}</code>",
                cap=roles.CAP_USERS,
                key=f"retry-flood-{task_id}",
                cooldown=3600,
            )
            return False

        async with get_session() as db:
            db.add(
                RetryItem(
                    task_id=task_id,
                    user_id=user_id,
                    src_chat_id=src_chat_id,
                    src_msg_ids=",".join(str(i) for i in src_ids),
                    dest_chat=dest_chat,
                    next_try_at=utcnow() + timedelta(seconds=RETRY_BACKOFF[0]),
                    last_error=error[:400],
                )
            )
            await db.commit()
        await self._bump_daily(task_id, user_id, field="failed")
        await log_activity(
            user_id=user_id,
            task_id=task_id,
            event="retry_queued",
            detail=f"{dest_chat}: {error}"[:600],
            level="warning",
        )
        return True

    async def _disable_destination(self, task_id: int, target) -> None:
        """مقصد اضافی‌ای که اجازه‌ی ارسال ندارد را خاموش می‌کند."""
        async with get_session() as db:
            rows = await db.execute(
                select(Destination).where(Destination.task_id == task_id)
            )
            for dest in rows.scalars():
                if str(dest.chat_id or dest.ref) == str(target):
                    dest.enabled = False
            await db.commit()
        cache.invalidate_task(task_id)
        await log_activity(
            task_id=task_id,
            event="dest_disabled",
            detail=f"مقصد {target} به‌دلیل نبود دسترسی خاموش شد",
            level="warning",
        )

    async def _trim_history(self, task_id: int, keep: int = 3000) -> None:
        """جدول نگاشت را کوتاه نگه می‌دارد تا بی‌نهایت رشد نکند."""
        async with get_session() as db:
            rows = await db.execute(
                select(MessageMap.id)
                .where(MessageMap.task_id == task_id)
                .order_by(MessageMap.id.desc())
                .offset(keep)
            )
            stale = [row for row in rows.scalars()]
            if stale:
                await db.execute(delete(MessageMap).where(MessageMap.id.in_(stale)))
                await db.commit()

    async def _bump(self, task_id: int, user_id: int, *, skipped: bool) -> None:
        async with get_session() as db:
            task = await db.get(Task, task_id)
            if task is None:
                return
            if skipped:
                task.skipped_count += 1
            else:
                task.copied_count += 1
                task.last_copy_at = utcnow()
                task.last_error = None
            await db.commit()
        await self._bump_daily(task_id, user_id, field="skipped" if skipped else "copied")

    async def _bump_daily(self, task_id: int, user_id: int, *, field: str) -> None:
        """شمارنده‌ی آمار امروز را یک واحد بالا می‌برد."""
        day = today_key()
        async with get_session() as db:
            row = await db.execute(
                select(DailyStat).where(DailyStat.task_id == task_id, DailyStat.day == day)
            )
            stat = row.scalar_one_or_none()
            if stat is None:
                # مقادیر صفر صریح لازم است: پیش‌فرض ستون تازه هنگام flush
                # اعمال می‌شود و تا آن لحظه None است
                stat = DailyStat(
                    task_id=task_id, user_id=user_id, day=day, copied=0, skipped=0, failed=0
                )
                db.add(stat)
            setattr(stat, field, (getattr(stat, field) or 0) + 1)
            try:
                await db.commit()
            except IntegrityError:
                # همزمانی دو کپی روی یک کار؛ دفعه‌ی بعد ثبت می‌شود
                await db.rollback()

    async def _record_error(self, task_id: int, detail: str) -> None:
        async with get_session() as db:
            task = await db.get(Task, task_id)
            if task is None:
                return
            task.last_error = detail[:400]
            await db.commit()
        await log_activity(task_id=task_id, event="error", detail=detail, level="error")

    async def _message_quota_ok(self, user_id: int, task_id: int, ent) -> tuple[bool, Any]:
        """سهمیه‌ی پیام دوره و سقف «مصرف منصفانه»‌ی روزانه را بررسی می‌کند.

        خروجی: (مجاز است؟، واحدِ برداشته‌شده). واحد برگردانده می‌شود تا اگر
        پست به هیچ‌جا نرسید، صداکننده پسش بدهد. تا امروز دور ریخته می‌شد —
        پستی که هرگز نرفت هم سهمیه می‌خورد.
        """
        plan = ent.plan
        day = today_key()
        if day != self._quota_day:
            # روز عوض شد؛ هشدارها دوباره مجاز می‌شوند
            self._quota_day = day
            self._quota_warned.clear()

        # ۱) سقف مصرف منصفانه‌ی روزانه (فقط برای طرح‌های نامحدود)
        fair = int(getattr(plan, "fair_use_daily", 0) or 0)
        if fair > 0:
            if not daily_quota.is_seeded(user_id, day):
                async with get_session() as db:
                    spent = await db.scalar(
                        select(func.coalesce(func.sum(DailyStat.copied), 0)).where(
                            DailyStat.user_id == user_id, DailyStat.day == day
                        )
                    )
                daily_quota.seed(user_id, day, int(spent or 0))
            if not daily_quota.allow(user_id, day, fair):
                await self._reject_quota(
                    user_id,
                    task_id,
                    detail=f"سقف مصرف منصفانه‌ی روزانه ({fair} پیام) پر شد",
                    notice=(
                        "📊 امروز به سقف مصرف منصفانه رسیدید و کپی تا نیمه‌شب "
                        "متوقف می‌شود.\n\nاگر به حجم بیشتری نیاز دارید، با "
                        "پشتیبانی در تماس باشید."
                    ),
                )
                return False, None

        # ۲) سهمیه‌ی پیام کل دوره
        grant = await entitlement.reserve(
            user_id, FEAT_MESSAGES, 1, plan, ent.subscription_id
        )
        if grant is not None:
            return True, grant

        limit = plan.quota(FEAT_MESSAGES) if plan else 0
        await self._reject_quota(
            user_id,
            task_id,
            detail=f"سهمیه‌ی پیام طرح ({limit} پیام) تمام شد",
            notice=(
                f"📊 سهمیه‌ی <b>{limit} پیام</b> طرح شما تمام شد و کپی متوقف شده است.\n\n"
                "با تمدید یا خرید طرح بالاتر، سهمیه از نو پر می‌شود.\n"
                "«💳 خرید اشتراک» را بزنید."
            ),
        )
        return False, None

    async def _reject_quota(
        self, user_id: int, task_id: int, *, detail: str, notice: str
    ) -> None:
        await self._bump(task_id, user_id, skipped=True)
        await log_activity(
            user_id=user_id, task_id=task_id, event="skip", detail=detail
        )
        if user_id in self._quota_warned:
            return
        self._quota_warned.add(user_id)
        if self.notifier:
            await self.notifier(user_id, notice)

    async def _warn_out_of_watermark(self, user_id: int, task_id: int) -> None:
        """یک بار به کاربر می‌گوید سهمیه و اعتبار واترمارکش تمام شده است."""
        if user_id in self._credit_warned:
            return
        self._credit_warned.add(user_id)
        await log_activity(
            user_id=user_id,
            task_id=task_id,
            event="watermark_empty",
            detail="سهمیه و اعتبار واترمارک تمام شد",
            level="warning",
        )
        if self.notifier:
            await self.notifier(
                user_id,
                "💧 سهمیه‌ی واترمارک امروز شما تمام شد و پست‌ها بدون واترمارک "
                "ارسال می‌شوند.\n\n"
                "سهمیه از نیمه‌شب دوباره پر می‌شود. اگر همین حالا لازم دارید، "
                "از «💳 خرید اشتراک» → «🎫 خرید اعتبار» اعتبار بگیرید یا طرح "
                "بالاتری تهیه کنید.",
            )

    async def _warn_no_premium(self, user_id: int, task_id: int) -> None:
        """یک بار به کاربر می‌گوید چرا ایموجی پریمیوم ساده شده است."""
        if task_id in self._premium_warned:
            return
        self._premium_warned.add(task_id)
        await log_activity(
            user_id=user_id,
            task_id=task_id,
            event="premium_required",
            detail="ارسال ایموجی پریمیوم رد شد؛ پیام بدون آن‌ها فرستاده شد",
            level="warning",
        )
        if self.notifier:
            await self.notifier(
                user_id,
                "ℹ️ پست‌های این کانال ایموجی پریمیوم دارند، ولی تلگرام ارسالشان را "
                "با این اکانت قبول نکرد و پیام بدون آن‌ها فرستاده شد.\n\n"
                "برای حفظ ایموجی‌های پریمیوم، اکانتی که در «👤 حساب کاربری» به ربات "
                "وصل کرده‌اید باید خودش تلگرام پریمیوم داشته باشد.",
            )

    async def _pause_task(self, task_id: int, reason: str) -> None:
        async with get_session() as db:
            task = await db.get(Task, task_id)
            if task is None:
                return
            task.enabled = False
            task.last_error = reason[:400]
            user_id = task.user_id
            await db.commit()
        hourly_quota.forget(task_id)
        cache.invalidate_task(task_id)
        await self.manager.reload_user(user_id)
        await log_activity(task_id=task_id, event="pause", detail=reason, level="warning")
