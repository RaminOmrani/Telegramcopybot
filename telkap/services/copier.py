"""موتور کپی: دریافت پیام از کانال مبدا، پردازش، و ارسال به کانال مقصد."""
from __future__ import annotations

import asyncio
import logging
import random
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, timedelta
from pathlib import Path
from typing import Any

from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError
from telethon.errors import (
    ChatWriteForbiddenError,
    FloodWaitError,
    MessageIdInvalidError,
    PremiumAccountRequiredError,
)
from telethon.tl.custom import Button
from telethon.tl.types import (
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
    Poll,
)

from telkap.config import get_settings
from telkap.db import get_session, log_activity
from telkap.models import (
    DailyStat,
    Destination,
    MessageMap,
    PendingPost,
    RetryItem,
    Task,
    utcnow,
)
from telkap.plans import FEAT_MESSAGES, FEAT_WATERMARK
from telkap.services import (
    aipass,
    alerts,
    cache,
    dedupe,
    docedit,
    entitlement,
    health,
    pending,
    richtext,
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

log = logging.getLogger(__name__)

# تایمر آلبومِ تلethon نیم ثانیه است. چهار ثانیه یعنی هم فرصت کافی
# برای رسیدن پیام‌های کندِ آلبوم، هم آن‌قدر کوتاه که کپی «لحظه‌ای»
# بماند. کمترش یعنی آلبوم دوتکه می‌شود، بیشترش یعنی تأخیر بی‌دلیل.
ALBUM_SAFETY_SECONDS = 4.0

# چقدر منتظر پر شدن یک جای رزروشده می‌مانیم. کمی بیشتر از تور ایمنی
# آلبوم، چون همان تور است که جا را آزاد می‌کند؛ این فقط برای وقتی است
# که خودِ تور هم به هر دلیلی نیامده باشد و صف نباید برای همیشه بایستد.
SLOT_WAIT_SECONDS = ALBUM_SAFETY_SECONDS + 2.0

# صف هر مبدا از این بلندتر شود یعنی چیزی گیر کرده — معمولاً یک فایل
# بزرگ. در لاگ هشدار می‌دهیم ولی چیزی دور ریخته نمی‌شود؛ دور ریختن،
# همان «پستِ گم‌شده»ای است که کل این کد برای نبودنش نوشته شده.
BUSY_QUEUE = 20

# کپی کندتر از این، در لاگ هشدار می‌گیرد. کاربر یک دقیقه را تحمل
# می‌کند؛ بیشترش یعنی چیزی درست کار نمی‌کند.
SLOW_COPY_SECONDS = 60

# فاصله‌ی تلاش‌های مجدد بر حسب ثانیه (۱ دقیقه، ۵ دقیقه، ۱۵ دقیقه، ۱ ساعت)
RETRY_BACKOFF = (60, 300, 900, 3600)


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
            log.warning(
                "صف مبدا %s برای کاربر %s به %d پست رسید؛ چیزی گیر کرده",
                chat_id, user_id, queue.qsize(),
            )
        await queue.put(slot)

    async def _worker(self, key: tuple, queue: asyncio.Queue) -> None:
        user_id, chat_id = key
        while True:
            slot = await queue.get()
            try:
                if not slot.ready.is_set():
                    try:
                        await asyncio.wait_for(
                            slot.ready.wait(), SLOT_WAIT_SECONDS
                        )
                    except TimeoutError:
                        # جا هیچ‌وقت پر نشد. صف نباید برای همیشه بایستد؛
                        # با هرچه جمع شده ادامه می‌دهیم.
                        log.warning(
                            "جای رزروشده در صف مبدا %s پر نشد؛ با %d پیام ادامه",
                            chat_id, len(slot.messages),
                        )
                if slot.messages:
                    slot.messages.sort(key=lambda m: m.id)
                    await self._dispatch(user_id, chat_id, slot.messages)
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("کارگر صف مبدا %s با خطا مواجه شد", chat_id)
            finally:
                queue.task_done()

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
                    await self._sync_edit(user_id, task_id, event.message)
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

        اگر پست از قبل در صف باشد یا نگه داشتن ممکن نباشد، False برمی‌گردد
        تا مسیر عادی ادامه پیدا کند — پستی نباید بی‌سروصدا گم شود.
        """
        first = messages[0]
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
                    f"⏳ یک پست تازه از «{snapshot.title}» منتظر تأیید شماست.\n\n"
                    f"<i>{item.preview}</i>\n\n"
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
    ) -> bool:
        """یک پیام یا آلبوم را برای یک کار پردازش و به همه‌ی مقصدها ارسال می‌کند.

        `released` می‌گوید این پست از کدام صف آزاد شده — خالی یعنی تازه
        رسیده. اهمیتش در زنجیر شدن است: پستی که برای تعامل منتظر مانده،
        پس از آزاد شدن هنوز باید به صف تأیید برود، ولی پستی که خودِ صف
        تأیید آزادش کرده نباید دوباره همان‌جا بنشیند.
        """
        waited = bool(released)      # هر انتظاری که بوده، تمام شده
        approved = released == PendingPost.REASON_APPROVAL
        snapshot = await cache.get_task(task_id)
        if snapshot is None or not snapshot.enabled:
            return False

        cfg = snapshot.cfg
        rules = snapshot.rules
        task_title = snapshot.title
        targets = list(snapshot.targets)
        src_ids = [m.id for m in messages]
        src_chat_id = snapshot.source_id

        if not within_active_hours(cfg):
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
        if _wants_engagement(cfg):
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
        if src_chat_id:
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

        if not hourly_quota.allow(task_id, int(cfg.get("max_per_hour") or 0)):
            await self._bump(task_id, user_id, skipped=True)
            await log_activity(
                user_id=user_id, task_id=task_id, event="skip", detail="سقف ساعتی پر شده است"
            )
            return False

        if not await self._message_quota_ok(user_id, task_id, ent):
            return False

        print_ = fingerprint_of(facts)
        if cfg.get("skip_duplicates") and await self._seen_before(
            MessageMap.task_id == task_id, print_, cfg
        ):
            await self._bump(task_id, user_id, skipped=True)
            await log_activity(
                user_id=user_id, task_id=task_id, event="skip", detail="پست تکراری"
            )
            return False

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
        delay = int(cfg.get("delay_seconds") or 0)
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
        any_sent = False
        routed_away = 0         # مقصدهایی که کلمه‌ی کلیدی‌شان نخورد
        cross_dupes = 0         # مقصدهایی که این محتوا را قبلاً گرفته بودند

        for spec in targets:
            target = spec.target
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
            if dest_cfg.get("skip_cross_duplicates") and await self._seen_before(
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
                    )
            except ChatWriteForbiddenError:
                await entitlement.release(user_id, grant, sub_id)
                # فقط اگر مقصد اصلی مشکل دارد کار را متوقف می‌کنیم
                if spec is targets[0]:
                    await self._pause_task(task_id, "دسترسی ارسال به کانال مقصد وجود ندارد")
                    if self.notifier:
                        await self.notifier(
                            user_id,
                            f"⚠️ کار «{task_title}» متوقف شد: "
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
                log.exception("ارسال به مقصد %s ناموفق بود؛ در صف تلاش مجدد", target)
                await self._enqueue_retry(
                    task_id, user_id, src_chat_id, src_ids, str(target), str(exc)
                )
                continue

            if not sent:
                # چیزی ارسال نشد (مثلاً متن خالی)؛ سهمیه نباید سوخته شود
                await entitlement.release(user_id, grant, sub_id)

            if sent:
                any_sent = True
                await self._remember(task_id, src_ids, sent, print_, str(target))

        if any_sent:
            await self._bump(task_id, user_id, skipped=False)
            await self._record_latency(user_id, task_id, primary)
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
        return any_sent

    async def _record_latency(self, user_id: int, task_id: int, message) -> None:
        """چقدر طول کشید تا این پست منتشر شود.

        <b>چرا اندازه می‌گیریم.</b> «گاهی یک دقیقه، گاهی بیست دقیقه»
        چند علتِ ممکن دارد و از بیرون همه یک‌شکل‌اند. بدون عدد، هر
        تشخیصی حدس است — و حدس‌های قبلی‌مان درست از آب درنیامدند.

        فاصله از <b>زمان خودِ پست در مبدا</b> حساب می‌شود، نه از زمانی
        که ما دیدیمش؛ وگرنه دیر رسیدنِ خودِ رویداد — که یکی از
        مظنون‌هاست — اصلاً در عدد نمی‌افتد.
        """
        posted = getattr(message, "date", None)
        if posted is None:
            return
        if posted.tzinfo is None:
            posted = posted.replace(tzinfo=UTC)
        seconds = int((utcnow() - posted).total_seconds())
        if seconds < 0:
            return

        path = self._last_path or "مستقیم"
        await log_activity(
            user_id=user_id,
            task_id=task_id,
            event="copy",
            detail=f"{seconds}s · {path}",
        )
        if seconds >= SLOW_COPY_SECONDS:
            log.warning(
                "کپی کند: کار %s، پست %s پس از %d ثانیه منتشر شد (%s)",
                task_id, getattr(message, "id", "?"), seconds, path,
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
    ) -> list[int]:
        """ارسال واقعی به مقصد؛ آیدی پیام‌های ارسالی را برمی‌گرداند."""
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

        self._last_path = "مستقیم"
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
            if not changed:
                # چیزی عوض نشد؛ همان مسیر عادی، بدون آپلود اضافه
                for path in files:
                    Path(path).unlink(missing_ok=True)
                rewriting = False
            else:
                self._last_path = "بازنویسی فایل"
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
                    for path in files:
                        Path(path).unlink(missing_ok=True)
                sent = sent if isinstance(sent, list) else [sent]
                return [m.id for m in sent if m]

        if watermarking:
            self._last_path = "واترمارک"
            files = await self._watermarked_files(client, messages, cfg)
            try:
                sent = await client.send_file(
                    target,
                    files,
                    caption=text or None,
                    buttons=buttons,
                    formatting_entities=entities,
                )
            finally:
                for path in files:
                    Path(path).unlink(missing_ok=True)
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
            # تک‌رسانه را تکی بفرست؛ لیست یعنی آلبوم و برای یک آیتم لازم نیست
            if len(payload) == 1:
                payload = payload[0]
            try:
                sent = await client.send_file(
                    target,
                    payload,
                    caption=text or None,
                    buttons=buttons,
                    formatting_entities=entities,
                )
            except (FloodWaitError, ChatWriteForbiddenError):
                raise  # این دو را لایه‌ی بالاتر مدیریت می‌کند
            except Exception:
                # کانال‌های محدودشده اجازه‌ی ارسال مجدد مستقیم رسانه را نمی‌دهند؛
                # در این حالت فایل دانلود و دوباره آپلود می‌شود.
                # <b>گران‌ترین مسیر، و بی‌صداترین.</b> کانال‌هایی که
                # «محافظت از محتوا» دارند اجازه‌ی ارسال مجدد مرجعِ رسانه
                # را نمی‌دهند، پس هر پستِ رسانه‌ای اینجا می‌افتد: دانلود
                # کامل و آپلود دوباره. برای یک ویدیو یعنی چند دقیقه —
                # همان چیزی که کاربر به‌عنوان «تأخیر» می‌بیند.
                self._last_path = "دانلود و آپلود مجدد"
                log.info("ارسال مستقیم رسانه ناموفق بود؛ دانلود و آپلود مجدد")
                files = await self._download_all(client, messages)
                if len(files) == 1:
                    files = files[0]
                try:
                    sent = await client.send_file(
                        target,
                        files,
                        caption=text or None,
                        buttons=buttons,
                        formatting_entities=entities,
                    )
                finally:
                    for path in files:
                        Path(path).unlink(missing_ok=True)

        sent = sent if isinstance(sent, list) else [sent]
        return [m.id for m in sent if m]

    async def _send_poll(self, client, target, message) -> int | None:
        """نظرسنجی را به‌صورت یک نظرسنجی تازه در مقصد می‌سازد.

        تلگرام اجازه‌ی «کپی» نظرسنجی را نمی‌دهد، فقط ساخت دوباره‌ی آن را.
        """
        poll = getattr(message.media, "poll", None)
        if poll is None:
            return None
        try:
            # سؤال و گزینه‌ها عیناً بازاستفاده می‌شوند تا با تغییر قالب
            # متن در نسخه‌های مختلف تلگرام سازگار بماند
            new_poll = InputMediaPoll(
                poll=Poll(
                    id=0,
                    question=poll.question,
                    answers=list(poll.answers),
                    multiple_choice=bool(getattr(poll, "multiple_choice", False)),
                    public_voters=bool(getattr(poll, "public_voters", False)),
                    quiz=bool(getattr(poll, "quiz", False)),
                )
            )
            sent = await client.send_file(target, new_poll)
            return sent.id
        except Exception:
            log.exception("ساخت دوباره‌ی نظرسنجی ناموفق بود")
            return None

    async def _download_all(self, client, messages: Sequence) -> list[str]:
        out_dir = get_settings().download_dir
        out_dir.mkdir(parents=True, exist_ok=True)
        paths: list[str] = []
        for msg in messages:
            if msg.media is None:
                continue
            path = await client.download_media(msg, file=str(out_dir))
            if path:
                paths.append(path)
        return paths

    async def _watermarked_files(self, client, messages: Sequence, cfg: dict[str, Any]) -> list[str]:
        out_dir = get_settings().download_dir
        out_dir.mkdir(parents=True, exist_ok=True)
        results: list[str] = []
        for msg in messages:
            if msg.media is None:
                continue
            src = await client.download_media(msg, file=str(out_dir))
            if not src:
                continue
            src_path = Path(src)
            dest_path = src_path.with_name(f"wm_{src_path.name}").with_suffix(".jpg")
            final = apply_watermark(src_path, dest_path, cfg)
            if final != src_path:
                src_path.unlink(missing_ok=True)
            results.append(str(final))
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
                log.exception("بازنویسی فایل %s ناموفق بود؛ نسخه‌ی اصلی می‌رود", src_path.name)
                final, count = src_path, 0
            if final != src_path:
                src_path.unlink(missing_ok=True)
            changed += count
            paths.append(str(final))
        return paths, changed

    # --------------------------------------------------------- همگام‌سازی
    async def _sync_edit(self, user_id: int, task_id: int, message) -> None:
        snapshot = await cache.get_task(task_id)
        if snapshot is None or not snapshot.enabled:
            return
        cfg, rules = snapshot.cfg, snapshot.rules
        if not cfg.get("sync_edits"):
            return
        async with get_session() as db:
            rows = await db.execute(
                select(MessageMap).where(
                    MessageMap.task_id == task_id, MessageMap.src_msg_id == message.id
                )
            )
            mappings = list(rows.scalars())
        if not mappings:
            return

        client = await self.manager.ensure_client(user_id)
        if client is None:
            return
        original = message.message or ""
        text = apply_transforms(original, cfg, rules)
        entities = remap_entities(original, text, getattr(message, "entities", None))
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
                await asyncio.sleep(min(exc.seconds, 300))
            except Exception:
                log.debug("ویرایش در مقصد %s ناموفق بود", mapping.dest_chat, exc_info=True)
        await log_activity(
            user_id=user_id, task_id=task_id, event="edit", detail=f"#{message.id}"
        )

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

    async def _enqueue_retry(
        self,
        task_id: int,
        user_id: int,
        src_chat_id: int | None,
        src_ids: Sequence[int],
        dest_chat: str,
        error: str,
    ) -> None:
        """پستی که ارسالش شکست خورد را در صف تلاش مجدد می‌گذارد."""
        if src_chat_id is None or not src_ids:
            # بدون نشانی مبدا نمی‌توان پیام را دوباره خواند
            await self._record_error(task_id, error)
            return
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

    async def _message_quota_ok(self, user_id: int, task_id: int, ent) -> bool:
        """سهمیه‌ی پیام دوره و سقف «مصرف منصفانه»‌ی روزانه را بررسی می‌کند."""
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
                return False

        # ۲) سهمیه‌ی پیام کل دوره
        grant = await entitlement.reserve(
            user_id, FEAT_MESSAGES, 1, plan, ent.subscription_id
        )
        if grant is not None:
            return True

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
        return False

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
