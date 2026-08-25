"""حساب کاربری: ورود با شماره تلفن، خروج، پین امنیتی."""
from __future__ import annotations

import logging

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy import func, select

from telkap.db import get_session
from telkap.handlers.common import (
    Flow,
    check_pin,
    clean_code,
    get_or_create_user,
    hash_pin,
)
from telkap.keyboards import BTN_ACCOUNT, account_menu, main_menu
from telkap.models import DailyStat, User
from telkap.plans import (
    CREDIT_HISTORY,
    CREDIT_WATERMARK,
    FEAT_HISTORY,
    FEAT_PRIVATE,
    FEAT_VIP,
    FEAT_WATERMARK,
    UNLIMITED,
)
from telkap.services import credits, entitlement
from telkap.services.copier import today_key
from telkap.services.subscription import active_plan_for, remaining_days
from telkap.services.userbot import LoginError, manager
from telkap.texts import (
    ASK_PIN,
    ASK_PIN_VERIFY,
    LOGIN_CODE,
    LOGIN_INTRO,
    LOGIN_OK,
    LOGIN_PASSWORD,
    PIN_WRONG,
    fa_num,
)

log = logging.getLogger(__name__)
router = Router(name="account")


async def _account_text(user: User) -> str:
    plan = await active_plan_for(user.id)
    days = await remaining_days(user.id)
    lines = ["👤 <b>حساب کاربری</b>", "━━━━━━━━━━━━━━━━━━", ""]
    if user.is_logged_in:
        lines.append(f"🔗 اتصال اکانت: ✅ متصل ({user.account_name or user.phone or '—'})")
    else:
        lines.append("🔗 اتصال اکانت: ❌ متصل نیست")
    lines.append(f"🔒 پین امنیتی: {'✅ فعال' if user.pin_hash else '❌ غیرفعال'}")
    lines.append("")

    if plan:
        used = await today_usage(user.id)
        quota = (
            f"{fa_num(used)} از {plan.daily_label}"
            if plan.daily_messages
            else f"{fa_num(used)} (نامحدود)"
        )
        day = today_key()
        wm_used = await entitlement.used_today(user.id, FEAT_WATERMARK, day)
        hist_used = await entitlement.used_today(user.id, FEAT_HISTORY, day)
        lines += [
            f"💎 طرح: <b>{plan.title}</b>",
            f"📅 باقی‌مانده: <b>{fa_num(days)} روز</b>",
            f"📋 سقف کار کپی: {fa_num(plan.max_tasks)}",
            f"📤 سقف مقصد هر کار: {fa_num(plan.max_destinations)}",
            "",
            "<b>سهمیه‌های امروز</b>",
            f"  📨 پیام: {quota}",
            f"  💧 واترمارک: {_usage(wm_used, plan.watermark_daily)}",
            f"  🕓 پیام گذشته: {_usage(hist_used, plan.history_daily)}",
            "",
            "<b>امکانات طرح شما</b>",
            f"  {_mark(plan.has(FEAT_PRIVATE))} کپی از کانال خصوصی",
            f"  {_mark(plan.has(FEAT_VIP))} پشتیبانی ویژه",
            "",
            "<i>سهمیه‌ها هر شب نیمه‌شب دوباره پر می‌شوند.</i>",
        ]
    else:
        lines.append("💎 طرح: ⛔️ اشتراک فعالی ندارید")

    balances = await credits.balances(user.id)
    wm, hist = balances.get(CREDIT_WATERMARK, 0), balances.get(CREDIT_HISTORY, 0)
    if wm or hist:
        lines += [
            "",
            "🎫 <b>اعتبار شما</b>",
            f"  💧 واترمارک: {fa_num(wm)} واحد",
            f"  🕓 پیام گذشته: {fa_num(hist)} واحد",
        ]
    return "\n".join(lines)


def _mark(flag: bool) -> str:
    return "✅" if flag else "➖"


def _usage(used: int, limit: int) -> str:
    """مصرف امروز از سهمیه، به شکل «۳ از ۲۰»."""
    if limit == UNLIMITED:
        return f"{fa_num(used)} (نامحدود)"
    if limit == 0:
        return "در طرح شما نیست"
    return f"{fa_num(used)} از {fa_num(limit)}"


async def today_usage(user_id: int) -> int:
    """تعداد پیام‌های کپی‌شده‌ی امروز این کاربر."""
    async with get_session() as db:
        used = await db.scalar(
            select(func.coalesce(func.sum(DailyStat.copied), 0)).where(
                DailyStat.user_id == user_id, DailyStat.day == today_key()
            )
        )
    return int(used or 0)


@router.message(Command("account"))
@router.message(F.text == BTN_ACCOUNT)
async def show_account(message: Message) -> None:
    user = await get_or_create_user(message.from_user)
    await message.answer(
        await _account_text(user),
        reply_markup=account_menu(user.is_logged_in, bool(user.pin_hash)),
    )


# ------------------------------------------------------------------ ورود
@router.message(Command("login"))
async def cmd_login(message: Message, state: FSMContext) -> None:
    await _begin_login(message, state)


@router.callback_query(F.data == "acc:login")
async def cb_login(call: CallbackQuery, state: FSMContext) -> None:
    await call.answer()
    await _begin_login(call.message, state, user_id=call.from_user.id)


