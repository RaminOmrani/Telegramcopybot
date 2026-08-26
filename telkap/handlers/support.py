"""پشتیبانی داخل ربات — کاربر پیام می‌دهد، مدیریت جواب می‌دهد.

کاربر هیچ‌وقت آیدی ادمین را نمی‌بیند و ادمین لازم نیست به کاربر پیام
مستقیم بدهد؛ همه‌چیز از خود ربات رد می‌شود.
"""
from __future__ import annotations

import logging

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder

from telkap.config import get_settings
from telkap.handlers.common import Flow, get_or_create_user
from telkap.keyboards import BTN_SUPPORT, main_menu
from telkap.models import SupportTicket
from telkap.services import roles, support
from telkap.texts import fa_num

log = logging.getLogger(__name__)
router = Router(name="support")

RULE = "━━━━━━━━━━━━━━━━━━"




# ============================================================ سمت کاربر
def _user_menu(has_open: bool) -> InlineKeyboardBuilder:
    kb = InlineKeyboardBuilder()
    label = "✍️ ادامه‌ی گفتگو" if has_open else "✍️ ارسال پیام به پشتیبانی"
    kb.row(InlineKeyboardButton(text=label, callback_data="sup:new"))
    if has_open:
        kb.row(
            InlineKeyboardButton(text="📜 دیدن گفتگو", callback_data="sup:view"),
            InlineKeyboardButton(text="✅ مشکلم حل شد", callback_data="sup:done"),
        )
    return kb


async def _user_text(user_id: int) -> tuple[str, bool]:
    ticket = await support.open_ticket(user_id)
    lines = ["🛟 <b>پشتیبانی</b>", RULE, ""]
    if ticket is None:
        lines += [
            "سؤال یا مشکلی دارید؟ همین‌جا بنویسید — تیم پشتیبانی می‌بیند و "
            "پاسخ را در همین ربات دریافت می‌کنید.",
            "",
            "<i>برای پاسخ سریع‌تر، مشکل را دقیق بنویسید: کدام بخش، چه کاری "
            "کردید، و چه پیامی دیدید.</i>",
        ]
    elif ticket.awaiting_reply:
        lines += [
            f"⏳ پیام شما ثبت شده (شماره‌ی پیگیری <code>{ticket.id}</code>) و "
            "در انتظار پاسخ است.",
            "",
            "پاسخ همین‌جا برایتان می‌آید. اگر توضیح بیشتری دارید، می‌توانید "
            "پیام دیگری هم بفرستید.",
        ]
    else:
        lines += [
            f"✅ به پیام شما پاسخ داده شده است (پیگیری "
            f"<code>{ticket.id}</code>).",
            "",
            "اگر هنوز سؤالی دارید، ادامه‌ی گفتگو را بزنید.",
        ]
    return "\n".join(lines), ticket is not None


@router.message(Command("support"))
@router.message(F.text == BTN_SUPPORT)
async def cmd_support(message: Message) -> None:
    await get_or_create_user(message.from_user)
    text, has_open = await _user_text(message.from_user.id)
    await message.answer(text, reply_markup=_user_menu(has_open).as_markup())


@router.callback_query(F.data == "sup:new")
async def cb_new(call: CallbackQuery, state: FSMContext) -> None:
    await call.answer()
    await state.set_state(Flow.support_message)
    await call.message.answer(
        "✍️ پیامتان را بنویسید و بفرستید.\n\n"
        "<i>می‌توانید چند خط بنویسید. برای انصراف /cancel را بزنید.</i>"
    )


@router.callback_query(F.data == "sup:view")
async def cb_view(call: CallbackQuery) -> None:
    ticket = await support.open_ticket(call.from_user.id)
    if ticket is None:
        await call.answer("گفتگوی بازی ندارید.", show_alert=True)
        return
    await call.answer()
    messages = await support.history(ticket.id)
    lines = [f"📜 <b>گفتگوی پشتیبانی</b> — پیگیری <code>{ticket.id}</code>", RULE, ""]
    for item in messages:
        who = "🛟 پشتیبانی" if item.from_admin else "👤 شما"
        stamp = fa_num(f"{item.created_at:%m/%d %H:%M}")
        lines.append(f"<b>{who}</b> <i>{stamp}</i>\n{item.text}\n")
    await call.message.answer("\n".join(lines))


@router.callback_query(F.data == "sup:done")
async def cb_done(call: CallbackQuery) -> None:
    ticket = await support.open_ticket(call.from_user.id)
    if ticket is None:
        await call.answer("گفتگوی بازی ندارید.", show_alert=True)
        return
    await support.close(ticket.id)
    await call.answer("گفتگو بسته شد. ممنون!")
    await call.message.answer(
        "✅ گفتگو بسته شد. هر وقت باز سؤالی داشتید، از «🛟 پشتیبانی» پیام بدهید.",
        reply_markup=main_menu(),
    )


