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
from telkap.handlers.common import Flow, get_or_create_user, parse_int
from telkap.keyboards import (
    BTN_PLANS,
    PLAN_ICONS,
    credit_packs_menu,
    credits_menu,
    plans_menu,
)
from telkap.models import PaymentRequest
from telkap.plans import (
    CREDIT_KINDS,
    FEAT_PRIVATE,
    FEAT_VIP,
    POPULAR_CODE,
    PURCHASABLE,
    WATERMARK_UNIT_TOMAN,
    credit_price,
    get_plan,
    toman,
)
from telkap.services import credits, payments
from telkap.services.subscription import active_subscription, remaining_days
from telkap.texts import fa_num

MAX_CREDIT_UNITS = 20_000

log = logging.getLogger(__name__)
router = Router(name="billing")


RULE = "━━━━━━━━━━━━━━━━━━"


def _plans_text() -> str:
    lines = ["💎 <b>طرح‌های اشتراک</b>", RULE]
    for plan in PURCHASABLE:
        icon = PLAN_ICONS.get(plan.code, "▫️")
        star = "  ⭐️ <i>محبوب‌ترین</i>" if plan.code == POPULAR_CODE else ""
        lines.append(f"\n{icon} <b>{plan.title}</b> — {plan.price_label}{star}")
        lines.append(f"<i>{plan.tagline}</i>")
        for perk in plan.perks:
            lines.append(f"   ✓ {perk}")
    lines.append(f"\n{RULE}")
    lines.append(
        "🎫 <b>اعتبار جداگانه</b> — سهمیه‌ی روزانه‌ی واترمارک و پیام‌های گذشته "
        "هر شب پر می‌شود. اگر بیشتر لازم داشتید، به‌جای طرح بالاتر می‌توانید "
        f"واحدی بخرید (هر واحد {toman(WATERMARK_UNIT_TOMAN)}، بدون انقضا)."
    )
    lines.append("\nبرای خرید، یکی از گزینه‌های زیر را بزنید 👇")
    return "\n".join(lines)


def _compare_text() -> str:
    """جدول مقایسه‌ی همه‌ی طرح‌ها در یک نگاه."""
    def mark(flag: bool) -> str:
        return "✅" if flag else "➖"

    rows = [
        ("💰 قیمت", lambda p: p.price_label.replace(" تومان", "")),
        ("📅 مدت", lambda p: f"{fa_num(p.days)} روز"),
        ("📨 پیام روزانه", lambda p: p.daily_label),
        ("📋 کار کپی", lambda p: fa_num(p.max_tasks)),
        ("📤 مقصد هر کار", lambda p: fa_num(p.max_destinations)),
        ("💧 واترمارک روزانه", lambda p: p.watermark_label),
        ("🕓 پیام گذشته روزانه", lambda p: p.history_label),
        ("🔒 کانال خصوصی", lambda p: mark(p.has(FEAT_PRIVATE))),
        ("👑 پشتیبانی ویژه", lambda p: mark(p.has(FEAT_VIP))),
    ]

    blocks = ["📊 <b>مقایسه‌ی طرح‌ها</b>", RULE]
    for plan in PURCHASABLE:
        icon = PLAN_ICONS.get(plan.code, "▫️")
        blocks.append(f"\n{icon} <b>{plan.title}</b>")
        for label, getter in rows:
            blocks.append(f"   {label}: {getter(plan)}")
    blocks.append(f"\n{RULE}")
    blocks.append(
        "<i>سهمیه‌ها هر شبانه‌روز از نو پر می‌شوند. اگر سهمیه‌ی روزتان تمام "
        "شد یا طرحتان آن را ندارد، می‌توانید اعتبار واحدی بخرید — اعتبار "
        "انقضا و سقف روزانه ندارد.</i>"
    )
    return "\n".join(blocks)


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


# ------------------------------------------------------- مقایسه و اعتبار
@router.callback_query(F.data == "cmp:plans")
async def cb_compare(call: CallbackQuery) -> None:
    await call.answer()
    await call.message.edit_text(_compare_text(), reply_markup=plans_menu())


@router.callback_query(F.data == "credit:plans")
async def cb_back_to_plans(call: CallbackQuery) -> None:
    await call.answer()
    await call.message.edit_text(_plans_text(), reply_markup=plans_menu())


@router.callback_query(F.data == "credit:menu")
async def cb_credits(call: CallbackQuery) -> None:
    await call.answer()
    await get_or_create_user(call.from_user)
    balances = await credits.balances(call.from_user.id)
    lines = ["🎫 <b>خرید اعتبار</b>", RULE, ""]
    for kind, (title, desc, price) in CREDIT_KINDS.items():
        lines.append(f"{title}")
        lines.append(f"   {desc}")
        lines.append(f"   قیمت هر واحد: <b>{toman(price)}</b>")
        lines.append(f"   مانده‌ی شما: <b>{fa_num(balances.get(kind, 0))}</b>\n")
    lines.append(
        "<i>اعتبار تاریخ انقضا ندارد و تا وقتی مصرف نشود باقی می‌ماند. "
        "اگر پلن شما خودش این قابلیت را داشته باشد، اعتبار مصرف نمی‌شود.</i>"
    )
    await call.message.edit_text("\n".join(lines), reply_markup=credits_menu(balances))


@router.callback_query(F.data.startswith("credit:pick:"))
async def cb_credit_pick(call: CallbackQuery) -> None:
    kind = call.data.split(":")[2]
    info = CREDIT_KINDS.get(kind)
    if info is None:
        await call.answer("این بسته وجود ندارد.", show_alert=True)
        return
    title, desc, price = info
    await call.answer()
    have = await credits.balance(call.from_user.id, kind)
    text = (
        f"{title}\n{RULE}\n\n"
        f"{desc}\n"
        f"قیمت هر واحد: <b>{toman(price)}</b>\n"
        f"مانده‌ی فعلی شما: <b>{fa_num(have)}</b>\n\n"
        "چند واحد می‌خواهید؟"
    )
    markup = credit_packs_menu(kind)
    try:
        await call.message.edit_text(text, reply_markup=markup)
    except Exception:
        await call.message.answer(text, reply_markup=markup)


