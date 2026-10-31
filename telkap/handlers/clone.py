"""کپی تنظیمات بین کارها، و خروجی/ورودی گرفتن به‌صورت فایل."""
from __future__ import annotations

import io
import json
import logging

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import BufferedInputFile, CallbackQuery, InlineKeyboardButton, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder
from sqlalchemy import delete, select

from telkap.db import get_session, log_activity
from telkap.handlers.common import Flow
from telkap.models import Rule, Task
from telkap.services import cache
from telkap.services.defaults import DEFAULT_SETTINGS, merged_settings
from telkap.texts import fa_num

log = logging.getLogger(__name__)
router = Router(name="clone")

EXPORT_VERSION = 1
MAX_IMPORT_BYTES = 256 * 1024


async def _owned(user_id: int, task_id: int) -> Task | None:
    async with get_session() as db:
        task = await db.get(Task, task_id)
    return task if task and task.user_id == user_id else None


def _menu(task_id: int) -> InlineKeyboardBuilder:
    kb = InlineKeyboardBuilder()
    kb.row(
        InlineKeyboardButton(text="⬇️ کپی از کار دیگر", callback_data=f"clone:from:{task_id}")
    )
    kb.row(
        InlineKeyboardButton(text="📤 خروجی فایل", callback_data=f"clone:export:{task_id}"),
        InlineKeyboardButton(text="📥 ورودی فایل", callback_data=f"clone:import:{task_id}"),
    )
    kb.row(InlineKeyboardButton(text="🔙 بازگشت", callback_data=f"task:open:{task_id}"))
    return kb


@router.callback_query(F.data.startswith("clone:pick:"))
async def cb_menu(call: CallbackQuery) -> None:
    task_id = int(call.data.split(":")[2])
    if await _owned(call.from_user.id, task_id) is None:
        await call.answer("دسترسی ندارید", show_alert=True)
        return
    await call.answer()
    text = (
        "📋 <b>کپی تنظیمات</b>\n\n"
        "می‌توانید همه‌ی تنظیمات، فیلترها و جایگزینی‌های یک کار دیگر را روی این کار "
        "بیاورید، یا آن‌ها را به‌صورت فایل ذخیره کنید.\n\n"
        "⚠️ کانال مبدا و مقصد تغییر نمی‌کنند — فقط تنظیمات."
    )
    try:
        await call.message.edit_text(text, reply_markup=_menu(task_id).as_markup())
    except Exception:
        await call.message.answer(text, reply_markup=_menu(task_id).as_markup())


# ------------------------------------------------- کپی از کار دیگر
@router.callback_query(F.data.startswith("clone:from:"))
async def cb_pick_source(call: CallbackQuery) -> None:
    task_id = int(call.data.split(":")[2])
    if await _owned(call.from_user.id, task_id) is None:
        await call.answer("دسترسی ندارید", show_alert=True)
        return

    async with get_session() as db:
        rows = await db.execute(
            select(Task).where(Task.user_id == call.from_user.id, Task.id != task_id)
        )
        others = list(rows.scalars())

    await call.answer()
    if not others:
        await call.message.edit_text(
            "کار دیگری ندارید که بتوان از آن کپی کرد.",
            reply_markup=_menu(task_id).as_markup(),
        )
        return

    kb = InlineKeyboardBuilder()
    for other in others:
        kb.row(
            InlineKeyboardButton(
                text=f"📋 {(other.title or other.source_ref)[:40]}",
                callback_data=f"clone:do:{other.id}:{task_id}",
            )
        )
    kb.row(InlineKeyboardButton(text="🔙 بازگشت", callback_data=f"clone:pick:{task_id}"))
    await call.message.edit_text(
        "تنظیمات کدام کار روی این کار کپی شود؟", reply_markup=kb.as_markup()
    )


@router.callback_query(F.data.startswith("clone:do:"))
async def cb_clone(call: CallbackQuery) -> None:
    _, _, raw_src, raw_dst = call.data.split(":")
    src_id, dst_id = int(raw_src), int(raw_dst)

    source = await _owned(call.from_user.id, src_id)
    target = await _owned(call.from_user.id, dst_id)
    if source is None or target is None:
        await call.answer("دسترسی ندارید", show_alert=True)
        return

    async with get_session() as db:
        rows = await db.execute(select(Rule).where(Rule.task_id == src_id))
        source_rules = [
            (r.kind, r.pattern, r.replacement, r.enabled) for r in rows.scalars()
        ]
        dst_task = await db.get(Task, dst_id)
        dst_task.settings = dict(merged_settings(source.settings))
        # قواعد قبلی مقصد جای خود را به قواعد مبدا می‌دهند
        await db.execute(delete(Rule).where(Rule.task_id == dst_id))
        for kind, pattern, replacement, enabled in source_rules:
            db.add(
                Rule(
                    task_id=dst_id,
                    kind=kind,
                    pattern=pattern,
                    replacement=replacement,
                    enabled=enabled,
                )
            )
        await db.commit()

    cache.invalidate_task(dst_id)
    await log_activity(
        user_id=call.from_user.id,
        task_id=dst_id,
        event="clone",
        detail=f"تنظیمات از کار {src_id} کپی شد",
    )
    await call.answer("کپی شد")
    await call.message.edit_text(
        f"✅ تنظیمات «{source.title or source.source_ref}» روی این کار اعمال شد.\n"
        f"({fa_num(len(source_rules))} قاعده منتقل شد)",
        reply_markup=_menu(dst_id).as_markup(),
    )


