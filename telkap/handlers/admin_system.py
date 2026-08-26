"""سیستم: نقش ادمین‌ها و حالت «در دست تعمیر»."""
from __future__ import annotations

import logging

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder

from telkap.config import get_settings
from telkap.db import log_activity
from telkap.handlers.admin_reports import guard
from telkap.handlers.common import Flow, parse_int
from telkap.services import maintenance, roles

log = logging.getLogger(__name__)
router = Router(name="admin-system")


def _menu_kb() -> InlineKeyboardBuilder:
    kb = InlineKeyboardBuilder()
    kb.row(
        InlineKeyboardButton(text="👮 نقش ادمین‌ها", callback_data="sys:roles"),
        InlineKeyboardButton(text="🛠 حالت تعمیر", callback_data="sys:maint"),
    )
    kb.row(InlineKeyboardButton(text="🔙 بازگشت", callback_data="adm:home"))
    return kb


async def _show(event: CallbackQuery | Message, text: str, kb) -> None:
    if isinstance(event, CallbackQuery):
        await event.answer()
        await event.message.edit_text(text, reply_markup=kb.as_markup())
    else:
        await event.answer(text, reply_markup=kb.as_markup())


@router.callback_query(F.data == "adm:sys")
async def cb_system(call: CallbackQuery) -> None:
    if await guard(call, roles.CAP_SYSTEM):
        return
    on, _note = await maintenance.mode()
    state = "🔴 روشن" if on else "🟢 خاموش"
    await _show(
        call,
        f"⚙️ <b>سیستم</b>\n\nحالت تعمیر: <b>{state}</b>",
        _menu_kb(),
    )


# --------------------------------------------------------- نقش ادمین‌ها
async def _roles_screen() -> tuple[str, InlineKeyboardBuilder]:
    owners = get_settings().admin_ids
    rows = await roles.listing()

    text = ["👮 <b>نقش ادمین‌ها</b>\n"]
    text.append("<b>مالکان (از فایل .env)</b>")
    text.append(
        "\n".join(f"• <code>{uid}</code>" for uid in owners) if owners else "—"
    )
    text.append("\nاین‌ها از پنل قابل حذف نیستند؛ فقط با ویرایش <code>.env</code>.\n")

    if rows:
        text.append("<b>ادمین‌های افزوده‌شده</b>")
        for row in rows:
            label = roles.ROLE_LABELS.get(row.role, row.role)
            note = f" — {row.note}" if row.note else ""
            text.append(f"• <code>{row.user_id}</code>: {label}{note}")
    else:
        text.append("هنوز ادمین دیگری اضافه نشده.")

    text.append(
        "\n<b>هر نقش چه می‌بیند</b>\n"
        + "\n".join(
            f"{roles.ROLE_LABELS[role]}"
            for role in (roles.ROLE_OWNER, roles.ROLE_FINANCE, roles.ROLE_SUPPORT)
        )
    )

    kb = InlineKeyboardBuilder()
    kb.row(InlineKeyboardButton(text="➕ افزودن ادمین", callback_data="sys:radd"))
    for row in rows:
        kb.row(
            InlineKeyboardButton(
                text=f"🗑 حذف {row.user_id}", callback_data=f"sys:rdel:{row.user_id}"
            )
        )
    kb.row(InlineKeyboardButton(text="🔙 بازگشت", callback_data="adm:sys"))
    return "\n".join(text), kb


@router.callback_query(F.data == "sys:roles")
async def cb_roles(call: CallbackQuery) -> None:
    if await guard(call, roles.CAP_SYSTEM):
        return
    text, kb = await _roles_screen()
    await _show(call, text, kb)


@router.callback_query(F.data == "sys:radd")
async def cb_role_add(call: CallbackQuery, state: FSMContext) -> None:
    if await guard(call, roles.CAP_SYSTEM):
        return
    await call.answer()
    await state.set_state(Flow.admin_role)
    kb = InlineKeyboardBuilder()
    kb.row(InlineKeyboardButton(text="🔙 انصراف", callback_data="sys:roles"))
    await call.message.edit_text(
        "➕ <b>افزودن ادمین</b>\n\n"
        "شناسه‌ی عددی و نقش را در یک خط بفرستید:\n"
        "<code>123456789 support</code>\n\n"
        "نقش‌های مجاز:\n"
        "<code>owner</code> — همه‌چیز\n"
        "<code>finance</code> — پرداخت، طرح‌ها، کد تخفیف، گزارش‌ها\n"
        "<code>support</code> — تیکت‌ها و کاربران\n\n"
        "می‌توانید بعد از نقش، یک یادداشت هم بنویسید:\n"
        "<code>123456789 support علی — شیفت شب</code>",
        reply_markup=kb.as_markup(),
    )