@router.callback_query(F.data.startswith("credit:ask:"))
async def cb_credit_ask(call: CallbackQuery, state: FSMContext) -> None:
    kind = call.data.split(":")[2]
    if kind not in CREDIT_KINDS:
        await call.answer("این بسته وجود ندارد.", show_alert=True)
        return
    await call.answer()
    await state.set_state(Flow.credit_amount)
    await state.update_data(credit_kind=kind)
    price = CREDIT_KINDS[kind][2]
    await call.message.answer(
        f"🔢 چند واحد می‌خواهید؟ عدد را بفرستید.\n"
        f"هر واحد {toman(price)} — مثلاً <code>250</code>\n\n"
        "انصراف: /cancel"
    )


@router.message(Flow.credit_amount)
async def got_credit_amount(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    kind = str(data.get("credit_kind", ""))
    quantity = parse_int(message.text or "")
    if kind not in CREDIT_KINDS:
        await state.clear()
        await message.answer("این بسته وجود ندارد.")
        return
    if quantity is None or quantity <= 0:
        await message.answer("یک عدد مثبت بفرستید، مثلاً <code>250</code>.")
        return
    if quantity > MAX_CREDIT_UNITS:
        await message.answer(f"حداکثر {fa_num(MAX_CREDIT_UNITS)} واحد در هر خرید.")
        return
    await state.clear()
    await _start_credit_purchase(message, message.from_user.id, kind, quantity, state)


@router.callback_query(F.data.startswith("credit:buy:"))
async def cb_credit_buy(call: CallbackQuery, state: FSMContext) -> None:
    _, _, kind, raw = call.data.split(":")
    if kind not in CREDIT_KINDS:
        await call.answer("این بسته وجود ندارد.", show_alert=True)
        return
    await call.answer()
    await _start_credit_purchase(call.message, call.from_user.id, kind, int(raw), state)


async def _start_credit_purchase(
    target: Message, user_id: int, kind: str, quantity: int, state: FSMContext
) -> None:
    """رسید کارت‌به‌کارت برای خرید اعتبار را می‌خواهد."""
    title = CREDIT_KINDS[kind][0]
    amount = credit_price(kind, quantity)
    request = await payments.create_credit_request(user_id, kind, quantity, amount)
    if request is None:
        await target.answer("⚠️ ثبت درخواست ناموفق بود. دوباره تلاش کنید.")
        return

    cfg = get_settings()
    if not cfg.card_number:
        support = f"@{cfg.support_username}" if cfg.support_username else "پشتیبانی"
        await target.answer(
            f"🎫 <b>{title}</b> — {fa_num(quantity)} واحد\n"
            f"مبلغ: <b>{toman(amount)}</b>\n\n"
            f"برای پرداخت با {support} در تماس باشید.\n"
            f"شناسه‌ی شما: <code>{user_id}</code>"
        )
        return

    kb = InlineKeyboardBuilder()
    kb.row(InlineKeyboardButton(text="❌ انصراف", callback_data="pay:cancel"))
    holder = f"\nبه نام: <b>{cfg.card_holder}</b>" if cfg.card_holder else ""
    await state.set_state(Flow.receipt)
    await state.update_data(request_id=request.id)
    await target.answer(
        f"🎫 <b>{title}</b>\n"
        f"تعداد: <b>{fa_num(quantity)}</b> واحد\n"
        f"مبلغ قابل پرداخت: <b>{toman(amount)}</b>\n\n"
        f"💳 شماره کارت:\n<code>{cfg.card_number}</code>{holder}\n\n"
        "پس از واریز، <b>تصویر رسید</b> را همین‌جا بفرستید.",
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

    await message.answer(
        "✅ رسید شما ثبت شد.\n\n"
        f"خرید: <b>{payments.describe(request)}</b>\n"
        f"مبلغ: <b>{toman(request.amount_toman)}</b>\n"
        f"کد پیگیری: <code>{request.id}</code>\n\n"
        "پس از بررسی، نتیجه همین‌جا اعلام می‌شود."
    )
    await _notify_admins(message, request)


async def _notify_admins(message: Message, request: PaymentRequest) -> None:
    """رسید را با دکمه‌های تأیید/رد برای همه‌ی ادمین‌ها می‌فرستد."""
    cfg = get_settings()
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
        f"خرید: <b>{payments.describe(request)}</b>\n"
        f"مبلغ: <b>{toman(request.amount_toman)}</b>\n"
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

    await call.answer("تأیید شد")
    await _mark_reviewed(call, f"✅ تأیید شد توسط {call.from_user.full_name}")

    if sub is not None:
        plan = get_plan(request.plan_code)
        note = (
            f"🎉 پرداخت شما تأیید شد!\n\n"
            f"اشتراک <b>{plan.title if plan else request.plan_code}</b> فعال است "
            f"تا {sub.expires_at:%Y-%m-%d}.\n\n"
            "حالا می‌توانید کار کپی بسازید."
        )
    else:
        left = await credits.balance(request.user_id, request.plan_code)
        note = (
            f"🎉 پرداخت شما تأیید شد!\n\n"
            f"<b>{payments.describe(request)}</b> به حساب شما اضافه شد.\n"
            f"مانده‌ی اعتبار: <b>{fa_num(left)}</b> واحد"
        )
    try:
        await call.bot.send_message(request.user_id, note)
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
