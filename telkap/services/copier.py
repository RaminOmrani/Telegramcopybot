"""موتور کپی: دریافت پیام از کانال مبدا، پردازش، و ارسال به کانال مقصد."""
from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any, Iterable, Sequence

from sqlalchemy import delete, select
from telethon.errors import ChatWriteForbiddenError, FloodWaitError, MessageIdInvalidError
from telethon.tl.types import (
    DocumentAttributeAnimated,
    DocumentAttributeAudio,
    DocumentAttributeSticker,
    DocumentAttributeVideo,
    MessageMediaDocument,
    MessageMediaPhoto,
    MessageMediaPoll,
    MessageMediaWebPage,
)

from telkap.config import get_settings
from telkap.db import get_session, log_activity
from telkap.models import MessageMap, Rule, Task, utcnow
from telkap.services.defaults import merged_settings
from telkap.services.filters import MessageFacts, content_hash, should_copy
from telkap.services.ratelimit import RateLimiter, hourly_quota
from telkap.services.subscription import active_plan_for
from telkap.services.transform import apply_transforms
from telkap.services.watermark import add_text_watermark
from telkap.plans import FEAT_WATERMARK

log = logging.getLogger(__name__)


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


def build_facts(message) -> MessageFacts:
    return MessageFacts(
        text=message.message or "",
        media_kind=classify_media(message),
        is_forwarded=getattr(message, "fwd_from", None) is not None,
        has_buttons=getattr(message, "reply_markup", None) is not None,
        size_bytes=media_size(message),
    )


