"""تست کار: آخرین پست مبدا را با تنظیمات فعلی پردازش می‌کند و نتیجه را
به کاربر نشان می‌دهد — بدون ارسال به کانال مقصد.

کاربر بدون آزمون‌وخطا می‌بیند فیلترها و جایگزینی‌هایش دقیقاً چه می‌کنند.
"""
from __future__ import annotations

import html
import logging

from aiogram import F, Router
from aiogram.types import CallbackQuery, InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder

from telkap.db import get_session
from telkap.keyboards import MEDIA_LABELS
from telkap.models import Task
from telkap.services import cache
from telkap.services.copier import build_facts, classify_media, within_active_hours
from telkap.services.filters import should_copy
from telkap.services.transform import apply_transforms
from telkap.services.userbot import manager
from telkap.texts import NO_LOGIN, fa_num

log = logging.getLogger(__name__)
router = Router(name="preview")

PREVIEW_LIMIT = 3       # چند پست آخر بررسی شود
SNIPPET_CHARS = 700     # سقف نمایش متن، تا پیام تلگرام سرریز نکند


def _clip(text: str, limit: int = SNIPPET_CHARS) -> str:
    text = (text or "").strip()
    if not text:
        return "<i>(بدون متن)</i>"
    if len(text) > limit:
        text = text[:limit] + "…"
    return html.escape(text)


@router.callback_query(F.data.startswith("task:test:"))
async def cb_test(call: CallbackQuery) -> None:
    task_id = int(call.data.split(":")[2])

    async with get_session() as db:
        task = await db.get(Task, task_id)
    if task is None or task.user_id != call.from_user.id:
        await call.answer("دسترسی ندارید", show_alert=True)
        return

    await call.answer("در حال بررسی…")
    notice = await call.message.answer("🧪 در حال خواندن آخرین پست‌های کانال مبدا…")

    client = await manager.ensure_client(call.from_user.id)
    if client is None:
        await notice.edit_text(NO_LOGIN)
        return

    snapshot = await cache.get_task(task_id)
    if snapshot is None:
        await notice.edit_text("این کار پیدا نشد.")
        return

    source = snapshot.source_id or snapshot.source_ref
    try:
        entity = await manager.resolve_entity(client, str(source))
        if entity is None:
            await notice.edit_text(
                "⚠️ کانال مبدا در دسترس نیست. مطمئن شوید اکانت شما هنوز آن را می‌بیند."
            )
            return
        messages = []
        async for message in client.iter_messages(entity, limit=PREVIEW_LIMIT):
            messages.append(message)
    except Exception as exc:
        log.exception("خواندن پیام‌های مبدا برای تست ناموفق بود")
        await notice.edit_text(f"⚠️ خواندن کانال مبدا ناموفق بود: {exc}")
        return

    if not messages:
        await notice.edit_text("کانال مبدا پستی ندارد.")
        return

    cfg, rules = snapshot.cfg, snapshot.rules
    blocks = [f"🧪 <b>تست «{html.escape(snapshot.title)}»</b>"]

    if not within_active_hours(cfg):
        start = fa_num(int(cfg.get("active_from_hour") or 0))
        end = fa_num(int(cfg.get("active_to_hour") or 0))
        blocks.append(
            f"\n⏰ <b>توجه:</b> الان خارج از بازه‌ی فعال کار است ({start}:۰۰ تا {end}:۰۰).\n"
            "پست‌های واقعی در این ساعت کپی نمی‌شوند."
        )

    for index, message in enumerate(messages, start=1):
        facts = build_facts(message)
        kind = MEDIA_LABELS.get(classify_media(message), "نامشخص")
        decision = should_copy(facts, cfg, rules)

        blocks.append(f"\n━━━━━━━━━━\n<b>پست {fa_num(index)}</b> — نوع: {kind}")
        blocks.append(f"\n<b>متن اصلی:</b>\n{_clip(facts.text, 300)}")

        if not decision.allowed:
            blocks.append(f"\n❌ <b>کپی نمی‌شود</b>\nعلت: {html.escape(decision.reason)}")
            continue

        result = apply_transforms(facts.text, cfg, rules)
        blocks.append("\n✅ <b>کپی می‌شود</b>")
        blocks.append(f"\n<b>متن نهایی در کانال شما:</b>\n{_clip(result, 400)}")

        if result.strip() != (facts.text or "").strip():
            blocks.append("\n<i>↑ متن طبق تنظیمات شما تغییر کرده است</i>")

    kb = InlineKeyboardBuilder()
    kb.row(InlineKeyboardButton(text="🔄 تست دوباره", callback_data=f"task:test:{task_id}"))
    kb.row(InlineKeyboardButton(text="🔙 بازگشت به کار", callback_data=f"task:open:{task_id}"))

    text = "\n".join(blocks)
    if len(text) > 4000:
        text = text[:3990] + "\n…"
    await notice.edit_text(text, reply_markup=kb.as_markup())
