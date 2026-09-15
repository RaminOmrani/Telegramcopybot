"""پنل مدیریت — کاملاً دکمه‌ای، داخل همان ربات."""
from __future__ import annotations

import asyncio
import logging

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder
from sqlalchemy import func, select

from telkap.db import get_session, log_activity
from telkap.handlers.common import Flow, parse_int
from telkap.models import DailyStat, PaymentRequest, RetryItem, Subscription, Task, User, utcnow
from telkap.plans import PLANS, toman
from telkap.services import audience, backup, health, payments, roles, support
from telkap.services.copier import today_key
from telkap.services.subscription import grant
from telkap.services.userbot import manager
from telkap.texts import fa_num

log = logging.getLogger(__name__)
router = Router(name="admin")


def admin_menu(
    pending: int = 0, tickets: int = 0, caps: frozenset[str] | None = None
) -> InlineKeyboardBuilder:
    """منوی پنل — فقط بخش‌هایی که این ادمین اجازه‌شان را دارد.

    نشان دادن دکمه‌ای که با زدنش «دسترسی ندارید» می‌گیرد، هم گیج‌کننده
    است و هم ساختار داخلی را لو می‌دهد.
    """
    caps = roles.ALL_CAPS if caps is None else caps
    kb = InlineKeyboardBuilder()
    badge = f" ({fa_num(pending)})" if pending else ""
    ticket_badge = f" ({fa_num(tickets)})" if tickets else ""

    if roles.CAP_USERS in caps:
        kb.row(InlineKeyboardButton(text="👥 مدیریت کاربران", callback_data="adm:users"))
    if roles.CAP_TICKETS in caps:
        kb.row(
            InlineKeyboardButton(
                text=f"🛟 تیکت‌های پشتیبانی{ticket_badge}", callback_data="adm:tickets"
            )
        )
    if roles.CAP_MONEY in caps:
        kb.row(
            InlineKeyboardButton(text="🧩 طرح‌ها و قیمت‌ها", callback_data="adm:plans"),
            InlineKeyboardButton(text="🎁 دعوت دوستان", callback_data="adm:ref"),
        )
        kb.row(InlineKeyboardButton(text="🎟 کدهای تخفیف", callback_data="adm:coupons"))
        kb.row(
            InlineKeyboardButton(
                text=f"🧾 رسیدهای در انتظار{badge}", callback_data="adm:pay"
            )
        )
    if roles.CAP_REPORTS in caps:
        kb.row(
            InlineKeyboardButton(text="📊 آمار", callback_data="adm:stats"),
            InlineKeyboardButton(text="📈 گزارش‌ها", callback_data="adm:reports"),
        )
    if roles.CAP_MONEY in caps:
        kb.row(InlineKeyboardButton(text="🎁 فعال‌سازی اشتراک", callback_data="adm:grant"))
    if roles.CAP_USERS in caps:
        kb.row(
            InlineKeyboardButton(text="📢 پیام همگانی", callback_data="adm:cast"),
            InlineKeyboardButton(text="🚫 مسدودسازی", callback_data="adm:ban"),
        )
    if roles.CAP_SYSTEM in caps:
        kb.row(
            InlineKeyboardButton(text="📢 عضویت اجباری", callback_data="adm:join"),
            InlineKeyboardButton(text="🔁 صف تلاش مجدد", callback_data="adm:retry"),
        )
        kb.row(
            InlineKeyboardButton(text="💾 پشتیبان‌گیری", callback_data="adm:backup"),
            InlineKeyboardButton(text="⚙️ سیستم", callback_data="adm:sys"),
        )
    return kb


async def _panel(user_id: int) -> tuple[str, InlineKeyboardBuilder]:
    caps = await roles.caps(user_id)
    pending = await payments.pending_count() if roles.CAP_MONEY in caps else 0
    tickets = await support.waiting_count() if roles.CAP_TICKETS in caps else 0
    head = "🛠 <b>پنل مدیریت</b>"
    role = await roles.role_of(user_id)
    if role and role != roles.ROLE_OWNER:
        head += f"\nنقش شما: {roles.ROLE_LABELS[role]}"
    return f"{head}\n\nیک بخش را انتخاب کنید:", admin_menu(pending, tickets, caps)


