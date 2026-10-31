"""پاسخ کاربر به «چرا تمدید نکردید؟».

یک کلیک کافی است. اگر «دلیل دیگر» را بزند، یک بار هم متن می‌پرسیم —
همان متن‌ها معمولاً مفیدترین چیزی است که از کاربر رفته می‌گیریم.
"""
from __future__ import annotations

import logging

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from telkap.handlers.common import Flow
from telkap.services import analytics

log = logging.getLogger(__name__)
router = Router(name="churn")

THANKS = "🙏 ممنون. همین یک جمله برای ما ارزش دارد."


@router.callback_query(F.data.startswith("churn:"))
async def cb_reason(call: CallbackQuery, state: FSMContext) -> None:
    try:
        _, raw_sub, reason = call.data.split(":", 2)
        sub_id = int(raw_sub)
    except ValueError:
        await call.answer()
        return

    if reason not in analytics.REASONS:
        await call.answer()
        return

    saved = await analytics.record_churn(call.from_user.id, sub_id, reason)
    await call.answer("ثبت شد" if saved else "قبلاً جواب داده‌اید")

    if reason == "other" and saved:
        await state.set_state(Flow.churn_note)
        await state.update_data(churn_sub=sub_id)
        await call.message.edit_text(
            "✍️ در یک جمله بنویسید چه چیزی باعث شد نمانید.\n\n"
            "اگر نمی‌خواهید، /cancel بزنید."
        )
        return

    await call.message.edit_text(THANKS)


@router.message(Flow.churn_note)
async def got_note(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    sub_id = data.get("churn_sub")
    await state.clear()
    note = (message.text or "").strip()
    if sub_id and note:
        await analytics.update_churn_note(message.from_user.id, int(sub_id), note)
    await message.answer(THANKS)
