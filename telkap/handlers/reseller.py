"""پنل نمایندگی: خرید اشتراک با تخفیف و فعال‌سازی فوری برای مشتری."""
from __future__ import annotations

import logging

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder

from telkap.handlers.common import Flow, parse_int
from telkap.plans import get_plan, purchasable, toman
from telkap.services import reseller, wallet
from telkap.texts import fa_num

log = logging.getLogger(__name__)
router = Router(name="reseller")

RULE = "━━━━━━━━━━━━━━━━━━"


async def _panel(user_id: int) -> tuple[str, InlineKeyboardBuilder] | None:
    is_reseller, discount = await reseller.profile(user_id)
    if not is_reseller:
        return None

    balance = await wallet.balance(user_id)
    stats = await reseller.stats(user_id)

    lines = [
        "🏪 <b>پنل نمایندگی</b>",
        RULE,
        "",
        f"تخفیف شما: <b>{fa_num(discount)}٪</b> روی همه‌ی طرح‌ها",
        f"موجودی کیف پول: <b>{toman(balance)}</b>",
        "",
    ]
    if stats.sales:
        lines += [
            "<b>کارنامه‌ی شما</b>",
            f"فروش: <b>{fa_num(stats.sales)}</b> اشتراک به "
            f"<b>{fa_num(stats.customers)}</b> مشتری",
            f"پرداختی: <b>{toman(stats.spent)}</b>",
            f"سود شما نسبت به قیمت فهرست: <b>{toman(stats.saved)}</b>",
        ]
        if stats.commission:
            lines.append(
                f"سهم از خریدهای مستقیم مشتری‌هایتان: <b>{toman(stats.commission)}</b>"
            )
        lines.append("")

    people = await reseller.customers(user_id)
    soon = [c for c in people if c.expiring]
    over = [c for c in people if c.expired]
    if soon or over:
        lines.append("⏳ <b>نیاز به پیگیری</b>")
        if soon:
            lines.append(
                f"<b>{fa_num(len(soon))}</b> مشتری تا کمتر از "
                f"{fa_num(reseller.EXPIRY_WARNING_DAYS)} روز دیگر تمام می‌شود"
            )
        if over:
            lines.append(f"<b>{fa_num(len(over))}</b> مشتری اشتراک فعال ندارد")
        lines.append("")

    lines.append("طرحی را که می‌خواهید برای مشتری فعال کنید انتخاب کنید 👇")

    kb = InlineKeyboardBuilder()
    for plan in purchasable():
        price = reseller.discounted(plan.price_toman, discount)
        kb.row(
            InlineKeyboardButton(
                text=f"{plan.title} — {toman(price)}",
                callback_data=f"rs:pick:{plan.code}",
            )
        )
    kb.row(InlineKeyboardButton(text="👛 شارژ کیف پول", callback_data="wal:home"))
    row = []
    if people:
        row.append(
            InlineKeyboardButton(text="👥 مشتری‌های من", callback_data="rs:customers")
        )
    if stats.sales:
        row.append(
            InlineKeyboardButton(text="📜 فروش‌های من", callback_data="rs:sales")
        )
    if row:
        kb.row(*row)
    return "\n".join(lines), kb


@router.message(Command("reseller"))
async def cmd_panel(message: Message) -> None:
    panel = await _panel(message.from_user.id)
    if panel is None:
        await message.answer(
            "🏪 <b>نمایندگی</b>\n\n"
            "شما هنوز نماینده نیستید. نمایندگان اشتراک را با تخفیف می‌خرند و "
            "مستقیم برای مشتری خودشان فعال می‌کنند.\n\n"
            "برای درخواست، از «🛟 پشتیبانی» پیام بدهید."
        )
        return
    text, kb = panel
    await message.answer(text, reply_markup=kb.as_markup())


@router.callback_query(F.data == "rs:home")
async def cb_home(call: CallbackQuery) -> None:
    panel = await _panel(call.from_user.id)
    if panel is None:
        await call.answer("شما نماینده نیستید", show_alert=True)
        return
    await call.answer()
    text, kb = panel
    try:
        await call.message.edit_text(text, reply_markup=kb.as_markup())
    except Exception:
        await call.message.answer(text, reply_markup=kb.as_markup())


@router.callback_query(F.data.startswith("rs:pick:"))
async def cb_pick(call: CallbackQuery, state: FSMContext) -> None:
    is_reseller, discount = await reseller.profile(call.from_user.id)
    if not is_reseller:
        await call.answer("شما نماینده نیستید", show_alert=True)
        return
    plan = get_plan(call.data.split(":")[2])
    if plan is None:
        await call.answer("این طرح پیدا نشد", show_alert=True)
        return

    price = reseller.discounted(plan.price_toman, discount)
    balance = await wallet.balance(call.from_user.id)
    if balance < price:
        await call.answer(
            f"موجودی کافی نیست. لازم: {toman(price)} — موجودی: {toman(balance)}",
            show_alert=True,
        )
        return

    await call.answer()
    await state.set_state(Flow.reseller_customer)
    await state.update_data(plan_code=plan.code)
    await call.message.answer(
        f"🏪 <b>{plan.title}</b>\n{RULE}\n\n"
        f"قیمت برای شما: <b>{toman(price)}</b> "
        f"(قیمت فهرست: {toman(plan.price_toman)})\n\n"
        "حالا <b>شناسه‌ی عددی</b> مشتری را بفرستید.\n\n"
        "<i>مشتری باید قبلاً ربات را استارت کرده باشد. شناسه‌اش را می‌تواند "
        "با زدن /start و دیدن «حساب کاربری» پیدا کند.</i>\n\n"
        "انصراف: /cancel"
    )


