"""کیف پول کاربر و صفحه‌ی دعوت دوستان."""
from __future__ import annotations

import logging
from urllib.parse import quote

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, InlineKeyboardButton, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder

from telkap.handlers.common import get_or_create_user
from telkap.keyboards import BTN_WALLET
from telkap.plans import toman
from telkap.services import referral, reseller, wallet
from telkap.texts import fa_num

log = logging.getLogger(__name__)
router = Router(name="wallet")

RULE = "━━━━━━━━━━━━━━━━━━"


def _menu(has_history: bool, is_reseller: bool = False) -> InlineKeyboardBuilder:
    kb = InlineKeyboardBuilder()
    if is_reseller:
        kb.row(InlineKeyboardButton(text="🏪 پنل نمایندگی", callback_data="rs:home"))
    kb.row(InlineKeyboardButton(text="🎁 دعوت دوستان", callback_data="wal:invite"))
    if has_history:
        kb.row(InlineKeyboardButton(text="📜 تاریخچه تراکنش‌ها", callback_data="wal:history"))
    kb.row(InlineKeyboardButton(text="💳 خرید اشتراک", callback_data="credit:plans"))
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

    return "\n".join(lines), _menu(bool(entries), is_reseller)


@router.message(Command("wallet"))
@router.message(F.text == BTN_WALLET)
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
