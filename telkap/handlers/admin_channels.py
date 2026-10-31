"""مدیریت کانال‌های عضویت اجباری — از داخل پنل ادمین، بدون محدودیت تعداد."""
from __future__ import annotations

import logging

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder

from telkap.handlers.common import Flow
from telkap.services import forcejoin, roles
from telkap.texts import fa_num

log = logging.getLogger(__name__)
router = Router(name="admin_channels")


def _is_admin(user_id: int) -> bool:
    # دسترسی این روتر روی خودِ روتر قفل شده؛ این گارد لایه‌ی دوم است
    return roles.can_cached(user_id, roles.CAP_SYSTEM)


def _menu(channels) -> InlineKeyboardBuilder:
    kb = InlineKeyboardBuilder()
    for channel in channels:
        mark = "🟢" if channel.enabled else "🔴"
        kb.row(
            InlineKeyboardButton(
                text=f"{mark} {(channel.title or channel.ref)[:32]}",
                callback_data=f"fj:toggle:{channel.id}",
            ),
            InlineKeyboardButton(text="🗑", callback_data=f"fj:del:{channel.id}"),
        )
    kb.row(InlineKeyboardButton(text="➕ افزودن کانال", callback_data="fj:add"))
    kb.row(InlineKeyboardButton(text="🔙 بازگشت", callback_data="adm:home"))
    return kb


async def _render(target: Message, *, edit: bool = True) -> None:
    channels = await forcejoin.all_channels()
    active = sum(1 for c in channels if c.enabled)
    lines = ["📢 <b>عضویت اجباری</b>", "━━━━━━━━━━━━━━━━━━", ""]
    if channels:
        lines.append(
            f"کاربر تا در <b>همه‌ی</b> {fa_num(active)} کانال فعال عضو نشود، "
            "نمی‌تواند از ربات استفاده کند.\n"
        )
        for channel in channels:
            mark = "🟢 فعال" if channel.enabled else "🔴 خاموش"
            lines.append(f"• <code>{channel.ref}</code> — {mark}")
    else:
        lines.append("هیچ کانالی تعیین نشده؛ ربات برای همه باز است.")
    lines.append(
        "\n⚠️ ربات باید در هر کانال <b>ادمین</b> باشد، وگرنه نمی‌تواند عضویت را "
        "بررسی کند و آن کانال نادیده گرفته می‌شود."
    )
    markup = _menu(channels).as_markup()
    if edit:
        try:
            await target.edit_text("\n".join(lines), reply_markup=markup)
            return
        except Exception:
            log.debug("ویرایش فهرست کانال‌ها ناموفق بود", exc_info=True)
    await target.answer("\n".join(lines), reply_markup=markup)


@router.callback_query(F.data == "adm:join")
async def cb_open(call: CallbackQuery) -> None:
    if not _is_admin(call.from_user.id):
        await call.answer("دسترسی ندارید", show_alert=True)
        return
    await call.answer()
    await _render(call.message)


@router.callback_query(F.data == "fj:add")
async def cb_add(call: CallbackQuery, state: FSMContext) -> None:
    if not _is_admin(call.from_user.id):
        await call.answer("دسترسی ندارید", show_alert=True)
        return
    await call.answer()
    await state.set_state(Flow.admin_join_add)
    await call.message.answer(
        "📢 آیدی کانال را بفرستید.\n\n"
        "مثال‌ها:\n"
        "<code>@mychannel</code>\n"
        "<code>https://t.me/mychannel</code>\n"
        "<code>-1001234567890</code> (کانال خصوصی)\n\n"
        "⚠️ ربات باید در آن کانال ادمین باشد.\n\nانصراف: /cancel"
    )


@router.message(Flow.admin_join_add)
async def got_channel(message: Message, state: FSMContext) -> None:
    if not _is_admin(message.from_user.id):
        await state.clear()
        return
    ref = forcejoin.normalize(message.text or "")
    if not ref:
        await message.answer("آیدی نامعتبر است. دوباره بفرستید یا /cancel بزنید.")
        return
    await state.clear()

    # اگر ربات آنجا ادمین باشد، عنوان و لینک دعوت را خودمان می‌گیریم
    title, invite = ref, ""
    chat_ref = ref if ref.lstrip("-").isdigit() else f"@{ref}"
    warning = ""
    try:
        chat = await message.bot.get_chat(chat_ref)
        title = chat.title or ref
        invite = chat.invite_link or ""
        me = await message.bot.get_me()
        member = await message.bot.get_chat_member(chat_ref, me.id)
        if member.status not in {"administrator", "creator"}:
            warning = "\n\n⚠️ ربات در این کانال ادمین نیست؛ تا ادمین نشود عضویت بررسی نمی‌شود."
    except Exception as exc:
        warning = (
            f"\n\n⚠️ ربات به این کانال دسترسی ندارد ({exc}).\n"
            "کانال ثبت شد، ولی تا ادمین نشدن ربات نادیده گرفته می‌شود."
        )

    channel = await forcejoin.add(
        ref, title, invite_link=invite, admin_id=message.from_user.id
    )
    if channel is None:
        await message.answer("افزودن کانال ناموفق بود.")
        return
    await message.answer(f"✅ کانال <b>{title}</b> اضافه شد.{warning}")
    await _render(message, edit=False)


@router.callback_query(F.data.startswith("fj:toggle:"))
async def cb_toggle(call: CallbackQuery) -> None:
    if not _is_admin(call.from_user.id):
        await call.answer("دسترسی ندارید", show_alert=True)
        return
    state = await forcejoin.toggle(int(call.data.split(":")[2]))
    if state is None:
        await call.answer("این کانال پیدا نشد.", show_alert=True)
        return
    await call.answer("فعال شد" if state else "خاموش شد")
    await _render(call.message)


@router.callback_query(F.data.startswith("fj:del:"))
async def cb_delete(call: CallbackQuery) -> None:
    if not _is_admin(call.from_user.id):
        await call.answer("دسترسی ندارید", show_alert=True)
        return
    await forcejoin.remove(int(call.data.split(":")[2]))
    await call.answer("حذف شد")
    await _render(call.message)
