"""چند مقصد برای یک کار: یک بار خواندن مبدا، انتشار در چند کانال."""
from __future__ import annotations

import logging

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder
from sqlalchemy import func, select

from telkap.db import get_session, log_activity
from telkap.handlers.common import Flow
from telkap.keyboards import destinations_menu
from telkap.models import Destination, Task
from telkap.services import cache, routing
from telkap.services.defaults import merged_settings
from telkap.services.subscription import active_plan_for
from telkap.services.userbot import manager
from telkap.texts import NO_LOGIN, fa_num

log = logging.getLogger(__name__)
router = Router(name="destinations")

MAX_EXTRA_DESTS = 9


async def _owned(user_id: int, task_id: int) -> Task | None:
    async with get_session() as db:
        task = await db.get(Task, task_id)
    return task if task and task.user_id == user_id else None


async def _extras(task_id: int) -> list[Destination]:
    async with get_session() as db:
        rows = await db.execute(
            select(Destination).where(Destination.task_id == task_id).order_by(Destination.id)
        )
        return list(rows.scalars())


async def _render(target: Message, task: Task, *, edit: bool = False) -> None:
    extras = await _extras(task.id)
    primary = task.dest_title or task.dest_ref
    plan = await active_plan_for(task.user_id)
    active = 1 + sum(1 for d in extras if d.enabled)
    cap = (
        f" از {fa_num(plan.max_destinations)} مجاز در طرح «{plan.title}»"
        if plan
        else ""
    )
    text = (
        "📤 <b>کانال‌های مقصد</b>\n\n"
        f"هر پست «{task.source_title or task.source_ref}» در همه‌ی کانال‌های "
        f"زیر منتشر می‌شود — مبدا فقط یک بار خوانده می‌شود.\n\n"
        f"🟢 فعال: {fa_num(active)} کانال{cap}"
    )
    markup = destinations_menu(task.id, primary, extras)
    if edit:
        try:
            await target.edit_text(text, reply_markup=markup)
            return
        except Exception:
            log.debug("ویرایش پیام مقصدها ناموفق بود", exc_info=True)
    await target.answer(text, reply_markup=markup)


@router.callback_query(F.data.startswith("dest:list:"))
async def cb_list(call: CallbackQuery) -> None:
    task_id = int(call.data.split(":")[2])
    task = await _owned(call.from_user.id, task_id)
    if task is None:
        await call.answer("دسترسی ندارید", show_alert=True)
        return
    await call.answer()
    await _render(call.message, task, edit=True)


@router.callback_query(F.data.startswith("dest:add:"))
async def cb_add(call: CallbackQuery, state: FSMContext) -> None:
    task_id = int(call.data.split(":")[2])
    if await _owned(call.from_user.id, task_id) is None:
        await call.answer("دسترسی ندارید", show_alert=True)
        return

    plan = await active_plan_for(call.from_user.id)
    if plan is None:
        await call.answer("اشتراک فعالی ندارید.", show_alert=True)
        return

    async with get_session() as db:
        count = await db.scalar(
            select(func.count(Destination.id)).where(Destination.task_id == task_id)
        )
    # سقف پلن و سقف فنی، هرکدام کمتر بود
    allowed_extra = min(plan.extra_destinations, MAX_EXTRA_DESTS)
    if (count or 0) >= allowed_extra:
        await call.answer(
            f"در طرح «{plan.title}» هر کار می‌تواند {fa_num(plan.max_destinations)} "
            "کانال مقصد داشته باشد.\nبرای بیشتر، طرح بالاتری بگیرید.",
            show_alert=True,
        )
        return

    await call.answer()
    await state.set_state(Flow.dest_add)
    await state.update_data(task_id=task_id)
    await call.message.answer(
        "📤 آیدی یا لینک کانال مقصد جدید را بفرستید.\n"
        "⚠️ اکانت شما باید در آن اجازه‌ی ارسال داشته باشد.\n\nانصراف: /cancel"
    )


