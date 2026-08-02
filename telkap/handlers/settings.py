"""تنظیمات هر کار: پاک‌سازی، فیلترها، نوع محتوا، واترمارک، قواعد و مقادیر."""
from __future__ import annotations

import logging

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy import select

from telkap.db import get_session
from telkap.handlers.common import Flow, parse_int
from telkap.handlers.tasks import show_task
from telkap.keyboards import (
    clean_menu,
    filters_menu,
    media_menu,
    rules_menu,
    send_menu,
    text_menu,
    time_menu,
    watermark_menu,
)
from telkap.models import Rule, Task
from telkap.plans import FEAT_WATERMARK
from telkap.services.defaults import merged_settings
from telkap.services.subscription import active_plan_for
from telkap.texts import (
    ASK_ALLOW_WORD,
    ASK_BLOCK_WORD,
    ASK_DELAY,
    ASK_FOOTER,
    ASK_HEADER,
    ASK_MAX_HOUR,
    ASK_MAX_LEN,
    ASK_MIN_LEN,
    ASK_REPLACE_FROM,
    ASK_REPLACE_TO,
    ASK_SIGNATURE,
    ASK_WATERMARK_TEXT,
    INVALID_NUMBER,
)

log = logging.getLogger(__name__)
router = Router(name="settings")

PANELS = {
    "clean": ("🧹 <b>پاک‌سازی متن</b>\nمشخص کنید چه چیزهایی از پست‌ها حذف شوند:", clean_menu),
    "filters": ("🚦 <b>فیلترها</b>\nتعیین کنید کدام پست‌ها اصلاً کپی نشوند:", filters_menu),
    "media": ("🎞 <b>نوع محتوا</b>\nفقط انواع علامت‌خورده کپی می‌شوند:", media_menu),
    "text": ("✍️ <b>هدر، فوتر و امضا</b>\nمتن‌های ثابت پست‌ها:", text_menu),
    "wm": ("💧 <b>واترمارک تصاویر</b>\nروی عکس‌های ارسالی درج می‌شود:", watermark_menu),
    "send": ("⚙️ <b>ارسال و ترافیک</b>\nشیوه و سرعت انتشار:", send_menu),
    "time": (
        "🕐 <b>زمان‌بندی</b>\nفقط در این بازه کپی انجام می‌شود.\n"
        "اگر شروع و پایان برابر باشند، کار ۲۴ ساعته است:",
        time_menu,
    ),
}

# متن پرسش و اعتبارسنجی برای هر تنظیم متنی/عددی
ASK_SPECS = {
    "header": (ASK_HEADER, "text"),
    "footer": (ASK_FOOTER, "text"),
    "signature": (ASK_SIGNATURE, "text"),
    "watermark_text": (ASK_WATERMARK_TEXT, "text"),
    "delay_seconds": (ASK_DELAY, "int"),
    "max_per_hour": (ASK_MAX_HOUR, "int"),
    "min_length": (ASK_MIN_LEN, "int"),
    "max_length": (ASK_MAX_LEN, "int"),
}


async def _owned_task(user_id: int, task_id: int) -> Task | None:
    async with get_session() as db:
        task = await db.get(Task, task_id)
    return task if task and task.user_id == user_id else None


async def _save_settings(task_id: int, cfg: dict) -> None:
    async with get_session() as db:
        task = await db.get(Task, task_id)
        if task is None:
            return
        # فقط کلیدهای شناخته‌شده ذخیره می‌شوند
        task.settings = dict(cfg)
        await db.commit()


async def _render(call: CallbackQuery, panel: str, task_id: int) -> None:
    task = await _owned_task(call.from_user.id, task_id)
    if task is None:
        await call.answer("دسترسی ندارید", show_alert=True)
        return
    title, builder = PANELS[panel]
    cfg = merged_settings(task.settings)
    try:
        await call.message.edit_text(title, reply_markup=builder(task_id, cfg))
    except Exception:
        await call.message.answer(title, reply_markup=builder(task_id, cfg))


