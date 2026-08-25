"""مدیریت کاربران در پنل ادمین — همه‌چیز با دکمه.

فهرست مشترکان، جزئیات هر کاربر، کم و زیاد کردن روزهای اشتراک، دادن پلن،
لغو اشتراک، مسدودسازی و پیام مستقیم؛ بدون نیاز به تایپ هیچ دستوری.
"""
from __future__ import annotations

import logging
from datetime import UTC, datetime

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder
from sqlalchemy import func, select

from telkap.config import get_settings
from telkap.db import get_session
from telkap.handlers.common import Flow, parse_int
from telkap.models import Subscription, Task, User, utcnow
from telkap.plans import CREDIT_HISTORY, CREDIT_KINDS, CREDIT_WATERMARK, PLANS, get_plan
from telkap.services import credits, limits, subscription
from telkap.services.userbot import manager
from telkap.texts import fa_num

log = logging.getLogger(__name__)
router = Router(name="admin_users")

PAGE_SIZE = 8

# کلید فیلتر → (عنوان دکمه، توضیح سربرگ)
FILTERS: dict[str, tuple[str, str]] = {
    "all": ("همه", "همه‌ی کاربران"),
    "active": ("مشترک", "کاربران با اشتراک فعال"),
    "expired": ("منقضی", "کاربرانی که اشتراکشان تمام شده"),
    "linked": ("متصل", "کاربرانی که اکانتشان متصل است"),
    "banned": ("مسدود", "کاربران مسدود"),
}

# روزهایی که با یک دکمه اضافه یا کم می‌شوند
DAY_STEPS = (1, 7, 30)


def _is_admin(user_id: int) -> bool:
    return get_settings().is_admin(user_id)


async def _guard(call: CallbackQuery) -> bool:
    if _is_admin(call.from_user.id):
        return True
    await call.answer("دسترسی ندارید", show_alert=True)
    return False


def _ctx(flt: str, page: int) -> str:
    """بخش پایانی مشترک همه‌ی callbackها: فیلتر و شماره‌ی صفحه."""
    return f"{flt}:{page}"


def _active_user_ids():
    return select(Subscription.user_id).where(Subscription.expires_at > utcnow())


def _filtered(stmt, flt: str):
    if flt == "active":
        return stmt.where(User.id.in_(_active_user_ids()))
    if flt == "expired":
        return stmt.where(User.id.not_in(_active_user_ids()))
    if flt == "linked":
        return stmt.where(User.session_enc.is_not(None))
    if flt == "banned":
        return stmt.where(User.is_banned.is_(True))
    return stmt


def _label(user: User, plan_title: str | None, days: int) -> str:
    name = (user.first_name or "").strip() or f"کاربر {user.id}"
    if user.username:
        name = f"{name} (@{user.username})"
    if user.is_banned:
        badge = "🚫"
    elif plan_title:
        badge = f"✅ {fa_num(days)}ر"
    else:
        badge = "⌛️"
    return f"{badge} {name}"[:60]


async def _plan_info(db, user_id: int) -> tuple[str | None, int, Subscription | None]:
    """عنوان پلن فعال، روزهای باقی‌مانده و خود اشتراک."""
    rows = await db.execute(
        select(Subscription)
        .where(Subscription.user_id == user_id, Subscription.expires_at > utcnow())
        .order_by(Subscription.expires_at.desc())
        .limit(1)
    )
    sub = rows.scalar_one_or_none()
    if sub is None:
        return None, 0, None
    plan = get_plan(sub.plan_code)
    delta = _aware(sub.expires_at) - utcnow()
    days = max(0, delta.days + (1 if delta.seconds else 0))
    return (plan.title if plan else sub.plan_code), days, sub


def _aware(value: datetime) -> datetime:
    """SQLite تاریخ را بدون منطقه‌ی زمانی برمی‌گرداند؛ اینجا UTC فرض می‌شود."""
    return value if value.tzinfo else value.replace(tzinfo=UTC)


