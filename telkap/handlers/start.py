"""دستورات پایه: شروع، راهنما، لغو، گزارش فعالیت."""
from __future__ import annotations

from aiogram import F, Router
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import Message
from sqlalchemy import select

from telkap.db import get_session
from telkap.handlers.common import get_or_create_user
from telkap.keyboards import BTN_LOGS, main_menu
from telkap.models import ActivityLog
from telkap.services.userbot import manager
from telkap.texts import CANCELLED, START, fa_num

router = Router(name="start")

LEVEL_ICON = {"info": "ℹ️", "warning": "⚠️", "error": "❌"}


@router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext) -> None:
    await state.clear()
    await get_or_create_user(message.from_user)
    await message.answer(START, reply_markup=main_menu())


@router.message(Command("cancel"))
async def cmd_cancel(message: Message, state: FSMContext) -> None:
    current = await state.get_state()
    await state.clear()
    await manager.cancel_login(message.from_user.id)
    await message.answer(CANCELLED if current else "چیزی برای لغو نبود.", reply_markup=main_menu())


@router.message(Command("logs"))
@router.message(F.text == BTN_LOGS)
async def cmd_logs(message: Message) -> None:
    async with get_session() as db:
        rows = await db.execute(
            select(ActivityLog)
            .where(ActivityLog.user_id == message.from_user.id)
            .order_by(ActivityLog.id.desc())
            .limit(20)
        )
        logs = list(rows.scalars())

    if not logs:
        await message.answer("🧾 هنوز فعالیتی ثبت نشده است.")
        return

    lines = ["🧾 <b>۲۰ رویداد آخر</b>\n"]
    for entry in logs:
        icon = LEVEL_ICON.get(entry.level, "•")
        stamp = entry.created_at.strftime("%m/%d %H:%M")
        detail = entry.detail or entry.event
        lines.append(f"{icon} <code>{fa_num(stamp)}</code> — {detail}")
    await message.answer("\n".join(lines))
