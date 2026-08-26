"""ساخت و مدیریت کدهای تخفیف از پنل ادمین."""
from __future__ import annotations

import logging

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder

from telkap.config import get_settings
from telkap.handlers.common import Flow, parse_int
from telkap.models import Coupon
from telkap.plans import toman
from telkap.services import coupons
from telkap.texts import fa_num

log = logging.getLogger(__name__)
router = Router(name="admin_coupons")

RULE = "━━━━━━━━━━━━━━━━━━"


def _is_admin(user_id: int) -> bool:
    return get_settings().is_admin(user_id)


async def _render_list(target: Message, *, edit: bool = True) -> None:
    rows = await coupons.all_coupons()
    lines = [
        "🎟 <b>کدهای تخفیف</b>",
        RULE,
        "",
    ]
    if not rows:
        lines.append("هنوز کدی ساخته نشده است.")
    else:
        lines.append("روی هر کد بزنید تا جزئیات و تنظیماتش را ببینید.")

    kb = InlineKeyboardBuilder()
    for coupon in rows:
        mark = "🟢" if coupon.enabled else "🔴"
        kb.row(
            InlineKeyboardButton(
                text=f"{mark} {coupon.code} · {coupons.describe(coupon)}",
                callback_data=f"cp:show:{coupon.id}",
            )
        )
    kb.row(InlineKeyboardButton(text="➕ کد جدید", callback_data="cp:new"))
    kb.row(InlineKeyboardButton(text="🔙 بازگشت", callback_data="adm:home"))

    markup = kb.as_markup()
    text = "\n".join(lines)
    if edit:
        try:
            await target.edit_text(text, reply_markup=markup)
            return
        except Exception:
            log.debug("ویرایش فهرست کدها ناموفق بود", exc_info=True)
    await target.answer(text, reply_markup=markup)


@router.callback_query(F.data == "adm:coupons")
async def cb_open(call: CallbackQuery) -> None:
    if not _is_admin(call.from_user.id):
        await call.answer("دسترسی ندارید", show_alert=True)
        return
    await call.answer()
    await _render_list(call.message)


@router.callback_query(F.data.startswith("cp:show:"))
async def cb_show(call: CallbackQuery) -> None:
    if not _is_admin(call.from_user.id):
        await call.answer("دسترسی ندارید", show_alert=True)
        return
    coupon_id = int(call.data.split(":")[2])
    rows = await coupons.all_coupons(limit=200)
    coupon = next((c for c in rows if c.id == coupon_id), None)
    if coupon is None:
        await call.answer("این کد پیدا نشد", show_alert=True)
        return
    await call.answer()

    count, total = await coupons.usage(coupon_id)
    amount = (
        f"{fa_num(coupon.value)}٪"
        if coupon.kind == Coupon.KIND_PERCENT
        else toman(coupon.value)
    )
    lines = [
        f"🎟 <code>{coupon.code}</code>",
        RULE,
        "",
        f"تخفیف: <b>{amount}</b>",
        f"وضعیت: {'🟢 فعال' if coupon.enabled else '🔴 خاموش'}",
        f"سقف استفاده: {fa_num(coupon.max_uses) if coupon.max_uses else 'بی‌نهایت'}",
        f"هر کاربر: {fa_num(coupon.per_user_limit) if coupon.per_user_limit else 'بی‌نهایت'} بار",
        f"حداقل خرید: {toman(coupon.min_toman) if coupon.min_toman else 'ندارد'}",
        f"طرح‌ها: {'، '.join(coupon.plan_codes) if coupon.plan_codes else 'همه'}",
        f"انقضا: {f'{coupon.expires_at:%Y-%m-%d}' if coupon.expires_at else 'ندارد'}",
        "",
        f"<b>استفاده شده: {fa_num(count)} بار · {toman(total)} تخفیف داده شده</b>",
    ]
    if coupon.note:
        lines += ["", f"<i>{coupon.note}</i>"]

    kb = InlineKeyboardBuilder()
    kb.row(
        InlineKeyboardButton(
            text="🔴 خاموش کن" if coupon.enabled else "🟢 روشن کن",
            callback_data=f"cp:toggle:{coupon.id}",
        ),
        InlineKeyboardButton(text="🗑 حذف", callback_data=f"cp:del:{coupon.id}"),
    )
    kb.row(InlineKeyboardButton(text="🔙 فهرست کدها", callback_data="adm:coupons"))
    await call.message.edit_text("\n".join(lines), reply_markup=kb.as_markup())


