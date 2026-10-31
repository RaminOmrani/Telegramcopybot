"""گزارش‌های تجاری پنل مدیریت: درآمد، قیف تبدیل، ریزش و لاگ حسابرسی.

فرق این بخش با «📊 آمار» این است که آمار می‌گوید ربات چه کرده، اینجا
می‌گوید کسب‌وکار چه حالی دارد و کجا باید کار کرد.
"""
from __future__ import annotations

import logging

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, InlineKeyboardButton, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder
from sqlalchemy import select

from telkap.db import get_session
from telkap.models import ActivityLog
from telkap.plans import toman
from telkap.services import analytics, roles
from telkap.texts import fa_num

log = logging.getLogger(__name__)
router = Router(name="admin-reports")

AUDIT_PAGE = 15


async def guard(event: CallbackQuery | Message, cap: str) -> bool:
    """True یعنی اجازه ندارد و پیام «دسترسی ندارید» رفته است."""
    if await roles.can(event.from_user.id, cap):
        return False
    if isinstance(event, CallbackQuery):
        await event.answer("به این بخش دسترسی ندارید", show_alert=True)
    return True


def _menu() -> InlineKeyboardBuilder:
    kb = InlineKeyboardBuilder()
    kb.row(
        InlineKeyboardButton(text="💰 درآمد", callback_data="rep:money"),
        InlineKeyboardButton(text="🔻 قیف تبدیل", callback_data="rep:funnel"),
    )
    kb.row(
        InlineKeyboardButton(text="💔 دلیل ریزش", callback_data="rep:churn"),
        InlineKeyboardButton(text="📜 لاگ حسابرسی", callback_data="rep:audit:0"),
    )
    kb.row(InlineKeyboardButton(text="🔙 بازگشت", callback_data="adm:home"))
    return kb


def _back() -> InlineKeyboardBuilder:
    kb = InlineKeyboardBuilder()
    kb.row(InlineKeyboardButton(text="🔙 گزارش‌ها", callback_data="adm:reports"))
    return kb


async def _show(event: CallbackQuery | Message, text: str, kb) -> None:
    if isinstance(event, CallbackQuery):
        await event.answer()
        await event.message.edit_text(text, reply_markup=kb.as_markup())
    else:
        await event.answer(text, reply_markup=kb.as_markup())


@router.callback_query(F.data == "adm:reports")
@router.message(Command("reports"))
async def cb_reports(event: CallbackQuery | Message) -> None:
    if await guard(event, roles.CAP_REPORTS):
        return
    await _show(
        event,
        "📈 <b>گزارش‌های کسب‌وکار</b>\n\n"
        "اینجا معلوم می‌شود پول از کجا می‌آید، کاربر کجا جا می‌ماند، "
        "و چرا می‌رود.",
        _menu(),
    )


# ------------------------------------------------------------- درآمد
def _revenue_block(title: str, rev: analytics.Revenue) -> str:
    if not rev.total:
        return f"<b>{title}</b>\nهنوز درآمدی ثبت نشده.\n"
    lines = [f"<b>{title}</b>", f"مجموع: <b>{toman(rev.total)}</b>"]
    if rev.plans:
        lines.append(f"• اشتراک: {toman(rev.plans)}")
    if rev.credits:
        lines.append(f"• اعتبار: {toman(rev.credits)}")
    if rev.reseller:
        lines.append(f"• نمایندگی: {toman(rev.reseller)}")
    if rev.payers:
        lines.append(
            f"خریداران: {fa_num(rev.payers)} — میانگین {toman(rev.per_payer)}"
        )
    return "\n".join(lines) + "\n"


@router.callback_query(F.data == "rep:money")
async def cb_money(call: CallbackQuery) -> None:
    if await guard(call, roles.CAP_REPORTS):
        return
    data = await analytics.dashboard()
    growth = data.growth
    if not data.last_month.total:
        trend = "ماه قبل درآمدی نبوده، پس مقایسه‌ای در کار نیست."
    elif growth > 0:
        trend = f"📈 نسبت به ۳۰ روز قبل: <b>{fa_num(growth)}٪ رشد</b>"
    elif growth < 0:
        trend = f"📉 نسبت به ۳۰ روز قبل: <b>{fa_num(abs(growth))}٪ افت</b>"
    else:
        trend = "بدون تغییر نسبت به ۳۰ روز قبل."

    ret = data.retention
    text = (
        "💰 <b>درآمد</b>\n\n"
        + _revenue_block("۳۰ روز اخیر", data.this_month)
        + "\n"
        + _revenue_block("۳۰ روز قبل‌تر", data.last_month)
        + "\n"
        + _revenue_block("از ابتدا", data.all_time)
        + f"\n{trend}\n\n"
        "<b>ماندگاری</b>\n"
        f"یک‌بارخرید: {fa_num(ret.once)} — چندبارخرید: {fa_num(ret.repeat)}\n"
        f"نرخ خرید دوباره: <b>{fa_num(ret.repeat_rate)}٪</b>\n"
        f"اشتراک فعال: {fa_num(ret.active_subs)} — "
        f"منقضی‌شده: {fa_num(ret.expired_users)}"
    )
    await _show(call, text, _back())