@router.message(Command("admin"))
async def cmd_admin(message: Message) -> None:
    if not await roles.is_staff(message.from_user.id):
        return
    text, kb = await _panel(message.from_user.id)
    await message.answer(text, reply_markup=kb.as_markup())


@router.callback_query(F.data == "adm:home")
async def cb_home(call: CallbackQuery) -> None:
    if not await roles.is_staff(call.from_user.id):
        await call.answer("دسترسی ندارید", show_alert=True)
        return
    text, kb = await _panel(call.from_user.id)
    await call.answer()
    await call.message.edit_text(text, reply_markup=kb.as_markup())


def _back() -> InlineKeyboardBuilder:
    kb = InlineKeyboardBuilder()
    kb.row(InlineKeyboardButton(text="🔙 بازگشت", callback_data="adm:home"))
    return kb


# ------------------------------------------------------------------- آمار
@router.callback_query(F.data == "adm:stats")
@router.message(Command("stats"))
async def show_stats(event: CallbackQuery | Message) -> None:
    user_id = event.from_user.id
    if not await roles.can(user_id, roles.CAP_REPORTS):
        return
    day = today_key()
    async with get_session() as db:
        users = await db.scalar(select(func.count(User.id)))
        logged = await db.scalar(select(func.count(User.id)).where(User.session_enc.is_not(None)))
        tasks = await db.scalar(select(func.count(Task.id)))
        active_tasks = await db.scalar(select(func.count(Task.id)).where(Task.enabled.is_(True)))
        copied = await db.scalar(select(func.coalesce(func.sum(Task.copied_count), 0)))
        subs = await db.scalar(
            select(func.count(Subscription.id)).where(Subscription.expires_at > utcnow())
        )
        today_copied = await db.scalar(
            select(func.coalesce(func.sum(DailyStat.copied), 0)).where(DailyStat.day == day)
        )
        today_failed = await db.scalar(
            select(func.coalesce(func.sum(DailyStat.failed), 0)).where(DailyStat.day == day)
        )
        queued = await db.scalar(select(func.count(RetryItem.id)))
        pending_pay = await db.scalar(
            select(func.count(PaymentRequest.id)).where(
                PaymentRequest.status == PaymentRequest.STATUS_PENDING,
                PaymentRequest.receipt_file_id.is_not(None),
            )
        )

    text = (
        "📊 <b>آمار ربات</b>\n\n"
        f"کاربران: {fa_num(users or 0)}\n"
        f"اکانت‌های متصل: {fa_num(logged or 0)}\n"
        f"اشتراک‌های فعال: {fa_num(subs or 0)}\n"
        f"کارهای کپی: {fa_num(tasks or 0)} (فعال: {fa_num(active_tasks or 0)})\n\n"
        f"<b>امروز</b>\n"
        f"کپی‌شده: {fa_num(int(today_copied or 0))}\n"
        f"ناموفق: {fa_num(int(today_failed or 0))}\n\n"
        f"مجموع کپی از ابتدا: {fa_num(int(copied or 0))}\n"
        f"در صف تلاش مجدد: {fa_num(queued or 0)}\n"
        f"رسید در انتظار بررسی: {fa_num(pending_pay or 0)}"
    )

    # سلامت اکانت‌های متصل — فقط وقتی مشکلی هست نمایش داده می‌شود
    states = await health.summary()
    problems = {
        state: count
        for state, count in states.items()
        if state != health.STATE_OK and count
    }
    if problems:
        text += "\n\n<b>⚠️ اکانت‌های نیازمند رسیدگی</b>\n"
        text += "\n".join(
            f"{health.STATE_LABELS.get(state, state)}: {fa_num(count)}"
            for state, count in sorted(problems.items(), key=lambda kv: -kv[1])
        )
    if isinstance(event, CallbackQuery):
        await event.answer()
        await event.message.edit_text(text, reply_markup=_back().as_markup())
    else:
        await event.answer(text)