class Copier:
    """هسته‌ی کپی. یک نمونه برای کل ربات ساخته می‌شود."""

    def __init__(self, manager, notifier=None) -> None:
        self.manager = manager
        self.notifier = notifier  # callable(user_id, text) برای هشدار به کاربر
        self.limiter = RateLimiter(get_settings().rate_per_minute)
        self._album_guard: set[tuple[int, int]] = set()

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
        """یک پیام یا آلبوم را برای یک کار پردازش و ارسال می‌کند."""
        async with get_session() as db:
            task = await db.get(Task, task_id)
            if task is None or not task.enabled:
                return False
            rules = list(
                (await db.execute(select(Rule).where(Rule.task_id == task_id))).scalars()
            )
            cfg = merged_settings(task.settings)
            dest_ref, dest_id = task.dest_ref, task.dest_id
            src_ids = [m.id for m in messages]

        plan = await active_plan_for(user_id)
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
        # در آلبوم، متن معمولاً روی یکی از آیتم‌ها است
        if not facts.text:
            for msg in messages:
                if msg.message:
                    facts.text = msg.message
                    break

        decision = should_copy(facts, cfg, rules)
        if not decision.allowed:
            await self._bump(task_id, skipped=True)
            await log_activity(
                user_id=user_id, task_id=task_id, event="skip", detail=decision.reason
            )
            return False

        if not hourly_quota.allow(task_id, int(cfg.get("max_per_hour") or 0)):
            await self._bump(task_id, skipped=True)
            await log_activity(
                user_id=user_id, task_id=task_id, event="skip", detail="سقف ساعتی پر شده است"
            )
            return False

        digest = content_hash(facts)
        if cfg.get("skip_duplicates") and await self._is_duplicate(task_id, digest):
            await self._bump(task_id, skipped=True)
            await log_activity(
                user_id=user_id, task_id=task_id, event="skip", detail="پست تکراری"
            )
            return False

        client = await self.manager.ensure_client(user_id)
        if client is None:
            await self._pause_task(task_id, "اکانت کاربری متصل نیست")
            return False

        text = apply_transforms(facts.text, cfg, rules)
        delay = int(cfg.get("delay_seconds") or 0)
        if delay > 0:
            await asyncio.sleep(min(delay, 3600))

        target = dest_id or dest_ref
        await self.limiter.acquire(str(target))

        allow_watermark = plan.has(FEAT_WATERMARK)
        try:
            sent = await self._send(
                client, target, messages, text, cfg, allow_watermark=allow_watermark
            )
        except ChatWriteForbiddenError:
            await self._pause_task(task_id, "دسترسی ارسال به کانال مقصد وجود ندارد")
            if self.notifier:
                await self.notifier(
                    user_id,
                    f"⚠️ کار «{task.title or task_id}» متوقف شد: "
                    "اکانت شما اجازه‌ی ارسال در کانال مقصد را ندارد.",
                )
            return False

        if sent:
            await self._remember(task_id, src_ids, sent, digest)
            await self._bump(task_id, skipped=False)
        return bool(sent)

    async def _send(
        self,
        client,
        target,
        messages: Sequence,
        text: str,
        cfg: dict[str, Any],
        *,
        allow_watermark: bool,
    ) -> list[int]:
        """ارسال واقعی به مقصد؛ آیدی پیام‌های ارسالی را برمی‌گرداند."""
        # حالت فوروارد ساده: برچسب «فورواردشده از» حفظ می‌شود
        if cfg.get("mode") == "forward":
            result = await client.forward_messages(target, list(messages))
            result = result if isinstance(result, list) else [result]
            return [m.id for m in result if m]

        media_kind = classify_media(messages[0])

        if cfg.get("caption_only") or media_kind in {"text", "poll"}:
            if not text.strip():
                return []
            sent = await client.send_message(target, text, link_preview=False)
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
                sent = await client.send_file(target, files, caption=text or None)
            finally:
                for path in files:
                    Path(path).unlink(missing_ok=True)
        else:
            payload = [m.media for m in messages if m.media is not None]
            if not payload:
                if not text.strip():
                    return []
                sent = await client.send_message(target, text, link_preview=False)
                return [sent.id]
            try:
                sent = await client.send_file(target, payload, caption=text or None)
            except (FloodWaitError, ChatWriteForbiddenError):
                raise  # این دو را لایه‌ی بالاتر مدیریت می‌کند
            except Exception:
                # کانال‌های محدودشده اجازه‌ی ارسال مجدد مستقیم رسانه را نمی‌دهند؛
                # در این حالت فایل دانلود و دوباره آپلود می‌شود.
                log.info("ارسال مستقیم رسانه ناموفق بود؛ دانلود و آپلود مجدد")
                files = await self._download_all(client, messages)
                try:
                    sent = await client.send_file(target, files, caption=text or None)
                finally:
                    for path in files:
                        Path(path).unlink(missing_ok=True)

        sent = sent if isinstance(sent, list) else [sent]
        return [m.id for m in sent if m]

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
        async with get_session() as db:
            task = await db.get(Task, task_id)
            if task is None or not task.enabled:
                return
            cfg = merged_settings(task.settings)
            if not cfg.get("sync_edits"):
                return
            rules = list(
                (await db.execute(select(Rule).where(Rule.task_id == task_id))).scalars()
            )
            row = await db.execute(
                select(MessageMap).where(
                    MessageMap.task_id == task_id, MessageMap.src_msg_id == message.id
                )
            )
            mapping = row.scalar_one_or_none()
            target = task.dest_id or task.dest_ref
        if mapping is None:
            return

        client = await self.manager.ensure_client(user_id)
        if client is None:
            return
        text = apply_transforms(message.message or "", cfg, rules)
        try:
            await client.edit_message(target, mapping.dst_msg_id, text)
            await log_activity(user_id=user_id, task_id=task_id, event="edit", detail=f"#{message.id}")
        except (MessageIdInvalidError, ValueError):
            log.debug("ویرایش پیام %s ممکن نبود", mapping.dst_msg_id)
        except FloodWaitError as exc:
            await asyncio.sleep(min(exc.seconds, 300))

    async def _sync_delete(self, user_id: int, task_id: int, deleted_ids: Iterable[int]) -> None:
        async with get_session() as db:
            task = await db.get(Task, task_id)
            if task is None:
                return
            cfg = merged_settings(task.settings)
            if not cfg.get("sync_deletes"):
                return
            rows = await db.execute(
                select(MessageMap).where(
                    MessageMap.task_id == task_id, MessageMap.src_msg_id.in_(list(deleted_ids))
                )
            )
            mappings = list(rows.scalars())
            target = task.dest_id or task.dest_ref

        if not mappings:
            return
        client = await self.manager.ensure_client(user_id)
        if client is None:
            return
        try:
            await client.delete_messages(target, [m.dst_msg_id for m in mappings])
            await log_activity(
                user_id=user_id, task_id=task_id, event="delete", detail=f"{len(mappings)} پیام"
            )
        except Exception:
            log.debug("حذف پیام‌های مقصد ناموفق بود", exc_info=True)

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
        self, task_id: int, src_ids: Sequence[int], dst_ids: Sequence[int], digest: str
    ) -> None:
        if not src_ids or not dst_ids:
            return
        async with get_session() as db:
            for index, src_id in enumerate(src_ids):
                dst_id = dst_ids[index] if index < len(dst_ids) else dst_ids[0]
                existing = await db.execute(
                    select(MessageMap).where(
                        MessageMap.task_id == task_id, MessageMap.src_msg_id == src_id
                    )
                )
                if existing.scalar_one_or_none() is not None:
                    continue
                db.add(
                    MessageMap(
                        task_id=task_id,
                        src_msg_id=src_id,
                        dst_msg_id=dst_id,
                        content_hash=digest if index == 0 else None,
                    )
                )
            await db.commit()
        await self._trim_history(task_id)

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

    async def _bump(self, task_id: int, *, skipped: bool) -> None:
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

    async def _record_error(self, task_id: int, detail: str) -> None:
        async with get_session() as db:
            task = await db.get(Task, task_id)
            if task is None:
                return
            task.last_error = detail[:400]
            await db.commit()
        await log_activity(task_id=task_id, event="error", detail=detail, level="error")

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
        await self.manager.reload_user(user_id)
        await log_activity(task_id=task_id, event="pause", detail=reason, level="warning")