# --------------------------------------------------------------- فهرست
async def _render_list(target: Message, flt: str, page: int) -> None:
    async with get_session() as db:
        total = await db.scalar(_filtered(select(func.count(User.id)), flt)) or 0
        pages = max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE)
        page = max(0, min(page, pages - 1))
        rows = await db.execute(
            _filtered(select(User), flt)
            .order_by(User.created_at.desc())
            .offset(page * PAGE_SIZE)
            .limit(PAGE_SIZE)
        )
        users = list(rows.scalars())
        info = {user.id: await _plan_info(db, user.id) for user in users}

    kb = InlineKeyboardBuilder()
    for user in users:
        plan_title, days, _ = info[user.id]
        kb.row(
            InlineKeyboardButton(
                text=_label(user, plan_title, days),
                callback_data=f"admu:show:{user.id}:-:{_ctx(flt, page)}",
            )
        )

    if pages > 1:
        nav = [
            InlineKeyboardButton(
                text="◀️", callback_data=f"admul:{_ctx(flt, max(0, page - 1))}"
            ),
            InlineKeyboardButton(
                text=f"{fa_num(page + 1)}/{fa_num(pages)}", callback_data="admu:noop"
            ),
            InlineKeyboardButton(
                text="▶️", callback_data=f"admul:{_ctx(flt, min(pages - 1, page + 1))}"
            ),
        ]
        kb.row(*nav)

    chips = [
        InlineKeyboardButton(
            text=("• " if key == flt else "") + title,
            callback_data=f"admul:{_ctx(key, 0)}",
        )
        for key, (title, _) in FILTERS.items()
    ]
    kb.row(*chips[:3])
    kb.row(*chips[3:])
    kb.row(InlineKeyboardButton(text="🔍 جستجوی کاربر", callback_data="admu:find:0:-:all:0"))
    kb.row(InlineKeyboardButton(text="🔙 بازگشت", callback_data="adm:home"))

    head = FILTERS.get(flt, FILTERS["all"])[1]
    body = f"👥 <b>{head}</b> — {fa_num(total)} نفر\n\n"
    body += "برای دیدن جزئیات و تغییر اشتراک، روی نام کاربر بزنید." if users else "موردی نیست."
    try:
        await target.edit_text(body, reply_markup=kb.as_markup())
    except Exception:
        await target.answer(body, reply_markup=kb.as_markup())


@router.callback_query(F.data == "adm:users")
async def cb_users(call: CallbackQuery) -> None:
    if not await _guard(call):
        return
    await call.answer()
    await _render_list(call.message, "all", 0)


@router.callback_query(F.data.startswith("admul:"))
async def cb_users_page(call: CallbackQuery) -> None:
    if not await _guard(call):
        return
    _, flt, page = call.data.split(":")
    await call.answer()
    await _render_list(call.message, flt, int(page))


@router.callback_query(F.data == "admu:noop")
async def cb_noop(call: CallbackQuery) -> None:
    await call.answer()


# -------------------------------------------------------------- جزئیات
async def _detail(user_id: int, flt: str, page: int):
    async with get_session() as db:
        user = await db.get(User, user_id)
        if user is None:
            return None
        plan_title, days, sub = await _plan_info(db, user_id)
        tasks = await db.scalar(select(func.count(Task.id)).where(Task.user_id == user_id)) or 0
        active_tasks = await db.scalar(
            select(func.count(Task.id)).where(Task.user_id == user_id, Task.enabled.is_(True))
        ) or 0
        copied = await db.scalar(
            select(func.coalesce(func.sum(Task.copied_count), 0)).where(Task.user_id == user_id)
        ) or 0

    name = (user.first_name or "").strip() or "—"
    lines = [
        f"👤 <b>{name}</b>",
        f"شناسه: <code>{user.id}</code>",
        f"نام کاربری: {'@' + user.username if user.username else '—'}",
        f"شماره: {fa_num(user.phone) if user.phone else '—'}",
        f"عضویت: {fa_num(f'{user.created_at:%Y-%m-%d}')}",
        "",
        f"اکانت متصل: {('✅ ' + user.account_name) if user.is_logged_in else '❌ ندارد'}",
        f"وضعیت: {'🚫 مسدود' if user.is_banned else '✅ عادی'}",
        "",
    ]
    if sub is not None:
        lines += [
            f"اشتراک: <b>{plan_title}</b>",
            f"باقی‌مانده: <b>{fa_num(days)} روز</b>",
            f"انقضا: {fa_num(f'{sub.expires_at:%Y-%m-%d %H:%M}')}",
        ]
    else:
        lines.append("اشتراک: ❌ فعال نیست")
    lines += [
        "",
        f"کارهای کپی: {fa_num(tasks)} (فعال: {fa_num(active_tasks)})",
        f"مجموع پست‌های کپی‌شده: {fa_num(int(copied))}",
        "",
        f"🎫 اعتبار واترمارک: <b>{fa_num(int(user.watermark_credits or 0))}</b>",
        f"🎫 اعتبار پیام گذشته: <b>{fa_num(int(user.history_credits or 0))}</b>",
    ]

    base_plan = get_plan(sub.plan_code) if sub is not None else None
    custom = limits.describe(base_plan, user.limits)
    if custom:
        lines += ["", "🎛 <b>سقف‌های اختصاصی</b>", *custom]

    return "\n".join(lines), _detail_keyboard(user, sub is not None, flt, page)


