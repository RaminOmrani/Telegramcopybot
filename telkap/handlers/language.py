"""انتخاب زبان رابط کاربری."""
from __future__ import annotations

import logging

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, InlineKeyboardButton, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder

from telkap import i18n
from telkap.keyboards import main_menu

log = logging.getLogger(__name__)
router = Router(name="language")


def picker(current: str | None = None) -> InlineKeyboardBuilder:
    kb = InlineKeyboardBuilder()
    for code in i18n.LANGS:
        mark = "🔘 " if code == current else ""
        kb.row(
            InlineKeyboardButton(
                text=f"{mark}{i18n.LANG_NAMES[code]}", callback_data=f"lang:{code}"
            )
        )
    return kb


def ask_text(lang: str | None = None) -> str:
    """پرسش را به هر سه زبان می‌آورد — کسی که فارسی نمی‌داند هم بفهمد."""
    lines = [i18n.t("lang.ask", lang)]
    if lang is None:
        lines = [i18n.t("lang.ask", code) for code in i18n.LANGS]
    return "\n".join(lines)


@router.message(Command("language"))
@router.message(Command("lang"))
async def cmd_language(message: Message) -> None:
    current = await i18n.language_of(
        message.from_user.id, fallback=message.from_user.language_code
    )
    await message.answer(ask_text(), reply_markup=picker(current).as_markup())


@router.callback_query(F.data == "acc:lang")
async def cb_open(call: CallbackQuery) -> None:
    current = await i18n.language_of(call.from_user.id)
    await call.answer()
    await call.message.answer(ask_text(), reply_markup=picker(current).as_markup())


@router.callback_query(F.data.startswith("lang:"))
async def cb_set(call: CallbackQuery) -> None:
    code = call.data.split(":", 1)[1]
    if code not in i18n.LANGS:
        await call.answer()
        return

    await i18n.set_language(call.from_user.id, code)
    await call.answer(i18n.t("lang.changed", code))
    try:
        await call.message.edit_text(
            i18n.t("lang.changed", code), reply_markup=picker(code).as_markup()
        )
    except Exception:
        log.debug("ویرایش پیام انتخاب زبان ناموفق بود", exc_info=True)

    # منوی پایین صفحه با زبان تازه دوباره ساخته می‌شود
    note = i18n.t("lang.partial", code)
    await call.message.answer(
        note or i18n.t("start.cta", code), reply_markup=main_menu(code)
    )
