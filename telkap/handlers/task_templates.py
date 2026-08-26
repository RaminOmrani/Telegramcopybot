"""انتخاب قالب آماده برای یک کار.

قالب پیش از اعمال نشان داده می‌شود و می‌گوید دقیقاً چند گزینه عوض
می‌شود؛ کاربر نباید با زدن یک دکمه غافلگیر شود.
"""
from __future__ import annotations

import logging

from aiogram import F, Router
from aiogram.types import CallbackQuery, InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder

from telkap.db import get_session, log_activity
from telkap.models import Task
from telkap.services import cache, templates
from telkap.services.defaults import merged_settings
from telkap.texts import fa_num

log = logging.getLogger(__name__)
router = Router(name="task-templates")


async def _owned(user_id: int, task_id: int) -> Task | None:
    async with get_session() as db:
        task = await db.get(Task, task_id)
    return task if task is not None and task.user_id == user_id else None


@router.callback_query(F.data.startswith("tpl:list:"))
async def cb_list(call: CallbackQuery) -> None:
    task_id = int(call.data.split(":")[2])
    if await _owned(call.from_user.id, task_id) is None:
        await call.answer("دسترسی ندارید", show_alert=True)
        return

    kb = InlineKeyboardBuilder()
    for template in templates.TEMPLATES:
        kb.row(
            InlineKeyboardButton(
                text=template.title, callback_data=f"tpl:show:{template.code}:{task_id}"
            )
        )
    kb.row(InlineKeyboardButton(text="🔙 بازگشت", callback_data=f"task:open:{task_id}"))

    lines = ["🧰 <b>قالب آماده</b>\n"]
    lines.append(
        "یک قالب انتخاب کنید تا تنظیم‌های متداولِ آن نوع کانال یک‌جا "
        "اعمال شود.\n"
    )
    for template in templates.TEMPLATES:
        lines.append(f"{template.title} — {template.summary}")
    lines.append(
        "\n<i>قالب فقط نقطه‌ی شروع است؛ بعدش هر گزینه‌ای را می‌توانید "
        "دستی عوض کنید.</i>"
    )

    await call.answer()
    await call.message.edit_text("\n".join(lines), reply_markup=kb.as_markup())


@router.callback_query(F.data.startswith("tpl:show:"))
async def cb_show(call: CallbackQuery) -> None:
    _, _, code, raw_task = call.data.split(":")
    task_id = int(raw_task)
    task = await _owned(call.from_user.id, task_id)
    template = templates.get(code)
    if task is None or template is None:
        await call.answer("این قالب پیدا نشد", show_alert=True)
        return

    cfg = merged_settings(task.settings)
    pending_changes = templates.changes(cfg, template)

    kb = InlineKeyboardBuilder()
    if pending_changes:
        kb.row(
            InlineKeyboardButton(
                text="✅ اعمال کن", callback_data=f"tpl:use:{code}:{task_id}"
            )
        )
    kb.row(InlineKeyboardButton(text="🔙 قالب‌های دیگر", callback_data=f"tpl:list:{task_id}"))

    body = f"{template.title}\n\n{template.detail}\n\n"
    if pending_changes:
        body += f"<b>{fa_num(len(pending_changes))} تنظیم عوض می‌شود.</b>"
    else:
        body += "✅ تنظیمات این کار از قبل با این قالب یکی است."

    await call.answer()
    await call.message.edit_text(body, reply_markup=kb.as_markup())


@router.callback_query(F.data.startswith("tpl:use:"))
async def cb_use(call: CallbackQuery) -> None:
    _, _, code, raw_task = call.data.split(":")
    task_id = int(raw_task)
    task = await _owned(call.from_user.id, task_id)
    template = templates.get(code)
    if task is None or template is None:
        await call.answer("این قالب پیدا نشد", show_alert=True)
        return

    async with get_session() as db:
        row = await db.get(Task, task_id)
        cfg = merged_settings(row.settings)
        applied = len(templates.changes(cfg, template))
        row.settings = templates.apply(cfg, template)
        await db.commit()
    cache.invalidate_task(task_id)

    await log_activity(
        user_id=call.from_user.id,
        task_id=task_id,
        event="template",
        detail=f"{template.title} ({applied} تنظیم)",
    )
    await call.answer(f"{template.title} اعمال شد")

    from telkap.handlers.tasks import show_task

    await show_task(call.message, task_id, edit=True)
