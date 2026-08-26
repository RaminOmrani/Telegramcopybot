"""ساخت و مدیریت کارهای کپی."""
from __future__ import annotations

import logging

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder
from sqlalchemy import func, select

from telkap.db import get_session, log_activity
from telkap.handlers import history as history_handlers
from telkap.handlers.chatpicker import picker_button
from telkap.handlers.common import Flow, get_or_create_user
from telkap.keyboards import (
    confirm,
    main_menu,
    menu_texts,
    task_menu,
    tasks_list,
)
from telkap.models import DailyStat, Destination, PendingPost, Task, User
from telkap.plans import FEAT_PRIVATE
from telkap.services import cache, pending
from telkap.services.copier import today_key
from telkap.services.defaults import merged_settings
from telkap.services.subscription import active_plan_for
from telkap.services.userbot import LoginError, manager
from telkap.texts import (
    ASK_DEST,
    ASK_SOURCE,
    ASK_TITLE,
    NO_LOGIN,
    NO_SUBSCRIPTION,
    fa_num,
    on_off,
)

log = logging.getLogger(__name__)
router = Router(name="tasks")

async def _load_tasks(user_id: int) -> list[Task]:
    async with get_session() as db:
        rows = await db.execute(
            select(Task).where(Task.user_id == user_id).order_by(Task.id)
        )
        return list(rows.scalars())


async def task_detail_text(task: Task) -> str:
    cfg = merged_settings(task.settings)
    lines = [
        f"📋 <b>{task.title or 'کار بدون نام'}</b>\n",
        f"وضعیت: {'🟢 فعال' if task.enabled else '🔴 متوقف'}",
        f"مبدا: <code>{task.source_ref}</code>",
        f"مقصد: <code>{task.dest_ref}</code>",
        f"شیوه: {'فوروارد' if cfg.get('mode') == 'forward' else 'کپی بدون برچسب'}",
        "",
        f"کپی‌شده: {fa_num(task.copied_count)} | رد‌شده: {fa_num(task.skipped_count)}",
    ]
    if task.last_copy_at:
        lines.append(f"آخرین کپی: {fa_num(task.last_copy_at.strftime('%Y/%m/%d %H:%M'))}")
    if task.last_error:
        lines.append(f"\n⚠️ آخرین خطا: {task.last_error}")
    lines.append("")
    lines.append(f"حذف لینک: {on_off(bool(cfg.get('remove_links')))}")
    lines.append(f"حذف هشتگ: {on_off(bool(cfg.get('remove_hashtags')))}")
    lines.append(f"حذف امضای مبدا: {on_off(bool(cfg.get('remove_source_signature')))}")
    lines.append(f"واترمارک: {on_off(bool(cfg.get('watermark_enabled')))}")
    return "\n".join(lines)


async def show_task(target: Message, task_id: int, *, edit: bool = False) -> None:
    async with get_session() as db:
        task = await db.get(Task, task_id)
    if task is None:
        await target.answer("این کار دیگر وجود ندارد.")
        return
    copier = history_handlers.history_copier
    running = bool(copier and copier.is_running(task.user_id))
    text = await task_detail_text(task)
    waiting = await pending.waiting_count(
        task.user_id, reason=PendingPost.REASON_APPROVAL, task_id=task.id
    )
    async with get_session() as db:
        owner = await db.get(User, task.user_id)
    pro = bool(owner and owner.display_level == "pro")
    markup = task_menu(
        task, backfill_running=running, waiting=waiting, pro=pro
    )
    if edit:
        try:
            await target.edit_text(text, reply_markup=markup)
            return
        except Exception:
            log.debug("ویرایش پیام ناموفق بود؛ پیام جدید ارسال می‌شود", exc_info=True)
    await target.answer(text, reply_markup=markup)


async def _dest_counts(tasks) -> dict[int, int]:
    """تعداد کل مقصدهای فعال هر کار (مقصد اصلی + مقصدهای اضافی)."""
    if not tasks:
        return {}
    ids = [task.id for task in tasks]
    async with get_session() as db:
        rows = await db.execute(
            select(Destination.task_id, func.count(Destination.id))
            .where(Destination.task_id.in_(ids), Destination.enabled.is_(True))
            .group_by(Destination.task_id)
        )
        extra = dict(rows.all())
    return {task_id: 1 + extra.get(task_id, 0) for task_id in ids}