# --------------------------------------------------------- خروجی فایل
@router.callback_query(F.data.startswith("clone:export:"))
async def cb_export(call: CallbackQuery) -> None:
    task_id = int(call.data.split(":")[2])
    task = await _owned(call.from_user.id, task_id)
    if task is None:
        await call.answer("دسترسی ندارید", show_alert=True)
        return

    async with get_session() as db:
        rows = await db.execute(select(Rule).where(Rule.task_id == task_id))
        rules = [
            {
                "kind": r.kind,
                "pattern": r.pattern,
                "replacement": r.replacement,
                "enabled": r.enabled,
            }
            for r in rows.scalars()
        ]

    payload = {
        "version": EXPORT_VERSION,
        "title": task.title,
        "settings": merged_settings(task.settings),
        "rules": rules,
    }
    raw = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
    await call.answer()
    await call.message.answer_document(
        BufferedInputFile(raw, filename=f"task-{task_id}-settings.json"),
        caption=(
            f"📤 تنظیمات «{task.title or task.source_ref}»\n"
            f"{fa_num(len(rules))} قاعده\n\n"
            "این فایل را می‌توانید روی کار دیگری وارد کنید."
        ),
    )


# --------------------------------------------------------- ورودی فایل
@router.callback_query(F.data.startswith("clone:import:"))
async def cb_import(call: CallbackQuery, state: FSMContext) -> None:
    task_id = int(call.data.split(":")[2])
    if await _owned(call.from_user.id, task_id) is None:
        await call.answer("دسترسی ندارید", show_alert=True)
        return
    await call.answer()
    await state.set_state(Flow.import_settings)
    await state.update_data(task_id=task_id)
    await call.message.answer(
        "📥 فایل تنظیمات (JSON) را بفرستید.\n\n"
        "⚠️ تنظیمات و قواعد فعلی این کار جایگزین می‌شوند.\n\nانصراف: /cancel"
    )


@router.message(Flow.import_settings)
async def got_import(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    task_id = int(data.get("task_id", 0))
    if await _owned(message.from_user.id, task_id) is None:
        await state.clear()
        await message.answer("این کار پیدا نشد.")
        return

    if message.document is None:
        await message.answer("⚠️ لطفاً فایل JSON را بفرستید.")
        return
    if (message.document.file_size or 0) > MAX_IMPORT_BYTES:
        await message.answer("⚠️ فایل خیلی بزرگ است.")
        return

    buffer = io.BytesIO()
    try:
        tg_file = await message.bot.get_file(message.document.file_id)
        await message.bot.download_file(tg_file.file_path, destination=buffer)
        payload = json.loads(buffer.getvalue().decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError):
        await message.answer("⚠️ فایل معتبر نیست. باید یک فایل JSON خروجی همین ربات باشد.")
        return
    except Exception as exc:
        log.exception("خواندن فایل تنظیمات ناموفق بود")
        await message.answer(f"⚠️ خواندن فایل ناموفق بود: {exc}")
        return

    if not isinstance(payload, dict) or "settings" not in payload:
        await message.answer("⚠️ ساختار فایل درست نیست.")
        return

    # فقط کلیدهای شناخته‌شده پذیرفته می‌شوند تا داده‌ی دلخواه وارد تنظیمات نشود
    incoming = payload.get("settings") or {}
    clean = {k: v for k, v in incoming.items() if k in DEFAULT_SETTINGS}

    raw_rules = payload.get("rules") or []
    valid_kinds = {Rule.KIND_REPLACE, Rule.KIND_REGEX, Rule.KIND_BLOCK, Rule.KIND_ALLOW}
    rules = [
        r
        for r in raw_rules
        if isinstance(r, dict) and r.get("kind") in valid_kinds and r.get("pattern")
    ]

    async with get_session() as db:
        task = await db.get(Task, task_id)
        task.settings = merged_settings(clean)
        await db.execute(delete(Rule).where(Rule.task_id == task_id))
        for r in rules[:200]:
            db.add(
                Rule(
                    task_id=task_id,
                    kind=r["kind"],
                    pattern=str(r["pattern"])[:512],
                    replacement=str(r.get("replacement") or "")[:512],
                    enabled=bool(r.get("enabled", True)),
                )
            )
        await db.commit()

    cache.invalidate_task(task_id)
    await state.clear()
    await log_activity(
        user_id=message.from_user.id, task_id=task_id, event="import", detail="تنظیمات وارد شد"
    )
    await message.answer(
        f"✅ تنظیمات وارد شد.\n"
        f"{fa_num(len(clean))} تنظیم و {fa_num(len(rules))} قاعده اعمال شد.",
        reply_markup=_menu(task_id).as_markup(),
    )
