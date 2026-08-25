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
from telkap.plans import (
    CREDIT_HISTORY,
    FEAT_HISTORY,
    HISTORY_UNIT_TOMAN,
    UNLIMITED,
    toman,
)
from telkap.services import credits, entitlement
from telkap.services.copier import today_key
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

    day = today_key()
    quota = await entitlement.quota_left(call.from_user.id, FEAT_HISTORY, plan, day)
    available = await credits.balance(call.from_user.id, CREDIT_HISTORY)
    if quota + available <= 0:
        await call.answer()
        await call.message.answer(
            "🕓 <b>کپی پیام‌های گذشته</b>\n\n"
            + (
                f"سهمیه‌ی امروز طرح «{plan.title}» شما تمام شده است.\n"
                "سهمیه از نیمه‌شب دوباره پر می‌شود.\n\n"
                if plan.history_daily > 0
                else f"این قابلیت در طرح «{plan.title}» شما نیست.\n\n"
            )
            + "دو راه دارید:\n"
            "۱) طرح بالاتری بگیرید (سهمیه‌ی روزانه بیشتر می‌شود)\n"
            f"۲) اعتبار بخرید — هر پیام {toman(HISTORY_UNIT_TOMAN)}، "
            "بدون انقضا و بدون سقف روزانه.",
            reply_markup=credit_offer_menu(CREDIT_HISTORY),
        )
        return

    if history_copier and history_copier.is_running(call.from_user.id):
        await call.answer("یک کپی گذشته در حال اجراست.", show_alert=True)
        return

    await call.answer()
    await state.set_state(Flow.history_count)
    await state.update_data(task_id=task_id)
    budget = (
        "نامحدود"
        if plan.history_daily == UNLIMITED
        else f"{fa_num(quota)} از سهمیه‌ی امروز + {fa_num(available)} اعتبار"
    )
    await call.message.answer(f"{ASK_HISTORY_COUNT}\n\n🎫 در دسترس شما: <b>{budget}</b>")


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

    # اول از سهمیه‌ی روزانه‌ی طرح، بعد از اعتبار خریداری‌شده
    plan = await active_plan_for(message.from_user.id)
    if plan is None:
        await message.answer("اشتراک فعالی ندارید.")
        return

    day = today_key()
    grant = await entitlement.reserve(
        message.from_user.id, FEAT_HISTORY, count, plan, day
    )
    if grant is None:
        quota = await entitlement.quota_left(
            message.from_user.id, FEAT_HISTORY, plan, day
        )
        available = await credits.balance(message.from_user.id, CREDIT_HISTORY)
        await message.answer(
            f"⚠️ برای {fa_num(count)} پیام کافی نیست.\n\n"
            f"سهمیه‌ی امروز: {fa_num(quota)} پیام\n"
            f"اعتبار شما: {fa_num(available)} پیام\n"
            f"مجموع در دسترس: <b>{fa_num(quota + available)}</b>\n\n"
            f"یا عدد کمتری بفرستید، یا اعتبار بخرید (هر پیام "
            f"{toman(HISTORY_UNIT_TOMAN)}).",
            reply_markup=credit_offer_menu(CREDIT_HISTORY),
        )
        return

    try:
        await history_copier.start(message.from_user.id, task_id, count)
    except RuntimeError as exc:
        # کار شروع نشد، پس سهمیه و اعتبار برمی‌گردند
        await entitlement.release(message.from_user.id, grant, day)
        await message.answer(f"⚠️ {exc}")
        return

    note = ""
    if not grant.unlimited:
        note = f"\n\n🎫 برداشت: {fa_num(grant.note)}"
        if grant.from_credits:
            left = await credits.balance(message.from_user.id, CREDIT_HISTORY)
            note += f"\nمانده‌ی اعتبار: {fa_num(left)} پیام"
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