async def _tasks_header(user_id: int, tasks) -> str:
    plan = await active_plan_for(user_id)
    head = f"📋 <b>کارهای کپی شما</b> — {fa_num(len(tasks))}"
    if plan:
        head += f" از {fa_num(plan.max_tasks)}"
        head += f"\n<i>طرح {plan.title} · تا {fa_num(plan.max_destinations)} مقصد برای هر کار</i>"
    return head


# ------------------------------------------------------------------- لیست
@router.message(Command("tasks"))
@router.message(F.text.in_(menu_texts("menu.tasks")))
async def cmd_tasks(message: Message) -> None:
    tasks = await _load_tasks(message.from_user.id)
    if not tasks:
        await message.answer(
            "هنوز کاری نساخته‌اید.\nبا «➕ کار جدید» اولین کار کپی خود را بسازید.",
            reply_markup=main_menu(),
        )
        return
    await message.answer(
        await _tasks_header(message.from_user.id, tasks),
        reply_markup=tasks_list(tasks, dest_counts=await _dest_counts(tasks)),
    )


@router.callback_query(F.data == "task:list")
async def cb_list(call: CallbackQuery) -> None:
    tasks = await _load_tasks(call.from_user.id)
    await call.answer()
    if not tasks:
        await call.message.edit_text("هنوز کاری نساخته‌اید.", reply_markup=tasks_list([]))
        return
    await call.message.edit_text(
        await _tasks_header(call.from_user.id, tasks),
        reply_markup=tasks_list(tasks, dest_counts=await _dest_counts(tasks)),
    )


@router.callback_query(F.data.startswith("task:open:"))
async def cb_open(call: CallbackQuery) -> None:
    task_id = int(call.data.split(":")[2])
    await call.answer()
    await show_task(call.message, task_id, edit=True)


@router.callback_query(F.data.startswith("task:stats:"))
async def cb_stats(call: CallbackQuery) -> None:
    """آمار ۷ روز گذشته‌ی یک کار."""
    task_id = int(call.data.split(":")[2])
    async with get_session() as db:
        task = await db.get(Task, task_id)
        if task is None or task.user_id != call.from_user.id:
            await call.answer("دسترسی ندارید", show_alert=True)
            return
        rows = await db.execute(
            select(DailyStat)
            .where(DailyStat.task_id == task_id)
            .order_by(DailyStat.day.desc())
            .limit(7)
        )
        stats = list(rows.scalars())

    await call.answer()
    lines = [f"📈 <b>آمار «{task.title or task.source_ref}»</b>\n"]
    if not stats:
        lines.append("هنوز آماری ثبت نشده است.")
    else:
        today = today_key()
        for stat in stats:
            label = "امروز" if stat.day == today else fa_num(stat.day)
            parts = [f"✅ {fa_num(stat.copied)}"]
            if stat.skipped:
                parts.append(f"⏭ {fa_num(stat.skipped)}")
            if stat.failed:
                parts.append(f"⚠️ {fa_num(stat.failed)}")
            lines.append(f"<code>{label}</code> — {' | '.join(parts)}")
        lines.append("\n✅ کپی‌شده | ⏭ رد‌شده | ⚠️ ناموفق")

    kb = InlineKeyboardBuilder()
    kb.row(InlineKeyboardButton(text="🔙 بازگشت", callback_data=f"task:open:{task_id}"))
    try:
        await call.message.edit_text("\n".join(lines), reply_markup=kb.as_markup())
    except Exception:
        await call.message.answer("\n".join(lines), reply_markup=kb.as_markup())


# ------------------------------------------------------------- ساخت کار
@router.message(Command("newtask"))
@router.message(F.text.in_(menu_texts("menu.new_task")))
async def cmd_new_task(message: Message, state: FSMContext) -> None:
    await _start_new_task(message, state, message.from_user.id)


@router.callback_query(F.data == "task:new")
async def cb_new_task(call: CallbackQuery, state: FSMContext) -> None:
    await call.answer()
    await _start_new_task(call.message, state, call.from_user.id)