@router.message(Flow.support_message)
async def got_message(message: Message, state: FSMContext) -> None:
    body = (message.text or message.caption or "").strip()
    if not body:
        await message.answer("لطفاً پیامتان را به‌صورت متن بنویسید.")
        return
    await state.clear()
    await get_or_create_user(message.from_user)
    ticket, created = await support.add_user_message(message.from_user.id, body)

    await message.answer(
        "✅ پیام شما برای پشتیبانی ارسال شد.\n"
        f"شماره‌ی پیگیری: <code>{ticket.id}</code>\n\n"
        "پاسخ را در همین ربات دریافت می‌کنید.",
        reply_markup=main_menu(),
    )
    await _notify_admins(message, ticket, body, created)


async def _notify_admins(
    message: Message, ticket: SupportTicket, body: str, created: bool
) -> None:
    user = message.from_user
    username = f"@{user.username}" if user.username else "—"
    kb = InlineKeyboardBuilder()
    kb.row(
        InlineKeyboardButton(text="✍️ پاسخ", callback_data=f"supa:reply:{ticket.id}"),
        InlineKeyboardButton(text="📜 گفتگو", callback_data=f"supa:view:{ticket.id}"),
    )
    kb.row(InlineKeyboardButton(text="✅ بستن", callback_data=f"supa:close:{ticket.id}"))
    head = "🛟 <b>پیام تازه‌ی پشتیبانی</b>" if created else "🛟 <b>پیام جدید در گفتگو</b>"
    text = (
        f"{head}\n"
        f"پیگیری: <code>{ticket.id}</code>\n"
        f"کاربر: {user.full_name} ({username})\n"
        f"شناسه: <code>{user.id}</code>\n"
        f"{RULE}\n\n{body}"
    )
    for admin_id in get_settings().admin_ids:
        try:
            await message.bot.send_message(admin_id, text, reply_markup=kb.as_markup())
        except Exception:
            log.warning("ارسال تیکت به ادمین %s ناموفق بود", admin_id, exc_info=True)


# ============================================================ سمت ادمین
def _ticket_keyboard(ticket: SupportTicket) -> InlineKeyboardBuilder:
    kb = InlineKeyboardBuilder()
    kb.row(
        InlineKeyboardButton(text="✍️ پاسخ", callback_data=f"supa:reply:{ticket.id}"),
        InlineKeyboardButton(text="📜 گفتگو", callback_data=f"supa:view:{ticket.id}"),
    )
    if ticket.status == SupportTicket.STATUS_OPEN:
        kb.row(
            InlineKeyboardButton(text="✅ بستن", callback_data=f"supa:close:{ticket.id}")
        )
    else:
        kb.row(
            InlineKeyboardButton(
                text="♻️ باز کردن دوباره", callback_data=f"supa:open:{ticket.id}"
            )
        )
    kb.row(InlineKeyboardButton(text="🔙 فهرست تیکت‌ها", callback_data="adm:tickets"))
    return kb


@router.callback_query(F.data == "adm:tickets")
async def cb_list(call: CallbackQuery) -> None:
    if not await roles.can(call.from_user.id, roles.CAP_TICKETS):
        await call.answer("دسترسی ندارید", show_alert=True)
        return
    await call.answer()
    tickets = await support.open_tickets()
    kb = InlineKeyboardBuilder()
    for ticket in tickets:
        mark = "🔴" if ticket.awaiting_reply else "🟡"
        subject = (ticket.subject or "—")[:32]
        kb.row(
            InlineKeyboardButton(
                text=f"{mark} #{ticket.id} — {subject}",
                callback_data=f"supa:view:{ticket.id}",
            )
        )
    kb.row(InlineKeyboardButton(text="🔙 بازگشت", callback_data="adm:home"))

    waiting = sum(1 for t in tickets if t.awaiting_reply)
    body = (
        f"🛟 <b>تیکت‌های پشتیبانی</b>\n{RULE}\n\n"
        f"باز: {fa_num(len(tickets))} | منتظر پاسخ: {fa_num(waiting)}\n\n"
        "🔴 منتظر پاسخ شماست | 🟡 پاسخ داده شده"
        if tickets
        else f"🛟 <b>تیکت‌های پشتیبانی</b>\n{RULE}\n\nتیکت بازی وجود ندارد."
    )
    try:
        await call.message.edit_text(body, reply_markup=kb.as_markup())
    except Exception:
        await call.message.answer(body, reply_markup=kb.as_markup())