@router.message(Flow.dest_add)
async def got_dest(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    task_id = int(data.get("task_id", 0))
    task = await _owned(message.from_user.id, task_id)
    if task is None:
        await state.clear()
        await message.answer("این کار پیدا نشد.")
        return

    ref = (message.text or "").strip()
    client = await manager.ensure_client(message.from_user.id)
    if client is None:
        await state.clear()
        await message.answer(NO_LOGIN)
        return

    notice = await message.answer("⏳ در حال بررسی کانال…")
    entity = await manager.resolve_entity(client, ref)
    if entity is None:
        await notice.edit_text("⚠️ کانال پیدا نشد یا اکانت شما به آن دسترسی ندارد.")
        return

    chat_id = await manager.resolve_chat_id(client, ref)
    title = getattr(entity, "title", None) or ref

    # مقصد تکراری نگذاریم
    if str(chat_id) == str(task.dest_id) or ref == task.dest_ref:
        await notice.edit_text("⚠️ این همان کانال مقصد اصلی است.")
        return
    for existing in await _extras(task_id):
        if str(existing.chat_id) == str(chat_id) or existing.ref == ref:
            await notice.edit_text("⚠️ این کانال قبلاً اضافه شده است.")
            return

    async with get_session() as db:
        db.add(
            Destination(
                task_id=task_id, chat_id=chat_id, ref=ref, title=title[:160]
            )
        )
        await db.commit()
    cache.invalidate_task(task_id)

    await state.clear()
    await notice.edit_text(f"✅ کانال <b>{title}</b> به مقصدها اضافه شد.")
    await log_activity(
        user_id=message.from_user.id, task_id=task_id, event="dest_add", detail=ref
    )
    task = await _owned(message.from_user.id, task_id)
    await _render(message, task)


@router.callback_query(F.data.startswith("dest:toggle:"))
async def cb_toggle(call: CallbackQuery) -> None:
    _, _, raw_dest, raw_task = call.data.split(":")
    task = await _owned(call.from_user.id, int(raw_task))
    if task is None:
        await call.answer("دسترسی ندارید", show_alert=True)
        return
    async with get_session() as db:
        dest = await db.get(Destination, int(raw_dest))
        if dest is None or dest.task_id != task.id:
            await call.answer("این مقصد پیدا نشد.", show_alert=True)
            return
        dest.enabled = not dest.enabled
        enabled = dest.enabled
        await db.commit()
    cache.invalidate_task(task.id)
    await call.answer("فعال شد" if enabled else "خاموش شد")
    await _render(call.message, task, edit=True)


@router.callback_query(F.data.startswith("dest:del:"))
async def cb_delete(call: CallbackQuery) -> None:
    _, _, raw_dest, raw_task = call.data.split(":")
    task = await _owned(call.from_user.id, int(raw_task))
    if task is None:
        await call.answer("دسترسی ندارید", show_alert=True)
        return
    async with get_session() as db:
        dest = await db.get(Destination, int(raw_dest))
        if dest is not None and dest.task_id == task.id:
            await db.delete(dest)
            await db.commit()
    cache.invalidate_task(task.id)
    await call.answer("حذف شد")
    await _render(call.message, task, edit=True)


# --------------------------------------------- مسیریابی مقصد اصلی
async def _render_main(target: Message, task: Task, *, edit: bool = False) -> None:
    cfg = merged_settings(task.settings)
    kb = InlineKeyboardBuilder()
    for key, label in ROUTE_FIELDS.items():
        mark = "✅" if cfg.get(key) else "▫️"
        kb.row(
            InlineKeyboardButton(
                text=f"{mark} {label}", callback_data=f"droute:{key}:{task.id}"
            )
        )
    kb.row(InlineKeyboardButton(text="🔙 بازگشت", callback_data=f"dest:list:{task.id}"))

    text = (
        f"⭐️ <b>مقصد اصلی: {task.dest_title or task.dest_ref}</b>\n\n"
        f"<b>مسیریابی</b>\n{routing.describe(cfg)}\n\n"
    )
    if routing.is_filtered(cfg):
        text += (
            "<i>این شرط‌ها روی همه‌ی مقصدهایی که شرط خودشان را ندارند هم "
            "اعمال می‌شوند.</i>"
        )
    else:
        text += (
            "<i>الان هر پستی که از فیلترهای کار رد شود اینجا منتشر می‌گردد. "
            "با کلمه‌ی کلیدی می‌توانید محتوا را بین کانال‌هایتان تقسیم کنید — "
            "مثلاً پست‌های «تخفیف» به یک کانال و بقیه به کانال دیگر.</i>"
        )

    markup = kb.as_markup()
    if edit:
        try:
            await target.edit_text(text, reply_markup=markup)
            return
        except Exception:
            log.debug("ویرایش صفحه‌ی مقصد اصلی ناموفق بود", exc_info=True)
    await target.answer(text, reply_markup=markup)


@router.callback_query(F.data.startswith("dmain:"))
async def cb_main(call: CallbackQuery) -> None:
    task = await _owned(call.from_user.id, int(call.data.split(":")[1]))
    if task is None:
        await call.answer("دسترسی ندارید", show_alert=True)
        return
    await call.answer()
    await _render_main(call.message, task, edit=True)


@router.callback_query(F.data.startswith("droute:"))
async def cb_main_route(call: CallbackQuery, state: FSMContext) -> None:
    _, key, raw_task = call.data.split(":")
    task = await _owned(call.from_user.id, int(raw_task))
    if task is None or key not in ROUTE_FIELDS:
        await call.answer("دسترسی ندارید", show_alert=True)
        return
    await call.answer()
    await state.set_state(Flow.task_route)
    await state.update_data(task_id=task.id, field=key)
    sense = (
        "فقط پستی که <b>یکی از</b> این کلمه‌ها را داشته باشد منتشر می‌شود"
        if key == "route_words"
        else "پستی که <b>یکی از</b> این کلمه‌ها را داشته باشد منتشر نمی‌شود"
    )
    await call.message.answer(
        f"{ROUTE_FIELDS[key]} — مقصد اصلی\n\n{sense}.\n\n"
        "کلمه‌ها را با ویرگول یا در خطهای جدا بنویسید:\n"
        "<code>فروش، تخفیف، کد هدیه</code>\n\n"
        "<i>بزرگی و کوچکی حروف مهم نیست و بخشی از کلمه هم کافی است.</i>\n\n"
        "برای برداشتن این شرط، یک نقطه <code>.</code> بفرستید.\n"
        "انصراف: /cancel"
    )


@router.message(Flow.task_route)
async def got_main_route(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    field = data.get("field")
    task = await _owned(message.from_user.id, int(data.get("task_id", 0)))
    if task is None or field not in ROUTE_FIELDS:
        await state.clear()
        await message.answer("این کار پیدا نشد.")
        return

    raw = (message.text or "").strip()
    words = [] if raw == "." else routing.parse_words(raw)
    if raw != "." and not words:
        await message.answer("کلمه‌ای پیدا نکردم. دوباره بفرستید یا /cancel بزنید.")
        return

    async with get_session() as db:
        row = await db.get(Task, task.id)
        cfg = merged_settings(row.settings)
        cfg[field] = words
        row.settings = cfg
        await db.commit()
    cache.invalidate_task(task.id)

    await state.clear()
    await message.answer(
        f"✅ ثبت شد: {'، '.join(words)}" if words else "✅ این شرط برداشته شد."
    )
    task = await _owned(message.from_user.id, task.id)
    await _render_main(message, task)


# ------------------------------------------------- امضای اختصاصی هر مقصد
SIG_FIELDS = {
    "footer": "🔻 فوتر",
    "signature": "🖋 امضای جایگزین",
    "header": "🔝 هدر",
}


# مسیریابی: چه پستی به این کانال برسد و چه پستی نه
ROUTE_FIELDS = {
    "route_words": "🎯 فقط پست‌های دارای این کلمه‌ها",
    "route_skip": "🚫 پست‌های دارای این کلمه‌ها را نفرست",
}


def _sig_menu(dest: Destination, task_id: int) -> InlineKeyboardBuilder:
    kb = InlineKeyboardBuilder()
    overrides = dest.overrides or {}
    for key, label in SIG_FIELDS.items():
        mark = "✅" if overrides.get(key) else "▫️"
        kb.row(
            InlineKeyboardButton(
                text=f"{mark} {label}", callback_data=f"dsig:set:{key}:{dest.id}:{task_id}"
            )
        )
    for key, label in ROUTE_FIELDS.items():
        mark = "✅" if overrides.get(key) else "▫️"
        kb.row(
            InlineKeyboardButton(
                text=f"{mark} {label}",
                callback_data=f"dsig:route:{key}:{dest.id}:{task_id}",
            )
        )
    if overrides:
        kb.row(
            InlineKeyboardButton(
                text="♻️ برگرداندن به تنظیمات کار",
                callback_data=f"dsig:clear:x:{dest.id}:{task_id}",
            )
        )
    kb.row(InlineKeyboardButton(text="🔙 بازگشت", callback_data=f"dest:list:{task_id}"))
    return kb


def _sig_text(dest: Destination) -> str:
    overrides = dest.overrides or {}
    lines = [
        f"⚙️ <b>تنظیمات اختصاصی «{dest.title or dest.ref}»</b>\n",
        "این مقادیر فقط برای همین کانال اعمال می‌شوند و جای تنظیمات کار را می‌گیرند.",
        "بقیه‌ی تنظیمات (فیلترها، جایگزینی‌ها، واترمارک) از خود کار می‌آید.\n",
    ]
    for key, label in SIG_FIELDS.items():
        value = overrides.get(key)
        lines.append(f"{label}: {('<code>' + value + '</code>') if value else '— از تنظیمات کار —'}")

    lines.append(f"\n<b>مسیریابی</b>\n{routing.describe(overrides)}")
    if not routing.is_filtered(overrides):
        lines.append(
            "<i>یعنی هر پستی که از فیلترهای کار رد شود، به این کانال هم "
            "می‌رود. با کلمه‌ی کلیدی می‌توانید فقط بخشی از پست‌ها را "
            "به اینجا بفرستید.</i>"
        )
    return "\n".join(lines)


async def _render_sig(target: Message, dest_id: int, task_id: int) -> None:
    async with get_session() as db:
        dest = await db.get(Destination, dest_id)
    if dest is None or dest.task_id != task_id:
        await target.answer("این مقصد پیدا نشد.")
        return
    markup = _sig_menu(dest, task_id).as_markup()
    try:
        await target.edit_text(_sig_text(dest), reply_markup=markup)
    except Exception:
        await target.answer(_sig_text(dest), reply_markup=markup)


@router.callback_query(F.data.startswith("dest:sig:"))
async def cb_sig(call: CallbackQuery) -> None:
    _, _, raw_dest, raw_task = call.data.split(":")
    task = await _owned(call.from_user.id, int(raw_task))
    if task is None:
        await call.answer("دسترسی ندارید", show_alert=True)
        return
    await call.answer()
    await _render_sig(call.message, int(raw_dest), task.id)


@router.callback_query(F.data.startswith("dsig:"))
async def cb_sig_action(call: CallbackQuery, state: FSMContext) -> None:
    _, action, key, raw_dest, raw_task = call.data.split(":")
    task_id, dest_id = int(raw_task), int(raw_dest)
    task = await _owned(call.from_user.id, task_id)
    if task is None:
        await call.answer("دسترسی ندارید", show_alert=True)
        return

    if action == "clear":
        async with get_session() as db:
            dest = await db.get(Destination, dest_id)
            if dest is not None and dest.task_id == task_id:
                dest.overrides = {}
                await db.commit()
        cache.invalidate_task(task_id)
        await call.answer("پاک شد")
        await _render_sig(call.message, dest_id, task_id)
        return

    if action == "route":
        if key not in ROUTE_FIELDS:
            await call.answer()
            return
        await call.answer()
        await state.set_state(Flow.dest_route)
        await state.update_data(dest_id=dest_id, task_id=task_id, field=key)
        sense = (
            "فقط پستی که <b>یکی از</b> این کلمه‌ها را داشته باشد به این "
            "کانال می‌رود"
            if key == "route_words"
            else "پستی که <b>یکی از</b> این کلمه‌ها را داشته باشد به این "
            "کانال نمی‌رود"
        )
        await call.message.answer(
            f"{ROUTE_FIELDS[key]}\n\n{sense}.\n\n"
            "کلمه‌ها را با ویرگول یا در خطهای جدا بنویسید:\n"
            "<code>فروش، تخفیف، کد هدیه</code>\n\n"
            "<i>بزرگی و کوچکی حروف مهم نیست و بخشی از کلمه هم کافی است.</i>\n\n"
            "برای برداشتن این شرط، یک نقطه <code>.</code> بفرستید.\n"
            "انصراف: /cancel"
        )
        return

    if key not in SIG_FIELDS:
        await call.answer()
        return
    await call.answer()
    await state.set_state(Flow.dest_override)
    await state.update_data(dest_id=dest_id, task_id=task_id, field=key)
    await call.message.answer(
        f"{SIG_FIELDS[key]} اختصاصی این کانال را بفرستید.\n"
        "برای برگشت به تنظیمات کار، یک نقطه <code>.</code> بفرستید.\n\n"
        "انصراف: /cancel"
    )


@router.message(Flow.dest_route)
async def got_route(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    task_id, dest_id = int(data.get("task_id", 0)), int(data.get("dest_id", 0))
    field = data.get("field")
    if await _owned(message.from_user.id, task_id) is None or field not in ROUTE_FIELDS:
        await state.clear()
        await message.answer("این مقصد پیدا نشد.")
        return

    raw = (message.text or "").strip()
    words = [] if raw == "." else routing.parse_words(raw)
    if raw != "." and not words:
        await message.answer("کلمه‌ای پیدا نکردم. دوباره بفرستید یا /cancel بزنید.")
        return

    async with get_session() as db:
        dest = await db.get(Destination, dest_id)
        if dest is None or dest.task_id != task_id:
            await state.clear()
            await message.answer("این مقصد پیدا نشد.")
            return
        overrides = dict(dest.overrides or {})
        if words:
            overrides[field] = words
        else:
            overrides.pop(field, None)
        dest.overrides = overrides
        await db.commit()

    cache.invalidate_task(task_id)
    await state.clear()
    await message.answer(
        f"✅ ثبت شد: {'، '.join(words)}" if words else "✅ این شرط برداشته شد."
    )
    await _render_sig(message, dest_id, task_id)


@router.message(Flow.dest_override)
async def got_override(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    task_id, dest_id = int(data.get("task_id", 0)), int(data.get("dest_id", 0))
    field = data.get("field")
    if await _owned(message.from_user.id, task_id) is None or field not in SIG_FIELDS:
        await state.clear()
        await message.answer("این مقصد پیدا نشد.")
        return

    raw = (message.text or "").strip()
    async with get_session() as db:
        dest = await db.get(Destination, dest_id)
        if dest is None or dest.task_id != task_id:
            await state.clear()
            await message.answer("این مقصد پیدا نشد.")
            return
        overrides = dict(dest.overrides or {})
        if raw == ".":
            overrides.pop(field, None)
        else:
            overrides[field] = raw[:1000]
        dest.overrides = overrides
        await db.commit()
        await db.refresh(dest)

    cache.invalidate_task(task_id)
    await state.clear()
    await log_activity(
        user_id=message.from_user.id,
        task_id=task_id,
        event="dest_override",
        detail=f"{dest.ref}: {field}",
    )
    await message.answer(_sig_text(dest), reply_markup=_sig_menu(dest, task_id).as_markup())
