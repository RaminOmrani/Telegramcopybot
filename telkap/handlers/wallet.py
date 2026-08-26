"""کیف پول کاربر و صفحه‌ی دعوت دوستان."""
from __future__ import annotations

import logging
from urllib.parse import quote

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder
from sqlalchemy import select

from telkap.db import get_session
from telkap.handlers.common import Flow, get_or_create_user
from telkap.keyboards import menu_texts
from telkap.models import PaymentRequest
from telkap.plans import toman
from telkap.services import (
    giftcodes,
    payments,
    referral,
    renewal,
    reseller,
    wallet,
)
from telkap.texts import fa_num

log = logging.getLogger(__name__)
router = Router(name="wallet")

RULE = "━━━━━━━━━━━━━━━━━━"


def _menu(
    has_history: bool, is_reseller: bool = False, auto_renew: bool = False
) -> InlineKeyboardBuilder:
    kb = InlineKeyboardBuilder()
    if is_reseller:
        kb.row(InlineKeyboardButton(text="🏪 پنل نمایندگی", callback_data="rs:home"))
    kb.row(InlineKeyboardButton(text="🎁 دعوت دوستان", callback_data="wal:invite"))
    if has_history:
        kb.row(InlineKeyboardButton(text="📜 تاریخچه تراکنش‌ها", callback_data="wal:history"))
    kb.row(InlineKeyboardButton(text="🎁 کد هدیه دارم", callback_data="wal:gift"))
    kb.row(
        InlineKeyboardButton(
            text=("🔄 تمدید خودکار: روشن" if auto_renew else "🔄 تمدید خودکار: خاموش"),
            callback_data="wal:renew",
        )
    )
    kb.row(
        InlineKeyboardButton(text="🧾 صورتحساب‌ها", callback_data="wal:bills"),
        InlineKeyboardButton(text="💳 خرید اشتراک", callback_data="credit:plans"),
    )
    return kb


async def _wallet_text(user_id: int) -> tuple[str, InlineKeyboardBuilder]:
    balance = await wallet.balance(user_id)
    entries = await wallet.history(user_id, limit=1)
    stats = await referral.stats(user_id)

    lines = [
        "👛 <b>کیف پول</b>",
        RULE,
        "",
        f"موجودی شما: <b>{toman(balance)}</b>",
        "",
    ]
    if balance:
        lines.append(
            "می‌توانید همین موجودی را خرج خرید اشتراک یا اعتبار کنید — "
            "هنگام خرید، گزینه‌ی پرداخت از کیف پول نمایش داده می‌شود."
        )
    else:
        lines.append(
            "کیف پولتان خالی است. با دعوت دوستان پُرش کنید: از هر خریدی که "
            "دوستانتان بزنند، سهمی به شما می‌رسد."
        )
    if stats.earned:
        lines += ["", f"🎁 تا امروز از دعوت: <b>{toman(stats.earned)}</b>"]

    is_reseller, discount = await reseller.profile(user_id)
    if is_reseller:
        lines += [
            "",
            f"🏪 شما نماینده‌اید — <b>{fa_num(discount)}٪</b> تخفیف روی همه‌ی طرح‌ها.",
        ]

    auto_renew = await renewal.is_on(user_id)
    if auto_renew:
        lines += [
            "",
            "🔄 <b>تمدید خودکار روشن است</b> — پیش از پایان اشتراک، هزینه از "
            "همین موجودی برداشت می‌شود.",
        ]

    return "\n".join(lines), _menu(bool(entries), is_reseller, auto_renew)


@router.message(Command("wallet"))
@router.message(F.text.in_(menu_texts("menu.wallet")))
async def cmd_wallet(message: Message) -> None:
    await get_or_create_user(message.from_user)
    text, kb = await _wallet_text(message.from_user.id)
    await message.answer(text, reply_markup=kb.as_markup())


@router.callback_query(F.data == "wal:home")
async def cb_home(call: CallbackQuery) -> None:
    await call.answer()
    text, kb = await _wallet_text(call.from_user.id)
    try:
        await call.message.edit_text(text, reply_markup=kb.as_markup())
    except Exception:
        await call.message.answer(text, reply_markup=kb.as_markup())


