"""ویرایش طرح‌ها، سقف‌ها و قیمت‌ها از داخل پنل ادمین.

هیچ عددی برای تغییر دادن نیاز به دست زدن به کد یا ری‌استارت ربات ندارد؛
مقدار تازه بلافاصله روی همه‌ی کاربران آن طرح اثر می‌کند.
"""
from __future__ import annotations

import logging

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder

from telkap.handlers.common import Flow, parse_int
from telkap.plans import (
    CREDIT_KINDS,
    DEFAULT_PLANS,
    credit_unit,
    get_plan,
    quota_label,
    toman,
)
from telkap.services import planstore, roles
from telkap.services.planstore import (
    FEATURE_FIELDS,
    NUMERIC_FIELDS,
    PLAN_ONLY_FIELDS,
    field_spec,
)

log = logging.getLogger(__name__)
router = Router(name="admin_plans")

RULE = "━━━━━━━━━━━━━━━━━━"


def _is_admin(user_id: int) -> bool:
    # دسترسی این روتر روی خودِ روتر قفل شده؛ این گارد لایه‌ی دوم است
    return roles.can_cached(user_id, roles.CAP_MONEY)


def _show(spec, plan) -> str:
    """مقدار فعلی یک فیلد، به شکل خوانا."""
    value = getattr(plan, spec.key, None)
    if spec.kind == "text":
        return str(value or "—")[:28]
    if spec.kind == "money":
        return toman(int(value or 0))
    if spec.kind == "quota":
        return quota_label(int(value))
    return quota_label(int(value))


# ------------------------------------------------------------- فهرست
async def _render_home(target: Message, *, edit: bool = True) -> None:
    changed = await planstore.customized_codes()
    lines = [
        "🧩 <b>طرح‌ها و قیمت‌ها</b>",
        RULE,
        "",
        "هر عددی را که اینجا عوض کنید بلافاصله روی همه‌ی کاربران آن طرح "
        "اثر می‌کند — بدون ری‌استارت.",
        "",
        "✏️ کنار طرح یعنی از مقدار پیش‌فرض فاصله گرفته است.",
    ]
    kb = InlineKeyboardBuilder()
    for code, plan in DEFAULT_PLANS.items():
        live = get_plan(code) or plan
        mark = "✏️ " if code in changed else ""
        kb.row(
            InlineKeyboardButton(
                text=f"{mark}{live.title} · {toman(live.price_toman)}",
                callback_data=f"pe:show:{code}",
            )
        )
    kb.row(InlineKeyboardButton(text="🎫 قیمت اعتبارها", callback_data="pe:credits"))
    kb.row(InlineKeyboardButton(text="🔙 بازگشت", callback_data="adm:home"))

    markup = kb.as_markup()
    text = "\n".join(lines)
    if edit:
        try:
            await target.edit_text(text, reply_markup=markup)
            return
        except Exception:
            log.debug("ویرایش فهرست طرح‌ها ناموفق بود", exc_info=True)
    await target.answer(text, reply_markup=markup)


# --------------------------------------------------------- کارت طرح
async def _render_plan(target: Message, code: str, *, edit: bool = True) -> None:
    plan = get_plan(code)
    base = DEFAULT_PLANS.get(code)
    if plan is None or base is None:
        await target.answer("این طرح پیدا نشد.")
        return

    changed = await planstore.customized_codes()
    lines = [
        f"🧩 <b>{plan.title}</b>",
        RULE,
        "",
        f"<i>{plan.tagline}</i>",
        "",
        f"💰 قیمت: <b>{toman(plan.price_toman)}</b>",
        f"📅 مدت: <b>{quota_label(plan.days)} روز</b>",
        "",
        "<b>سقف‌های دوره</b>",
        f"📨 پیام: <b>{plan.messages_label}</b>",
        f"💧 واترمارک: <b>{plan.watermark_label}</b>",
        f"🕓 پیام گذشته: <b>{plan.history_label}</b>",
        f"📋 کار کپی: <b>{quota_label(plan.max_tasks)}</b>",
        f"📤 مقصد هر کار: <b>{quota_label(plan.max_destinations)}</b>",
        f"⚖️ سقف منصفانه‌ی روزانه: <b>{quota_label(plan.fair_use_daily)}</b>",
        "",
        "<b>قابلیت‌ها</b>",
    ]
    for feature, label in FEATURE_FIELDS:
        lines.append(f"{'✅' if plan.has(feature) else '⛔️'} {label}")

    if code in changed:
        lines.append("\n✏️ این طرح از پیش‌فرض فاصله گرفته است.")

    kb = InlineKeyboardBuilder()
    for spec in NUMERIC_FIELDS:
        kb.row(
            InlineKeyboardButton(
                text=f"{spec.label}: {_show(spec, plan)}",
                callback_data=f"pe:set:{code}:{spec.key}",
            )
        )
    for spec in PLAN_ONLY_FIELDS:
        kb.row(
            InlineKeyboardButton(
                text=f"{spec.label}: {_show(spec, plan)}",
                callback_data=f"pe:set:{code}:{spec.key}",
            )
        )
    for feature, label in FEATURE_FIELDS:
        kb.row(
            InlineKeyboardButton(
                text=f"{'✅' if plan.has(feature) else '⛔️'} {label}",
                callback_data=f"pe:feat:{code}:{feature}",
            )
        )
    if code in changed:
        kb.row(
            InlineKeyboardButton(
                text="♻️ بازگردانی به پیش‌فرض", callback_data=f"pe:reset:{code}"
            )
        )
    kb.row(InlineKeyboardButton(text="🔙 فهرست طرح‌ها", callback_data="pe:home"))

    markup = kb.as_markup()
    text = "\n".join(lines)
    if edit:
        try:
            await target.edit_text(text, reply_markup=markup)
            return
        except Exception:
            log.debug("ویرایش کارت طرح ناموفق بود", exc_info=True)
    await target.answer(text, reply_markup=markup)


