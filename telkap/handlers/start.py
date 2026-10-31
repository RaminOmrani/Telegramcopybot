"""دستورات پایه: شروع، راهنما، لغو، گزارش فعالیت."""
from __future__ import annotations

from aiogram import F, Router
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy import select

from telkap import i18n
from telkap.db import get_session
from telkap.handlers.common import get_or_create_user
from telkap.keyboards import BTN_LOGS, main_menu
from telkap.models import ActivityLog, User
from telkap.services import referral
from telkap.services.userbot import manager
from telkap.texts import CANCELLED, fa_num

router = Router(name="start")

RULE = "━━━━━━━━━━━━━━━━━━"

LEVEL_ICON = {"info": "ℹ️", "warning": "⚠️", "error": "❌"}


def welcome(lang: str | None = None) -> str:
    """متن خوش‌آمد به زبان کاربر."""
    return "\n\n".join(
        part
        for part in (
            i18n.t("start.title", lang) + "\n" + RULE,
            i18n.t("start.pitch", lang),
            i18n.t("start.features_title", lang)
            + "\n"
            + i18n.t("start.features", lang),
            i18n.t("start.newcomer", lang),
            i18n.t("start.cta", lang),
        )
        if part
    )


@router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext) -> None:
    await state.clear()
    async with get_session() as db:
        first_visit = await db.get(User, message.from_user.id) is None
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

    # اولین بار: اگر زبان تلگرامش فارسی نیست، انتخاب زبان را نشان بده
    lang = await i18n.language_of(user.id, fallback=message.from_user.language_code)
    if first_visit and lang != i18n.DEFAULT:
        await i18n.set_language(user.id, lang)
    i18n.set_current(lang)

    await message.answer(welcome(lang), reply_markup=main_menu(lang))
    if first_visit:
        from telkap.handlers.language import ask_text, picker

        await message.answer(ask_text(), reply_markup=picker(lang).as_markup())


@router.message(Command("cancel"))
async def cmd_cancel(message: Message, state: FSMContext) -> None:
    current = await state.get_state()
    await state.clear()
    await manager.cancel_login(message.from_user.id)
    await message.answer(
        CANCELLED if current else "چیزی برای لغو نبود.", reply_markup=main_menu()
    )


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
