"""سقف‌ها و قابلیت‌های اختصاصیِ یک کاربر خاص.

ادمین می‌تواند برای هر کاربر جداگانه هر سقفی را کم و زیاد کند یا هر
قابلیتی را روشن و خاموش کند، بدون اینکه طرحش عوض شود یا بقیه‌ی کاربران آن
طرح تأثیر بگیرند. هر مقداری که دست نخورد، از طرح می‌آید.
"""
from __future__ import annotations

import logging

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder

from telkap.config import get_settings
from telkap.handlers.common import Flow, parse_int
from telkap.plans import get_plan, quota_label
from telkap.services import limits
from telkap.services.limits import USER_FIELDS, feature_key
from telkap.services.planstore import FEATURE_FIELDS, field_spec
from telkap.services.subscription import active_plan_for, active_subscription

log = logging.getLogger(__name__)
router = Router(name="admin_limits")

RULE = "━━━━━━━━━━━━━━━━━━"


def _is_admin(user_id: int) -> bool:
    return get_settings().is_admin(user_id)


async def _guard(call: CallbackQuery) -> bool:
    if _is_admin(call.from_user.id):
        return True
    await call.answer("دسترسی ندارید", show_alert=True)
    return False


async def _base_plan(user_id: int):
    """طرح کاربر بدون تغییرهای اختصاصی‌اش."""
    sub = await active_subscription(user_id)
    return get_plan(sub.plan_code) if sub is not None else None


async def _render(
    target: Message, uid: int, flt: str, page: int, *, edit: bool = True
) -> None:
    """صفحه‌ی «سقف‌های اختصاصی» یک کاربر."""
    plan = await active_plan_for(uid)          # طرح، با تغییرهای همین کاربر
    stored = await limits.get(uid)
    ctx = f"{flt}:{page}"

    lines = [
        f"🎛 <b>سقف‌های اختصاصی</b> — <code>{uid}</code>",
        RULE,
        "",
    ]
    if plan is None:
        lines.append(
            "این کاربر اشتراک فعالی ندارد. اول از کارتش یک طرح بدهید تا "
            "معلوم شود سقف‌ها روی چه چیزی سوار می‌شوند.\n"
        )
    else:
        lines.append(f"طرح فعال: <b>{plan.title}</b>")
        lines.append(
            "هر مقداری که دست نخورده باشد از طرح می‌آید. ✏️ یعنی برای این "
            "کاربر دستی تعیین شده.\n"
        )

    kb = InlineKeyboardBuilder()
    for spec in USER_FIELDS:
        custom = spec.key in stored
        value = spec.clean(stored[spec.key]) if custom else (
            getattr(plan, spec.key, None) if plan else None
        )
        shown = quota_label(int(value)) if value is not None else "—"
        mark = "✏️ " if custom else ""
        lines.append(f"{mark}{spec.label}: <b>{shown}</b>")
        row = [
            InlineKeyboardButton(
                text=f"{mark}{spec.label}: {shown}",
                callback_data=f"ul:set:{uid}:{spec.key}:{ctx}",
            )
        ]
        if custom:
            row.append(
                InlineKeyboardButton(
                    text="↩️", callback_data=f"ul:clr:{uid}:{spec.key}:{ctx}"
                )
            )
        kb.row(*row)

    lines.append("\n<b>قابلیت‌ها</b>")
    for code, label in FEATURE_FIELDS:
        key = feature_key(code)
        if key in stored:
            state = "روشن ✅ (دستی)" if stored[key] else "خاموش ⛔️ (دستی)"
            icon = "✅" if stored[key] else "⛔️"
            tag = "✏️"
        else:
            has = bool(plan and plan.has(code))
            state = f"{'✅ دارد' if has else '⛔️ ندارد'} (از طرح)"
            icon = "✅" if has else "⛔️"
            tag = ""
        lines.append(f"{tag}{label}: <b>{state}</b>")
        kb.row(
            InlineKeyboardButton(
                text=f"{tag}{icon} {label}",
                callback_data=f"ul:feat:{uid}:{code}:{ctx}",
            )
        )

    if stored:
        kb.row(
            InlineKeyboardButton(
                text="♻️ همه به حالت طرح", callback_data=f"ul:clrall:{uid}:{ctx}"
            )
        )
    else:
        lines.append("\n<i>هیچ تغییر اختصاصی‌ای ثبت نشده؛ همه‌چیز از طرح می‌آید.</i>")
    kb.row(
        InlineKeyboardButton(
            text="🔙 کارت کاربر", callback_data=f"admu:show:{uid}:-:{ctx}"
        )
    )

    markup = kb.as_markup()
    text = "\n".join(lines)
    if edit:
        try:
            await target.edit_text(text, reply_markup=markup)
            return
        except Exception:
            log.debug("ویرایش صفحه‌ی سقف‌ها ناموفق بود", exc_info=True)
    await target.answer(text, reply_markup=markup)