@router.callback_query(F.data == "wal:history")
async def cb_history(call: CallbackQuery) -> None:
    await call.answer()
    entries = await wallet.history(call.from_user.id, limit=15)
    lines = ["📜 <b>تاریخچه کیف پول</b>", RULE, ""]
    if not entries:
        lines.append("هنوز تراکنشی ثبت نشده است.")
    for entry in entries:
        sign = "+" if entry.amount_toman > 0 else "−"
        stamp = entry.created_at.strftime("%m/%d %H:%M")
        lines.append(
            f"{sign} <b>{toman(abs(entry.amount_toman))}</b> · "
            f"{wallet.reason_label(entry.reason)}\n"
            f"<code>{fa_num(stamp)}</code> — مانده: {toman(entry.balance_after)}"
        )
        if entry.note:
            lines.append(f"<i>{entry.note}</i>")
        lines.append("")

    kb = InlineKeyboardBuilder()
    kb.row(InlineKeyboardButton(text="🔙 کیف پول", callback_data="wal:home"))
    await call.message.edit_text("\n".join(lines), reply_markup=kb.as_markup())


@router.callback_query(F.data == "wal:renew")
async def cb_toggle_renew(call: CallbackQuery) -> None:
    """تمدید خودکار — عمداً یک کلید صریح، نه چیزی که پنهانی روشن باشد."""
    state = await renewal.toggle(call.from_user.id)
    if state is None:
        await call.answer("کاربر پیدا نشد", show_alert=True)
        return
    await call.answer(
        "تمدید خودکار روشن شد ✅" if state else "تمدید خودکار خاموش شد"
    )
    if state:
        await call.message.answer(
            "🔄 <b>تمدید خودکار روشن شد.</b>\n\n"
            "تا ۲۴ ساعت مانده به پایان اشتراک، هزینه‌ی همان طرح از کیف "
            "پولتان برداشت و اشتراک تمدید می‌شود.\n\n"
            "<i>اگر موجودی کافی نباشد چیزی برداشت نمی‌شود و فقط به شما خبر "
            "می‌رسد. هر وقت خواستید می‌توانید خاموشش کنید.</i>"
        )
    text, kb = await _wallet_text(call.from_user.id)
    try:
        await call.message.edit_text(text, reply_markup=kb.as_markup())
    except Exception:
        log.debug("به‌روزرسانی کیف پول ناموفق بود", exc_info=True)


@router.callback_query(F.data == "wal:gift")
async def cb_gift_ask(call: CallbackQuery, state: FSMContext) -> None:
    await call.answer()
    await state.set_state(Flow.gift_code)
    await call.message.answer(
        "🎁 <b>کد هدیه</b>\n\n"
        "کد را بفرستید تا اشتراکش برایتان فعال شود.\n\n"
        "<i>حروف کوچک و بزرگ و خط تیره فرقی نمی‌کند.</i>\n\n"
        "انصراف: /cancel"
    )


@router.message(Flow.gift_code)
async def got_gift(message: Message, state: FSMContext) -> None:
    await get_or_create_user(message.from_user)
    try:
        plan, _sub = await giftcodes.redeem(message.from_user.id, message.text or "")
    except giftcodes.GiftError as exc:
        await message.answer(f"⚠️ {exc}")
        return
    except Exception:
        log.exception("مصرف کد هدیه ناموفق بود")
        await message.answer("⚠️ خطای غیرمنتظره. دوباره تلاش کنید.")
        return

    await state.clear()
    await message.answer(
        f"🎉 <b>{plan.title}</b> برای شما فعال شد!\n\n"
        f"مدت: {fa_num(plan.days)} روز\n\n"
        "از «👤 حساب کاربری» می‌توانید سهمیه‌هایتان را ببینید."
    )