def _detail_keyboard(user: User, has_sub: bool, flt: str, page: int) -> InlineKeyboardBuilder:
    kb = InlineKeyboardBuilder()
    uid = user.id
    ctx = _ctx(flt, page)
    kb.row(
        *[
            InlineKeyboardButton(
                text=f"➕{fa_num(step)}", callback_data=f"admu:day:{uid}:{step}:{ctx}"
            )
            for step in DAY_STEPS
        ]
    )
    kb.row(
        *[
            InlineKeyboardButton(
                text=f"➖{fa_num(step)}", callback_data=f"admu:day:{uid}:-{step}:{ctx}"
            )
            for step in DAY_STEPS
        ]
    )
    kb.row(
        InlineKeyboardButton(text="🎁 دادن پلن", callback_data=f"admu:plans:{uid}:-:{ctx}"),
        InlineKeyboardButton(text="✏️ روز دلخواه", callback_data=f"admu:ask:{uid}:-:{ctx}"),
    )
    if has_sub:
        kb.row(
            InlineKeyboardButton(text="⛔️ لغو اشتراک", callback_data=f"admu:revoke:{uid}:-:{ctx}")
        )
    if user.is_banned:
        kb.row(
            InlineKeyboardButton(text="✅ رفع مسدودی", callback_data=f"admu:unban:{uid}:-:{ctx}")
        )
    else:
        kb.row(
            InlineKeyboardButton(text="🚫 مسدود کردن", callback_data=f"admu:ban:{uid}:-:{ctx}")
        )
    kb.row(
        InlineKeyboardButton(text="🎫 اعتبار واترمارک", callback_data=f"admu:cwm:{uid}:-:{ctx}"),
        InlineKeyboardButton(text="🎫 اعتبار گذشته", callback_data=f"admu:chist:{uid}:-:{ctx}"),
    )
    kb.row(
        InlineKeyboardButton(text="🎛 سقف‌های اختصاصی", callback_data=f"ul:show:{uid}:{ctx}")
    )
    kb.row(
        InlineKeyboardButton(text="📋 کارهای کاربر", callback_data=f"admu:tasks:{uid}:-:{ctx}"),
        InlineKeyboardButton(text="✉️ پیام", callback_data=f"admu:dm:{uid}:-:{ctx}"),
    )
    kb.row(InlineKeyboardButton(text="🔙 فهرست کاربران", callback_data=f"admul:{ctx}"))
    return kb


async def _show_user(target: Message, user_id: int, flt: str, page: int, *, edit=True) -> bool:
    """کارت کاربر را نمایش می‌دهد؛ در صورت نبود کاربر False برمی‌گرداند."""
    detail = await _detail(user_id, flt, page)
    if detail is None:
        return False
    text, kb = detail
    markup = kb.as_markup()
    if edit:
        try:
            await target.edit_text(text, reply_markup=markup)
            return True
        except Exception:
            log.debug("ویرایش کارت کاربر ناموفق بود؛ پیام تازه فرستاده می‌شود", exc_info=True)
    await target.answer(text, reply_markup=markup)
    return True


def _parse(data: str) -> tuple[str, int, str, str, int]:
    """admu:{action}:{uid}:{arg}:{flt}:{page}"""
    _, action, uid, arg, flt, page = data.split(":")
    return action, int(uid), arg, flt, int(page)


@router.callback_query(F.data.startswith("admu:show:"))
async def cb_show(call: CallbackQuery) -> None:
    if not await _guard(call):
        return
    _, uid, _arg, flt, page = _parse(call.data)
    await call.answer()
    if not await _show_user(call.message, uid, flt, page):
        await call.message.answer("کاربر پیدا نشد.")