async def _start_new_task(message: Message, state: FSMContext, user_id: int) -> None:
    async with get_session() as db:
        user = await db.get(User, user_id)
    if user is None or not user.is_logged_in:
        await message.answer(NO_LOGIN)
        return

    plan = await active_plan_for(user_id)
    if plan is None:
        await message.answer(NO_SUBSCRIPTION)
        return

    async with get_session() as db:
        count = await db.scalar(select(func.count(Task.id)).where(Task.user_id == user_id))
    if (count or 0) >= plan.max_tasks:
        await message.answer(
            f"⚠️ در پلن «{plan.title}» حداکثر {fa_num(plan.max_tasks)} کار کپی می‌توانید داشته باشید.\n"
            "برای افزایش سقف، پلن بالاتری تهیه کنید."
        )
        return

    await state.set_state(Flow.task_source)
    await message.answer(ASK_SOURCE, reply_markup=picker_button("source").as_markup())


async def _lookup(target: Message, user_id: int, ref: str, busy: str):
    """چت را پیدا می‌کند. خروجی (entity، پیام وضعیت) یا (None، پیام وضعیت).

    خطای «باید اول عضو شوید» را می‌گیرد تا به‌جای پیام عمومی خطا،
    دلیل واقعی به کاربر نشان داده شود.
    """
    client = await manager.ensure_client(user_id)
    if client is None:
        await target.answer(NO_LOGIN)
        return None, None

    notice = await target.answer(busy)
    try:
        entity = await manager.resolve_entity(client, ref)
    except LoginError as exc:
        await notice.edit_text(f"⚠️ {exc}")
        return None, notice
    return entity, notice


async def accept_source(target: Message, state: FSMContext, user_id: int, ref: str) -> None:
    """مبدا را بررسی و ثبت می‌کند — چه دستی وارد شده باشد چه از لیست."""
    entity, notice = await _lookup(target, user_id, ref, "⏳ در حال بررسی مبدا…")
    if entity is None:
        if notice is not None and "⚠️" not in (notice.text or ""):
            await notice.edit_text(
                "⚠️ این کانال یا گروه پیدا نشد یا اکانت شما به آن دسترسی ندارد.\n\n"
                "اگر <b>خصوصی</b> است، با همان شماره‌ای که در ربات وارد شده‌اید عضو آن شوید، "
                "سپس دکمه‌ی «📋 انتخاب از لیست چت‌های من» را بزنید."
            )
        return

    is_private = not getattr(entity, "username", None)
    if is_private:
        plan = await active_plan_for(user_id)
        if plan and not plan.has(FEAT_PRIVATE):
            await notice.edit_text(
                "⚠️ کپی از کانال‌های خصوصی در پلن فعلی شما فعال نیست.\n"
                "برای این قابلیت، پلن ۱۴ روزه یا بالاتر تهیه کنید."
            )
            return

    title = getattr(entity, "title", None) or ref
    lock = "🔒 خصوصی" if is_private else "🌐 عمومی"
    await state.update_data(source_ref=ref, source_title=title)
    await notice.edit_text(f"✅ مبدا: <b>{title}</b> ({lock})")
    await state.set_state(Flow.task_dest)
    await target.answer(ASK_DEST, reply_markup=picker_button("dest").as_markup())


async def accept_dest(target: Message, state: FSMContext, user_id: int, ref: str) -> None:
    entity, notice = await _lookup(target, user_id, ref, "⏳ در حال بررسی مقصد…")
    if entity is None:
        if notice is not None and "⚠️" not in (notice.text or ""):
            await notice.edit_text(
                "⚠️ مقصد پیدا نشد. مطمئن شوید اکانت شما عضو آن است و اجازه‌ی ارسال دارد."
            )
        return

    title = getattr(entity, "title", None) or ref
    await state.update_data(dest_ref=ref, dest_title=title)
    await notice.edit_text(f"✅ مقصد: <b>{title}</b>")
    await state.set_state(Flow.task_title)
    await target.answer(ASK_TITLE)


@router.message(Flow.task_source)
async def got_source(message: Message, state: FSMContext) -> None:
    ref = (message.text or "").strip()
    if not ref:
        await message.answer("⚠️ آیدی یا لینک کانال را بفرستید.")
        return
    await accept_source(message, state, message.from_user.id, ref)


@router.message(Flow.task_dest)
async def got_dest(message: Message, state: FSMContext) -> None:
    ref = (message.text or "").strip()
    if not ref:
        await message.answer("⚠️ آیدی یا لینک کانال را بفرستید.")
        return
    await accept_dest(message, state, message.from_user.id, ref)


