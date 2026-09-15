"""تنظیمات هر کار: پاک‌سازی، فیلترها، نوع محتوا، واترمارک، قواعد و مقادیر."""
from __future__ import annotations

import logging
from pathlib import Path

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy import select

from telkap.config import get_settings
from telkap.db import get_session
from telkap.handlers.common import Flow, parse_int
from telkap.handlers.tasks import show_task
from telkap.keyboards import (
    ai_menu,
    clean_menu,
    configs_menu,
    engagement_menu,
    filters_menu,
    media_menu,
    rules_menu,
    send_menu,
    text_menu,
    time_menu,
    watermark_menu,
)
from telkap.models import PendingPost, Rule, Task
from telkap.plans import CREDIT_AI, FEAT_WATERMARK
from telkap.services import aiskills, cache, credits, dedupe, pending, richtext
from telkap.services.defaults import merged_settings
from telkap.services.subscription import active_plan_for
from telkap.services.transform import utf16_len
from telkap.texts import (
    ASK_ALLOW_WORD,
    ASK_BLOCK_WORD,
    ASK_CONFIG_TAG,
    ASK_DELAY,
    ASK_ENGAGE_WAIT,
    ASK_FILE_RENAME,
    ASK_FOOTER,
    ASK_HEADER,
    ASK_MAX_HOUR,
    ASK_MAX_LEN,
    ASK_MIN_FORWARDS,
    ASK_MIN_GAP,
    ASK_MIN_LEN,
    ASK_MIN_REACTIONS,
    ASK_MIN_VIEWS,
    ASK_REPLACE_FROM,
    ASK_REPLACE_TO,
    ASK_SIGNATURE,
    ASK_SIMILARITY,
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
    "send": (
        "⚙️ <b>ارسال و ترافیک</b>\nشیوه و سرعت انتشار:\n\n"
        "<b>ترتیب انتشار</b>\n"
        "• <b>هوشمند</b> — به ترتیب مبدا، ولی اگر پستی (مثلاً ویدئوی "
        "سنگین) بیش از مهلت طول بکشد، بقیه جلو می‌روند و خودش هم بعداً "
        "می‌رسد. برای اغلب کانال‌ها بهترین است.\n"
        "• <b>دقیق</b> — همیشه به ترتیب. یک فایل سنگین همه‌ی پست‌های "
        "پشت سرش را نگه می‌دارد.\n"
        "• <b>سریع</b> — هرکدام زودتر آماده شد. ترتیب تضمینی نیست.\n\n"
        "<i>پستی که فقط کپی می‌شود اصلاً دانلود نمی‌شود و حجمش مهم "
        "نیست. کندی فقط وقتی پیش می‌آید که فایل باید دانلود و دوباره "
        "آپلود شود: واترمارک، بازنویسی فایل، یا مبدایی که «محافظت از "
        "محتوا» دارد.</i>",
        send_menu,
    ),
    "engage": (
        "📈 <b>فیلتر تعامل</b>\n"
        "فقط پست‌هایی کپی شوند که در کانال مبدا گرفته‌اند.\n\n"
        "<i>پستِ تازه هنوز بازدید ندارد، پس ربات اول صبر می‌کند، بعد "
        "پست را دوباره می‌خواند و آن‌وقت می‌سنجد. بدون تعیین مدت انتظار، "
        "آمار همان لحظه‌ی رسیدن ملاک است که معمولاً صفر است.</i>",
        engagement_menu,
    ),
    "cfg": (
        "🧩 <b>کانفیگ پروکسی</b>\n"
        "نام (تگ) کانفیگ‌های داخل پست را با نام کانال خودتان عوض می‌کند.\n\n"
        "<i>در <code>vmess</code> نام داخل یک بسته‌ی کدشده است و با "
        "«جایگزینی کلمات» عوض نمی‌شود؛ اینجا کانفیگ باز، نامش عوض و "
        "دوباره بسته می‌شود.</i>",
        configs_menu,
    ),
    "time": (
        "🕐 <b>زمان‌بندی و تأیید</b>\n"
        "کپی فقط در بازه‌ی زیر انجام می‌شود؛ اگر شروع و پایان برابر باشند "
        "کار ۲۴ ساعته است.\n\n"
        "<i>هر سه گزینه‌ی پایین اختیاری‌اند و پیش‌فرض خاموش‌اند — تا "
        "روشنشان نکنید، هیچ پستی معطل نمی‌ماند.</i>",
        time_menu,
    ),
    "ai": (
        "🤖 <b>هوش مصنوعی</b>\n"
        "هر پست پیش از انتشار از این مرحله‌ها رد می‌شود.\n\n"
        "<i>ترتیب اجرا: خلاصه ← بازنویسی ← ترجمه. هر مرحله روی خروجی "
        "مرحله‌ی قبل کار می‌کند، و هرکدام روی هر پست یک واحد اعتبار "
        "می‌برد. اگر اعتبار تمام شود یا سرویس جواب ندهد، پست بدون تغییر "
        "منتشر می‌شود — کپی هیچ‌وقت به‌خاطر این متوقف نمی‌شود.</i>",
        ai_menu,
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
    "min_gap_seconds": (ASK_MIN_GAP, "int"),
    "similarity_percent": (ASK_SIMILARITY, "int"),
    "engagement_wait_minutes": (ASK_ENGAGE_WAIT, "int"),
    "min_views": (ASK_MIN_VIEWS, "int"),
    "min_reactions": (ASK_MIN_REACTIONS, "int"),
    "min_forwards": (ASK_MIN_FORWARDS, "int"),
    "config_tag": (ASK_CONFIG_TAG, "text"),
    "file_rename": (ASK_FILE_RENAME, "text"),
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
    cache.invalidate_task(task_id)


async def _extras(panel: str, user_id: int) -> dict:
    """چیزهایی که یک پنل خاص علاوه بر تنظیمات لازم دارد.

    پنل هوش مصنوعی باید مانده‌ی اعتبار را نشان بدهد، وگرنه کاربر تازه
    وقتی می‌فهمد اعتبار ندارد که پست‌هایش بی‌تغییر رفته‌اند. بقیه‌ی
    پنل‌ها چیزی لازم ندارند، پس این پرسش برای آن‌ها اجرا نمی‌شود.
    """
    if panel != "ai":
        return {}
    return {"balance": await credits.balance(user_id, CREDIT_AI)}


async def _render(call: CallbackQuery, panel: str, task_id: int) -> None:
    task = await _owned_task(call.from_user.id, task_id)
    if task is None:
        await call.answer("دسترسی ندارید", show_alert=True)
        return
    title, builder = PANELS[panel]
    cfg = merged_settings(task.settings)
    markup = builder(task_id, cfg, **await _extras(panel, call.from_user.id))
    try:
        await call.message.edit_text(title, reply_markup=markup)
    except Exception:
        await call.message.answer(title, reply_markup=markup)


async def _render_message(message: Message, panel: str, task_id: int) -> None:
    """همان پنل، ولی به‌صورت پیام تازه (پس از یک مرحله‌ی گفتگویی)."""
    task = await _owned_task(message.from_user.id, task_id)
    if task is None:
        return
    title, builder = PANELS[panel]
    cfg = merged_settings(task.settings)
    markup = builder(task_id, cfg, **await _extras(panel, message.from_user.id))
    await message.answer(title, reply_markup=markup)


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
    "skip_cross_duplicates": "filters",
    "skip_bots": "filters",
    "skip_replies": "filters",
    "caption_only": "media",
    "watermark_enabled": "wm",
    "sync_edits": "send",
    "sync_deletes": "send",
    "copy_buttons": "send",
    "rewrite_configs": "cfg",
    "rewrite_files": "cfg",
    "approval": "time",
    "hold_outside_hours": "time",
    "ai_summarize": "ai",
    "ai_rewrite": "ai",
    "ai_translate": "ai",
}


def _cycle(values, current, fallback):
    """گزینه‌ی بعدی در یک چرخه. مقدار ناشناخته به اولی برمی‌گردد."""
    items = list(values)
    if current not in items:
        return fallback
    return items[(items.index(current) + 1) % len(items)]


@router.callback_query(F.data.startswith("aistyle:"))
async def cb_ai_style(call: CallbackQuery) -> None:
    """چرخش بین لحن‌های بازنویسی."""
    task_id = int(call.data.split(":")[1])
    task = await _owned_task(call.from_user.id, task_id)
    if task is None:
        await call.answer("دسترسی ندارید", show_alert=True)
        return
    cfg = merged_settings(task.settings)
    cfg["ai_style"] = _cycle(aiskills.STYLES, cfg.get("ai_style"), "same")
    await _save_settings(task_id, cfg)
    await call.answer(aiskills.STYLES[cfg["ai_style"]])
    await _render(call, "ai", task_id)


@router.callback_query(F.data.startswith("ailang:"))
async def cb_ai_language(call: CallbackQuery) -> None:
    """چرخش بین زبان‌های ترجمه."""
    task_id = int(call.data.split(":")[1])
    task = await _owned_task(call.from_user.id, task_id)
    if task is None:
        await call.answer("دسترسی ندارید", show_alert=True)
        return
    cfg = merged_settings(task.settings)
    cfg["ai_language"] = _cycle(aiskills.LANGUAGES, cfg.get("ai_language"), "en")
    await _save_settings(task_id, cfg)
    await call.answer(aiskills.LANGUAGES[cfg["ai_language"]])
    await _render(call, "ai", task_id)


@router.callback_query(F.data.startswith("aisent:"))
async def cb_ai_sentences(call: CallbackQuery) -> None:
    """چرخش تعداد جمله‌های خلاصه بین ۱ تا ۴."""
    task_id = int(call.data.split(":")[1])
    task = await _owned_task(call.from_user.id, task_id)
    if task is None:
        await call.answer("دسترسی ندارید", show_alert=True)
        return
    cfg = merged_settings(task.settings)
    current = int(cfg.get("ai_sentences") or 2)
    cfg["ai_sentences"] = current % 4 + 1
    await _save_settings(task_id, cfg)
    await call.answer()
    await _render(call, "ai", task_id)


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


@router.callback_query(F.data.startswith("adsens:"))
async def cb_ad_sensitivity(call: CallbackQuery) -> None:
    """چرخش بین سه سطح حساسیت فیلتر تبلیغات."""
    task_id = int(call.data.split(":")[1])
    task = await _owned_task(call.from_user.id, task_id)
    if task is None:
        await call.answer("دسترسی ندارید", show_alert=True)
        return
    order = ["low", "medium", "high"]
    cfg = merged_settings(task.settings)
    current = cfg.get("ad_sensitivity", "medium")
    cfg["ad_sensitivity"] = order[(order.index(current) + 1) % len(order)] if current in order else "medium"
    await _save_settings(task_id, cfg)
    await call.answer()
    await _render(call, "filters", task_id)


@router.callback_query(F.data.startswith("dupmode:"))
async def cb_duplicate_mode(call: CallbackQuery) -> None:
    """چرخش بین سه سطح سخت‌گیریِ تشخیص تکراری."""
    task_id = int(call.data.split(":")[1])
    task = await _owned_task(call.from_user.id, task_id)
    if task is None:
        await call.answer("دسترسی ندارید", show_alert=True)
        return
    cfg = merged_settings(task.settings)
    current = dedupe.mode_of(cfg)
    nxt = dedupe.MODES[(dedupe.MODES.index(current) + 1) % len(dedupe.MODES)]
    cfg["duplicate_mode"] = nxt
    await _save_settings(task_id, cfg)
    await call.answer(dedupe.MODE_LABELS[nxt])
    await _render(call, "filters", task_id)


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

    note = "روشن شد" if new_value else "خاموش شد"
    # خاموش کردن نگهدارنده‌ها باید صف را هم آزاد کند، وگرنه پست‌هایی که
    # منتظر مانده‌اند تا ابد آنجا می‌ماندند و کاربر فکر می‌کرد گم شده‌اند.
    if key in {"approval", "hold_outside_hours"} and not new_value:
        cleared = await pending.drop_task(
            task_id,
            reason=(
                PendingPost.REASON_APPROVAL
                if key == "approval"
                else PendingPost.REASON_SCHEDULE
            ),
        )
        if cleared:
            note = f"خاموش شد؛ {cleared} پستِ منتظر از صف پاک شد"

    await call.answer(note)
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


@router.callback_query(F.data.startswith("order:"))
async def cb_order(call: CallbackQuery) -> None:
    """حالت ترتیب انتشار را عوض می‌کند.

    سه حالت: «هوشمند» (پیش‌فرض)، «دقیق» و «سریع». توضیحشان در
    <code>Copier._worker</code> است.
    """
    _, mode, raw_id = call.data.split(":")
    if mode not in ("strict", "fast", "grace"):
        await call.answer("این حالت وجود ندارد", show_alert=True)
        return
    task_id = int(raw_id)
    task = await _owned_task(call.from_user.id, task_id)
    if task is None:
        await call.answer("دسترسی ندارید", show_alert=True)
        return
    cfg = merged_settings(task.settings)
    cfg["order_mode"] = mode
    await _save_settings(task_id, cfg)
    await call.answer(
        {
            "grace": "هوشمند: به ترتیب، ولی پشت پستِ کند نمی‌ماند",
            "strict": "دقیق: همیشه به ترتیب مبدا",
            "fast": "سریع: هرکدام زودتر آماده شد",
        }[mode],
        show_alert=True,
    )
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


@router.callback_query(F.data.startswith("wmkind:"))
async def cb_wm_kind(call: CallbackQuery) -> None:
    """جابه‌جایی بین واترمارک متنی و لوگوی تصویری."""
    _, kind, raw_id = call.data.split(":")
    task_id = int(raw_id)
    task = await _owned_task(call.from_user.id, task_id)
    if task is None or kind not in {"text", "logo"}:
        await call.answer("دسترسی ندارید", show_alert=True)
        return
    cfg = merged_settings(task.settings)
    cfg["watermark_kind"] = kind
    await _save_settings(task_id, cfg)
    await call.answer("واترمارک متنی" if kind == "text" else "واترمارک لوگو")
    await _render(call, "wm", task_id)


@router.callback_query(F.data.startswith("wmlogo:"))
async def cb_wm_logo_ask(call: CallbackQuery, state: FSMContext) -> None:
    task_id = int(call.data.split(":")[1])
    if await _owned_task(call.from_user.id, task_id) is None:
        await call.answer("دسترسی ندارید", show_alert=True)
        return
    await call.answer()
    await state.set_state(Flow.watermark_logo)
    await state.update_data(task_id=task_id)
    await call.message.answer(
        "🖼 <b>لوگوی واترمارک</b>\n\n"
        "تصویر لوگو را همین‌جا بفرستید. می‌توانید:\n"
        "• یک <b>عکس</b> بفرستید\n"
        "• یک <b>استیکر</b> بفرستید (مثلاً لوگوی کانالتان)\n"
        "• یا فایل <b>PNG</b> با پس‌زمینه‌ی شفاف — بهترین نتیجه را می‌دهد\n\n"
        "<i>اندازه و شفافیت را بعداً از همان منو تنظیم می‌کنید.</i>\n\n"
        "انصراف: /cancel"
    )


@router.message(Flow.watermark_logo)
async def got_wm_logo(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    task_id = int(data.get("task_id", 0))
    task = await _owned_task(message.from_user.id, task_id)
    if task is None:
        await state.clear()
        await message.answer("این کار پیدا نشد.")
        return

    file_id = None
    if message.photo:
        file_id = message.photo[-1].file_id
    elif message.sticker and not message.sticker.is_animated and not message.sticker.is_video:
        file_id = message.sticker.file_id
    elif message.document and (message.document.mime_type or "").startswith("image/"):
        file_id = message.document.file_id

    if file_id is None:
        await message.answer(
            "⚠️ یک <b>عکس</b>، <b>استیکر ساده</b> یا فایل تصویری بفرستید.\n"
            "<i>استیکر متحرک و ویدیویی پشتیبانی نمی‌شود.</i>"
        )
        return

    notice = await message.answer("⏳ در حال ذخیره‌ی لوگو…")
    path = await _download_logo(message, file_id, task_id)
    if path is None:
        await state.clear()
        await notice.edit_text("⚠️ دریافت فایل ناموفق بود. دوباره تلاش کنید.")
        return

    cfg = merged_settings(task.settings)
    cfg["watermark_logo"] = str(path)
    cfg["watermark_kind"] = "logo"
    if not cfg.get("watermark_size") or int(cfg.get("watermark_size", 4)) < 8:
        cfg["watermark_size"] = 15   # لوگو معمولاً بزرگ‌تر از متن دیده می‌شود
    await _save_settings(task_id, cfg)
    await state.clear()
    await notice.edit_text(
        "✅ لوگو ذخیره شد و واترمارک روی حالت لوگو رفت.\n\n"
        "حالا از منوی واترمارک موقعیت، اندازه و شفافیتش را تنظیم کنید."
    )
    await _render_message(message, "wm", task_id)


async def _download_logo(message: Message, file_id: str, task_id: int):
    """فایل لوگو را از تلگرام می‌گیرد و کنار دیتابیس ذخیره می‌کند."""
    logo_dir = get_settings().download_dir / "logos"
    logo_dir.mkdir(parents=True, exist_ok=True)
    target = logo_dir / f"task-{task_id}.png"
    try:
        info = await message.bot.get_file(file_id)
        await message.bot.download_file(info.file_path, destination=str(target))
    except Exception:
        log.exception("دانلود لوگوی واترمارک ناموفق بود")
        return None
    return target


@router.callback_query(F.data.startswith("wmlogodel:"))
async def cb_wm_logo_delete(call: CallbackQuery) -> None:
    task_id = int(call.data.split(":")[1])
    task = await _owned_task(call.from_user.id, task_id)
    if task is None:
        await call.answer("دسترسی ندارید", show_alert=True)
        return
    cfg = merged_settings(task.settings)
    old = cfg.get("watermark_logo")
    cfg["watermark_logo"] = ""
    await _save_settings(task_id, cfg)
    if old:
        Path(old).unlink(missing_ok=True)
    await call.answer("لوگو حذف شد")
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


def _strip_shift(raw: str) -> int:
    """چند واحد UTF-16 از ابتدای متن با strip حذف می‌شود.

    آفست entity ها نسبت به متنِ خام است. چون پیش از ذخیره strip
    می‌کنیم، بدون این تصحیح همه‌ی قالب‌ها به اندازه‌ی فاصله‌های ابتدایی
    جابه‌جا می‌مانند.
    """
    return utf16_len(raw) - utf16_len(raw.lstrip())


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

    # امضا و هدر و فوتر ممکن است ایموجی پریمیوم یا بولد داشته باشند.
    # آن‌ها متن نیستند، entity کنار متن‌اند — و تا امروز دور ریخته
    # می‌شدند، پس کاربر امضایش را ساده در کانالش می‌دید.
    if key in richtext.RICH_KEYS:
        cfg[richtext.entities_key(key)] = richtext.capture(
            message.entities, shift=_strip_shift(message.text or ""),
        ) if value else []

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
    cache.invalidate_task(task_id)
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
    cache.invalidate_task(task_id)
    await call.answer("حذف شد")
    rules = await _rules_of(task_id, kind)
    try:
        await call.message.edit_text(RULE_TITLES[kind], reply_markup=rules_menu(task_id, kind, rules))
    except Exception:
        await call.message.answer(RULE_TITLES[kind], reply_markup=rules_menu(task_id, kind, rules))