# ------------------------------------------------------ کم و زیاد کردن روز
async def _apply_days(call: CallbackQuery, uid: int, days: int, flt: str, page: int) -> None:
    sub = await subscription.adjust_days(uid, days, admin_id=call.from_user.id)
    if sub is None:
        await call.answer(
            "این کاربر اشتراک فعال ندارد. اول از «🎁 دادن پلن» یک پلن بدهید.", show_alert=True
        )
        return
    await call.answer(f"{days:+d} روز اعمال شد.")
    await _show_user(call.message, uid, flt, page)
    verb = "افزوده شد به" if days > 0 else "کم شد از"
    try:
        await call.bot.send_message(
            uid,
            f"ℹ️ {fa_num(abs(days))} روز {verb} اشتراک شما.\n"
            f"اعتبار تا: {fa_num(f'{sub.expires_at:%Y-%m-%d}')}",
        )
    except Exception:
        log.debug("اطلاع تغییر اشتراک به کاربر نرسید", exc_info=True)


@router.callback_query(F.data.startswith("admu:day:"))
async def cb_day(call: CallbackQuery) -> None:
    if not await _guard(call):
        return
    _, uid, arg, flt, page = _parse(call.data)
    await _apply_days(call, uid, int(arg), flt, page)


@router.callback_query(F.data.startswith("admu:ask:"))
async def cb_ask_days(call: CallbackQuery, state: FSMContext) -> None:
    if not await _guard(call):
        return
    _, uid, _arg, flt, page = _parse(call.data)
    await call.answer()
    await state.set_state(Flow.admin_days)
    await state.update_data(admin_target=uid, admin_flt=flt, admin_page=page)
    await call.message.answer(
        f"✏️ چند روز به اشتراک کاربر <code>{uid}</code> اضافه شود؟\n"
        "عدد منفی یعنی کم کردن. مثال: <code>45</code> یا <code>-10</code>\n\nانصراف: /cancel"
    )


@router.message(Flow.admin_days)
async def got_days(message: Message, state: FSMContext) -> None:
    if not _is_admin(message.from_user.id):
        await state.clear()
        return
    days = parse_int(message.text or "")
    if days is None or days == 0:
        await message.answer("یک عدد صحیح غیر صفر بفرستید، مثلاً <code>30</code> یا <code>-7</code>.")
        return
    data = await state.get_data()
    uid = int(data.get("admin_target", 0))
    await state.clear()
    sub = await subscription.adjust_days(uid, days, admin_id=message.from_user.id)
    if sub is None:
        await message.answer("این کاربر اشتراک فعال ندارد. اول یک پلن به او بدهید.")
        return
    await message.answer(
        f"✅ {days:+d} روز اعمال شد.\nاعتبار تا: {fa_num(f'{sub.expires_at:%Y-%m-%d %H:%M}')}"
    )
    await _show_user(
        message,
        uid,
        str(data.get("admin_flt", "all")),
        int(data.get("admin_page", 0)),
        edit=False,
    )


# --------------------------------------------------------------- دادن پلن
@router.callback_query(F.data.startswith("admu:plans:"))
async def cb_plans(call: CallbackQuery) -> None:
    if not await _guard(call):
        return
    _, uid, _arg, flt, page = _parse(call.data)
    await call.answer()
    kb = InlineKeyboardBuilder()
    for code, plan in PLANS.items():
        kb.row(
            InlineKeyboardButton(
                text=f"{plan.title} — {fa_num(plan.days)} روز",
                callback_data=f"admu:give:{uid}:{code}:{_ctx(flt, page)}",
            )
        )
    kb.row(
        InlineKeyboardButton(
            text="🔙 بازگشت", callback_data=f"admu:show:{uid}:-:{_ctx(flt, page)}"
        )
    )
    await call.message.edit_text(
        f"🎁 کدام پلن برای کاربر <code>{uid}</code> فعال شود؟\n\n"
        "اگر اشتراک فعالی داشته باشد، از انتهای آن تمدید می‌شود.",
        reply_markup=kb.as_markup(),
    )


