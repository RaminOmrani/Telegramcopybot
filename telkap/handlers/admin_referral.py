"""تنظیم برنامه‌ی دعوت دوستان از پنل ادمین.

همه‌ی کلیدها از `referral.FIELDS` ساخته می‌شوند، پس افزودن یک تنظیم تازه
فقط یک سطر در آن جدول است و اینجا دست نمی‌خورد.
"""
from __future__ import annotations

import logging

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder

from telkap.config import get_settings
from telkap.handlers.common import Flow, parse_int
from telkap.plans import toman
from telkap.services import referral
from telkap.texts import fa_num

log = logging.getLogger(__name__)
router = Router(name="admin_referral")

RULE = "━━━━━━━━━━━━━━━━━━"


def _is_admin(user_id: int) -> bool:
    return get_settings().is_admin(user_id)


def _shown(key: str, kind: str, cfg: dict) -> str:
    value = cfg.get(key)
    if kind == "bool":
        return "روشن ✅" if value else "خاموش ⛔️"
    if kind == "mode":
        return "درصدی" if value == "percent" else "مبلغ ثابت"
    number = int(value or 0)
    if key in {"value", "invitee_discount_percent"}:
        if key == "value" and cfg.get("mode") == "percent":
            return f"{fa_num(number)}٪"
        if key == "invitee_discount_percent":
            return f"{fa_num(number)}٪" if number else "ندارد"
    return toman(number) if number else "بی‌سقف" if "cap" in key or "max" in key else toman(0)


async def _render(target: Message, *, edit: bool = True) -> None:
    cfg = await referral.settings()

    lines = [
        "🎁 <b>برنامه‌ی دعوت دوستان</b>",
        RULE,
        "",
        f"وضعیت: <b>{'فعال ✅' if cfg.get('enabled') else 'غیرفعال ⛔️'}</b>",
        f"پاداش: <b>{referral.describe(cfg)}</b>",
        "",
        "<i>پاداش هنگام ثبت‌نام پرداخت نمی‌شود — فقط وقتی خرید کاربر "
        "دعوت‌شده را تأیید کنید. این تنها سد مؤثر در برابر فارم اکانت است.</i>",
        "",
    ]

    kb = InlineKeyboardBuilder()
    for key, label, kind, _hint in referral.FIELDS:
        kb.row(
            InlineKeyboardButton(
                text=f"{label}: {_shown(key, kind, cfg)}",
                callback_data=f"rf:set:{key}",
            )
        )
    kb.row(InlineKeyboardButton(text="🔙 بازگشت", callback_data="adm:home"))

    markup = kb.as_markup()
    text = "\n".join(lines)
    if edit:
        try:
            await target.edit_text(text, reply_markup=markup)
            return
        except Exception:
            log.debug("ویرایش صفحه‌ی دعوت ناموفق بود", exc_info=True)
    await target.answer(text, reply_markup=markup)


@router.callback_query(F.data == "adm:ref")
async def cb_open(call: CallbackQuery) -> None:
    if not _is_admin(call.from_user.id):
        await call.answer("دسترسی ندارید", show_alert=True)
        return
    await call.answer()
    await _render(call.message)


@router.callback_query(F.data.startswith("rf:set:"))
async def cb_set(call: CallbackQuery, state: FSMContext) -> None:
    if not _is_admin(call.from_user.id):
        await call.answer("دسترسی ندارید", show_alert=True)
        return
    key = call.data.split(":", 2)[2]
    field = next((f for f in referral.FIELDS if f[0] == key), None)
    if field is None:
        await call.answer("نامعتبر", show_alert=True)
        return
    _key, label, kind, hint = field
    cfg = await referral.settings()

    # کلیدهای دوحالته و سه‌حالته با یک زدن عوض می‌شوند، عدد پرسیده می‌شود
    if kind == "bool":
        await referral.set_value(key, not cfg.get(key), admin_id=call.from_user.id)
        await call.answer("عوض شد")
        await _render(call.message)
        return
    if kind == "mode":
        new_mode = "fixed" if cfg.get("mode") == "percent" else "percent"
        await referral.set_value(key, new_mode, admin_id=call.from_user.id)
        await call.answer("درصدی" if new_mode == "percent" else "مبلغ ثابت")
        await _render(call.message)
        return

    await call.answer()
    await state.set_state(Flow.admin_referral_value)
    await state.update_data(ref_key=key)
    unit = (
        "درصد (۰ تا ۱۰۰)"
        if key == "invitee_discount_percent"
        or (key == "value" and cfg.get("mode") == "percent")
        else "تومان"
    )
    await call.message.answer(
        f"✏️ <b>{label}</b>\n{RULE}\n\n"
        f"مقدار فعلی: <b>{_shown(key, kind, cfg)}</b>\n\n"
        f"<i>{hint}</i>\n\n"
        f"عدد تازه را بر حسب {unit} بفرستید.\n\nانصراف: /cancel"
    )


@router.message(Flow.admin_referral_value)
async def got_value(message: Message, state: FSMContext) -> None:
    if not _is_admin(message.from_user.id):
        await state.clear()
        return
    key = (await state.get_data()).get("ref_key", "")
    value = parse_int(message.text or "")
    if value is None or value < 0:
        await message.answer("عدد نامعتبر است. دوباره بفرستید یا /cancel بزنید.")
        return
    if await referral.set_value(key, value, admin_id=message.from_user.id) is None:
        await message.answer("ثبت نشد. دوباره بفرستید یا /cancel بزنید.")
        return
    await state.clear()
    await message.answer("✅ ثبت شد.")
    await _render(message, edit=False)