@router.callback_query(F.data.startswith("set:"))
async def cb_panel(call: CallbackQuery) -> None:
    _, panel, raw_id = call.data.split(":")
    await call.answer()
    await _render(call, panel, int(raw_id))


@router.callback_query(F.data == "noop")
async def cb_noop(call: CallbackQuery) -> None:
    await call.answer()


# ------------------------------------------------------------ کلیدهای بولی
FLAG_PANEL = {
    "remove_links": "clean",
    "remove_hashtags": "clean",
    "remove_mentions": "clean",
    "remove_emails": "clean",
    "remove_emoji": "clean",
    "remove_source_signature": "clean",
    "block_ads": "filters",
    "block_forwarded": "filters",
    "block_with_links": "filters",
    "block_with_buttons": "filters",
    "skip_duplicates": "filters",
    "caption_only": "media",
    "watermark_enabled": "wm",
    "sync_edits": "send",
    "sync_deletes": "send",
    "copy_buttons": "send",
}


@router.callback_query(F.data.startswith("hour:"))
async def cb_hours(call: CallbackQuery) -> None:
    """تنظیم بازه‌ی ساعتی فعال بودن کار."""
    _, field, raw_step, raw_id = call.data.split(":")
    task_id = int(raw_id)
    task = await _owned_task(call.from_user.id, task_id)
    if task is None:
        await call.answer("دسترسی ندارید", show_alert=True)
        return

    cfg = merged_settings(task.settings)
    if field == "reset":
        cfg["active_from_hour"] = 0
        cfg["active_to_hour"] = 0
    else:
        key = "active_from_hour" if field == "from" else "active_to_hour"
        cfg[key] = (int(cfg.get(key) or 0) + int(raw_step)) % 24

    await _save_settings(task_id, cfg)
    await call.answer()
    await _render(call, "time", task_id)


@router.callback_query(F.data.startswith("flag:"))
async def cb_flag(call: CallbackQuery) -> None:
    _, key, raw_id = call.data.split(":")
    task_id = int(raw_id)
    task = await _owned_task(call.from_user.id, task_id)
    if task is None or key not in FLAG_PANEL:
        await call.answer("دسترسی ندارید", show_alert=True)
        return

    cfg = merged_settings(task.settings)
    new_value = not bool(cfg.get(key))

    if key == "watermark_enabled" and new_value:
        plan = await active_plan_for(call.from_user.id)
        if plan is None or not plan.has(FEAT_WATERMARK):
            await call.answer(
                "واترمارک در پلن فعلی شما فعال نیست. پلن ۱۴ روزه یا بالاتر لازم است.",
                show_alert=True,
            )
            return

    cfg[key] = new_value
    await _save_settings(task_id, cfg)
    await call.answer("روشن شد" if new_value else "خاموش شد")
    await _render(call, FLAG_PANEL[key], task_id)


@router.callback_query(F.data.startswith("media:"))
async def cb_media(call: CallbackQuery) -> None:
    _, kind, raw_id = call.data.split(":")
    task_id = int(raw_id)
    task = await _owned_task(call.from_user.id, task_id)
    if task is None:
        await call.answer("دسترسی ندارید", show_alert=True)
        return
    cfg = merged_settings(task.settings)
    allowed = set(cfg.get("allowed_media") or [])
    if kind in allowed:
        allowed.discard(kind)
    else:
        allowed.add(kind)
    cfg["allowed_media"] = sorted(allowed)
    await _save_settings(task_id, cfg)
    await call.answer()
    await _render(call, "media", task_id)


@router.callback_query(F.data.startswith("mode:"))
async def cb_mode(call: CallbackQuery) -> None:
    _, mode, raw_id = call.data.split(":")
    task_id = int(raw_id)
    task = await _owned_task(call.from_user.id, task_id)
    if task is None:
        await call.answer("دسترسی ندارید", show_alert=True)
        return
    cfg = merged_settings(task.settings)
    cfg["mode"] = mode
    await _save_settings(task_id, cfg)
    await call.answer("ذخیره شد")
    await _render(call, "send", task_id)