@router.callback_query(F.data.in_({"adm:plans", "pe:home"}))
async def cb_home(call: CallbackQuery) -> None:
    if not _is_admin(call.from_user.id):
        await call.answer("دسترسی ندارید", show_alert=True)
        return
    await call.answer()
    await _render_home(call.message)


@router.callback_query(F.data.startswith("pe:show:"))
async def cb_show(call: CallbackQuery) -> None:
    if not _is_admin(call.from_user.id):
        await call.answer("دسترسی ندارید", show_alert=True)
        return
    await call.answer()
    await _render_plan(call.message, call.data.split(":")[2])


# ------------------------------------------------------ تغییر مقدار
@router.callback_query(F.data.startswith("pe:set:"))
async def cb_set(call: CallbackQuery, state: FSMContext) -> None:
    if not _is_admin(call.from_user.id):
        await call.answer("دسترسی ندارید", show_alert=True)
        return
    _, _, code, key = call.data.split(":", 3)
    spec = field_spec(key)
    plan = get_plan(code)
    if spec is None or plan is None:
        await call.answer("نامعتبر", show_alert=True)
        return
    await call.answer()
    await state.set_state(Flow.admin_plan_value)
    await state.update_data(plan_code=code, plan_key=key)

    hint = [f"✏️ <b>{spec.label}</b> — {plan.title}", RULE, ""]
    hint.append(f"مقدار فعلی: <b>{_show(spec, plan)}</b>")
    default = getattr(DEFAULT_PLANS[code], key)
    hint.append(
        f"پیش‌فرض کارخانه: <b>"
        f"{default if spec.kind == 'text' else quota_label(int(default))}</b>"
    )
    if spec.hint:
        hint.append(f"\n<i>{spec.hint}</i>")
    if spec.kind == "text":
        hint.append("\nمتن تازه را بفرستید.")
    else:
        hint.append(f"\nعدد تازه را بفرستید (بین {spec.minimum} و {spec.maximum}).")
        if spec.allows_unlimited:
            hint.append("برای «نامحدود» عدد <code>-1</code> بفرستید.")
    hint.append("\nانصراف: /cancel")
    await call.message.answer("\n".join(hint))


@router.message(Flow.admin_plan_value)
async def got_value(message: Message, state: FSMContext) -> None:
    if not _is_admin(message.from_user.id):
        await state.clear()
        return
    data = await state.get_data()
    code, key = data.get("plan_code", ""), data.get("plan_key", "")
    spec = field_spec(key)
    if spec is None:
        await state.clear()
        return

    raw = (message.text or "").strip()
    value = raw if spec.kind == "text" else parse_int(raw)
    if value is None:
        await message.answer("عدد نامعتبر است. دوباره بفرستید یا /cancel بزنید.")
        return

    plan = await planstore.set_field(
        code, key, value, admin_id=message.from_user.id
    )
    if plan is None:
        await message.answer("مقدار پذیرفته نشد. دوباره بفرستید یا /cancel بزنید.")
        return
    await state.clear()
    await message.answer(f"✅ <b>{spec.label}</b> شد: <b>{_show(spec, plan)}</b>")
    await _render_plan(message, code, edit=False)


