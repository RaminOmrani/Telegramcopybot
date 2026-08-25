"""کپی پیام‌های گذشته‌ی کانال مبدا."""
from __future__ import annotations

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from telkap.db import get_session
from telkap.handlers.common import Flow, parse_int
from telkap.keyboards import credit_offer_menu, main_menu
from telkap.models import Task
from telkap.plans import CREDIT_HISTORY, FEAT_HISTORY, HISTORY_UNIT_TOMAN, toman
from telkap.services import credits
from telkap.services.subscription import active_plan_for
from telkap.texts import ASK_HISTORY_COUNT, INVALID_NUMBER, fa_num

router = Router(name="history")

MAX_HISTORY = 1000
history_copier = None


def bind(copier) -> None:
    global history_copier
    history_copier = copier


@router.callback_query(F.data.startswith("hist:start:"))
async def cb_start(call: CallbackQuery, state: FSMContext) -> None:
    task_id = int(call.data.split(":")[2])
    async with get_session() as db:
        task = await db.get(Task, task_id)
    if task is None or task.user_id != call.from_user.id:
        await call.answer("دسترسی ندارید", show_alert=True)
        return

    plan = await active_plan_for(call.from_user.id)
    if plan is None:
        await call.answer("اشتراک فعالی ندارید.", show_alert=True)
        return

    included = plan.has(FEAT_HISTORY)
    available = await credits.balance(call.from_user.id, CREDIT_HISTORY)
    if not included and available <= 0:
        await call.answer()
        await call.message.answer(
            "🕓 <b>کپی پیام‌های گذشته</b>\n\n"
            f"این قابلیت در پلن «{plan.title}» شما نیست.\n\n"
            "دو راه دارید:\n"
            "۱) پلن ۳۰ روزه یا اختصاصی بگیرید (بدون هزینه‌ی اضافه)\n"
            f"۲) اعتبار بخرید — هر پیام {toman(HISTORY_UNIT_TOMAN)}؛ "
            "خودتان تعداد را انتخاب می‌کنید.",
            reply_markup=credit_offer_menu(CREDIT_HISTORY),
        )
        return

    if history_copier and history_copier.is_running(call.from_user.id):
        await call.answer("یک کپی گذشته در حال اجراست.", show_alert=True)
        return

    await call.answer()
    await state.set_state(Flow.history_count)
    await state.update_data(task_id=task_id)
    await call.message.answer(ASK_HISTORY_COUNT)


@router.message(Flow.history_count)
async def got_count(message: Message, state: FSMContext) -> None:
    count = parse_int(message.text or "")
    if count is None:
        await message.answer(INVALID_NUMBER)
        return
    if not 1 <= count <= MAX_HISTORY:
        await message.answer(f"⚠️ عدد باید بین ۱ تا {fa_num(MAX_HISTORY)} باشد.")
        return

    data = await state.get_data()
    task_id = int(data.get("task_id", 0))
    await state.clear()

    async with get_session() as db:
        task = await db.get(Task, task_id)
    if task is None or task.user_id != message.from_user.id:
        await message.answer("این کار پیدا نشد.")
        return

    if history_copier is None:
        await message.answer("⚠️ این قابلیت در حال حاضر در دسترس نیست.")
        return

    # اگر پلن این قابلیت را ندارد، به‌اندازه‌ی همین تعداد اعتبار کم می‌شود
    plan = await active_plan_for(message.from_user.id)
    if plan is None:
        await message.answer("اشتراک فعالی ندارید.")
        return
    charged = 0
    if not plan.has(FEAT_HISTORY):
        if not await credits.consume(message.from_user.id, CREDIT_HISTORY, count):
            available = await credits.balance(message.from_user.id, CREDIT_HISTORY)
            await message.answer(
                f"⚠️ اعتبار شما {fa_num(available)} پیام است و برای "
                f"{fa_num(count)} پیام کافی نیست.\n\n"
                f"هر پیام {toman(HISTORY_UNIT_TOMAN)}.",
                reply_markup=credit_offer_menu(CREDIT_HISTORY),
            )
            return
        charged = count

    try:
        await history_copier.start(message.from_user.id, task_id, count)
    except RuntimeError as exc:
        if charged:
            # کار شروع نشد، پس اعتبار برمی‌گردد
            await credits.add(
                message.from_user.id, CREDIT_HISTORY, charged, note="بازگشت؛ کار شروع نشد"
            )
        await message.answer(f"⚠️ {exc}")
        return

    note = ""
    if charged:
        left = await credits.balance(message.from_user.id, CREDIT_HISTORY)
        note = (
            f"\n\n🎫 {fa_num(charged)} واحد از اعتبار شما کم شد "
            f"(مانده: {fa_num(left)} پیام)."
        )
    await message.answer(
        f"🕓 شروع شد: {fa_num(count)} پیام آخر «{task.source_title or task.source_ref}» "
        f"با تنظیمات همین کار در کانال مقصد کپی می‌شود.{note}\n\n"
        "پیشرفت کار را با /progress ببینید. برای توقف، از منوی همان کار «⏹ توقف کپی گذشته» را بزنید.",
        reply_markup=main_menu(),
    )


@router.callback_query(F.data.startswith("hist:cancel:"))
async def cb_cancel(call: CallbackQuery) -> None:
    if history_copier and history_copier.cancel(call.from_user.id):
        await call.answer("در حال توقف…", show_alert=True)
    else:
        await call.answer("کپی گذشته‌ای در حال اجرا نیست.", show_alert=True)


@router.message(Command("progress"))
async def cmd_progress(message: Message) -> None:
    job = history_copier.job_for(message.from_user.id) if history_copier else None
    if job is None:
        await message.answer("هیچ کپی گذشته‌ای ثبت نشده است.")
        return
    status = "✅ پایان‌یافته" if job.finished else "⏳ در حال اجرا"
    await message.answer(
        f"{status}\n"
        f"پیشرفت: {fa_num(job.progress_percent)}٪ ({fa_num(job.done)}/{fa_num(job.total)})\n"
        f"کپی‌شده: {fa_num(job.copied)} | رد‌شده: {fa_num(job.skipped)}"
    )