# ------------------------------------------------------------- رسیدها
@router.callback_query(F.data == "adm:pay")
async def cb_payments(call: CallbackQuery) -> None:
    if not await roles.can(call.from_user.id, roles.CAP_MONEY):
        await call.answer("دسترسی ندارید", show_alert=True)
        return
    await call.answer()
    requests = await payments.pending_requests()
    if not requests:
        await call.message.edit_text(
            "🧾 رسید در انتظار بررسی وجود ندارد.", reply_markup=_back().as_markup()
        )
        return

    kb = InlineKeyboardBuilder()
    for req in requests:
        kb.row(
            InlineKeyboardButton(
                text=f"#{req.id} — {payments.describe(req)}",
                callback_data=f"adm:payshow:{req.id}",
            )
        )
    kb.row(InlineKeyboardButton(text="🔙 بازگشت", callback_data="adm:home"))
    await call.message.edit_text(
        f"🧾 <b>{fa_num(len(requests))} رسید در انتظار بررسی</b>\n\nبرای دیدن، انتخاب کنید:",
        reply_markup=kb.as_markup(),
    )


@router.callback_query(F.data.startswith("adm:payshow:"))
async def cb_payment_show(call: CallbackQuery) -> None:
    if not await roles.can(call.from_user.id, roles.CAP_MONEY):
        await call.answer("دسترسی ندارید", show_alert=True)
        return
    request_id = int(call.data.split(":")[2])
    async with get_session() as db:
        req = await db.get(PaymentRequest, request_id)
    if req is None or req.receipt_file_id is None:
        await call.answer("این رسید پیدا نشد.", show_alert=True)
        return

    kb = InlineKeyboardBuilder()
    kb.row(
        InlineKeyboardButton(text="✅ تأیید", callback_data=f"pay:ok:{req.id}"),
        InlineKeyboardButton(text="❌ رد", callback_data=f"pay:no:{req.id}"),
    )
    caption = (
        f"🧾 رسید <code>{req.id}</code>\n"
        f"کاربر: <code>{req.user_id}</code>\n"
        f"خرید: <b>{payments.describe(req)}</b>\n"
        f"مبلغ: <b>{toman(req.amount_toman)}</b>"
    )
    await call.answer()
    if req.receipt_kind == "photo":
        await call.message.answer_photo(
            req.receipt_file_id, caption=caption, reply_markup=kb.as_markup()
        )
    else:
        await call.message.answer_document(
            req.receipt_file_id, caption=caption, reply_markup=kb.as_markup()
        )


# ------------------------------------------------------- صف تلاش مجدد
@router.callback_query(F.data == "adm:retry")
async def cb_retry(call: CallbackQuery) -> None:
    if not await roles.can(call.from_user.id, roles.CAP_SYSTEM):
        await call.answer("دسترسی ندارید", show_alert=True)
        return
    await call.answer()
    async with get_session() as db:
        rows = await db.execute(select(RetryItem).order_by(RetryItem.id).limit(15))
        items = list(rows.scalars())

    if not items:
        await call.message.edit_text(
            "🔁 صف تلاش مجدد خالی است — همه‌ی پست‌ها ارسال شده‌اند.",
            reply_markup=_back().as_markup(),
        )
        return

    lines = [f"🔁 <b>{fa_num(len(items))} مورد در صف</b>\n"]
    for item in items:
        lines.append(
            f"• کار {item.task_id} → {item.dest_chat}\n"
            f"  تلاش: {fa_num(item.attempts)} | {item.last_error[:70]}"
        )
    await call.message.edit_text("\n".join(lines), reply_markup=_back().as_markup())


# ------------------------------------------------- پیدا کردن شناسه‌ی چت
@router.message(Command("chatid"))
async def cmd_chatid(message: Message, state: FSMContext) -> None:
    """شناسه‌ی عددی یک کانال خصوصی را با فوروارد کردن یک پیام می‌دهد.

    پیدا کردن این عدد به‌صورت دستی برای کانال خصوصی دردسر دارد؛ اینطور
    فقط یک فوروارد لازم است.
    """
    if not await roles.can(message.from_user.id, roles.CAP_SYSTEM):
        return
    await state.set_state(Flow.admin_chatid)
    await message.answer(
        "🆔 <b>پیدا کردن شناسه‌ی کانال</b>\n\n"
        "یک پیام از آن کانال را برای من <b>فوروارد</b> کنید تا شناسه‌اش "
        "را بگویم.\n\n"
        "<i>اگر کانال «فوروارد» را بسته باشد، به‌جایش ربات را در کانال "
        "ادمین کنید و یک پیام آنجا بفرستید — بعد همان را فوروارد کنید. یا "
        "کانال را موقتاً عمومی کنید و آیدی‌اش را بدهید.</i>\n\n"
        "انصراف: /cancel"
    )


