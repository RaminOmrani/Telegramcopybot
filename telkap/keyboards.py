"""کیبوردهای ربات."""
from __future__ import annotations

from aiogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup,
)
from aiogram.utils.keyboard import InlineKeyboardBuilder

from telkap.models import Task
from telkap.plans import POPULAR_CODE, PURCHASABLE
from telkap.services.defaults import MEDIA_KINDS
from telkap.services.watermark import POSITIONS
from telkap.texts import fa_num, on_off

BTN_TASKS = "📋 کارهای کپی"
BTN_NEW_TASK = "➕ کار جدید"
BTN_FORWARD = "↪️ فوروارد پیشرفته"
BTN_ACCOUNT = "👤 حساب کاربری"
BTN_PLANS = "💳 خرید اشتراک"
BTN_LOGS = "🧾 گزارش فعالیت"
BTN_HELP = "📚 راهنما"


def main_menu() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=BTN_TASKS), KeyboardButton(text=BTN_NEW_TASK)],
            [KeyboardButton(text=BTN_FORWARD), KeyboardButton(text=BTN_ACCOUNT)],
            [KeyboardButton(text=BTN_PLANS), KeyboardButton(text=BTN_LOGS)],
            [KeyboardButton(text=BTN_HELP)],
        ],
        resize_keyboard=True,
        input_field_placeholder="یک گزینه را انتخاب کنید…",
    )


MEDIA_LABELS = {
    "text": "متن",
    "photo": "عکس",
    "video": "ویدیو",
    "animation": "گیف",
    "audio": "موزیک",
    "voice": "ویس",
    "document": "فایل",
    "sticker": "استیکر",
    "poll": "نظرسنجی",
    "video_note": "ویدیو‌پیام",
}


def tasks_list(tasks: list[Task]) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    for task in tasks:
        status = "🟢" if task.enabled else "🔴"
        title = task.title or task.source_title or task.source_ref
        kb.row(
            InlineKeyboardButton(
                text=f"{status} {title[:40]}", callback_data=f"task:open:{task.id}"
            )
        )
    kb.row(InlineKeyboardButton(text="➕ کار جدید", callback_data="task:new"))
    return kb.as_markup()


def task_menu(task: Task, *, backfill_running: bool = False) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    toggle = "⏸ توقف کار" if task.enabled else "▶️ فعال‌سازی"
    kb.row(InlineKeyboardButton(text=toggle, callback_data=f"task:toggle:{task.id}"))
    kb.row(
        InlineKeyboardButton(text="🧹 پاک‌سازی متن", callback_data=f"set:clean:{task.id}"),
        InlineKeyboardButton(text="🔤 جایگزینی کلمات", callback_data=f"rule:replace:{task.id}"),
    )
    kb.row(
        InlineKeyboardButton(text="🚦 فیلترها", callback_data=f"set:filters:{task.id}"),
        InlineKeyboardButton(text="🎞 نوع محتوا", callback_data=f"set:media:{task.id}"),
    )
    kb.row(
        InlineKeyboardButton(text="✍️ هدر / فوتر / امضا", callback_data=f"set:text:{task.id}"),
        InlineKeyboardButton(text="💧 واترمارک", callback_data=f"set:wm:{task.id}"),
    )
    kb.row(
        InlineKeyboardButton(text="⚙️ ارسال و ترافیک", callback_data=f"set:send:{task.id}"),
        InlineKeyboardButton(text="🕐 زمان‌بندی", callback_data=f"set:time:{task.id}"),
    )
    kb.row(
        InlineKeyboardButton(text="📤 کانال‌های مقصد", callback_data=f"dest:list:{task.id}"),
        InlineKeyboardButton(text="📈 آمار", callback_data=f"task:stats:{task.id}"),
    )
    kb.row(
        InlineKeyboardButton(text="🧪 تست تنظیمات", callback_data=f"task:test:{task.id}"),
        InlineKeyboardButton(text="📋 کپی تنظیمات", callback_data=f"clone:pick:{task.id}"),
    )
    if backfill_running:
        kb.row(
            InlineKeyboardButton(text="⏹ توقف کپی گذشته", callback_data=f"hist:cancel:{task.id}")
        )
    else:
        kb.row(
            InlineKeyboardButton(text="🕓 کپی پیام‌های گذشته", callback_data=f"hist:start:{task.id}")
        )
    kb.row(
        InlineKeyboardButton(text="🗑 حذف کار", callback_data=f"task:del:{task.id}"),
        InlineKeyboardButton(text="🔙 بازگشت", callback_data="task:list"),
    )
    return kb.as_markup()