@router.callback_query(F.data == "wal:bills")
async def cb_bills(call: CallbackQuery) -> None:
    """تاریخچه‌ی پرداخت‌ها با ریز هر صورتحساب."""
    await call.answer()
    async with get_session() as db:
        rows = await db.execute(
            select(PaymentRequest)
            .where(
                PaymentRequest.user_id == call.from_user.id,
                PaymentRequest.status != PaymentRequest.STATUS_PENDING,
            )
            .order_by(PaymentRequest.id.desc())
            .limit(12)
        )
        bills = list(rows.scalars())

    lines = ["🧾 <b>صورتحساب‌ها</b>", RULE, ""]
    if not bills:
        lines.append("هنوز خرید تأییدشده‌ای ندارید.")
    for bill in bills:
        approved = bill.status == PaymentRequest.STATUS_APPROVED
        icon = "✅" if approved else "❌"
        stamp = bill.created_at.strftime("%Y/%m/%d")
        title = payments.describe(bill)
        lines.append(f"{icon} <b>#{fa_num(bill.id)}</b> — {title}")
        lines.append(f"<code>{fa_num(stamp)}</code>")

        listed = int(bill.list_toman or 0)
        if listed and listed != bill.amount_toman:
            lines.append(f"   قیمت طرح: {toman(listed)}")
            if bill.credit_toman:
                lines.append(f"   اعتبار اشتراک قبلی: −{toman(bill.credit_toman)}")
            if bill.discount_toman:
                code = f" ({bill.coupon_code})" if bill.coupon_code else ""
                lines.append(f"   تخفیف{code}: −{toman(bill.discount_toman)}")
        lines.append(f"   <b>پرداختی: {toman(bill.amount_toman)}</b>")
        lines.append("")

    kb = InlineKeyboardBuilder()
    kb.row(InlineKeyboardButton(text="🔙 کیف پول", callback_data="wal:home"))
    await call.message.edit_text("\n".join(lines), reply_markup=kb.as_markup())


@router.callback_query(F.data == "wal:invite")
async def cb_invite(call: CallbackQuery) -> None:
    await call.answer()
    cfg = await referral.settings()
    stats = await referral.stats(call.from_user.id)

    me = await call.bot.get_me()
    link = referral.link_for(me.username or "", call.from_user.id)

    lines = ["🎁 <b>دعوت دوستان</b>", RULE, ""]
    if not cfg.get("enabled"):
        lines.append("برنامه‌ی دعوت فعلاً غیرفعال است. بعداً سر بزنید.")
    else:
        lines += [
            f"به ازای <b>{referral.describe(cfg)}</b> به کیف پولتان اضافه می‌شود.",
            "",
            "لینک اختصاصی شما:",
            f"<code>{link}</code>",
            "",
        ]
        minimum = int(cfg.get("min_purchase_toman") or 0)
        if minimum:
            lines.append(
                f"<i>پاداش وقتی واریز می‌شود که خرید دوستتان دست‌کم "
                f"{toman(minimum)} باشد و تأیید شود.</i>"
            )
        if cfg.get("first_purchase_only"):
            lines.append("<i>پاداش فقط برای اولین خرید هر نفر است.</i>")
        discount = int(cfg.get("invitee_discount_percent") or 0)
        if discount:
            lines.append(
                f"<i>دوستتان هم {fa_num(discount)}٪ تخفیف اولین خرید می‌گیرد — "
                "پس دعوت برای هر دو سود دارد.</i>"
            )
        lines += [
            "",
            "<b>آمار شما</b>",
            f"دعوت‌شده: <b>{fa_num(stats.invited)}</b> نفر",
            f"از این‌ها خرید کرده: <b>{fa_num(stats.buyers)}</b> نفر",
            f"مجموع دریافتی: <b>{toman(stats.earned)}</b>",
        ]

    kb = InlineKeyboardBuilder()
    if cfg.get("enabled"):
        share_text = quote(
            "با این ربات پست‌های هر کانالی را خودکار در کانال خودت منتشر کن 👇"
        )
        kb.row(
            InlineKeyboardButton(
                text="📤 فرستادن لینک به دوستان",
                url=f"https://t.me/share/url?url={quote(link)}&text={share_text}",
            )
        )
    kb.row(InlineKeyboardButton(text="🔙 کیف پول", callback_data="wal:home"))
    await call.message.edit_text("\n".join(lines), reply_markup=kb.as_markup())