@router.callback_query(F.data.startswith("wmpos:"))
async def cb_wm_position(call: CallbackQuery) -> None:
    _, position, raw_id = call.data.split(":")
    task_id = int(raw_id)
    task = await _owned_task(call.from_user.id, task_id)
    if task is None:
        await call.answer("دسترسی ندارید", show_alert=True)
        return
    cfg = merged_settings(task.settings)
    cfg["watermark_position"] = position
    await _save_settings(task_id, cfg)
    await call.answer()
    await _render(call, "wm", task_id)


@router.callback_query(F.data.startswith("wmop:"))
@router.callback_query(F.data.startswith("wmsz:"))
async def cb_wm_numbers(call: CallbackQuery) -> None:
    prefix, raw_step, raw_id = call.data.split(":")
    task_id = int(raw_id)
    task = await _owned_task(call.from_user.id, task_id)
    if task is None:
        await call.answer("دسترسی ندارید", show_alert=True)
        return
    cfg = merged_settings(task.settings)
    step = int(raw_step)
    if prefix == "wmop":
        cfg["watermark_opacity"] = max(10, min(100, int(cfg.get("watermark_opacity", 60)) + step))
    else:
        cfg["watermark_size"] = max(1, min(20, int(cfg.get("watermark_size", 4)) + step))
    await _save_settings(task_id, cfg)
    await call.answer()
    await _render(call, "wm", task_id)


# ------------------------------------------------------ مقادیر متنی/عددی
@router.callback_query(F.data.startswith("ask:"))
async def cb_ask(call: CallbackQuery, state: FSMContext) -> None:
    _, key, raw_id = call.data.split(":")
    task_id = int(raw_id)
    if key not in ASK_SPECS or await _owned_task(call.from_user.id, task_id) is None:
        await call.answer("دسترسی ندارید", show_alert=True)
        return
    prompt, _kind = ASK_SPECS[key]
    await state.set_state(Flow.ask_value)
    await state.update_data(ask_key=key, task_id=task_id)
    await call.answer()
    await call.message.answer(prompt + "\n\nانصراف: /cancel")