@router.callback_query(F.data.startswith("ul:show:"))
async def cb_show(call: CallbackQuery) -> None:
    if not await _guard(call):
        return
    _, _, uid, flt, page = call.data.split(":")
    await call.answer()
    await _render(call.message, int(uid), flt, int(page))


@router.callback_query(F.data.startswith("ul:set:"))
async def cb_set(call: CallbackQuery, state: FSMContext) -> None:
    if not await _guard(call):
        return
    _, _, uid, key, flt, page = call.data.split(":")
    spec = field_spec(key)
    if spec is None or not spec.per_user:
        await call.answer("نامعتبر", show_alert=True)
        return
    await call.answer()
    await state.set_state(Flow.admin_limit_value)
    await state.update_data(limit_uid=int(uid), limit_key=key, limit_ctx=f"{flt}:{page}")

    # مقدار خودِ طرح، بدون تغییرهای همین کاربر — وگرنه «مقدار طرح» همان
    # عددی می‌شود که ادمین قبلاً دستی گذاشته است
    base = await _base_plan(int(uid))
    from_plan = getattr(base, key, None) if base else None
    hint = [
        f"🎛 <b>{spec.label}</b> — کاربر <code>{uid}</code>",
        RULE,
        "",
        f"مقدار طرح: <b>{quota_label(int(from_plan)) if from_plan is not None else '—'}</b>",
    ]
    if spec.hint:
        hint.append(f"\n<i>{spec.hint}</i>")
    hint.append(f"\nعدد تازه را بفرستید (بین {spec.minimum} و {spec.maximum}).")
    if spec.allows_unlimited:
        hint.append("برای «نامحدود» عدد <code>-1</code> بفرستید.")
    hint.append("\nانصراف: /cancel")
    await call.message.answer("\n".join(hint))


@router.message(Flow.admin_limit_value)
async def got_value(message: Message, state: FSMContext) -> None:
    if not _is_admin(message.from_user.id):
        await state.clear()
        return
    data = await state.get_data()
    uid, key = data.get("limit_uid"), data.get("limit_key", "")
    ctx = data.get("limit_ctx", "all:0")
    spec = field_spec(key)
    if uid is None or spec is None:
        await state.clear()
        return

    value = parse_int(message.text or "")
    if value is None:
        await message.answer("عدد نامعتبر است. دوباره بفرستید یا /cancel بزنید.")
        return
    if not await limits.set_value(uid, key, value, admin_id=message.from_user.id):
        await message.answer("ثبت نشد. دوباره بفرستید یا /cancel بزنید.")
        return

    await state.clear()
    stored = (await limits.get(uid)).get(key)
    await message.answer(
        f"✅ <b>{spec.label}</b> برای این کاربر شد: <b>{quota_label(int(stored))}</b>"
    )
    flt, page = ctx.split(":")
    await _render(message, uid, flt, int(page), edit=False)


@router.callback_query(F.data.startswith("ul:feat:"))
async def cb_feature(call: CallbackQuery) -> None:
    """سه‌حالته: از طرح ← روشن ← خاموش ← از طرح."""
    if not await _guard(call):
        return
    _, _, uid, code, flt, page = call.data.split(":")
    uid = int(uid)
    stored = await limits.get(uid)
    key = feature_key(code)

    if key not in stored:
        await limits.set_feature(uid, code, True, admin_id=call.from_user.id)
        note = "روشن شد"
    elif stored[key]:
        await limits.set_feature(uid, code, False, admin_id=call.from_user.id)
        note = "خاموش شد"
    else:
        await limits.clear(uid, key, admin_id=call.from_user.id)
        note = "به حالت طرح برگشت"

    await call.answer(note)
    await _render(call.message, uid, flt, int(page))


@router.callback_query(F.data.startswith("ul:clrall:"))
async def cb_clear_all(call: CallbackQuery) -> None:
    if not await _guard(call):
        return
    _, _, uid, flt, page = call.data.split(":")
    await limits.clear_all(int(uid), admin_id=call.from_user.id)
    await call.answer("همه به حالت طرح برگشت")
    await _render(call.message, int(uid), flt, int(page))


@router.callback_query(F.data.startswith("ul:clr:"))
async def cb_clear(call: CallbackQuery) -> None:
    if not await _guard(call):
        return
    _, _, uid, key, flt, page = call.data.split(":")
    await limits.clear(int(uid), key, admin_id=call.from_user.id)
    await call.answer("به حالت طرح برگشت")
    await _render(call.message, int(uid), flt, int(page))
