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
from telkap.plans import PLANS, toman
from telkap.services import coupons, giftcodes, reminders
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
    winback = await reminders.current_winback()
    kb.row(
        InlineKeyboardButton(
            text=f"↩️ کد بازگشت: {winback or 'ندارد'}", callback_data="cp:winback"
        )
    )
    kb.row(InlineKeyboardButton(text="🎁 کدهای هدیه", callback_data="gf:home"))
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

    if step == "winback":
        raw = (message.text or "").strip()
        code = "" if raw == "-" else raw
        if code and await coupons.find(code) is None:
            await message.answer(
                "چنین کدی وجود ندارد. اول از «➕ کد جدید» بسازیدش، یا "
                "<code>-</code> بفرستید تا برداشته شود."
            )
            return
        stored = await reminders.set_winback(code, admin_id=message.from_user.id)
        await state.clear()
        await message.answer(
            f"✅ کد بازگشت شد: <b>{stored}</b>"
            if stored
            else "✅ کد بازگشت برداشته شد."
        )
        await _render_list(message, edit=False)
        return

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


# ------------------------------------------------- کد تخفیف بازگشت
@router.callback_query(F.data == "cp:winback")
async def cb_winback(call: CallbackQuery, state: FSMContext) -> None:
    if not _is_admin(call.from_user.id):
        await call.answer("دسترسی ندارید", show_alert=True)
        return
    await call.answer()
    await state.set_state(Flow.admin_coupon)
    await state.update_data(step="winback")
    current = await reminders.current_winback()
    await call.message.answer(
        "↩️ <b>کد تخفیف بازگشت</b>\n" + RULE + "\n\n"
        f"کد فعلی: <b>{current or 'ندارد'}</b>\n\n"
        "این کد به یادآوری‌های انقضای اشتراک پیوست می‌شود. یادآوری خالی "
        "فقط می‌گوید «تمام شد»؛ یک کد تخفیف همان پیام را به دلیلی برای "
        "برگشتن تبدیل می‌کند.\n\n"
        "کد را بفرستید، یا برای برداشتن <code>-</code> بفرستید.\n\n"
        "انصراف: /cancel"
    )


# ------------------------------------------------------- کدهای هدیه
@router.callback_query(F.data == "gf:home")
async def cb_gifts(call: CallbackQuery) -> None:
    if not _is_admin(call.from_user.id):
        await call.answer("دسترسی ندارید", show_alert=True)
        return
    await call.answer()
    rows = await giftcodes.batches()

    lines = [
        "🎁 <b>کدهای هدیه و پیش‌فروش</b>",
        RULE,
        "",
        "<i>برخلاف کد تخفیف که روی قیمت اثر می‌گذارد، کد هدیه خودش اشتراک "
        "است: کاربر واردش می‌کند و طرح بدون پرداخت فعال می‌شود.</i>",
        "",
    ]
    if not rows:
        lines.append("هنوز کدی ساخته نشده است.")
    kb = InlineKeyboardBuilder()
    for batch, plan_code, total, used in rows:
        plan = PLANS.get(plan_code)
        lines.append(
            f"• <code>{batch}</code> — {plan.title if plan else plan_code}: "
            f"<b>{fa_num(used)}/{fa_num(total)}</b> استفاده شده"
        )
        if used < total:
            kb.row(
                InlineKeyboardButton(
                    text=f"📄 کدهای نخورده‌ی {batch}", callback_data=f"gf:list:{batch}"
                )
            )
    kb.row(InlineKeyboardButton(text="➕ ساخت کد هدیه", callback_data="gf:new"))
    kb.row(InlineKeyboardButton(text="🔙 کدهای تخفیف", callback_data="adm:coupons"))
    await call.message.edit_text("\n".join(lines), reply_markup=kb.as_markup())


@router.callback_query(F.data.startswith("gf:list:"))
async def cb_gift_list(call: CallbackQuery) -> None:
    if not _is_admin(call.from_user.id):
        await call.answer("دسترسی ندارید", show_alert=True)
        return
    batch = call.data.split(":", 2)[2]
    codes = await giftcodes.unused_codes(batch)
    await call.answer()
    body = "\n".join(f"<code>{code}</code>" for code in codes) or "کد نخورده‌ای نیست."
    kb = InlineKeyboardBuilder()
    kb.row(InlineKeyboardButton(text="🔙 کدهای هدیه", callback_data="gf:home"))
    await call.message.answer(
        f"🎁 <b>کدهای استفاده‌نشده‌ی دسته {batch}</b>\n\n{body}",
        reply_markup=kb.as_markup(),
    )


@router.callback_query(F.data == "gf:new")
async def cb_gift_new(call: CallbackQuery) -> None:
    if not _is_admin(call.from_user.id):
        await call.answer("دسترسی ندارید", show_alert=True)
        return
    await call.answer()
    kb = InlineKeyboardBuilder()
    for code, plan in PLANS.items():
        kb.row(
            InlineKeyboardButton(text=plan.title, callback_data=f"gf:plan:{code}")
        )
    kb.row(InlineKeyboardButton(text="🔙 بازگشت", callback_data="gf:home"))
    await call.message.answer(
        "🎁 برای کدام طرح کد ساخته شود؟", reply_markup=kb.as_markup()
    )


@router.callback_query(F.data.startswith("gf:plan:"))
async def cb_gift_plan(call: CallbackQuery, state: FSMContext) -> None:
    if not _is_admin(call.from_user.id):
        await call.answer("دسترسی ندارید", show_alert=True)
        return
    plan_code = call.data.split(":")[2]
    if plan_code not in PLANS:
        await call.answer("نامعتبر", show_alert=True)
        return
    await call.answer()
    await state.set_state(Flow.admin_gift)
    await state.update_data(plan_code=plan_code)
    await call.message.answer(
        f"🎁 <b>{PLANS[plan_code].title}</b>\n\n"
        f"چند کد ساخته شود؟ (۱ تا {fa_num(giftcodes.MAX_BATCH)})\n\n"
        "انصراف: /cancel"
    )


@router.message(Flow.admin_gift)
async def got_gift_count(message: Message, state: FSMContext) -> None:
    if not _is_admin(message.from_user.id):
        await state.clear()
        return
    plan_code = (await state.get_data()).get("plan_code", "")
    count = parse_int(message.text or "")
    if count is None:
        await message.answer("یک عدد بفرستید یا /cancel بزنید.")
        return

    made = await giftcodes.generate(plan_code, count, admin_id=message.from_user.id)
    if isinstance(made, str):
        await message.answer(f"⚠️ {made}")
        return

    await state.clear()
    body = "\n".join(f"<code>{gift.code}</code>" for gift in made)
    await message.answer(
        f"✅ <b>{fa_num(len(made))}</b> کد ساخته شد "
        f"(دسته <code>{made[0].batch}</code>):\n\n{body}\n\n"
        "<i>هر کد یک بار مصرف می‌شود. کاربر از «👛 کیف پول و دعوت» ← "
        "«🎁 کد هدیه دارم» واردش می‌کند.</i>"
    )