# -------------------------------------------------------- قیف تبدیل
@router.callback_query(F.data == "rep:funnel")
async def cb_funnel(call: CallbackQuery) -> None:
    if await guard(call, roles.CAP_REPORTS):
        return
    data = await analytics.funnel()
    if not data.started:
        await _show(call, "🔻 <b>قیف تبدیل</b>\n\nهنوز کاربری نداریم.", _back())
        return

    rows = []
    for title, count, pct in data.steps:
        filled = round(pct / 10)
        bar = "█" * filled + "░" * (10 - filled)
        rows.append(f"{bar} {fa_num(pct)}٪\n{title}: <b>{fa_num(count)}</b>")

    where, lost = data.biggest_drop
    text = (
        "🔻 <b>قیف تبدیل</b>\n\n"
        + "\n\n".join(rows)
        + "\n\n<b>بزرگ‌ترین افت</b>\n"
        + f"{where}: {fa_num(lost)} نفر\n\n"
        "همان‌جا بیشترین سود را می‌دهد؛ یک قدم راحت‌تر کردنِ آن مرحله "
        "از هر تبلیغی مؤثرتر است."
    )
    await _show(call, text, _back())


# --------------------------------------------------------- دلیل ریزش
@router.callback_query(F.data == "rep:churn")
async def cb_churn(call: CallbackQuery) -> None:
    if await guard(call, roles.CAP_REPORTS):
        return
    summary = await analytics.churn_summary()
    if not summary:
        await _show(
            call,
            "💔 <b>دلیل ریزش</b>\n\n"
            "هنوز کسی به سؤال «چرا تمدید نکردید؟» جواب نداده.\n"
            "این سؤال خودکار همراه پیام انقضا فرستاده می‌شود.",
            _back(),
        )
        return

    total = sum(count for _reason, count in summary)
    lines = [
        f"• {analytics.REASONS.get(reason, reason)}: "
        f"<b>{fa_num(count)}</b> ({fa_num(round(count * 100 / total))}٪)"
        for reason, count in summary
    ]
    text = "💔 <b>دلیل ریزش (۹۰ روز اخیر)</b>\n\n" + "\n".join(lines)

    notes = await analytics.churn_notes()
    if notes:
        text += "\n\n<b>حرف‌های خود کاربران</b>\n"
        text += "\n".join(f"«{note.note[:120]}»" for note in notes[:5])
    await _show(call, text, _back())


# ----------------------------------------------------- لاگ حسابرسی
@router.callback_query(F.data.startswith("rep:audit:"))
async def cb_audit(call: CallbackQuery) -> None:
    if await guard(call, roles.CAP_REPORTS):
        return
    try:
        page = max(0, int(call.data.split(":")[2]))
    except (IndexError, ValueError):
        page = 0

    async with get_session() as db:
        rows = await db.execute(
            select(ActivityLog)
            .where(ActivityLog.actor_id.is_not(None))
            .order_by(ActivityLog.id.desc())
            .offset(page * AUDIT_PAGE)
            .limit(AUDIT_PAGE + 1)
        )
        entries = list(rows.scalars())

    has_next = len(entries) > AUDIT_PAGE
    entries = entries[:AUDIT_PAGE]

    if not entries:
        body = (
            "هنوز کار ادمینی ثبت نشده.\n\n"
            "از این پس هر تغییری که ادمین‌ها روی کاربران، طرح‌ها و "
            "پرداخت‌ها بدهند اینجا با نام انجام‌دهنده می‌ماند."
        )
    else:
        body = "\n\n".join(
            f"<code>{entry.created_at:%m-%d %H:%M}</code> "
            f"ادمین <code>{entry.actor_id}</code> → "
            f"<b>{entry.event}</b>"
            + (f" (کاربر <code>{entry.user_id}</code>)" if entry.user_id else "")
            + (f"\n{entry.detail[:150]}" if entry.detail else "")
            for entry in entries
        )

    kb = InlineKeyboardBuilder()
    nav = []
    if page:
        nav.append(
            InlineKeyboardButton(text="⬅️ جدیدتر", callback_data=f"rep:audit:{page - 1}")
        )
    if has_next:
        nav.append(
            InlineKeyboardButton(text="قدیمی‌تر ➡️", callback_data=f"rep:audit:{page + 1}")
        )
    if nav:
        kb.row(*nav)
    kb.row(InlineKeyboardButton(text="🔙 گزارش‌ها", callback_data="adm:reports"))

    await _show(call, f"📜 <b>لاگ حسابرسی</b>\n\n{body}", kb)