@router.callback_query(F.data.startswith("pe:feat:"))
async def cb_feature(call: CallbackQuery) -> None:
    if not _is_admin(call.from_user.id):
        await call.answer("دسترسی ندارید", show_alert=True)
        return
    _, _, code, feature = call.data.split(":", 3)
    plan = await planstore.toggle_feature(code, feature, admin_id=call.from_user.id)
    if plan is None:
        await call.answer("نامعتبر", show_alert=True)
        return
    await call.answer("روشن شد" if plan.has(feature) else "خاموش شد")
    await _render_plan(call.message, code)


@router.callback_query(F.data.startswith("pe:reset:"))
async def cb_reset(call: CallbackQuery) -> None:
    if not _is_admin(call.from_user.id):
        await call.answer("دسترسی ندارید", show_alert=True)
        return
    code = call.data.split(":")[2]
    if await planstore.reset(code, admin_id=call.from_user.id) is None:
        await call.answer("نامعتبر", show_alert=True)
        return
    await call.answer("به پیش‌فرض برگشت")
    await _render_plan(call.message, code)


# ----------------------------------------------------- قیمت اعتبارها
async def _render_credits(target: Message, *, edit: bool = True) -> None:
    lines = [
        "🎫 <b>قیمت اعتبارها</b>",
        RULE,
        "",
        "این قیمت‌ها وقتی به کار می‌آیند که سهمیه‌ی طرح کاربر تمام شود و "
        "بخواهد واحدی بخرد.",
        "",
    ]
    kb = InlineKeyboardBuilder()
    for kind, (title, desc, default) in CREDIT_KINDS.items():
        price = credit_unit(kind)
        lines.append(f"<b>{title}</b> — {toman(price)} هر واحد")
        lines.append(f"<i>{desc}</i>")
        if price != default:
            lines.append(f"<i>پیش‌فرض: {toman(default)}</i>")
        lines.append("")
        kb.row(
            InlineKeyboardButton(
                text=f"{title}: {toman(price)}", callback_data=f"pe:cu:{kind}"
            )
        )
    kb.row(InlineKeyboardButton(text="🔙 فهرست طرح‌ها", callback_data="pe:home"))

    markup = kb.as_markup()
    text = "\n".join(lines)
    if edit:
        try:
            await target.edit_text(text, reply_markup=markup)
            return
        except Exception:
            log.debug("ویرایش قیمت اعتبارها ناموفق بود", exc_info=True)
    await target.answer(text, reply_markup=markup)


@router.callback_query(F.data == "pe:credits")
async def cb_credits(call: CallbackQuery) -> None:
    if not _is_admin(call.from_user.id):
        await call.answer("دسترسی ندارید", show_alert=True)
        return
    await call.answer()
    await _render_credits(call.message)


@router.callback_query(F.data.startswith("pe:cu:"))
async def cb_credit_price(call: CallbackQuery, state: FSMContext) -> None:
    if not _is_admin(call.from_user.id):
        await call.answer("دسترسی ندارید", show_alert=True)
        return
    kind = call.data.split(":")[2]
    info = CREDIT_KINDS.get(kind)
    if info is None:
        await call.answer("نامعتبر", show_alert=True)
        return
    await call.answer()
    await state.set_state(Flow.admin_credit_price)
    await state.update_data(credit_kind=kind)
    await call.message.answer(
        f"💰 <b>{info[0]}</b>\n{RULE}\n\n"
        f"قیمت فعلی هر واحد: <b>{toman(credit_unit(kind))}</b>\n"
        f"پیش‌فرض کارخانه: <b>{toman(info[2])}</b>\n\n"
        "قیمت تازه را به تومان بفرستید (مثلاً <code>1500</code>).\n\n"
        "انصراف: /cancel"
    )


@router.message(Flow.admin_credit_price)
async def got_credit_price(message: Message, state: FSMContext) -> None:
    if not _is_admin(message.from_user.id):
        await state.clear()
        return
    kind = (await state.get_data()).get("credit_kind", "")
    price = parse_int(message.text or "")
    if price is None or price < 0:
        await message.answer("عدد نامعتبر است. دوباره بفرستید یا /cancel بزنید.")
        return
    stored = await planstore.set_credit_unit(
        kind, price, admin_id=message.from_user.id
    )
    if stored is None:
        await message.answer("ثبت نشد. دوباره بفرستید یا /cancel بزنید.")
        return
    await state.clear()
    await message.answer(f"✅ قیمت هر واحد شد: <b>{toman(stored)}</b>")
    await _render_credits(message, edit=False)
