"""نمایش پلن‌ها و راهنمای خرید اشتراک.

پرداخت آنلاین عمداً پیاده‌سازی نشده است؛ فعال‌سازی توسط ادمین انجام می‌شود
(دستور /grant). برای اتصال درگاه، کافی است در `cb_plan` لینک پرداخت خود را
تولید کنید و پس از تأیید پرداخت، `subscription.grant` را صدا بزنید.
"""
from __future__ import annotations

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, Message

from telkap.config import get_settings
from telkap.keyboards import BTN_PLANS, plans_menu
from telkap.plans import PURCHASABLE, POPULAR_CODE, get_plan
from telkap.services.subscription import active_subscription, remaining_days
from telkap.texts import fa_num

router = Router(name="billing")


def _plans_text() -> str:
    lines = ["💳 <b>تعرفه‌های اشتراک</b>\n"]
    for plan in PURCHASABLE:
        star = " ⭐️ <i>پیشنهاد ویژه</i>" if plan.code == POPULAR_CODE else ""
        lines.append(f"\n<b>{plan.title}</b> — {plan.price_label}{star}")
        lines.append(f"<i>{plan.tagline}</i>")
        for perk in plan.perks:
            lines.append(f"  • {perk}")
    lines.append("\nبرای فعال‌سازی، پلن موردنظر را انتخاب کنید.")
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
            f"اشتراک فعلی شما: <b>{plan.title if plan else sub.plan_code}</b> "
            f"({fa_num(days)} روز باقی‌مانده)\n"
            "با خرید پلن جدید، از انتهای اشتراک فعلی تمدید می‌شود.\n\n"
        )
    await message.answer(header + _plans_text(), reply_markup=plans_menu())


@router.callback_query(F.data.startswith("plan:"))
async def cb_plan(call: CallbackQuery) -> None:
    plan = get_plan(call.data.split(":")[1])
    if plan is None:
        await call.answer("این پلن وجود ندارد.", show_alert=True)
        return
    await call.answer()
    support = get_settings().support_username
    contact = f"@{support}" if support else "پشتیبانی"
    await call.message.answer(
        f"🧾 <b>{plan.title}</b>\n"
        f"مبلغ: {plan.price_label}\n"
        f"مدت: {fa_num(plan.days)} روز\n"
        f"سقف کارهای کپی: {fa_num(plan.max_tasks)}\n\n"
        f"برای فعال‌سازی، مبلغ را پرداخت کرده و رسید را برای {contact} بفرستید.\n"
        f"شناسه‌ی کاربری شما: <code>{call.from_user.id}</code>\n\n"
        "پس از تأیید، اشتراک بلافاصله فعال می‌شود."
    )