@router.callback_query(F.data.startswith("admu:give:"))
async def cb_give(call: CallbackQuery) -> None:
    if not await _guard(call):
        return
    _, uid, code, flt, page = _parse(call.data)
    plan = get_plan(code)
    if plan is None:
        await call.answer("پلن نامعتبر است.", show_alert=True)
        return
    sub = await subscription.grant(uid, code, granted_by=call.from_user.id, note="پنل ادمین")
    if sub is None:
        await call.answer("فعال‌سازی ناموفق بود.", show_alert=True)
        return
    await call.answer(f"{plan.title} فعال شد.")
    await _show_user(call.message, uid, flt, page)
    try:
        await call.bot.send_message(
            uid,
            f"🎉 اشتراک <b>{plan.title}</b> برای شما فعال شد.\n"
            f"اعتبار تا: {fa_num(f'{sub.expires_at:%Y-%m-%d}')}",
        )
    except Exception:
        log.debug("اطلاع فعال‌سازی به کاربر نرسید", exc_info=True)


@router.callback_query(F.data.startswith("admu:revoke:"))
async def cb_revoke(call: CallbackQuery) -> None:
    if not await _guard(call):
        return
    _, uid, _arg, flt, page = _parse(call.data)
    count = await subscription.revoke(uid, admin_id=call.from_user.id)
    await call.answer(f"{count} اشتراک لغو شد." if count else "اشتراک فعالی نبود.")
    await _show_user(call.message, uid, flt, page)
    if count:
        try:
            await call.bot.send_message(uid, "⛔️ اشتراک شما توسط مدیریت لغو شد.")
        except Exception:
            log.debug("اطلاع لغو اشتراک به کاربر نرسید", exc_info=True)


# ------------------------------------------------------------ مسدودسازی
async def _set_ban(user_id: int, banning: bool) -> bool:
    async with get_session() as db:
        user = await db.get(User, user_id)
        if user is None:
            return False
        user.is_banned = banning
        if banning:
            rows = await db.execute(select(Task).where(Task.user_id == user_id))
            for task in rows.scalars():
                task.enabled = False
        await db.commit()
    if banning:
        await manager.stop_user(user_id)
    else:
        await manager.reload_user(user_id)
    return True


@router.callback_query(F.data.startswith("admu:ban:"))
@router.callback_query(F.data.startswith("admu:unban:"))
async def cb_ban(call: CallbackQuery) -> None:
    if not await _guard(call):
        return
    action, uid, _arg, flt, page = _parse(call.data)
    banning = action == "ban"
    if not await _set_ban(uid, banning):
        await call.answer("کاربر پیدا نشد.", show_alert=True)
        return
    await call.answer("🚫 مسدود شد. کارهایش هم متوقف شد." if banning else "✅ آزاد شد.")
    await _show_user(call.message, uid, flt, page)


# ------------------------------------------------------------- اعتبارها
@router.callback_query(F.data.startswith("admu:cwm:"))
@router.callback_query(F.data.startswith("admu:chist:"))
async def cb_credit_ask(call: CallbackQuery, state: FSMContext) -> None:
    if not await _guard(call):
        return
    action, uid, _arg, flt, page = _parse(call.data)
    kind = CREDIT_WATERMARK if action == "cwm" else CREDIT_HISTORY
    title = CREDIT_KINDS[kind][0]
    have = await credits.balance(uid, kind)
    await call.answer()
    await state.set_state(Flow.admin_credit)
    await state.update_data(
        admin_target=uid, admin_credit_kind=kind, admin_flt=flt, admin_page=page
    )
    await call.message.answer(
        f"{title}\n\n"
        f"مانده‌ی کاربر <code>{uid}</code>: <b>{fa_num(have)}</b> واحد\n\n"
        "چند واحد اضافه شود؟ عدد منفی یعنی کم کردن.\n"
        "مثال: <code>100</code> یا <code>-20</code>\n\nانصراف: /cancel"
    )


@router.message(Flow.admin_credit)
async def got_credit(message: Message, state: FSMContext) -> None:
    if not _is_admin(message.from_user.id):
        await state.clear()
        return
    amount = parse_int(message.text or "")
    if amount is None or amount == 0:
        await message.answer("یک عدد صحیح غیر صفر بفرستید.")
        return
    data = await state.get_data()
    uid = int(data.get("admin_target", 0))
    kind = str(data.get("admin_credit_kind", CREDIT_WATERMARK))
    await state.clear()

    left = await credits.add(uid, kind, amount, note=f"ادمین {message.from_user.id}")
    title = CREDIT_KINDS[kind][0]
    await message.answer(
        f"✅ {amount:+d} واحد اعمال شد.\n{title} — مانده: <b>{fa_num(left)}</b>"
    )
    try:
        verb = "به حساب شما اضافه شد" if amount > 0 else "از حساب شما کم شد"
        await message.bot.send_message(
            uid,
            f"🎫 {fa_num(abs(amount))} واحد {title} {verb}.\n"
            f"مانده: <b>{fa_num(left)}</b> واحد",
        )
    except Exception:
        log.debug("اطلاع تغییر اعتبار به کاربر نرسید", exc_info=True)
    await _show_user(
        message,
        uid,
        str(data.get("admin_flt", "all")),
        int(data.get("admin_page", 0)),
        edit=False,
    )


