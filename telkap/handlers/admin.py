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

from telkap.config import get_settings
from telkap.db import get_session
from telkap.handlers.common import Flow, parse_int
from telkap.models import DailyStat, PaymentRequest, RetryItem, Subscription, Task, User, utcnow
from telkap.plans import PLANS, toman
from telkap.services import backup, payments, support
from telkap.services.copier import today_key
from telkap.services.subscription import grant
from telkap.services.userbot import manager
from telkap.texts import fa_num

log = logging.getLogger(__name__)
router = Router(name="admin")


def _is_admin(user_id: int) -> bool:
    return get_settings().is_admin(user_id)


def admin_menu(pending: int = 0, tickets: int = 0) -> InlineKeyboardBuilder:
    kb = InlineKeyboardBuilder()
    badge = f" ({fa_num(pending)})" if pending else ""
    kb.row(InlineKeyboardButton(text="👥 مدیریت کاربران", callback_data="adm:users"))
    ticket_badge = f" ({fa_num(tickets)})" if tickets else ""
    kb.row(
        InlineKeyboardButton(
            text=f"🛟 تیکت‌های پشتیبانی{ticket_badge}", callback_data="adm:tickets"
        )
    )
    kb.row(InlineKeyboardButton(text="📢 عضویت اجباری", callback_data="adm:join"))
    kb.row(InlineKeyboardButton(text=f"🧾 رسیدهای در انتظار{badge}", callback_data="adm:pay"))
    kb.row(
        InlineKeyboardButton(text="📊 آمار", callback_data="adm:stats"),
        InlineKeyboardButton(text="🔁 صف تلاش مجدد", callback_data="adm:retry"),
    )
    kb.row(
        InlineKeyboardButton(text="🎁 فعال‌سازی اشتراک", callback_data="adm:grant"),
        InlineKeyboardButton(text="📢 پیام همگانی", callback_data="adm:cast"),
    )
    kb.row(
        InlineKeyboardButton(text="💾 پشتیبان‌گیری", callback_data="adm:backup"),
        InlineKeyboardButton(text="🚫 مسدودسازی", callback_data="adm:ban"),
    )
    return kb


@router.message(Command("admin"))
async def cmd_admin(message: Message) -> None:
    if not _is_admin(message.from_user.id):
        return
    pending = await payments.pending_count()
    tickets = await support.waiting_count()
    await message.answer(
        "🛠 <b>پنل مدیریت</b>\n\nیک بخش را انتخاب کنید:",
        reply_markup=admin_menu(pending, tickets).as_markup(),
    )


@router.callback_query(F.data == "adm:home")
async def cb_home(call: CallbackQuery) -> None:
    if not _is_admin(call.from_user.id):
        await call.answer("دسترسی ندارید", show_alert=True)
        return
    pending = await payments.pending_count()
    tickets = await support.waiting_count()
    await call.answer()
    await call.message.edit_text(
        "🛠 <b>پنل مدیریت</b>\n\nیک بخش را انتخاب کنید:",
        reply_markup=admin_menu(pending, tickets).as_markup(),
    )


def _back() -> InlineKeyboardBuilder:
    kb = InlineKeyboardBuilder()
    kb.row(InlineKeyboardButton(text="🔙 بازگشت", callback_data="adm:home"))
    return kb


# ------------------------------------------------------------------- آمار
@router.callback_query(F.data == "adm:stats")
@router.message(Command("stats"))
async def show_stats(event: CallbackQuery | Message) -> None:
    user_id = event.from_user.id
    if not _is_admin(user_id):
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
    if isinstance(event, CallbackQuery):
        await event.answer()
        await event.message.edit_text(text, reply_markup=_back().as_markup())
    else:
        await event.answer(text)


# ------------------------------------------------------------- رسیدها
@router.callback_query(F.data == "adm:pay")
async def cb_payments(call: CallbackQuery) -> None:
    if not _is_admin(call.from_user.id):
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
    if not _is_admin(call.from_user.id):
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
    if not _is_admin(call.from_user.id):
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


# ------------------------------------------------------- پشتیبان‌گیری
@router.callback_query(F.data == "adm:backup")
async def cb_backup(call: CallbackQuery) -> None:
    if not _is_admin(call.from_user.id):
        await call.answer("دسترسی ندارید", show_alert=True)
        return
    await call.answer("در حال ساخت نسخه‌ی پشتیبان…")
    path = await asyncio.to_thread(backup.make_backup)
    if path is None:
        await call.message.answer("⚠️ پشتیبان‌گیری انجام نشد (دیتابیس SQLite نیست یا خطا رخ داد).")
        return
    size_kb = path.stat().st_size // 1024
    try:
        from aiogram.types import FSInputFile

        await call.message.answer_document(
            FSInputFile(path),
            caption=f"💾 نسخه‌ی پشتیبان\nحجم: {fa_num(size_kb)} کیلوبایت",
        )
    except Exception:
        log.warning("ارسال فایل پشتیبان ناموفق بود", exc_info=True)
        await call.message.answer(f"💾 نسخه‌ی پشتیبان ساخته شد:\n<code>{path}</code>")


# ------------------------------------------------------ فعال‌سازی دستی
@router.callback_query(F.data == "adm:grant")
async def cb_grant_start(call: CallbackQuery, state: FSMContext) -> None:
    if not _is_admin(call.from_user.id):
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
    if not _is_admin(message.from_user.id):
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
    if not _is_admin(message.from_user.id):
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
    if not _is_admin(call.from_user.id):
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
    if not _is_admin(message.from_user.id):
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
@router.callback_query(F.data == "adm:cast")
async def cb_broadcast(call: CallbackQuery, state: FSMContext) -> None:
    if not _is_admin(call.from_user.id):
        await call.answer("دسترسی ندارید", show_alert=True)
        return
    await call.answer()
    await state.set_state(Flow.admin_broadcast)
    await call.message.answer("📢 متن پیام همگانی را بفرستید.\n\nانصراف: /cancel")


@router.message(Command("broadcast"))
async def cmd_broadcast(message: Message, state: FSMContext) -> None:
    if not _is_admin(message.from_user.id):
        return
    await state.set_state(Flow.admin_broadcast)
    await message.answer("📢 متن پیام همگانی را بفرستید.\n\nانصراف: /cancel")


@router.message(Flow.admin_broadcast)
async def do_broadcast(message: Message, state: FSMContext) -> None:
    if not _is_admin(message.from_user.id):
        await state.clear()
        return
    await state.clear()
    async with get_session() as db:
        rows = await db.execute(select(User.id).where(User.is_banned.is_(False)))
        user_ids = [uid for uid in rows.scalars()]

    sent = failed = 0
    notice = await message.answer(f"در حال ارسال به {fa_num(len(user_ids))} کاربر…")
    for index, user_id in enumerate(user_ids, start=1):
        try:
            await message.bot.send_message(user_id, message.html_text)
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

    await notice.edit_text(
        f"📢 پایان ارسال همگانی.\nموفق: {fa_num(sent)} | ناموفق: {fa_num(failed)}"
    )
