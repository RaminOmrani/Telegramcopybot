"""دستورات مدیریتی ربات."""
from __future__ import annotations

import asyncio
import logging

from aiogram import Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import Message
from sqlalchemy import func, select

from telkap.config import get_settings
from telkap.db import get_session
from telkap.models import Subscription, Task, User, utcnow
from telkap.handlers.common import Flow, parse_int
from telkap.plans import PLANS
from telkap.services.subscription import grant
from telkap.services.userbot import manager
from telkap.texts import fa_num

log = logging.getLogger(__name__)
router = Router(name="admin")


def _is_admin(user_id: int) -> bool:
    return get_settings().is_admin(user_id)


@router.message(Command("admin"))
async def cmd_admin(message: Message) -> None:
    if not _is_admin(message.from_user.id):
        return
    await message.answer(
        "🛠 <b>پنل مدیریت</b>\n\n"
        "<code>/stats</code> — آمار کلی\n"
        "<code>/grant &lt;user_id&gt; &lt;plan&gt;</code> — فعال‌سازی اشتراک\n"
        f"پلن‌های معتبر: {', '.join(PLANS)}\n"
        "<code>/ban &lt;user_id&gt;</code> / <code>/unban &lt;user_id&gt;</code>\n"
        "<code>/broadcast</code> — ارسال پیام همگانی"
    )


@router.message(Command("stats"))
async def cmd_stats(message: Message) -> None:
    if not _is_admin(message.from_user.id):
        return
    async with get_session() as db:
        users = await db.scalar(select(func.count(User.id)))
        logged = await db.scalar(select(func.count(User.id)).where(User.session_enc.is_not(None)))
        tasks = await db.scalar(select(func.count(Task.id)))
        active_tasks = await db.scalar(select(func.count(Task.id)).where(Task.enabled.is_(True)))
        copied = await db.scalar(select(func.coalesce(func.sum(Task.copied_count), 0)))
        subs = await db.scalar(
            select(func.count(Subscription.id)).where(Subscription.expires_at > utcnow())
        )
    await message.answer(
        "📊 <b>آمار ربات</b>\n\n"
        f"کاربران: {fa_num(users or 0)}\n"
        f"اکانت‌های متصل: {fa_num(logged or 0)}\n"
        f"اشتراک‌های فعال: {fa_num(subs or 0)}\n"
        f"کارهای کپی: {fa_num(tasks or 0)} (فعال: {fa_num(active_tasks or 0)})\n"
        f"مجموع پست‌های کپی‌شده: {fa_num(int(copied or 0))}"
    )


@router.message(Command("grant"))
async def cmd_grant(message: Message) -> None:
    if not _is_admin(message.from_user.id):
        return
    parts = (message.text or "").split()
    if len(parts) != 3:
        await message.answer("قالب درست: <code>/grant &lt;user_id&gt; &lt;plan&gt;</code>")
        return
    user_id = parse_int(parts[1])
    plan_code = parts[2]
    if user_id is None or plan_code not in PLANS:
        await message.answer(f"آیدی یا پلن نامعتبر. پلن‌ها: {', '.join(PLANS)}")
        return

    async with get_session() as db:
        if await db.get(User, user_id) is None:
            await message.answer("این کاربر هنوز ربات را استارت نکرده است.")
            return

    sub = await grant(user_id, plan_code, granted_by=message.from_user.id, note="فعال‌سازی توسط ادمین")
    if sub is None:
        await message.answer("فعال‌سازی ناموفق بود.")
        return

    await message.answer(
        f"✅ پلن <b>{PLANS[plan_code].title}</b> برای <code>{user_id}</code> فعال شد.\n"
        f"انقضا: {sub.expires_at:%Y-%m-%d %H:%M}"
    )
    try:
        await message.bot.send_message(
            user_id,
            f"🎉 اشتراک <b>{PLANS[plan_code].title}</b> برای شما فعال شد.\n"
            f"تا تاریخ {sub.expires_at:%Y-%m-%d} می‌توانید از همه‌ی امکانات استفاده کنید.",
        )
    except Exception:
        log.debug("اطلاع‌رسانی فعال‌سازی به کاربر ناموفق بود", exc_info=True)


@router.message(Command("ban"))
@router.message(Command("unban"))
async def cmd_ban(message: Message) -> None:
    if not _is_admin(message.from_user.id):
        return
    parts = (message.text or "").split()
    if len(parts) != 2:
        await message.answer("قالب درست: <code>/ban &lt;user_id&gt;</code>")
        return
    user_id = parse_int(parts[1])
    if user_id is None:
        await message.answer("آیدی نامعتبر است.")
        return
    banning = parts[0].startswith("/ban")

    async with get_session() as db:
        user = await db.get(User, user_id)
        if user is None:
            await message.answer("کاربر پیدا نشد.")
            return
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
    await message.answer(f"{'🚫 مسدود' if banning else '✅ آزاد'} شد: <code>{user_id}</code>")


@router.message(Command("broadcast"))
async def cmd_broadcast(message: Message, state: FSMContext) -> None:
    if not _is_admin(message.from_user.id):
        return
    await state.set_state(Flow.admin_broadcast)
    await message.answer("📢 متن پیام همگانی را بفرستید.\n\nانصراف: /cancel")


@router.message(Flow.admin_broadcast)
async def do_broadcast(message: Message, state: FSMContext) -> None:
    if not _is_admin(message.from_user.id):
        await state.clear()
        return
    await state.clear()
    async with get_session() as db:
        rows = await db.execute(select(User.id).where(User.is_banned.is_(False)))
        user_ids = [uid for uid in rows.scalars()]

    sent = failed = 0
    notice = await message.answer(f"در حال ارسال به {fa_num(len(user_ids))} کاربر…")
    for index, user_id in enumerate(user_ids, start=1):
        try:
            await message.bot.send_message(user_id, message.html_text)
            sent += 1
        except Exception:
            failed += 1
        # نرخ امن Bot API برای ارسال انبوه
        await asyncio.sleep(0.05)
        if index % 100 == 0:
            try:
                await notice.edit_text(f"ارسال‌شده: {fa_num(sent)} | ناموفق: {fa_num(failed)}")
            except Exception:
                pass

    await notice.edit_text(
        f"📢 پایان ارسال همگانی.\nموفق: {fa_num(sent)} | ناموفق: {fa_num(failed)}"
    )