def _flag(label: str, value: bool, cb: str) -> InlineKeyboardButton:
    return InlineKeyboardButton(text=f"{label}: {on_off(value)}", callback_data=cb)


def clean_menu(task_id: int, cfg: dict) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    options = [
        ("حذف لینک‌ها", "remove_links"),
        ("حذف هشتگ‌ها", "remove_hashtags"),
        ("حذف آیدی‌ها (@)", "remove_mentions"),
        ("حذف ایمیل‌ها", "remove_emails"),
        ("حذف ایموجی‌ها", "remove_emoji"),
        ("حذف امضای کانال مبدا", "remove_source_signature"),
    ]
    for label, key in options:
        kb.row(_flag(label, bool(cfg.get(key)), f"flag:{key}:{task_id}"))
    kb.row(InlineKeyboardButton(text="🔙 بازگشت", callback_data=f"task:open:{task_id}"))
    return kb.as_markup()


def filters_menu(task_id: int, cfg: dict) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    for label, key in [
        ("فیلتر پیام‌های تبلیغاتی", "block_ads"),
        ("رد کردن پست‌های فورواردی", "block_forwarded"),
        ("رد کردن پست‌های دارای لینک", "block_with_links"),
        ("رد کردن پست‌های دکمه‌دار", "block_with_buttons"),
        ("جلوگیری از پست تکراری", "skip_duplicates"),
        ("رد کردن پیام ربات‌ها (گروه)", "skip_bots"),
        ("رد کردن پیام‌های پاسخ (گروه)", "skip_replies"),
    ]:
        kb.row(_flag(label, bool(cfg.get(key)), f"flag:{key}:{task_id}"))
    if cfg.get("block_ads"):
        labels = {"low": "کم", "medium": "متوسط", "high": "زیاد"}
        current = cfg.get("ad_sensitivity", "medium")
        kb.row(
            InlineKeyboardButton(
                text=f"🎚 حساسیت فیلتر تبلیغات: {labels.get(current, 'متوسط')}",
                callback_data=f"adsens:{task_id}",
            )
        )
    kb.row(
        InlineKeyboardButton(text="🚫 کلمات ممنوعه", callback_data=f"rule:block:{task_id}"),
        InlineKeyboardButton(text="✅ کلمات مجاز", callback_data=f"rule:allow:{task_id}"),
    )
    kb.row(
        InlineKeyboardButton(
            text=f"📏 حداقل طول: {fa_num(int(cfg.get('min_length') or 0))}",
            callback_data=f"ask:min_length:{task_id}",
        ),
        InlineKeyboardButton(
            text=f"📐 حداکثر طول: {fa_num(int(cfg.get('max_length') or 0))}",
            callback_data=f"ask:max_length:{task_id}",
        ),
    )
    kb.row(InlineKeyboardButton(text="🔙 بازگشت", callback_data=f"task:open:{task_id}"))
    return kb.as_markup()


def media_menu(task_id: int, cfg: dict) -> InlineKeyboardMarkup:
    allowed = set(cfg.get("allowed_media") or [])
    kb = InlineKeyboardBuilder()
    row: list[InlineKeyboardButton] = []
    for kind in MEDIA_KINDS:
        mark = "✅" if kind in allowed else "❌"
        row.append(
            InlineKeyboardButton(
                text=f"{mark} {MEDIA_LABELS[kind]}", callback_data=f"media:{kind}:{task_id}"
            )
        )
        if len(row) == 2:
            kb.row(*row)
            row = []
    if row:
        kb.row(*row)
    kb.row(_flag("فقط متن (بدون رسانه)", bool(cfg.get("caption_only")), f"flag:caption_only:{task_id}"))
    kb.row(InlineKeyboardButton(text="🔙 بازگشت", callback_data=f"task:open:{task_id}"))
    return kb.as_markup()


def text_menu(task_id: int, cfg: dict) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.row(InlineKeyboardButton(text="🔝 متن ابتدای پست (هدر)", callback_data=f"ask:header:{task_id}"))
    kb.row(InlineKeyboardButton(text="🔻 متن انتهای پست (فوتر)", callback_data=f"ask:footer:{task_id}"))
    kb.row(InlineKeyboardButton(text="🖋 امضای جایگزین", callback_data=f"ask:signature:{task_id}"))
    kb.row(InlineKeyboardButton(text="🔙 بازگشت", callback_data=f"task:open:{task_id}"))
    return kb.as_markup()


