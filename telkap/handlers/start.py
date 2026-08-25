"""دستورات پایه: شروع، راهنما، لغو، گزارش فعالیت."""
from __future__ import annotations

from aiogram import F, Router
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy import select

from telkap.db import get_session
from telkap.handlers.common import get_or_create_user
from telkap.keyboards import BTN_LOGS, main_menu
from telkap.models import ActivityLog
from telkap.services import referral
from telkap.services.userbot import manager
from telkap.texts import CANCELLED, START, fa_num

router = Router(name="start")

LEVEL_ICON = {"info": "ℹ️", "warning": "⚠️", "error": "❌"}


@router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext) -> None:
    await state.clear()
    user = await get_or_create_user(message.from_user)

    # لینک دعوت: /start ref_12345 — فقط برای کاربری که هنوز معرفی ندارد
    payload = (message.text or "").partition(" ")[2].strip()
    referrer_id = referral.parse_payload(payload)
    if referrer_id and user.referred_by is None:
        if await referral.bind(user.id, referrer_id):
            cfg = await referral.settings()
            discount = int(cfg.get("invitee_discount_percent") or 0)
            if cfg.get("enabled") and discount:
                await message.answer(
                    f"🎉 با لینک دعوت وارد شدید — <b>{fa_num(discount)}٪ تخفیف</b> "
                    "روی اولین خریدتان اعمال می‌شود."
                )

    await message.answer(START, reply_markup=main_menu())


@router.message(Command("cancel"))
async def cmd_cancel(message: Message, state: FSMContext) -> None:
    current = await state.get_state()
    await state.clear()
    await manager.cancel_login(message.from_user.id)
    await message.answer(CANCELLED if current else "چیزی برای لغو نبود.", reply_markup=main_menu())


async def _logs_text(user_id: int) -> str:
    async with get_session() as db:
        rows = await db.execute(
            select(ActivityLog)
            .where(ActivityLog.user_id == user_id)
            .order_by(ActivityLog.id.desc())
            .limit(20)
        )
        logs = list(rows.scalars())

    if not logs:
        return "🧾 هنوز فعالیتی ثبت نشده است."

    lines = ["🧾 <b>۲۰ رویداد آخر</b>\n"]
    for entry in logs:
        icon = LEVEL_ICON.get(entry.level, "•")
        stamp = entry.created_at.strftime("%m/%d %H:%M")
        detail = entry.detail or entry.event
        lines.append(f"{icon} <code>{fa_num(stamp)}</code> — {detail}")
    return "\n".join(lines)


@router.message(Command("logs"))
@router.message(F.text == BTN_LOGS)
async def cmd_logs(message: Message) -> None:
    await message.answer(await _logs_text(message.from_user.id))


@router.callback_query(F.data == "acc:logs")
async def cb_logs(call: CallbackQuery) -> None:
    await call.answer()
    await call.message.answer(await _logs_text(call.from_user.id))