@router.message(Flow.reseller_customer)
async def got_customer(message: Message, state: FSMContext) -> None:
    plan_code = (await state.get_data()).get("plan_code", "")
    customer_id = parse_int(message.text or "")
    if customer_id is None or customer_id <= 0:
        await message.answer(
            "شناسه نامعتبر است. یک عدد بفرستید (مثل <code>123456789</code>) "
            "یا /cancel بزنید."
        )
        return

    try:
        sale = await reseller.activate(message.from_user.id, customer_id, plan_code)
    except reseller.ResellerError as exc:
        await message.answer(f"⚠️ {exc}")
        return
    except Exception:
        log.exception("فعال‌سازی نمایندگی ناموفق بود")
        await message.answer("⚠️ خطای غیرمنتظره. دوباره تلاش کنید.")
        return

    await state.clear()
    plan = get_plan(sale.plan_code)
    title = plan.title if plan else sale.plan_code
    left = await wallet.balance(message.from_user.id)

    await message.answer(
        f"✅ <b>{title}</b> برای کاربر <code>{sale.customer_id}</code> فعال شد.\n\n"
        f"پرداختی شما: <b>{toman(sale.paid_toman)}</b>\n"
        f"موجودی باقی‌مانده: <b>{toman(left)}</b>"
    )

    # مشتری باید بفهمد اشتراکش فعال شده، وگرنه سراغ نماینده را می‌گیرد
    try:
        await message.bot.send_message(
            sale.customer_id,
            f"🎉 اشتراک <b>{title}</b> برای شما فعال شد.\n\n"
            "از «👤 حساب کاربری» می‌توانید مدت و سهمیه‌هایتان را ببینید.",
        )
    except Exception:
        log.debug("اطلاع فعال‌سازی به مشتری نرسید", exc_info=True)

    panel = await _panel(message.from_user.id)
    if panel is not None:
        text, kb = panel
        await message.answer(text, reply_markup=kb.as_markup())


@router.callback_query(F.data == "rs:sales")
async def cb_sales(call: CallbackQuery) -> None:
    is_reseller, _discount = await reseller.profile(call.from_user.id)
    if not is_reseller:
        await call.answer("شما نماینده نیستید", show_alert=True)
        return
    await call.answer()

    rows = await reseller.sales(call.from_user.id)
    lines = ["📜 <b>فروش‌های اخیر شما</b>", RULE, ""]
    if not rows:
        lines.append("هنوز فروشی ثبت نشده است.")
    for sale in rows:
        plan = get_plan(sale.plan_code)
        stamp = sale.created_at.strftime("%m/%d %H:%M")
        lines.append(
            f"• <b>{plan.title if plan else sale.plan_code}</b> → "
            f"<code>{sale.customer_id}</code>\n"
            f"  {toman(sale.paid_toman)} · <code>{fa_num(stamp)}</code>"
        )

    kb = InlineKeyboardBuilder()
    kb.row(InlineKeyboardButton(text="🔙 پنل نمایندگی", callback_data="rs:home"))
    await call.message.edit_text("\n".join(lines), reply_markup=kb.as_markup())


@router.callback_query(F.data == "rs:customers")
async def cb_customers(call: CallbackQuery) -> None:
    """مشتری‌های نماینده، آنکه زودتر تمام می‌شود اول.

    <b>چرا این صفحه هست.</b> نگرانی هر نماینده این است که مشتری‌اش
    مستقیم بیاید و دفعه‌ی بعد از او نخرد. بخشی از جوابش سهمِ خرید
    مستقیم است که خودکار پرداخت می‌شود؛ بخش دیگرش این است که نماینده
    <b>پیش از ما</b> بداند اشتراک چه کسی دارد تمام می‌شود و خودش سراغش
    برود.
    """
    is_reseller, _discount = await reseller.profile(call.from_user.id)
    if not is_reseller:
        await call.answer("شما نماینده نیستید", show_alert=True)
        return
    await call.answer()

    people = await reseller.customers(call.from_user.id)
    lines = ["👥 <b>مشتری‌های شما</b>", RULE, ""]
    if not people:
        lines.append("هنوز مشتری‌ای ندارید.")
    for person in people:
        if person.expired:
            state = "❌ بدون اشتراک"
        elif person.expiring:
            state = f"⏳ {fa_num(person.days_left)} روز مانده"
        else:
            state = f"✅ {fa_num(person.days_left)} روز مانده"
        lines.append(f"• <b>{person.name}</b> — <code>{person.user_id}</code>\n  {state}")

    lines += [
        "",
        RULE,
        "<i>هر خریدی که خودشان مستقیم انجام بدهند هم سهم شما را به کیف "
        "پولتان می‌ریزد — مشتری از دست شما نمی‌رود.</i>",
    ]

    kb = InlineKeyboardBuilder()
    kb.row(InlineKeyboardButton(text="🔙 پنل نمایندگی", callback_data="rs:home"))
    await call.message.edit_text("\n".join(lines), reply_markup=kb.as_markup())
