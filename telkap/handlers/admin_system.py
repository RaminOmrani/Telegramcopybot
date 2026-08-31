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
from telkap.plans import toman
from telkap.services import ai, backup, crypto, maintenance, roles

log = logging.getLogger(__name__)
router = Router(name="admin-system")


def _menu_kb() -> InlineKeyboardBuilder:
    kb = InlineKeyboardBuilder()
    kb.row(
        InlineKeyboardButton(text="👮 نقش ادمین‌ها", callback_data="sys:roles"),
        InlineKeyboardButton(text="🛠 حالت تعمیر", callback_data="sys:maint"),
    )
    kb.row(InlineKeyboardButton(text="🆔 گرفتن شناسه‌ی کانال", callback_data="sys:chatid"))
    if get_settings().web_enabled:
        kb.row(InlineKeyboardButton(text="🖥 پنل وب", callback_data="sys:web"))
    kb.row(InlineKeyboardButton(text="🤖 هوش مصنوعی", callback_data="sys:ai"))
    kb.row(InlineKeyboardButton(text="₮ پرداخت تتر", callback_data="sys:usdt"))
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
    channel = await backup.chat_id()
    await _show(
        call,
        "⚙️ <b>سیستم</b>\n\n"
        f"حالت تعمیر: <b>{'🔴 روشن' if on else '🟢 خاموش'}</b>\n"
        + (
            f"کانال پشتیبان: <code>{channel}</code>"
            if channel
            else "کانال پشتیبان: <b>⚠️ تنظیم نشده</b> — نسخه‌ها فقط روی همین "
            "سرورند. «🆔 گرفتن شناسه‌ی کانال» را بزنید."
        ),
        _menu_kb(),
    )


@router.callback_query(F.data == "sys:ai")
async def cb_ai_status(call: CallbackQuery) -> None:
    """هر چهار مدل را واقعاً صدا می‌زند و می‌گوید کدام جواب داد.

    نام مدل‌ها باید مو‌به‌مو با فهرست سرویس بخوانند. به‌جای اینکه کسی
    حدس بزند و بعد در لاگ دنبال ۴۰۴ بگردد، همین‌جا معلوم می‌شود.
    """
    if await guard(call, roles.CAP_SYSTEM):
        return
    await call.answer("در حال تست…")
    _ok, report = await ai.health()
    kb = InlineKeyboardBuilder()
    kb.row(InlineKeyboardButton(text="🔄 تست دوباره", callback_data="sys:ai"))
    kb.row(InlineKeyboardButton(text="🔙 بازگشت", callback_data="adm:sys"))
    try:
        await call.message.edit_text(report, reply_markup=kb.as_markup())
    except Exception:
        await call.message.answer(report, reply_markup=kb.as_markup())


# ─────────────────────────────────────────────────── پرداخت تتر
async def _usdt_screen(event) -> None:
    wallet = await crypto.address()
    rate = await crypto.rate()
    ready = bool(wallet) and rate > 0

    if ready:
        head = "₮ <b>پرداخت تتر</b> — فعال ✅\n\n"
    else:
        head = (
            "₮ <b>پرداخت تتر</b> — غیرفعال\n\n"
            "<i>تا هر دو مقدار پر نشوند، این راه پرداخت به کاربران "
            "نشان داده نمی‌شود.</i>\n\n"
        )

    body = (
        f"نشانی ولت (TRC20):\n<code>{wallet or 'تنظیم نشده'}</code>\n\n"
        f"نرخ هر تتر: <b>{toman(rate) if rate else 'تنظیم نشده'}</b>\n\n"
        "<i>نرخ را روزانه به‌روز کنید. مبلغ هر خرید در لحظه‌ی ساخت "
        "درخواست با همین نرخ قفل می‌شود، پس تغییر نرخ روی پرداخت‌های "
        "در جریان اثر نمی‌گذارد.</i>"
    )

    kb = InlineKeyboardBuilder()
    kb.row(InlineKeyboardButton(text="🏦 تغییر نشانی ولت", callback_data="sys:usdtaddr"))
    kb.row(InlineKeyboardButton(text="💱 تغییر نرخ", callback_data="sys:usdtrate"))
    kb.row(InlineKeyboardButton(text="🔙 بازگشت", callback_data="adm:sys"))
    await _show(event, head + body, kb)


@router.callback_query(F.data == "sys:usdt")
async def cb_usdt(call: CallbackQuery) -> None:
    if await guard(call, roles.CAP_MONEY):
        return
    await _usdt_screen(call)


@router.callback_query(F.data == "sys:usdtaddr")
async def cb_usdt_address(call: CallbackQuery, state: FSMContext) -> None:
    if await guard(call, roles.CAP_MONEY):
        return
    await call.answer()
    await state.set_state(Flow.usdt_address)
    await call.message.answer(
        "نشانی ولت <b>TRC20</b> خود را بفرستید.\n\n"
        "<i>با T شروع می‌شود و ۳۴ نویسه است. اشتباه بودنش یعنی پولِ "
        "مشتری به جای دیگری می‌رود، پس دوبار چک کنید.</i>\n\n"
        "برای انصراف /cancel را بزنید."
    )