def watermark_menu(task_id: int, cfg: dict) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.row(_flag("واترمارک", bool(cfg.get("watermark_enabled")), f"flag:watermark_enabled:{task_id}"))
    kb.row(InlineKeyboardButton(text="✏️ متن واترمارک", callback_data=f"ask:watermark_text:{task_id}"))
    row: list[InlineKeyboardButton] = []
    current = cfg.get("watermark_position", "bottom-right")
    for key, label in POSITIONS.items():
        mark = "🔘" if key == current else "⚪️"
        row.append(InlineKeyboardButton(text=f"{mark} {label}", callback_data=f"wmpos:{key}:{task_id}"))
        if len(row) == 2:
            kb.row(*row)
            row = []
    if row:
        kb.row(*row)
    opacity = int(cfg.get("watermark_opacity", 60))
    size = int(cfg.get("watermark_size", 4))
    kb.row(
        InlineKeyboardButton(text="➖", callback_data=f"wmop:-10:{task_id}"),
        InlineKeyboardButton(text=f"شفافیت: {fa_num(opacity)}٪", callback_data="noop"),
        InlineKeyboardButton(text="➕", callback_data=f"wmop:10:{task_id}"),
    )
    kb.row(
        InlineKeyboardButton(text="➖", callback_data=f"wmsz:-1:{task_id}"),
        InlineKeyboardButton(text=f"اندازه: {fa_num(size)}", callback_data="noop"),
        InlineKeyboardButton(text="➕", callback_data=f"wmsz:1:{task_id}"),
    )
    kb.row(InlineKeyboardButton(text="🔙 بازگشت", callback_data=f"task:open:{task_id}"))
    return kb.as_markup()


def send_menu(task_id: int, cfg: dict) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    mode = cfg.get("mode", "copy")
    kb.row(
        InlineKeyboardButton(
            text=("🔘" if mode == "copy" else "⚪️") + " کپی (بدون برچسب)",
            callback_data=f"mode:copy:{task_id}",
        ),
        InlineKeyboardButton(
            text=("🔘" if mode == "forward" else "⚪️") + " فوروارد",
            callback_data=f"mode:forward:{task_id}",
        ),
    )
    kb.row(
        InlineKeyboardButton(
            text=f"⏱ تأخیر: {fa_num(int(cfg.get('delay_seconds') or 0))} ثانیه",
            callback_data=f"ask:delay_seconds:{task_id}",
        )
    )
    kb.row(
        InlineKeyboardButton(
            text=f"📊 سقف ساعتی: {fa_num(int(cfg.get('max_per_hour') or 0))}",
            callback_data=f"ask:max_per_hour:{task_id}",
        )
    )
    kb.row(_flag("همگام‌سازی ویرایش‌ها", bool(cfg.get("sync_edits")), f"flag:sync_edits:{task_id}"))
    kb.row(_flag("همگام‌سازی حذف‌ها", bool(cfg.get("sync_deletes")), f"flag:sync_deletes:{task_id}"))
    kb.row(_flag("کپی دکمه‌های شیشه‌ای", bool(cfg.get("copy_buttons")), f"flag:copy_buttons:{task_id}"))
    kb.row(InlineKeyboardButton(text="🔙 بازگشت", callback_data=f"task:open:{task_id}"))
    return kb.as_markup()


def time_menu(task_id: int, cfg: dict) -> InlineKeyboardMarkup:
    start = int(cfg.get("active_from_hour") or 0)
    end = int(cfg.get("active_to_hour") or 0)
    kb = InlineKeyboardBuilder()
    kb.row(
        InlineKeyboardButton(text="➖", callback_data=f"hour:from:-1:{task_id}"),
        InlineKeyboardButton(text=f"شروع: {fa_num(start)}:۰۰", callback_data="noop"),
        InlineKeyboardButton(text="➕", callback_data=f"hour:from:1:{task_id}"),
    )
    kb.row(
        InlineKeyboardButton(text="➖", callback_data=f"hour:to:-1:{task_id}"),
        InlineKeyboardButton(text=f"پایان: {fa_num(end)}:۰۰", callback_data="noop"),
        InlineKeyboardButton(text="➕", callback_data=f"hour:to:1:{task_id}"),
    )
    kb.row(InlineKeyboardButton(text="🔄 ۲۴ ساعته", callback_data=f"hour:reset:0:{task_id}"))
    kb.row(InlineKeyboardButton(text="🔙 بازگشت", callback_data=f"task:open:{task_id}"))
    return kb.as_markup()


