"""موتور کپی: دریافت پیام از کانال مبدا، پردازش، و ارسال به کانال مقصد."""
from __future__ import annotations

import asyncio
import logging
from collections.abc import Iterable, Sequence
from datetime import timedelta
from pathlib import Path
from typing import Any

from sqlalchemy import delete, select
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
from telkap.models import DailyStat, Destination, MessageMap, RetryItem, Task, utcnow
from telkap.plans import FEAT_WATERMARK
from telkap.services import cache
from telkap.services.filters import MessageFacts, content_hash, should_copy
from telkap.services.ratelimit import RateLimiter, hourly_quota
from telkap.services.transform import apply_transforms, drop_custom_emoji, remap_entities
from telkap.services.watermark import add_text_watermark

log = logging.getLogger(__name__)

# فاصله‌ی تلاش‌های مجدد بر حسب ثانیه (۱ دقیقه، ۵ دقیقه، ۱۵ دقیقه، ۱ ساعت)
RETRY_BACKOFF = (60, 300, 900, 3600)


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


def sender_is_bot(message) -> bool:
    """آیا فرستنده‌ی پیام ربات است؟ در کانال‌ها معمولاً فرستنده‌ای نیست."""
    sender = getattr(message, "sender", None)
    return bool(getattr(sender, "bot", False))


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

    # ------------------------------------------------------------- هندلرها
    def make_new_message_handler(self, user_id: int):
        async def handler(event):
            if getattr(event, "grouped_id", None):
                return  # آلبوم‌ها را هندلر مخصوص خودشان می‌گیرد
            await self._dispatch(user_id, event.chat_id, [event.message])
        return handler

    def make_album_handler(self, user_id: int):
        async def handler(event):
            await self._dispatch(user_id, event.chat_id, list(event.messages))
        return handler

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
                await asyncio.sleep(min(exc.seconds, 300))
            except Exception as exc:
                log.exception("پردازش کار %s ناموفق بود", task_id)
                await self._record_error(task_id, str(exc))

    async def process(self, user_id: int, task_id: int, messages: Sequence) -> bool:
        """یک پیام یا آلبوم را برای یک کار پردازش و به همه‌ی مقصدها ارسال می‌کند."""
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
            await self._bump(task_id, user_id, skipped=True)
            await log_activity(
                user_id=user_id, task_id=task_id, event="skip", detail="خارج از ساعت فعال کار"
            )
            return False

        plan = await cache.get_plan(user_id)
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

        if not hourly_quota.allow(task_id, int(cfg.get("max_per_hour") or 0)):
            await self._bump(task_id, user_id, skipped=True)
            await log_activity(
                user_id=user_id, task_id=task_id, event="skip", detail="سقف ساعتی پر شده است"
            )
            return False

        digest = content_hash(facts)
        if cfg.get("skip_duplicates") and await self._is_duplicate(task_id, digest):
            await self._bump(task_id, user_id, skipped=True)
            await log_activity(
                user_id=user_id, task_id=task_id, event="skip", detail="پست تکراری"
            )
            return False

        client = await self.manager.ensure_client(user_id)
        if client is None:
            await self._pause_task(task_id, "اکانت کاربری متصل نیست")
            return False

        text = apply_transforms(facts.text, cfg, rules)
        # فرمت‌ها و ایموجی پریمیوم فقط وقتی حفظ می‌شوند که متن اصلی
        # دست‌نخورده مانده باشد؛ در غیر این صورت آفست‌ها معتبر نیستند
        entities = remap_entities(facts.text, text, src_entities)
        delay = int(cfg.get("delay_seconds") or 0)
        if delay > 0:
            await asyncio.sleep(min(delay, 3600))

        allow_watermark = plan.has(FEAT_WATERMARK)
        any_sent = False

        for spec in targets:
            target = spec.target
            # مقصدی که امضا یا فوتر اختصاصی دارد، متنش جدا ساخته می‌شود
            if spec.overrides:
                dest_cfg = {**cfg, **spec.overrides}
                dest_text = apply_transforms(facts.text, dest_cfg, rules)
                dest_entities = remap_entities(facts.text, dest_text, src_entities)
            else:
                dest_cfg, dest_text, dest_entities = cfg, text, entities

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
                raise
            except Exception as exc:
                log.exception("ارسال به مقصد %s ناموفق بود؛ در صف تلاش مجدد", target)
                await self._enqueue_retry(
                    task_id, user_id, src_chat_id, src_ids, str(target), str(exc)
                )
                continue

            if sent:
                any_sent = True
                await self._remember(task_id, src_ids, sent, digest, str(target))

        if any_sent:
            await self._bump(task_id, user_id, skipped=False)
        return any_sent

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

        watermarking = (
            allow_watermark
            and cfg.get("watermark_enabled")
            and cfg.get("watermark_text")
            and media_kind == "photo"
        )

        if watermarking:
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
            final = add_text_watermark(
                src_path,
                dest_path,
                cfg.get("watermark_text", ""),
                position=cfg.get("watermark_position", "bottom-right"),
                opacity=int(cfg.get("watermark_opacity", 60)),
                size_percent=int(cfg.get("watermark_size", 4)),
            )
            if final != src_path:
                src_path.unlink(missing_ok=True)
            results.append(str(final))
        return results

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
    async def _is_duplicate(self, task_id: int, digest: str) -> bool:
        async with get_session() as db:
            row = await db.execute(
                select(MessageMap.id).where(
                    MessageMap.task_id == task_id, MessageMap.content_hash == digest
                ).limit(1)
            )
            return row.scalar_one_or_none() is not None

    async def _remember(
        self,
        task_id: int,
        src_ids: Sequence[int],
        dst_ids: Sequence[int],
        digest: str,
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
                db.add(
                    MessageMap(
                        task_id=task_id,
                        src_msg_id=src_id,
                        dst_msg_id=dst_id,
                        dest_chat=dest_chat,
                        content_hash=digest if index == 0 else None,
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