@router.message(Flow.usdt_address)
async def got_usdt_address(message: Message, state: FSMContext) -> None:
    saved = await crypto.set_address(message.text or "", admin_id=message.from_user.id)
    if saved is None:
        await message.answer(
            "⚠️ این نشانی معتبر نیست. نشانی ترون با T شروع می‌شود و ۳۴ "
            "نویسه دارد.\nبرای انصراف /cancel را بزنید."
        )
        return
    await state.clear()
    await log_activity(
        user_id=message.from_user.id, event="admin", detail="نشانی ولت تتر عوض شد"
    )
    await message.answer("✅ نشانی ولت ثبت شد.")
    await _usdt_screen(message)


@router.callback_query(F.data == "sys:usdtrate")
async def cb_usdt_rate(call: CallbackQuery, state: FSMContext) -> None:
    if await guard(call, roles.CAP_MONEY):
        return
    await call.answer()
    await state.set_state(Flow.usdt_rate)
    await call.message.answer(
        "نرخ هر تتر را به <b>تومان</b> بفرستید.\n\n"
        "<i>مثلاً اگر هر تتر ۹۵٬۰۰۰ تومان است، بنویسید 95000</i>\n\n"
        "برای انصراف /cancel را بزنید."
    )


@router.message(Flow.usdt_rate)
async def got_usdt_rate(message: Message, state: FSMContext) -> None:
    saved = await crypto.set_rate(message.text or "", admin_id=message.from_user.id)
    if saved is None:
        await message.answer(
            "⚠️ عدد معتبر نیست.\nنرخ را به تومان و بدون جداکننده بنویسید، "
            "مثل 95000.\nبرای انصراف /cancel را بزنید."
        )
        return
    await state.clear()
    await log_activity(
        user_id=message.from_user.id, event="admin", detail=f"نرخ تتر → {saved}"
    )
    await message.answer(f"✅ نرخ ثبت شد: {toman(saved)} برای هر تتر.")
    await _usdt_screen(message)


@router.callback_query(F.data == "sys:web")
async def cb_web_panel(call: CallbackQuery) -> None:
    """لینک یک‌بارمصرف ورود به پنل وب.

    رمز عبوری در کار نیست: هویت ادمین را همین چت تلگرام تأیید کرده. لینک
    پنج دقیقه اعتبار دارد و با اولین باز شدن می‌سوزد.
    """
    if await guard(call, roles.CAP_SYSTEM):
        return
    cfg = get_settings()
    if not cfg.web_enabled:
        await call.answer("پنل وب روشن نیست.", show_alert=True)
        return
    if not cfg.web_base_url:
        await call.answer()
        await call.message.answer(
            "🖥 <b>پنل وب</b>\n\n"
            "پنل بالا آمده ولی <code>WEB_BASE_URL</code> در فایل <code>.env</code> "
            "خالی است، پس نمی‌دانم چه آدرسی به شما بدهم.\n\n"
            "آدرس عمومی پنل را آنجا بگذارید و ربات را ری‌استارت کنید، مثل:\n"
            "<code>WEB_BASE_URL=https://botpanel.softmiliac.com</code>"
        )
        return

    from telkap.web import auth as web_auth

    token = web_auth.issue_login_token(call.from_user.id)
    await call.answer()
    await call.message.answer(
        "🖥 <b>ورود به پنل وب</b>\n\n"
        f"{cfg.web_base_url}/enter?t={token}\n\n"
        "⏳ این لینک <b>۵ دقیقه</b> اعتبار دارد و با اولین باز شدن می‌سوزد.\n"
        "🔒 آن را برای کسی نفرستید — هرکس بازش کند با نام شما وارد می‌شود.",
        disable_web_page_preview=True,
    )
    await log_activity(
        user_id=call.from_user.id,
        event="web_login_link",
        detail="لینک ورود به پنل وب ساخته شد",
    )


@router.callback_query(F.data == "sys:chatid")
async def cb_chatid(call: CallbackQuery, state: FSMContext) -> None:
    if await guard(call, roles.CAP_SYSTEM):
        return
    await call.answer()
    await state.set_state(Flow.admin_chatid)
    await call.message.answer(
        "🆔 <b>گرفتن شناسه‌ی کانال</b>\n\n"
        "یک پیام از آن کانال را برای من <b>فوروارد</b> کنید تا شناسه‌اش را "
        "بگویم و بتوانید با یک دکمه کانال پشتیبانش کنید.\n\n"
        "<i>اگر کانال فوروارد را بسته، ربات را همان‌جا ادمین کنید، یک پیام "
        "بفرستید و همان را فوروارد کنید.</i>\n\n"
        "انصراف: /cancel"
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