@router.message(Flow.admin_chatid)
async def got_chatid(message: Message, state: FSMContext) -> None:
    if not await roles.can(message.from_user.id, roles.CAP_SYSTEM):
        await state.clear()
        return

    origin = getattr(message, "forward_from_chat", None)
    if origin is None:
        # روی نسخه‌های تازه‌ی Bot API منبع فوروارد در forward_origin است
        forward_origin = getattr(message, "forward_origin", None)
        origin = getattr(forward_origin, "chat", None)

    if origin is None:
        text = (message.text or "").strip()
        if text.startswith("@") or text.lstrip("-").isdigit():
            await state.clear()
            await message.answer(
                f"🆔 مقداری که فرستادید:\n<code>{text}</code>\n\n"
                "اگر کانال خصوصی است، شناسه باید عددی و با <code>-100</code> "
                "شروع شود. برای گرفتنش یک پیام از کانال را فوروارد کنید."
            )
            return
        await message.answer(
            "این یک پیام فورواردشده از کانال نیست.\n"
            "یک پیام از خود کانال را فوروارد کنید یا /cancel بزنید."
        )
        return

    await state.clear()
    title = getattr(origin, "title", "") or "—"
    kb = InlineKeyboardBuilder()
    kb.row(
        InlineKeyboardButton(
            text="💾 همین را کانال پشتیبان کن",
            callback_data=f"adm:setbk:{origin.id}",
        )
    )
    await message.answer(
        f"🆔 <b>{title}</b>\n\n"
        f"شناسه: <code>{origin.id}</code>\n\n"
        "اگر می‌خواهید نسخه‌های پشتیبان به همین کانال بروند، دکمه‌ی زیر را "
        "بزنید — همین‌جا ذخیره می‌شود و نیازی به ویرایش فایل یا ری‌استارت "
        "نیست.\n\n"
        "<i>ربات باید در آن کانال ادمین باشد و اجازه‌ی ارسال داشته باشد.</i>",
        reply_markup=kb.as_markup(),
    )


@router.callback_query(F.data.startswith("adm:setbk:"))
async def cb_set_backup_chat(call: CallbackQuery) -> None:
    if not await roles.can(call.from_user.id, roles.CAP_SYSTEM):
        await call.answer("دسترسی ندارید", show_alert=True)
        return
    target = call.data.split(":")[-1]

    # پیش از ذخیره امتحان می‌کنیم؛ وگرنه تازه موقع فاجعه می‌فهمیدیم نمی‌شود
    try:
        probe = await call.bot.send_message(
            int(target),
            "✅ این کانال به‌عنوان مقصد نسخه‌های پشتیبان تنظیم شد.",
            disable_notification=True,
        )
    except Exception as exc:
        log.warning("تنظیم کانال پشتیبان ناموفق بود: %s", exc)
        await call.answer()
        await call.message.answer(
            "⚠️ نتوانستم در آن کانال پیام بفرستم.\n\n"
            "ربات را در کانال <b>ادمین</b> کنید و اجازه‌ی «ارسال پیام» بدهید، "
            "بعد دوباره تلاش کنید."
        )
        return

    await backup.set_chat_id(target, by=call.from_user.id)
    await log_activity(
        actor_id=call.from_user.id,
        event="backup_channel_set",
        detail=target,
        level="warning",
    )
    await call.answer("کانال پشتیبان تنظیم شد")
    await call.message.answer(
        f"💾 کانال پشتیبان روی <code>{target}</code> تنظیم شد.\n"
        f"یک پیام آزمایشی همان‌جا فرستادم (شماره {probe.message_id}).\n\n"
        "از این پس هر نسخه‌ی پشتیبان به‌صورت خودکار آنجا بایگانی می‌شود."
    )