async def _begin_login(message: Message, state: FSMContext, user_id: int | None = None) -> None:
    await state.set_state(Flow.phone)
    await message.answer(LOGIN_INTRO)


@router.message(Flow.phone)
async def got_phone(message: Message, state: FSMContext) -> None:
    phone = (message.text or "").strip().replace(" ", "").replace("-", "")
    if not phone.startswith("+") or not phone[1:].isdigit() or len(phone) < 8:
        await message.answer("⚠️ شماره را با کد کشور و به شکل <code>+989121234567</code> بفرستید.")
        return
    await get_or_create_user(message.from_user)
    notice = await message.answer("⏳ در حال ارسال کد…")
    try:
        await manager.start_login(message.from_user.id, phone)
    except LoginError as exc:
        await notice.edit_text(f"⚠️ {exc}")
        await state.clear()
        return
    await notice.edit_text(LOGIN_CODE)
    await state.set_state(Flow.code)


@router.message(Flow.code)
async def got_code(message: Message, state: FSMContext) -> None:
    code = clean_code(message.text or "")
    if not code:
        await message.answer("⚠️ کد باید فقط شامل ارقام باشد.")
        return
    try:
        done = await manager.submit_code(message.from_user.id, code)
    except LoginError as exc:
        await message.answer(f"⚠️ {exc}")
        if "منقضی" in str(exc):
            await state.clear()
        return
    if done:
        await state.clear()
        await message.answer(LOGIN_OK, reply_markup=main_menu())
    else:
        await state.set_state(Flow.password)
        await message.answer(LOGIN_PASSWORD)


@router.message(Flow.password)
async def got_password(message: Message, state: FSMContext) -> None:
    try:
        await manager.submit_password(message.from_user.id, message.text or "")
    except LoginError as exc:
        await message.answer(f"⚠️ {exc}")
        return
    await state.clear()
    await message.answer(LOGIN_OK, reply_markup=main_menu())
    # پیام حاوی رمز را از چت ربات پاک می‌کنیم
    try:
        await message.delete()
    except Exception:
        log.debug("حذف پیام رمز ناموفق بود", exc_info=True)


# ------------------------------------------------------------------ خروج
@router.callback_query(F.data == "acc:logout")
async def cb_logout(call: CallbackQuery, state: FSMContext) -> None:
    user = await get_or_create_user(call.from_user)
    if user.pin_hash:
        await state.set_state(Flow.pin_verify)
        await state.update_data(pin_action="logout")
        await call.message.answer(ASK_PIN_VERIFY)
        await call.answer()
        return
    await manager.logout(call.from_user.id)
    await call.answer()
    await call.message.answer(
        "🚪 از حساب خارج شدید. کارهای کپی تا اتصال مجدد متوقف می‌مانند."
    )


# --------------------------------------------------------------- پین امنیتی
@router.callback_query(F.data == "acc:pin")
async def cb_pin(call: CallbackQuery, state: FSMContext) -> None:
    user = await get_or_create_user(call.from_user)
    await call.answer()
    if user.pin_hash:
        await state.set_state(Flow.pin_verify)
        await state.update_data(pin_action="disable")
        await call.message.answer(ASK_PIN_VERIFY)
    else:
        await state.set_state(Flow.pin_set)
        await call.message.answer(ASK_PIN)


@router.message(Flow.pin_set)
async def set_pin(message: Message, state: FSMContext) -> None:
    pin = clean_code(message.text or "")
    if not 4 <= len(pin) <= 8:
        await message.answer("⚠️ پین باید بین ۴ تا ۸ رقم باشد.")
        return
    async with get_session() as db:
        user = await db.get(User, message.from_user.id)
        if user:
            user.pin_hash = hash_pin(user.id, pin)
            await db.commit()
    await state.clear()
    await message.answer("🔒 پین امنیتی فعال شد.", reply_markup=main_menu())
    try:
        await message.delete()
    except Exception:
        log.debug("حذف پیام پین ناموفق بود", exc_info=True)


@router.message(Flow.pin_verify)
async def verify_pin(message: Message, state: FSMContext) -> None:
    user = await get_or_create_user(message.from_user)
    if not check_pin(user, clean_code(message.text or "")):
        await message.answer(PIN_WRONG)
        return
    data = await state.get_data()
    action = data.get("pin_action")
    await state.clear()

    if action == "logout":
        await manager.logout(user.id)
        await message.answer("🚪 از حساب خارج شدید.", reply_markup=main_menu())
    elif action == "disable":
        async with get_session() as db:
            db_user = await db.get(User, user.id)
            if db_user:
                db_user.pin_hash = None
                await db.commit()
        await message.answer("🔓 پین امنیتی غیرفعال شد.", reply_markup=main_menu())
    elif action == "task_delete":
        from telkap.handlers.tasks import delete_task_confirmed

        await delete_task_confirmed(message, int(data.get("task_id", 0)))
    else:
        await message.answer("✅ تأیید شد.", reply_markup=main_menu())


@router.callback_query(F.data == "acc:sub")
async def cb_sub(call: CallbackQuery) -> None:
    user = await get_or_create_user(call.from_user)
    await call.answer()
    await call.message.answer(await _account_text(user))