@router.callback_query(F.data.startswith("cp:toggle:"))
async def cb_toggle(call: CallbackQuery) -> None:
    if not _is_admin(call.from_user.id):
        await call.answer("دسترسی ندارید", show_alert=True)
        return
    state = await coupons.toggle(int(call.data.split(":")[2]))
    await call.answer("روشن شد" if state else "خاموش شد")
    await _render_list(call.message)


@router.callback_query(F.data.startswith("cp:del:"))
async def cb_delete(call: CallbackQuery) -> None:
    if not _is_admin(call.from_user.id):
        await call.answer("دسترسی ندارید", show_alert=True)
        return
    await coupons.remove(int(call.data.split(":")[2]))
    await call.answer("حذف شد")
    await _render_list(call.message)


# ------------------------------------------------------------ ساخت کد
@router.callback_query(F.data == "cp:new")
async def cb_new(call: CallbackQuery, state: FSMContext) -> None:
    if not _is_admin(call.from_user.id):
        await call.answer("دسترسی ندارید", show_alert=True)
        return
    await call.answer()
    await state.set_state(Flow.admin_coupon)
    await state.update_data(step="code")
    await call.message.answer(
        "🎟 <b>کد تخفیف تازه</b>\n" + RULE + "\n\n"
        "<b>گام ۱ از ۳:</b> خود کد را بفرستید.\n\n"
        "مثال: <code>NOWRUZ</code> یا <code>BACK20</code>\n"
        "<i>فقط حرف انگلیسی، عدد، خط تیره و زیرخط. حروف کوچک خودکار بزرگ "
        "می‌شوند تا کاربر بابت شکل نوشتن گیر نکند.</i>\n\n"
        "انصراف: /cancel"
    )


@router.callback_query(F.data.startswith("cp:kind:"))
async def cb_kind(call: CallbackQuery, state: FSMContext) -> None:
    if not _is_admin(call.from_user.id):
        await call.answer("دسترسی ندارید", show_alert=True)
        return
    kind = call.data.split(":")[2]
    if kind not in {Coupon.KIND_PERCENT, Coupon.KIND_FIXED}:
        await call.answer("نامعتبر", show_alert=True)
        return
    await call.answer()
    await state.set_state(Flow.admin_coupon)
    await state.update_data(step="value", kind=kind)
    unit = "درصد (مثلاً <code>۲۰</code>)" if kind == Coupon.KIND_PERCENT else (
        "مبلغ به تومان (مثلاً <code>۵۰۰۰۰</code>)"
    )
    await call.message.answer(
        f"<b>گام ۳ از ۳:</b> مقدار تخفیف را بفرستید — {unit}\n\nانصراف: /cancel"
    )


@router.message(Flow.admin_coupon)
async def got_coupon_step(message: Message, state: FSMContext) -> None:
    if not _is_admin(message.from_user.id):
        await state.clear()
        return
    data = await state.get_data()
    step = data.get("step", "code")

    if step == "code":
        code = coupons.normalize(message.text or "")
        if not code:
            await message.answer("کد خالی است. دوباره بفرستید یا /cancel بزنید.")
            return
        if await coupons.find(code) is not None:
            await message.answer("کدی با همین نام از قبل هست. یکی دیگر بفرستید.")
            return
        await state.update_data(code=code, step="kind")

        kb = InlineKeyboardBuilder()
        kb.row(
            InlineKeyboardButton(text="٪ درصدی", callback_data="cp:kind:percent"),
            InlineKeyboardButton(text="💰 مبلغ ثابت", callback_data="cp:kind:fixed"),
        )
        await message.answer(
            f"کد: <code>{code}</code>\n\n<b>گام ۲ از ۳:</b> نوع تخفیف؟",
            reply_markup=kb.as_markup(),
        )
        return

    if step != "value":
        await state.clear()
        return

    value = parse_int(message.text or "")
    if value is None or value <= 0:
        await message.answer("عدد نامعتبر است. دوباره بفرستید یا /cancel بزنید.")
        return

    result = await coupons.create(
        data.get("code", ""),
        data.get("kind", Coupon.KIND_PERCENT),
        value,
        admin_id=message.from_user.id,
    )
    if isinstance(result, str):
        await message.answer(f"⚠️ {result}")
        return

    await state.clear()
    await message.answer(
        f"✅ کد <code>{result.code}</code> ساخته شد.\n\n"
        "<i>پیش‌فرض: بدون سقف کل، هر کاربر یک بار، روی همه‌ی طرح‌ها، بدون "
        "انقضا. اگر می‌خواهید محدودش کنید، از کارت خودِ کد اقدام کنید.</i>"
    )
    await _render_list(message, edit=False)