@router.message(Flow.ask_value)
async def got_value(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    key = data.get("ask_key")
    task_id = int(data.get("task_id", 0))
    if key not in ASK_SPECS:
        await state.clear()
        return

    _prompt, kind = ASK_SPECS[key]
    raw = (message.text or "").strip()

    if kind == "int":
        value = parse_int(raw)
        if value is None or value < 0:
            await message.answer(INVALID_NUMBER)
            return
        if key == "delay_seconds":
            value = min(value, 3600)
    else:
        value = "" if raw == "." else raw[:1000]

    task = await _owned_task(message.from_user.id, task_id)
    if task is None:
        await state.clear()
        await message.answer("این کار پیدا نشد.")
        return

    cfg = merged_settings(task.settings)
    cfg[key] = value
    await _save_settings(task_id, cfg)
    await state.clear()
    await message.answer("✅ ذخیره شد.")
    await show_task(message, task_id)


# ------------------------------------------------------------------ قواعد
RULE_PROMPTS = {"block": ASK_BLOCK_WORD, "allow": ASK_ALLOW_WORD}
RULE_TITLES = {
    "replace": "🔤 <b>جایگزینی کلمات</b>\nهر کلمه‌ای که در پست باشد با معادل شما جایگزین می‌شود:",
    "block": "🚫 <b>کلمات ممنوعه</b>\nپستی که شامل این کلمات باشد کپی نمی‌شود:",
    "allow": "✅ <b>کلمات مجاز</b>\nفقط پست‌هایی که یکی از این کلمات را دارند کپی می‌شوند:",
}


async def _rules_of(task_id: int, kind: str) -> list[Rule]:
    async with get_session() as db:
        rows = await db.execute(
            select(Rule).where(Rule.task_id == task_id, Rule.kind == kind).order_by(Rule.id)
        )
        return list(rows.scalars())


@router.callback_query(F.data.startswith("rule:"))
async def cb_rules(call: CallbackQuery) -> None:
    _, kind, raw_id = call.data.split(":")
    task_id = int(raw_id)
    if await _owned_task(call.from_user.id, task_id) is None:
        await call.answer("دسترسی ندارید", show_alert=True)
        return
    rules = await _rules_of(task_id, kind)
    await call.answer()
    try:
        await call.message.edit_text(RULE_TITLES[kind], reply_markup=rules_menu(task_id, kind, rules))
    except Exception:
        await call.message.answer(RULE_TITLES[kind], reply_markup=rules_menu(task_id, kind, rules))


@router.callback_query(F.data.startswith("ruleadd:"))
async def cb_rule_add(call: CallbackQuery, state: FSMContext) -> None:
    _, kind, raw_id = call.data.split(":")
    task_id = int(raw_id)
    if await _owned_task(call.from_user.id, task_id) is None:
        await call.answer("دسترسی ندارید", show_alert=True)
        return
    await call.answer()
    await state.update_data(task_id=task_id, rule_kind=kind)
    if kind == "replace":
        await state.set_state(Flow.rule_from)
        await call.message.answer(ASK_REPLACE_FROM)
    else:
        await state.set_state(Flow.rule_word)
        await call.message.answer(RULE_PROMPTS[kind])


@router.message(Flow.rule_from)
async def got_rule_from(message: Message, state: FSMContext) -> None:
    pattern = (message.text or "").strip()
    if not pattern:
        await message.answer("⚠️ متن خالی است.")
        return
    await state.update_data(rule_pattern=pattern[:512])
    await state.set_state(Flow.rule_to)
    await message.answer(ASK_REPLACE_TO)


@router.message(Flow.rule_to)
async def got_rule_to(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    raw = (message.text or "").strip()
    replacement = "" if raw == "." else raw[:512]
    await _add_rule(message, state, data, "replace", data["rule_pattern"], replacement)


@router.message(Flow.rule_word)
async def got_rule_word(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    pattern = (message.text or "").strip()
    if not pattern:
        await message.answer("⚠️ متن خالی است.")
        return
    await _add_rule(message, state, data, data["rule_kind"], pattern[:512], "")


async def _add_rule(
    message: Message, state: FSMContext, data: dict, kind: str, pattern: str, replacement: str
) -> None:
    task_id = int(data.get("task_id", 0))
    if await _owned_task(message.from_user.id, task_id) is None:
        await state.clear()
        await message.answer("این کار پیدا نشد.")
        return
    async with get_session() as db:
        db.add(Rule(task_id=task_id, kind=kind, pattern=pattern, replacement=replacement))
        await db.commit()
    await state.clear()
    rules = await _rules_of(task_id, kind)
    await message.answer(
        "✅ اضافه شد.\n\n" + RULE_TITLES[kind], reply_markup=rules_menu(task_id, kind, rules)
    )


@router.callback_query(F.data.startswith("ruledel:"))
async def cb_rule_delete(call: CallbackQuery) -> None:
    _, raw_rule, raw_task = call.data.split(":")
    task_id = int(raw_task)
    if await _owned_task(call.from_user.id, task_id) is None:
        await call.answer("دسترسی ندارید", show_alert=True)
        return
    async with get_session() as db:
        rule = await db.get(Rule, int(raw_rule))
        kind = rule.kind if rule else "replace"
        if rule and rule.task_id == task_id:
            await db.delete(rule)
            await db.commit()
    await call.answer("حذف شد")
    rules = await _rules_of(task_id, kind)
    try:
        await call.message.edit_text(RULE_TITLES[kind], reply_markup=rules_menu(task_id, kind, rules))
    except Exception:
        await call.message.answer(RULE_TITLES[kind], reply_markup=rules_menu(task_id, kind, rules))
