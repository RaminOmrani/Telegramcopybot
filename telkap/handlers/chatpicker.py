"""انتخاب کانال/گروه از فهرست چت‌های خود کاربر.

برای کانال‌های خصوصی، پیدا کردن آیدی عددی یا لینک دعوت دردسر دارد.
اینجا فهرست چت‌های اکانت کاربر نشان داده می‌شود و او فقط انتخاب می‌کند —
آیدی عددی خودکار برداشته می‌شود که برای کانال خصوصی پایدارترین شناسه است.
"""
from __future__ import annotations

import logging

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder

from telkap.services.userbot import manager
from telkap.texts import NO_LOGIN, fa_num

log = logging.getLogger(__name__)
router = Router(name="chatpicker")

PAGE_SIZE = 8
MAX_DIALOGS = 200

# فهرست چت‌ها بین باز کردن صفحه‌ها کش می‌شود تا هر بار از تلگرام خوانده نشود
_cache: dict[int, list[dict]] = {}


def picker_button(kind: str) -> InlineKeyboardBuilder:
    """دکمه‌ی «انتخاب از لیست» برای مرحله‌ی مبدا یا مقصد."""
    kb = InlineKeyboardBuilder()
    kb.row(
        InlineKeyboardButton(
            text="📋 انتخاب از لیست چت‌های من", callback_data=f"pick:{kind}:0"
        )
    )
    return kb


async def _load_chats(user_id: int, *, writable_only: bool) -> list[dict] | None:
    """کانال‌ها و گروه‌های اکانت کاربر را می‌خواند."""
    client = await manager.ensure_client(user_id)
    if client is None:
        return None

    chats: list[dict] = []
    try:
        async for dialog in client.iter_dialogs(limit=MAX_DIALOGS):
            entity = dialog.entity
            # فقط کانال و گروه؛ چت خصوصی با افراد به درد نمی‌خورد
            if not (dialog.is_channel or dialog.is_group):
                continue

            # برای مقصد، جایی که اجازه‌ی ارسال نداریم را نشان ندهیم
            if writable_only:
                if getattr(entity, "broadcast", False) and not (
                    getattr(entity, "creator", False)
                    or getattr(entity, "admin_rights", None)
                ):
                    continue
                if getattr(entity, "left", False):
                    continue

            chats.append(
                {
                    "id": dialog.id,
                    "title": dialog.name or str(dialog.id),
                    "private": not getattr(entity, "username", None),
                    "channel": bool(getattr(entity, "broadcast", False)),
                }
            )
    except Exception:
        log.exception("خواندن فهرست چت‌های کاربر %s ناموفق بود", user_id)
        return None
    return chats


def _page_markup(kind: str, chats: list[dict], page: int) -> InlineKeyboardBuilder:
    start = page * PAGE_SIZE
    kb = InlineKeyboardBuilder()

    for index, chat in enumerate(chats[start : start + PAGE_SIZE], start=start):
        lock = "🔒" if chat["private"] else "🌐"
        icon = "📢" if chat["channel"] else "👥"
        kb.row(
            InlineKeyboardButton(
                text=f"{lock}{icon} {chat['title'][:38]}",
                callback_data=f"picked:{kind}:{index}",
            )
        )

    nav: list[InlineKeyboardButton] = []
    if page > 0:
        nav.append(InlineKeyboardButton(text="⬅️ قبلی", callback_data=f"pick:{kind}:{page - 1}"))
    total_pages = max(1, (len(chats) + PAGE_SIZE - 1) // PAGE_SIZE)
    nav.append(
        InlineKeyboardButton(
            text=f"{fa_num(page + 1)}/{fa_num(total_pages)}", callback_data="noop"
        )
    )
    if start + PAGE_SIZE < len(chats):
        nav.append(InlineKeyboardButton(text="بعدی ➡️", callback_data=f"pick:{kind}:{page + 1}"))
    if nav:
        kb.row(*nav)
    return kb


@router.callback_query(F.data.startswith("pick:"))
async def cb_page(call: CallbackQuery) -> None:
    _, kind, raw_page = call.data.split(":")
    page = int(raw_page)

    if page == 0 or call.from_user.id not in _cache:
        await call.answer("در حال خواندن فهرست چت‌ها…")
        chats = await _load_chats(call.from_user.id, writable_only=(kind == "dest"))
        if chats is None:
            await call.message.answer(NO_LOGIN)
            return
        _cache[call.from_user.id] = chats
    else:
        await call.answer()

    chats = _cache[call.from_user.id]
    if not chats:
        await call.message.answer(
            "چت مناسبی پیدا نشد.\n"
            "اگر کانال خصوصی است، مطمئن شوید با <b>همان شماره‌ای که در ربات وارد شدید</b> "
            "عضو آن هستید."
        )
        return

    label = "مبدا" if kind == "source" else "مقصد"
    text = (
        f"📋 <b>انتخاب {label}</b>\n\n"
        f"{fa_num(len(chats))} چت پیدا شد. یکی را انتخاب کنید:\n\n"
        "🔒 خصوصی | 🌐 عمومی | 📢 کانال | 👥 گروه"
    )
    markup = _page_markup(kind, chats, page).as_markup()
    try:
        await call.message.edit_text(text, reply_markup=markup)
    except Exception:
        await call.message.answer(text, reply_markup=markup)


@router.callback_query(F.data.startswith("picked:"))
async def cb_picked(call: CallbackQuery, state: FSMContext) -> None:
    _, kind, raw_index = call.data.split(":")
    chats = _cache.get(call.from_user.id) or []
    index = int(raw_index)
    if index >= len(chats):
        await call.answer("فهرست منقضی شده است. دوباره باز کنید.", show_alert=True)
        return

    chat = chats[index]
    await call.answer()

    # همان مسیری که ورود دستی طی می‌کند، تا اعتبارسنجی و پلن یکسان بماند
    from telkap.handlers.tasks import accept_dest, accept_source

    if kind == "source":
        await accept_source(call.message, state, call.from_user.id, str(chat["id"]))
    else:
        await accept_dest(call.message, state, call.from_user.id, str(chat["id"]))


def forget(user_id: int) -> None:
    _cache.pop(user_id, None)