@router.message(Flow.task_title)
async def got_title(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    title = (message.text or "").strip()[:128] or data.get("source_title", "کار جدید")
    await state.clear()

    async with get_session() as db:
        task = Task(
            user_id=message.from_user.id,
            title=title,
            source_ref=data["source_ref"],
            source_title=data.get("source_title", "")[:160],
            dest_ref=data["dest_ref"],
            dest_title=data.get("dest_title", "")[:160],
            settings={},
        )
        db.add(task)
        await db.commit()
        await db.refresh(task)
        task_id = task.id

    # آیدی مقصد را یک‌بار resolve و ذخیره می‌کنیم تا ارسال‌ها سریع‌تر شود
    client = await manager.ensure_client(message.from_user.id)
    if client:
        dest_id = await manager.resolve_chat_id(client, data["dest_ref"])
        if dest_id is not None:
            async with get_session() as db:
                db_task = await db.get(Task, task_id)
                if db_task:
                    db_task.dest_id = dest_id
                    await db.commit()

    active = await manager.reload_user(message.from_user.id)
    await log_activity(
        user_id=message.from_user.id,
        task_id=task_id,
        event="task_create",
        detail=f"{data['source_ref']} ← {data['dest_ref']}",
    )
    await message.answer(
        f"✅ کار «{title}» ساخته شد و از همین حالا فعال است.\n"
        f"کارهای فعال شما: {fa_num(active)}",
        reply_markup=main_menu(),
    )
    await show_task(message, task_id)


# ------------------------------------------------------------ فعال/غیرفعال
@router.callback_query(F.data.startswith("task:pro:"))
async def cb_go_pro(call: CallbackQuery) -> None:
    """کاربر گزینه‌های پیشرفته را خواست؛ از این پس همه‌ی منوها کامل‌اند."""
    task_id = int(call.data.split(":")[2])
    async with get_session() as db:
        user = await db.get(User, call.from_user.id)
        if user is not None:
            user.display_level = "pro"
            await db.commit()
    await call.answer("حالت پیشرفته روشن شد")
    await show_task(call.message, task_id, edit=True)


@router.callback_query(F.data.startswith("task:toggle:"))
async def cb_toggle(call: CallbackQuery) -> None:
    task_id = int(call.data.split(":")[2])
    async with get_session() as db:
        task = await db.get(Task, task_id)
        if task is None or task.user_id != call.from_user.id:
            await call.answer("دسترسی ندارید", show_alert=True)
            return
        task.enabled = not task.enabled
        if task.enabled:
            task.last_error = None
        enabled = task.enabled
        await db.commit()
    cache.invalidate_task(task_id)
    await manager.reload_user(call.from_user.id)
    await call.answer("فعال شد" if enabled else "متوقف شد")
    await show_task(call.message, task_id, edit=True)


# ------------------------------------------------------------------ حذف
@router.callback_query(F.data.startswith("task:del:"))
async def cb_delete(call: CallbackQuery, state: FSMContext) -> None:
    task_id = int(call.data.split(":")[2])
    user = await get_or_create_user(call.from_user)
    await call.answer()
    if user.pin_hash:
        await state.set_state(Flow.pin_verify)
        await state.update_data(pin_action="task_delete", task_id=task_id)
        from telkap.texts import ASK_PIN_VERIFY

        await call.message.answer(ASK_PIN_VERIFY)
        return
    await call.message.answer(
        "🗑 این کار و همه‌ی تنظیماتش حذف شود؟",
        reply_markup=confirm(f"task:delyes:{task_id}", f"task:open:{task_id}"),
    )


@router.callback_query(F.data.startswith("task:delyes:"))
async def cb_delete_yes(call: CallbackQuery) -> None:
    task_id = int(call.data.split(":")[2])
    await call.answer()
    await delete_task_confirmed(call.message, task_id, user_id=call.from_user.id)


async def delete_task_confirmed(
    message: Message, task_id: int, user_id: int | None = None
) -> None:
    user_id = user_id or message.chat.id
    async with get_session() as db:
        task = await db.get(Task, task_id)
        if task is None or task.user_id != user_id:
            await message.answer("این کار پیدا نشد.")
            return
        title = task.title
        await db.delete(task)
        await db.commit()
    cache.invalidate_task(task_id)
    await manager.reload_user(user_id)
    await log_activity(user_id=user_id, event="task_delete", detail=title)
    await message.answer(f"🗑 کار «{title}» حذف شد.", reply_markup=main_menu())