def destinations_menu(task_id: int, primary: str, extras: list) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.row(InlineKeyboardButton(text=f"⭐️ {primary[:40]} (اصلی)", callback_data="noop"))
    for dest in extras:
        mark = "🟢" if dest.enabled else "🔴"
        kb.row(
            InlineKeyboardButton(
                text=f"{mark} {(dest.title or dest.ref)[:32]}",
                callback_data=f"dest:toggle:{dest.id}:{task_id}",
            ),
            InlineKeyboardButton(text="🗑", callback_data=f"dest:del:{dest.id}:{task_id}"),
        )
    kb.row(InlineKeyboardButton(text="➕ افزودن مقصد", callback_data=f"dest:add:{task_id}"))
    kb.row(InlineKeyboardButton(text="🔙 بازگشت", callback_data=f"task:open:{task_id}"))
    return kb.as_markup()


def rules_menu(task_id: int, kind: str, rules: list) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    for rule in rules:
        label = (
            f"{rule.pattern} ← {rule.replacement or '(حذف)'}"
            if kind == "replace"
            else rule.pattern
        )
        kb.row(
            InlineKeyboardButton(text=f"🗑 {label[:50]}", callback_data=f"ruledel:{rule.id}:{task_id}")
        )
    kb.row(InlineKeyboardButton(text="➕ افزودن", callback_data=f"ruleadd:{kind}:{task_id}"))
    back = f"set:filters:{task_id}" if kind in {"block", "allow"} else f"task:open:{task_id}"
    kb.row(InlineKeyboardButton(text="🔙 بازگشت", callback_data=back))
    return kb.as_markup()


def confirm(yes_cb: str, no_cb: str) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.row(
        InlineKeyboardButton(text="✅ بله", callback_data=yes_cb),
        InlineKeyboardButton(text="❌ خیر", callback_data=no_cb),
    )
    return kb.as_markup()


def plans_menu() -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    for plan in PURCHASABLE:
        star = "⭐️ " if plan.code == POPULAR_CODE else ""
        kb.row(
            InlineKeyboardButton(
                text=f"{star}{plan.title} — {plan.price_label}",
                callback_data=f"plan:{plan.code}",
            )
        )
    return kb.as_markup()


def account_menu(logged_in: bool, has_pin: bool) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    if logged_in:
        kb.row(InlineKeyboardButton(text="🚪 خروج از حساب", callback_data="acc:logout"))
    else:
        kb.row(InlineKeyboardButton(text="🔐 اتصال اکانت", callback_data="acc:login"))
    kb.row(
        InlineKeyboardButton(
            text="🔒 غیرفعال‌سازی پین" if has_pin else "🔒 فعال‌سازی پین امنیتی",
            callback_data="acc:pin",
        )
    )
    kb.row(InlineKeyboardButton(text="💳 اشتراک من", callback_data="acc:sub"))
    return kb.as_markup()


def forward_menu(profile) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    if profile is None:
        kb.row(InlineKeyboardButton(text="➕ تعیین کانال مقصد", callback_data="fwd:new"))
        return kb.as_markup()
    toggle = "⏸ غیرفعال" if profile.enabled else "▶️ فعال"
    kb.row(InlineKeyboardButton(text=toggle, callback_data="fwd:toggle"))
    kb.row(
        InlineKeyboardButton(text="🧹 پاک‌سازی متن", callback_data="fwdset:clean"),
        InlineKeyboardButton(text="✍️ هدر / فوتر / امضا", callback_data="fwdset:text"),
    )
    kb.row(InlineKeyboardButton(text="🔁 تغییر مقصد", callback_data="fwd:new"))
    kb.row(InlineKeyboardButton(text="🗑 حذف پروفایل", callback_data="fwd:del"))
    return kb.as_markup()


def fwd_clean_menu(cfg: dict) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    for label, key in [
        ("حذف لینک‌ها", "remove_links"),
        ("حذف هشتگ‌ها", "remove_hashtags"),
        ("حذف آیدی‌ها (@)", "remove_mentions"),
        ("حذف امضای کانال مبدا", "remove_source_signature"),
    ]:
        kb.row(_flag(label, bool(cfg.get(key)), f"fwdflag:{key}"))
    kb.row(InlineKeyboardButton(text="🔙 بازگشت", callback_data="fwd:open"))
    return kb.as_markup()


def fwd_text_menu() -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.row(InlineKeyboardButton(text="🔝 هدر", callback_data="fwdask:header"))
    kb.row(InlineKeyboardButton(text="🔻 فوتر", callback_data="fwdask:footer"))
    kb.row(InlineKeyboardButton(text="🖋 امضای جایگزین", callback_data="fwdask:signature"))
    kb.row(InlineKeyboardButton(text="🔙 بازگشت", callback_data="fwd:open"))
    return kb.as_markup()