# ------------------------------------------------------- پشتیبان‌گیری
@router.callback_query(F.data == "adm:backup")
async def cb_backup(call: CallbackQuery) -> None:
    if not await roles.can(call.from_user.id, roles.CAP_SYSTEM):
        await call.answer("دسترسی ندارید", show_alert=True)
        return
    await call.answer("در حال ساخت نسخه‌ی پشتیبان…")
    path, offsite = await backup.run_once(call.bot)
    if path is None:
        await call.message.answer("⚠️ پشتیبان‌گیری انجام نشد (دیتابیس SQLite نیست یا خطا رخ داد).")
        return

    size_kb = path.stat().st_size // 1024
    note = (
        f"\n☁️ نسخه‌ای هم به کانال پشتیبان (<code>{await backup.chat_id()}</code>) رفت."
        if offsite
        else "\n⚠️ کانال پشتیبان تنظیم نشده؛ این نسخه فقط روی همین سرور است.\n"
        "<i>یک کانال خصوصی بسازید، ربات را در آن ادمین کنید، بعد /chatid را "
        "بزنید و یک پیام از آن کانال را فوروارد کنید — با یک دکمه تمام "
        "می‌شود.</i>"
    )
    try:
        from aiogram.types import FSInputFile

        await call.message.answer_document(
            FSInputFile(path),
            caption=f"💾 نسخه‌ی پشتیبان\nحجم: {fa_num(size_kb)} کیلوبایت{note}",
        )
    except Exception:
        log.warning("ارسال فایل پشتیبان ناموفق بود", exc_info=True)
        await call.message.answer(
            f"💾 نسخه‌ی پشتیبان ساخته شد:\n<code>{path}</code>{note}"
        )


# ------------------------------------------------------ فعال‌سازی دستی
@router.callback_query(F.data == "adm:grant")
async def cb_grant_start(call: CallbackQuery, state: FSMContext) -> None:
    if not await roles.can(call.from_user.id, roles.CAP_MONEY):
        await call.answer("دسترسی ندارید", show_alert=True)
        return
    await call.answer()
    await state.set_state(Flow.admin_grant)
    await call.message.answer(
        "🎁 شناسه‌ی کاربر و کد پلن را بفرستید:\n"
        "<code>123456789 month</code>\n\n"
        f"پلن‌ها: {', '.join(PLANS)}\n\nانصراف: /cancel"
    )


@router.message(Flow.admin_grant)
async def do_grant(message: Message, state: FSMContext) -> None:
    if not await roles.can(message.from_user.id, roles.CAP_MONEY):
        await state.clear()
        return
    parts = (message.text or "").split()
    if len(parts) != 2:
        await message.answer("قالب درست: <code>123456789 month</code>")
        return
    user_id, plan_code = parse_int(parts[0]), parts[1]
    if user_id is None or plan_code not in PLANS:
        await message.answer(f"شناسه یا پلن نامعتبر. پلن‌ها: {', '.join(PLANS)}")
        return
    await state.clear()
    await _grant_and_notify(message, user_id, plan_code)


@router.message(Command("grant"))
async def cmd_grant(message: Message) -> None:
    if not await roles.can(message.from_user.id, roles.CAP_MONEY):
        return
    parts = (message.text or "").split()
    if len(parts) != 3:
        await message.answer("قالب درست: <code>/grant &lt;user_id&gt; &lt;plan&gt;</code>")
        return
    user_id, plan_code = parse_int(parts[1]), parts[2]
    if user_id is None or plan_code not in PLANS:
        await message.answer(f"شناسه یا پلن نامعتبر. پلن‌ها: {', '.join(PLANS)}")
        return
    await _grant_and_notify(message, user_id, plan_code)


