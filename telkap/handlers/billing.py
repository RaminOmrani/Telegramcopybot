"""خرید اشتراک: انتخاب پلن، پرداخت کارت‌به‌کارت، ارسال رسید.

کل جریان داخل تلگرام است. ادمین رسید را با یک دکمه تأیید می‌کند و
اشتراک خودکار فعال می‌شود.
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
from telkap.keyboards import BTN_PLANS, plans_menu
from telkap.models import PaymentRequest
from telkap.plans import POPULAR_CODE, PURCHASABLE, get_plan
from telkap.services import payments
from telkap.services.subscription import active_subscription, remaining_days
from telkap.texts import fa_num

log = logging.getLogger(__name__)
router = Router(name="billing")


def _plans_text() -> str:
    lines = ["💳 <b>تعرفه‌های اشتراک</b>\n"]
    for plan in PURCHASABLE:
        star = " ⭐️ <i>پیشنهاد ویژه</i>" if plan.code == POPULAR_CODE else ""
        lines.append(f"\n<b>{plan.title}</b> — {plan.price_label}{star}")
        lines.append(f"<i>{plan.tagline}</i>")
        for perk in plan.perks:
            lines.append(f"  • {perk}")
    lines.append("\nبرای خرید، پلن موردنظر را انتخاب کنید.")
    return "\n".join(lines)


@router.message(Command("plans"))
@router.message(F.text == BTN_PLANS)
async def show_plans(message: Message) -> None:
    sub = await active_subscription(message.from_user.id)
    header = ""
    if sub:
        plan = get_plan(sub.plan_code)
        days = await remaining_days(message.from_user.id)
        header = (
            f"اشتراک فعلی: <b>{plan.title if plan else sub.plan_code}</b> "
            f"({fa_num(days)} روز باقی‌مانده)\n"
            "با خرید پلن جدید، از انتهای اشتراک فعلی تمدید می‌شود.\n\n"
        )
    await message.answer(header + _plans_text(), reply_markup=plans_menu())


@router.callback_query(F.data.startswith("plan:"))
async def cb_plan(call: CallbackQuery, state: FSMContext) -> None:
    plan = get_plan(call.data.split(":")[1])
    if plan is None:
        await call.answer("این پلن وجود ندارد.", show_alert=True)
        return
    await call.answer()
    await get_or_create_user(call.from_user)

    cfg = get_settings()
    request = await payments.create_request(call.from_user.id, plan.code)
    if request is None:
        await call.message.answer("⚠️ ثبت درخواست ناموفق بود. دوباره تلاش کنید.")
        return

    if not cfg.card_number:
        support = f"@{cfg.support_username}" if cfg.support_username else "پشتیبانی"
        await call.message.answer(
            f"🧾 <b>{plan.title}</b> — {plan.price_label}\n\n"
            f"برای فعال‌سازی با {support} در تماس باشید.\n"
            f"شناسه‌ی شما: <code>{call.from_user.id}</code>"
        )
        return

    kb = InlineKeyboardBuilder()
    kb.row(InlineKeyboardButton(text="❌ انصراف", callback_data="pay:cancel"))

    holder = f"\nبه نام: <b>{cfg.card_holder}</b>" if cfg.card_holder else ""
    await state.set_state(Flow.receipt)
    await state.update_data(request_id=request.id)
    await call.message.answer(
        f"🧾 <b>{plan.title}</b>\n"
        f"مبلغ قابل پرداخت: <b>{plan.price_label}</b>\n"
        f"مدت: {fa_num(plan.days)} روز\n\n"
        f"💳 شماره کارت:\n<code>{cfg.card_number}</code>{holder}\n\n"
        "پس از واریز، <b>تصویر رسید</b> را همین‌جا بفرستید.\n"
        "اشتراک بلافاصله پس از تأیید فعال می‌شود.",
        reply_markup=kb.as_markup(),
    )


@router.callback_query(F.data == "pay:cancel")
async def cb_cancel(call: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await call.answer("لغو شد")
    await call.message.answer("خرید لغو شد.")


@router.message(Flow.receipt)
async def got_receipt(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    request_id = int(data.get("request_id", 0))

    file_id, kind = None, "text"
    if message.photo:
        file_id, kind = message.photo[-1].file_id, "photo"
    elif message.document:
        file_id, kind = message.document.file_id, "document"

    if file_id is None:
        await message.answer(
            "⚠️ لطفاً <b>تصویر</b> رسید را بفرستید.\nبرای انصراف /cancel را بزنید."
        )
        return

    request = await payments.attach_receipt(
        request_id, file_id, kind, note=message.caption or ""
    )
    await state.clear()
    if request is None:
        await message.answer("⚠️ این درخواست دیگر معتبر نیست. دوباره از «💳 خرید اشتراک» شروع کنید.")
        return

    plan = get_plan(request.plan_code)
    await message.answer(
        "✅ رسید شما ثبت شد.\n\n"
        f"پلن: <b>{plan.title if plan else request.plan_code}</b>\n"
        f"کد پیگیری: <code>{request.id}</code>\n\n"
        "پس از بررسی، نتیجه همین‌جا اعلام می‌شود."
    )
    await _notify_admins(message, request)


async def _notify_admins(message: Message, request: PaymentRequest) -> None:
    """رسید را با دکمه‌های تأیید/رد برای همه‌ی ادمین‌ها می‌فرستد."""
    cfg = get_settings()
    plan = get_plan(request.plan_code)
    user = message.from_user
    username = f"@{user.username}" if user.username else "—"

    kb = InlineKeyboardBuilder()
    kb.row(
        InlineKeyboardButton(text="✅ تأیید", callback_data=f"pay:ok:{request.id}"),
        InlineKeyboardButton(text="❌ رد", callback_data=f"pay:no:{request.id}"),
    )
    caption = (
        "🧾 <b>رسید جدید</b>\n\n"
        f"کاربر: {user.full_name} ({username})\n"
        f"شناسه: <code>{user.id}</code>\n"
        f"پلن: <b>{plan.title if plan else request.plan_code}</b>"
        f" — {plan.price_label if plan else ''}\n"
        f"کد پیگیری: <code>{request.id}</code>"
    )

    for admin_id in cfg.admin_ids:
        try:
            if request.receipt_kind == "photo":
                await message.bot.send_photo(
                    admin_id, request.receipt_file_id, caption=caption,
                    reply_markup=kb.as_markup(),
                )
            else:
                await message.bot.send_document(
                    admin_id, request.receipt_file_id, caption=caption,
                    reply_markup=kb.as_markup(),
                )
        except Exception:
            log.warning("ارسال رسید به ادمین %s ناموفق بود", admin_id, exc_info=True)


@router.callback_query(F.data.startswith("pay:ok:"))
async def cb_approve(call: CallbackQuery) -> None:
    if not get_settings().is_admin(call.from_user.id):
        await call.answer("دسترسی ندارید", show_alert=True)
        return
    request_id = int(call.data.split(":")[2])
    request, sub = await payments.approve(request_id, call.from_user.id)
    if request is None:
        await call.answer("این درخواست قبلاً بررسی شده است.", show_alert=True)
        return

    plan = get_plan(request.plan_code)
    await call.answer("تأیید شد")
    await _mark_reviewed(call, f"✅ تأیید شد توسط {call.from_user.full_name}")

    try:
        await call.bot.send_message(
            request.user_id,
            f"🎉 پرداخت شما تأیید شد!\n\n"
            f"اشتراک <b>{plan.title if plan else request.plan_code}</b> فعال است "
            f"تا {sub.expires_at:%Y-%m-%d}.\n\n"
            "حالا می‌توانید کار کپی بسازید.",
        )
    except Exception:
        log.warning("اطلاع تأیید به کاربر %s نرسید", request.user_id, exc_info=True)


@router.callback_query(F.data.startswith("pay:no:"))
async def cb_reject(call: CallbackQuery) -> None:
    if not get_settings().is_admin(call.from_user.id):
        await call.answer("دسترسی ندارید", show_alert=True)
        return
    request_id = int(call.data.split(":")[2])
    request = await payments.reject(request_id, call.from_user.id)
    if request is None:
        await call.answer("این درخواست قبلاً بررسی شده است.", show_alert=True)
        return

    await call.answer("رد شد")
    await _mark_reviewed(call, f"❌ رد شد توسط {call.from_user.full_name}")

    support = get_settings().support_username
    contact = f"\nدر صورت اشتباه با @{support} تماس بگیرید." if support else ""
    try:
        await call.bot.send_message(
            request.user_id,
            f"❌ رسید شما (کد {request.id}) تأیید نشد.{contact}",
        )
    except Exception:
        log.warning("اطلاع رد به کاربر %s نرسید", request.user_id, exc_info=True)


async def _mark_reviewed(call: CallbackQuery, verdict: str) -> None:
    """دکمه‌ها را برمی‌دارد تا ادمین دیگری دوباره بررسی نکند."""
    try:
        base = call.message.caption or call.message.text or ""
        new_text = f"{base}\n\n{verdict}"
        if call.message.caption is not None:
            await call.message.edit_caption(caption=new_text, reply_markup=None)
        else:
            await call.message.edit_text(new_text, reply_markup=None)
    except Exception:
        log.debug("به‌روزرسانی پیام رسید ناموفق بود", exc_info=True)