@router.callback_query(F.data.startswith("supa:view:"))
async def cb_admin_view(call: CallbackQuery) -> None:
    if not await roles.can(call.from_user.id, roles.CAP_TICKETS):
        await call.answer("دسترسی ندارید", show_alert=True)
        return
    ticket_id = int(call.data.split(":")[2])
    ticket = await support.get(ticket_id)
    if ticket is None:
        await call.answer("این تیکت پیدا نشد.", show_alert=True)
        return
    await call.answer()
    messages = await support.history(ticket_id)
    status = "🔴 منتظر پاسخ" if ticket.awaiting_reply else "🟡 پاسخ داده شده"
    if ticket.status == SupportTicket.STATUS_CLOSED:
        status = "⚫️ بسته"
    lines = [
        f"🛟 <b>تیکت #{ticket.id}</b> — {status}",
        f"کاربر: <code>{ticket.user_id}</code>",
        RULE,
        "",
    ]
    for item in messages:
        who = "🛟 پشتیبانی" if item.from_admin else "👤 کاربر"
        stamp = fa_num(f"{item.created_at:%m/%d %H:%M}")
        lines.append(f"<b>{who}</b> <i>{stamp}</i>\n{item.text}\n")
    body = "\n".join(lines)
    if len(body) > 3900:
        body = body[:3890] + "\n…"
    markup = _ticket_keyboard(ticket).as_markup()
    try:
        await call.message.edit_text(body, reply_markup=markup)
    except Exception:
        await call.message.answer(body, reply_markup=markup)


@router.callback_query(F.data.startswith("supa:reply:"))
async def cb_admin_reply(call: CallbackQuery, state: FSMContext) -> None:
    if not await roles.can(call.from_user.id, roles.CAP_TICKETS):
        await call.answer("دسترسی ندارید", show_alert=True)
        return
    ticket_id = int(call.data.split(":")[2])
    await call.answer()
    await state.set_state(Flow.support_reply)
    await state.update_data(ticket_id=ticket_id)
    await call.message.answer(
        f"✍️ پاسخ خود به تیکت <code>{ticket_id}</code> را بنویسید.\n\n"
        "<i>کاربر آن را با عنوان «پشتیبانی» می‌بیند و آیدی شما را نمی‌بیند.</i>\n\n"
        "انصراف: /cancel"
    )


@router.message(Flow.support_reply)
async def got_reply(message: Message, state: FSMContext) -> None:
    if not await roles.can(message.from_user.id, roles.CAP_TICKETS):
        await state.clear()
        return
    body = (message.text or "").strip()
    if not body:
        await message.answer("پاسخ باید متن باشد.")
        return
    data = await state.get_data()
    ticket_id = int(data.get("ticket_id", 0))
    await state.clear()

    ticket = await support.add_admin_reply(ticket_id, message.from_user.id, body)
    if ticket is None:
        await message.answer("این تیکت پیدا نشد.")
        return

    kb = InlineKeyboardBuilder()
    kb.row(InlineKeyboardButton(text="✍️ پاسخ به پشتیبانی", callback_data="sup:new"))
    try:
        await message.bot.send_message(
            ticket.user_id,
            f"🛟 <b>پاسخ پشتیبانی</b>\n"
            f"پیگیری: <code>{ticket.id}</code>\n"
            f"{RULE}\n\n{body}",
            reply_markup=kb.as_markup(),
        )
    except Exception as exc:
        await message.answer(f"⚠️ پاسخ ثبت شد ولی به کاربر نرسید: {exc}")
        return
    await message.answer(f"✅ پاسخ به کاربر <code>{ticket.user_id}</code> ارسال شد.")


@router.callback_query(F.data.startswith("supa:close:"))
@router.callback_query(F.data.startswith("supa:open:"))
async def cb_admin_toggle(call: CallbackQuery) -> None:
    if not await roles.can(call.from_user.id, roles.CAP_TICKETS):
        await call.answer("دسترسی ندارید", show_alert=True)
        return
    _, action, raw = call.data.split(":")
    ticket_id = int(raw)
    ticket = (
        await support.close(ticket_id)
        if action == "close"
        else await support.reopen(ticket_id)
    )
    if ticket is None:
        await call.answer("این تیکت پیدا نشد.", show_alert=True)
        return
    await call.answer("بسته شد" if action == "close" else "باز شد")
    if action == "close":
        try:
            await call.bot.send_message(
                ticket.user_id,
                "✅ گفتگوی پشتیبانی شما بسته شد.\n"
                "اگر باز هم سؤالی داشتید، از «🛟 پشتیبانی» پیام بدهید.",
            )
        except Exception:
            log.debug("اطلاع بسته شدن تیکت به کاربر نرسید", exc_info=True)
    await cb_admin_view(call)