# --------------------------------------------------------- کارهای کاربر
@router.callback_query(F.data.startswith("admu:tasks:"))
async def cb_tasks(call: CallbackQuery) -> None:
    if not await _guard(call):
        return
    _, uid, _arg, flt, page = _parse(call.data)
    await call.answer()
    async with get_session() as db:
        rows = await db.execute(
            select(Task).where(Task.user_id == uid).order_by(Task.id).limit(20)
        )
        tasks = list(rows.scalars())

    kb = InlineKeyboardBuilder()
    kb.row(
        InlineKeyboardButton(
            text="🔙 بازگشت", callback_data=f"admu:show:{uid}:-:{_ctx(flt, page)}"
        )
    )
    if not tasks:
        await call.message.edit_text("این کاربر هنوز کار کپی نساخته است.", reply_markup=kb.as_markup())
        return

    lines = [f"📋 <b>کارهای کاربر</b> <code>{uid}</code>\n"]
    for task in tasks:
        mark = "🟢" if task.enabled else "⚪️"
        lines.append(
            f"{mark} <b>{task.title or task.id}</b>\n"
            f"   {task.source_title or task.source_ref} ← {task.dest_title or task.dest_ref}\n"
            f"   کپی‌شده: {fa_num(task.copied_count)}"
            + (f"\n   ⚠️ {task.last_error[:60]}" if task.last_error else "")
        )
    await call.message.edit_text("\n".join(lines), reply_markup=kb.as_markup())


# --------------------------------------------------------- پیام مستقیم
@router.callback_query(F.data.startswith("admu:dm:"))
async def cb_dm(call: CallbackQuery, state: FSMContext) -> None:
    if not await _guard(call):
        return
    _, uid, _arg, flt, page = _parse(call.data)
    await call.answer()
    await state.set_state(Flow.admin_dm)
    await state.update_data(admin_target=uid, admin_flt=flt, admin_page=page)
    await call.message.answer(
        f"✉️ متن پیام برای کاربر <code>{uid}</code> را بفرستید.\n\nانصراف: /cancel"
    )


@router.message(Flow.admin_dm)
async def got_dm(message: Message, state: FSMContext) -> None:
    if not _is_admin(message.from_user.id):
        await state.clear()
        return
    data = await state.get_data()
    uid = int(data.get("admin_target", 0))
    await state.clear()
    try:
        await message.bot.send_message(uid, f"📩 <b>پیام از پشتیبانی</b>\n\n{message.html_text}")
    except Exception as exc:
        await message.answer(f"⚠️ ارسال نشد: {exc}")
        return
    await message.answer("✅ پیام ارسال شد.")


# ---------------------------------------------------------- جستجوی کاربر
@router.callback_query(F.data.startswith("admu:find:"))
async def cb_find(call: CallbackQuery, state: FSMContext) -> None:
    if not await _guard(call):
        return
    await call.answer()
    await state.set_state(Flow.admin_user_find)
    await call.message.answer(
        "🔍 شناسه‌ی عددی یا نام کاربری را بفرستید.\n"
        "مثال: <code>123456789</code> یا <code>@username</code>\n\nانصراف: /cancel"
    )


@router.message(Flow.admin_user_find)
async def got_find(message: Message, state: FSMContext) -> None:
    if not _is_admin(message.from_user.id):
        await state.clear()
        return
    raw = (message.text or "").strip().lstrip("@")
    await state.clear()
    user_id = parse_int(raw)
    async with get_session() as db:
        if user_id is not None:
            found = await db.get(User, user_id)
        else:
            rows = await db.execute(
                select(User).where(func.lower(User.username) == raw.lower()).limit(1)
            )
            found = rows.scalar_one_or_none()
    if found is None:
        await message.answer("کاربری با این مشخصات پیدا نشد.")
        return
    await _show_user(message, found.id, "all", 0, edit=False)