@router.message(Flow.admin_role)
async def do_role_add(message: Message, state: FSMContext) -> None:
    if not await roles.can(message.from_user.id, roles.CAP_SYSTEM):
        await state.clear()
        return
    parts = (message.text or "").split(maxsplit=2)
    user_id = parse_int(parts[0]) if parts else None
    role = parts[1].strip().lower() if len(parts) > 1 else ""
    note = parts[2].strip() if len(parts) > 2 else ""

    if user_id is None or role not in roles.ROLE_CAPS:
        await message.answer(
            "قالب درست نیست. مثال:\n<code>123456789 support</code>"
        )
        return

    await state.clear()
    ok = await roles.set_role(
        user_id, role, note=note, added_by=message.from_user.id
    )
    if not ok:
        await message.answer(
            "این شناسه در <code>.env</code> ثبت شده و از قبل مالک است."
        )
        return

    await log_activity(
        user_id=user_id,
        actor_id=message.from_user.id,
        event="admin_role_set",
        detail=f"نقش {role}",
        level="warning",
    )
    text, kb = await _roles_screen()
    await message.answer(
        f"✅ نقش <b>{roles.ROLE_LABELS[role]}</b> به <code>{user_id}</code> داده شد."
    )
    await message.answer(text, reply_markup=kb.as_markup())


@router.callback_query(F.data.startswith("sys:rdel:"))
async def cb_role_del(call: CallbackQuery) -> None:
    if await guard(call, roles.CAP_SYSTEM):
        return
    user_id = parse_int(call.data.split(":")[-1])
    if user_id is None:
        await call.answer("شناسه نامعتبر", show_alert=True)
        return
    removed = await roles.remove(user_id)
    await call.answer("دسترسی گرفته شد" if removed else "چیزی برای حذف نبود")
    if removed:
        await log_activity(
            user_id=user_id,
            actor_id=call.from_user.id,
            event="admin_role_removed",
            level="warning",
        )
    text, kb = await _roles_screen()
    await call.message.edit_text(text, reply_markup=kb.as_markup())


# ---------------------------------------------------------- حالت تعمیر
async def _maint_screen() -> tuple[str, InlineKeyboardBuilder]:
    on, note = await maintenance.mode()
    text = (
        "🛠 <b>حالت تعمیر</b>\n\n"
        f"وضعیت: <b>{'🔴 روشن' if on else '🟢 خاموش'}</b>\n\n"
        "وقتی روشن باشد، کاربران عادی به‌جای خطای مبهم این پیام را می‌بینند "
        "و ادمین‌ها بدون محدودیت کار می‌کنند:\n\n"
        f"<i>{note}</i>\n\n"
        "کارهای کپی در پس‌زمینه ادامه دارند؛ فقط کار با ربات متوقف می‌شود."
    )
    kb = InlineKeyboardBuilder()
    kb.row(
        InlineKeyboardButton(
            text="🟢 خاموش کن" if on else "🔴 روشن کن",
            callback_data="sys:maint:off" if on else "sys:maint:on",
        )
    )
    kb.row(InlineKeyboardButton(text="✍️ تغییر متن پیام", callback_data="sys:maint:note"))
    kb.row(InlineKeyboardButton(text="🔙 بازگشت", callback_data="adm:sys"))
    return text, kb


@router.callback_query(F.data == "sys:maint")
async def cb_maint(call: CallbackQuery) -> None:
    if await guard(call, roles.CAP_SYSTEM):
        return
    text, kb = await _maint_screen()
    await _show(call, text, kb)


@router.callback_query(F.data.in_({"sys:maint:on", "sys:maint:off"}))
async def cb_maint_toggle(call: CallbackQuery) -> None:
    if await guard(call, roles.CAP_SYSTEM):
        return
    turning_on = call.data.endswith(":on")
    _on, note = await maintenance.mode()
    await maintenance.set_mode(turning_on, note=note, by=call.from_user.id)
    await log_activity(
        actor_id=call.from_user.id,
        event="maintenance_mode",
        detail="روشن" if turning_on else "خاموش",
        level="warning",
    )
    await call.answer("حالت تعمیر روشن شد" if turning_on else "ربات دوباره باز است")
    text, kb = await _maint_screen()
    await call.message.edit_text(text, reply_markup=kb.as_markup())


@router.callback_query(F.data == "sys:maint:note")
async def cb_maint_note(call: CallbackQuery, state: FSMContext) -> None:
    if await guard(call, roles.CAP_SYSTEM):
        return
    await call.answer()
    await state.set_state(Flow.admin_maint_note)
    kb = InlineKeyboardBuilder()
    kb.row(InlineKeyboardButton(text="🔙 انصراف", callback_data="sys:maint"))
    await call.message.edit_text(
        "✍️ متنی که کاربران در حالت تعمیر می‌بینند را بفرستید.\n\n"
        "برای برگشت به متن پیش‌فرض، یک خط تیره <code>-</code> بفرستید.",
        reply_markup=kb.as_markup(),
    )


@router.message(Flow.admin_maint_note)
async def do_maint_note(message: Message, state: FSMContext) -> None:
    if not await roles.can(message.from_user.id, roles.CAP_SYSTEM):
        await state.clear()
        return
    raw = (message.text or "").strip()
    await state.clear()
    on, _note = await maintenance.mode()
    await maintenance.set_mode(
        on,
        note="" if raw in {"", "-"} else raw,
        by=message.from_user.id,
    )
    text, kb = await _maint_screen()
    await message.answer("✅ متن ذخیره شد.")
    await message.answer(text, reply_markup=kb.as_markup())