async def _grant_and_notify(message: Message, user_id: int, plan_code: str) -> None:
    async with get_session() as db:
        if await db.get(User, user_id) is None:
            await message.answer("این کاربر هنوز ربات را استارت نکرده است.")
            return

    sub = await grant(user_id, plan_code, granted_by=message.from_user.id, note="فعال‌سازی ادمین")
    if sub is None:
        await message.answer("فعال‌سازی ناموفق بود.")
        return

    await message.answer(
        f"✅ پلن <b>{PLANS[plan_code].title}</b> برای <code>{user_id}</code> فعال شد.\n"
        f"انقضا: {sub.expires_at:%Y-%m-%d %H:%M}"
    )
    try:
        await message.bot.send_message(
            user_id,
            f"🎉 اشتراک <b>{PLANS[plan_code].title}</b> برای شما فعال شد.\n"
            f"تا تاریخ {sub.expires_at:%Y-%m-%d} می‌توانید از همه‌ی امکانات استفاده کنید.",
        )
    except Exception:
        log.debug("اطلاع فعال‌سازی به کاربر نرسید", exc_info=True)


# ----------------------------------------------------------- مسدودسازی
@router.callback_query(F.data == "adm:ban")
async def cb_ban_help(call: CallbackQuery) -> None:
    if not await roles.can(call.from_user.id, roles.CAP_USERS):
        await call.answer("دسترسی ندارید", show_alert=True)
        return
    await call.answer()
    await call.message.edit_text(
        "🚫 <b>مسدودسازی</b>\n\n"
        "ساده‌ترین راه: «👥 مدیریت کاربران» → انتخاب کاربر → دکمه‌ی مسدود کردن.\n\n"
        "معادل دستوری:\n"
        "<code>/ban 123456789</code> — مسدود کردن\n"
        "<code>/unban 123456789</code> — آزاد کردن\n\n"
        "با مسدود شدن، کارهای کپی کاربر هم متوقف می‌شوند.",
        reply_markup=_back().as_markup(),
    )


@router.message(Command("ban"))
@router.message(Command("unban"))
async def cmd_ban(message: Message) -> None:
    if not await roles.can(message.from_user.id, roles.CAP_USERS):
        return
    parts = (message.text or "").split()
    if len(parts) != 2:
        await message.answer("قالب درست: <code>/ban &lt;user_id&gt;</code>")
        return
    user_id = parse_int(parts[1])
    if user_id is None:
        await message.answer("شناسه نامعتبر است.")
        return
    banning = parts[0].split("@")[0] == "/ban"

    async with get_session() as db:
        user = await db.get(User, user_id)
        if user is None:
            await message.answer("کاربر پیدا نشد.")
            return
        user.is_banned = banning
        if banning:
            rows = await db.execute(select(Task).where(Task.user_id == user_id))
            for task in rows.scalars():
                task.enabled = False
        await db.commit()

    if banning:
        await manager.stop_user(user_id)
    else:
        await manager.reload_user(user_id)
    await message.answer(f"{'🚫 مسدود' if banning else '✅ آزاد'} شد: <code>{user_id}</code>")


# --------------------------------------------------------- پیام همگانی
async def _segment_screen() -> tuple[str, InlineKeyboardBuilder]:
    counts = await audience.sizes()
    kb = InlineKeyboardBuilder()
    for segment in audience.SEGMENTS:
        kb.row(
            InlineKeyboardButton(
                text=f"{segment.title} ({fa_num(counts[segment.code])})",
                callback_data=f"cast:{segment.code}",
            )
        )
    kb.row(InlineKeyboardButton(text="🔙 بازگشت", callback_data="adm:home"))

    lines = ["📢 <b>پیام همگانی</b>\n", "به چه کسانی فرستاده شود؟\n"]
    lines.extend(f"{s.title} — {s.hint}" for s in audience.SEGMENTS)
    lines.append(
        "\n<i>کاربران مسدود هرگز پیام نمی‌گیرند. پیش از ارسال، متن را با "
        "تعداد دقیق مخاطبان دوباره می‌بینید.</i>"
    )
    return "\n".join(lines), kb


@router.callback_query(F.data == "adm:cast")
async def cb_broadcast(call: CallbackQuery) -> None:
    if not await roles.can(call.from_user.id, roles.CAP_USERS):
        await call.answer("دسترسی ندارید", show_alert=True)
        return
    await call.answer()
    text, kb = await _segment_screen()
    await call.message.edit_text(text, reply_markup=kb.as_markup())


