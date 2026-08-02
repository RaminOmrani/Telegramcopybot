"""چند مقصد برای یک کار: یک بار خواندن مبدا، انتشار در چند کانال."""
from __future__ import annotations

import logging

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy import func, select

from telkap.db import get_session, log_activity
from telkap.keyboards import destinations_menu
from telkap.models import Destination, Task
from telkap.handlers.common import Flow
from telkap.services.userbot import manager
from telkap.texts import NO_LOGIN, fa_num

log = logging.getLogger(__name__)
router = Router(name="destinations")

MAX_EXTRA_DESTS = 9


async def _owned(user_id: int, task_id: int) -> Task | None:
    async with get_session() as db:
        task = await db.get(Task, task_id)
    return task if task and task.user_id == user_id else None


async def _extras(task_id: int) -> list[Destination]:
    async with get_session() as db:
        rows = await db.execute(
            select(Destination).where(Destination.task_id == task_id).order_by(Destination.id)
        )
        return list(rows.scalars())


async def _render(target: Message, task: Task, *, edit: bool = False) -> None:
    extras = await _extras(task.id)
    primary = task.dest_title or task.dest_ref
    text = (
        "📤 <b>کانال‌های مقصد</b>\n\n"
        f"پست‌های «{task.source_title or task.source_ref}» در همه‌ی کانال‌های "
        f"زیر منتشر می‌شوند.\n\n"
        f"تعداد: {fa_num(1 + sum(1 for d in extras if d.enabled))} کانال فعال"
    )
    markup = destinations_menu(task.id, primary, extras)
    if edit:
        try:
            await target.edit_text(text, reply_markup=markup)
            return
        except Exception:
            log.debug("ویرایش پیام مقصدها ناموفق بود", exc_info=True)
    await target.answer(text, reply_markup=markup)


@router.callback_query(F.data.startswith("dest:list:"))
async def cb_list(call: CallbackQuery) -> None:
    task_id = int(call.data.split(":")[2])
    task = await _owned(call.from_user.id, task_id)
    if task is None:
        await call.answer("دسترسی ندارید", show_alert=True)
        return
    await call.answer()
    await _render(call.message, task, edit=True)


@router.callback_query(F.data.startswith("dest:add:"))
async def cb_add(call: CallbackQuery, state: FSMContext) -> None:
    task_id = int(call.data.split(":")[2])
    if await _owned(call.from_user.id, task_id) is None:
        await call.answer("دسترسی ندارید", show_alert=True)
        return

    async with get_session() as db:
        count = await db.scalar(
            select(func.count(Destination.id)).where(Destination.task_id == task_id)
        )
    if (count or 0) >= MAX_EXTRA_DESTS:
        await call.answer(
            f"حداکثر {fa_num(MAX_EXTRA_DESTS)} مقصد اضافی مجاز است.", show_alert=True
        )
        return

    await call.answer()
    await state.set_state(Flow.dest_add)
    await state.update_data(task_id=task_id)
    await call.message.answer(
        "📤 آیدی یا لینک کانال مقصد جدید را بفرستید.\n"
        "⚠️ اکانت شما باید در آن اجازه‌ی ارسال داشته باشد.\n\nانصراف: /cancel"
    )


@router.message(Flow.dest_add)
async def got_dest(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    task_id = int(data.get("task_id", 0))
    task = await _owned(message.from_user.id, task_id)
    if task is None:
        await state.clear()
        await message.answer("این کار پیدا نشد.")
        return

    ref = (message.text or "").strip()
    client = await manager.ensure_client(message.from_user.id)
    if client is None:
        await state.clear()
        await message.answer(NO_LOGIN)
        return

    notice = await message.answer("⏳ در حال بررسی کانال…")
    entity = await manager.resolve_entity(client, ref)
    if entity is None:
        await notice.edit_text("⚠️ کانال پیدا نشد یا اکانت شما به آن دسترسی ندارد.")
        return

    chat_id = await manager.resolve_chat_id(client, ref)
    title = getattr(entity, "title", None) or ref

    # مقصد تکراری نگذاریم
    if str(chat_id) == str(task.dest_id) or ref == task.dest_ref:
        await notice.edit_text("⚠️ این همان کانال مقصد اصلی است.")
        return
    for existing in await _extras(task_id):
        if str(existing.chat_id) == str(chat_id) or existing.ref == ref:
            await notice.edit_text("⚠️ این کانال قبلاً اضافه شده است.")
            return

    async with get_session() as db:
        db.add(
            Destination(
                task_id=task_id, chat_id=chat_id, ref=ref, title=title[:160]
            )
        )
        await db.commit()

    await state.clear()
    await notice.edit_text(f"✅ کانال <b>{title}</b> به مقصدها اضافه شد.")
    await log_activity(
        user_id=message.from_user.id, task_id=task_id, event="dest_add", detail=ref
    )
    task = await _owned(message.from_user.id, task_id)
    await _render(message, task)


@router.callback_query(F.data.startswith("dest:toggle:"))
async def cb_toggle(call: CallbackQuery) -> None:
    _, _, raw_dest, raw_task = call.data.split(":")
    task = await _owned(call.from_user.id, int(raw_task))
    if task is None:
        await call.answer("دسترسی ندارید", show_alert=True)
        return
    async with get_session() as db:
        dest = await db.get(Destination, int(raw_dest))
        if dest is None or dest.task_id != task.id:
            await call.answer("این مقصد پیدا نشد.", show_alert=True)
            return
        dest.enabled = not dest.enabled
        enabled = dest.enabled
        await db.commit()
    await call.answer("فعال شد" if enabled else "خاموش شد")
    await _render(call.message, task, edit=True)


@router.callback_query(F.data.startswith("dest:del:"))
async def cb_delete(call: CallbackQuery) -> None:
    _, _, raw_dest, raw_task = call.data.split(":")
    task = await _owned(call.from_user.id, int(raw_task))
    if task is None:
        await call.answer("دسترسی ندارید", show_alert=True)
        return
    async with get_session() as db:
        dest = await db.get(Destination, int(raw_dest))
        if dest is not None and dest.task_id == task.id:
            await db.delete(dest)
            await db.commit()
    await call.answer("حذف شد")
    await _render(call.message, task, edit=True)