@router.callback_query(F.data.startswith("cast:"))
async def cb_pick_segment(call: CallbackQuery, state: FSMContext) -> None:
    if not await roles.can(call.from_user.id, roles.CAP_USERS):
        await call.answer("دسترسی ندارید", show_alert=True)
        return
    code = call.data.split(":", 1)[1]
    segment = audience.BY_CODE.get(code)
    if segment is None:
        await call.answer()
        return

    await call.answer()
    await state.set_state(Flow.admin_broadcast)
    await state.update_data(segment=code)
    await call.message.answer(
        f"📢 متن پیام برای «{segment.title}» را بفرستید.\n\n"
        "<i>قالب‌بندی (بولد، لینک، ایموجی) حفظ می‌شود.</i>\n\nانصراف: /cancel"
    )


@router.message(Command("broadcast"))
async def cmd_broadcast(message: Message) -> None:
    if not await roles.can(message.from_user.id, roles.CAP_USERS):
        return
    text, kb = await _segment_screen()
    await message.answer(text, reply_markup=kb.as_markup())


@router.message(Flow.admin_broadcast)
async def got_broadcast_text(message: Message, state: FSMContext) -> None:
    """متن گرفته شد؛ پیش از ارسال یک بار نشان داده و تأیید گرفته می‌شود.

    پیام همگانی برگشت‌پذیر نیست — یک تأیید ارزشش را دارد.
    """
    if not await roles.can(message.from_user.id, roles.CAP_USERS):
        await state.clear()
        return
    data = await state.get_data()
    code = data.get("segment", audience.ALL)
    segment = audience.BY_CODE.get(code) or audience.BY_CODE[audience.ALL]
    body = message.html_text

    await state.update_data(body=body)
    count = await audience.size(code)
    kb = InlineKeyboardBuilder()
    if count:
        kb.row(
            InlineKeyboardButton(
                text=f"✅ ارسال به {fa_num(count)} نفر", callback_data="castgo"
            )
        )
    kb.row(InlineKeyboardButton(text="❌ انصراف", callback_data="adm:home"))

    await message.answer(
        f"📢 <b>پیش‌نمایش</b>\nمخاطب: {segment.title} — {fa_num(count)} نفر\n"
        f"{'─' * 18}\n\n{body}",
        reply_markup=kb.as_markup(),
    )


@router.callback_query(F.data == "castgo")
async def do_broadcast(call: CallbackQuery, state: FSMContext) -> None:
    if not await roles.can(call.from_user.id, roles.CAP_USERS):
        await call.answer("دسترسی ندارید", show_alert=True)
        return
    data = await state.get_data()
    body = data.get("body")
    code = data.get("segment", audience.ALL)
    await state.clear()
    if not body:
        await call.answer("متن پیام پیدا نشد؛ از نو شروع کنید", show_alert=True)
        return

    user_ids = await audience.members(code)
    await call.answer()
    sent = failed = 0
    notice = await call.message.answer(
        f"در حال ارسال به {fa_num(len(user_ids))} کاربر…"
    )
    for index, user_id in enumerate(user_ids, start=1):
        try:
            await call.bot.send_message(user_id, body)
            sent += 1
        except Exception:
            failed += 1
        # نرخ امن Bot API برای ارسال انبوه
        await asyncio.sleep(0.05)
        if index % 100 == 0:
            try:
                await notice.edit_text(f"ارسال‌شده: {fa_num(sent)} | ناموفق: {fa_num(failed)}")
            except Exception:
                log.debug("به‌روزرسانی پیشرفت ارسال ناموفق بود", exc_info=True)

    await log_activity(
        actor_id=call.from_user.id,
        event="broadcast",
        detail=f"{audience.BY_CODE[code].title}: {sent} موفق، {failed} ناموفق",
        level="warning",
    )

    await notice.edit_text(
        f"📢 پایان ارسال همگانی.\nموفق: {fa_num(sent)} | ناموفق: {fa_num(failed)}"
    )
