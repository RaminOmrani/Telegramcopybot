"""کیبوردهای ربات."""
from __future__ import annotations

from aiogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup,
)
from aiogram.utils.keyboard import InlineKeyboardBuilder

from telkap import i18n
from telkap.models import Task
from telkap.plans import (
    CREDIT_KINDS,
    CREDIT_PACKS,
    POPULAR_CODE,
    credit_price,
    credit_unit,
    purchasable,
    toman,
)
from telkap.services import ai, aiskills, dedupe
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
BTN_SUPPORT = "🛟 پشتیبانی"
BTN_WALLET = "👛 کیف پول و دعوت"


def main_menu(lang: str | None = None) -> ReplyKeyboardMarkup:
    # منوی اصلی عمداً روی ۸ دکمه نگه داشته می‌شود؛ «گزارش فعالیت» که کم
    # استفاده است به «حساب کاربری» منتقل شد تا جای کیف پول باز شود.
    def btn(key: str) -> KeyboardButton:
        return KeyboardButton(text=i18n.t(key, lang))

    return ReplyKeyboardMarkup(
        keyboard=[
            [btn("menu.new_task"), btn("menu.tasks")],
            [btn("menu.forward"), btn("menu.account")],
            [btn("menu.plans"), btn("menu.wallet")],
            [btn("menu.help"), btn("menu.support")],
        ],
        resize_keyboard=True,
        input_field_placeholder=i18n.t("menu.placeholder", lang),
    )


def menu_texts(key: str) -> set[str]:
    """همه‌ی ترجمه‌های یک دکمه — برای فیلتر کردن پیام‌های ورودی.

    کاربری که زبانش را عوض کرده ممکن است هنوز دکمه‌ی قدیمی را ببیند، پس
    هندلرها باید همه‌ی زبان‌ها را بشناسند.
    """
    return {i18n.t(key, code) for code in i18n.LANGS}


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


def tasks_list(tasks: list[Task], *, dest_counts: dict[int, int] | None = None) -> InlineKeyboardMarkup:
    """فهرست کارها؛ کنار هر کار وضعیت و تعداد مقصدهایش دیده می‌شود."""
    kb = InlineKeyboardBuilder()
    counts = dest_counts or {}
    for task in tasks:
        status = "🟢" if task.enabled else "🔴"
        title = task.title or task.source_title or task.source_ref
        dests = counts.get(task.id, 1)
        badge = f" · 📤{fa_num(dests)}" if dests > 1 else ""
        kb.row(
            InlineKeyboardButton(
                text=f"{status} {title[:36]}{badge}", callback_data=f"task:open:{task.id}"
            )
        )
    kb.row(InlineKeyboardButton(text="➕ ساخت کار جدید", callback_data="task:new"))
    return kb.as_markup()


def _divider(label: str) -> InlineKeyboardButton:
    """خط جداکننده‌ی تزئینی؛ فشردنش کاری نمی‌کند."""
    return InlineKeyboardButton(text=f"─── {label} ───", callback_data="noop")


def task_menu(
    task: Task,
    *,
    backfill_running: bool = False,
    waiting: int = 0,
    pro: bool = False,
) -> InlineKeyboardMarkup:
    """منوی یک کار.

    در حالت ساده فقط چیزهایی هست که تقریباً همه لازمشان دارند. بقیه پشت
    یک دکمه می‌مانند — نه حذف شده‌اند، فقط سر راهِ کاربر تازه نیستند.
    """
    kb = InlineKeyboardBuilder()
    toggle = "⏸  توقف این کار" if task.enabled else "▶️  فعال‌سازی این کار"
    kb.row(InlineKeyboardButton(text=toggle, callback_data=f"task:toggle:{task.id}"))

    # فقط وقتی چیزی در صف هست دیده می‌شود؛ منوی خلوت‌تر یعنی گیجی کمتر
    if waiting:
        kb.row(
            InlineKeyboardButton(
                text=f"⏳ در انتظار تأیید ({fa_num(waiting)})",
                callback_data=f"pend:list:{task.id}",
            )
        )

    kb.row(_divider("محتوا"))
    kb.row(
        InlineKeyboardButton(text="🧹 پاک‌سازی متن", callback_data=f"set:clean:{task.id}"),
        InlineKeyboardButton(text="🔤 جایگزینی کلمات", callback_data=f"rule:replace:{task.id}"),
    )
    kb.row(
        InlineKeyboardButton(text="✍️ هدر / فوتر / امضا", callback_data=f"set:text:{task.id}"),
        InlineKeyboardButton(text="💧 واترمارک", callback_data=f"set:wm:{task.id}"),
    )
    if pro:
        kb.row(
            InlineKeyboardButton(text="🚦 فیلترها", callback_data=f"set:filters:{task.id}"),
            InlineKeyboardButton(text="🎞 نوع محتوا", callback_data=f"set:media:{task.id}"),
        )
        kb.row(
            InlineKeyboardButton(
                text="🧩 کانفیگ پروکسی", callback_data=f"set:cfg:{task.id}"
            )
        )
        # فقط وقتی سرویس تنظیم شده باشد؛ دکمه‌ای که به بن‌بست می‌رسد
        # بدتر از نبودنش است.
        if ai.configured():
            kb.row(
                InlineKeyboardButton(
                    text="🤖 هوش مصنوعی", callback_data=f"set:ai:{task.id}"
                )
            )

    kb.row(_divider("انتشار"))
    if pro:
        kb.row(
            InlineKeyboardButton(text="📤 کانال‌های مقصد", callback_data=f"dest:list:{task.id}"),
            InlineKeyboardButton(text="⚙️ ارسال و ترافیک", callback_data=f"set:send:{task.id}"),
        )
        kb.row(
            InlineKeyboardButton(text="🕐 زمان‌بندی", callback_data=f"set:time:{task.id}"),
            InlineKeyboardButton(text="📈 آمار این کار", callback_data=f"task:stats:{task.id}"),
        )
    else:
        kb.row(
            InlineKeyboardButton(
                text="📤 کانال‌های مقصد", callback_data=f"dest:list:{task.id}"
            )
        )

    kb.row(_divider("ابزارها"))
    kb.row(
        InlineKeyboardButton(text="🧰 قالب آماده", callback_data=f"tpl:list:{task.id}"),
        InlineKeyboardButton(text="🧪 تست تنظیمات", callback_data=f"task:test:{task.id}"),
    )
    if pro:
        kb.row(
            InlineKeyboardButton(text="📋 کپی تنظیمات", callback_data=f"clone:pick:{task.id}")
        )
        if backfill_running:
            kb.row(
                InlineKeyboardButton(
                    text="⏹ توقف کپی گذشته", callback_data=f"hist:cancel:{task.id}"
                )
            )
        else:
            kb.row(
                InlineKeyboardButton(
                    text="🕓 کپی پیام‌های گذشته", callback_data=f"hist:start:{task.id}"
                )
            )
    else:
        kb.row(
            InlineKeyboardButton(
                text="⚙️ گزینه‌های پیشرفته", callback_data=f"task:pro:{task.id}"
            )
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
        ("تکراری بین چند مبدا", "skip_cross_duplicates"),
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
    # سطح سخت‌گیری فقط وقتی معنی دارد که یکی از دو فیلتر تکراری روشن باشد
    if cfg.get("skip_duplicates") or cfg.get("skip_cross_duplicates"):
        mode = dedupe.mode_of(cfg)
        kb.row(
            InlineKeyboardButton(
                text=f"🎚 تشخیص تکراری: {dedupe.MODE_LABELS[mode]}",
                callback_data=f"dupmode:{task_id}",
            )
        )
        if mode == dedupe.MODE_SIMILAR:
            kb.row(
                InlineKeyboardButton(
                    text=f"📊 حداقل شباهت: {fa_num(int(cfg.get('similarity_percent') or 85))}٪",
                    callback_data=f"ask:similarity_percent:{task_id}",
                )
            )
    kb.row(
        InlineKeyboardButton(
            text="📈 فیلتر تعامل (بازدید و واکنش)",
            callback_data=f"set:engage:{task_id}",
        )
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


def engagement_menu(task_id: int, cfg: dict) -> InlineKeyboardMarkup:
    """حد نصاب تعامل: فقط پست‌هایی که در مبدا گرفته‌اند کپی شوند."""
    kb = InlineKeyboardBuilder()
    wait = int(cfg.get("engagement_wait_minutes") or 0)
    kb.row(
        InlineKeyboardButton(
            text=(
                f"⏱ مدت انتظار: {fa_num(wait)} دقیقه"
                if wait
                else "⏱ مدت انتظار: بدون انتظار"
            ),
            callback_data=f"ask:engagement_wait_minutes:{task_id}",
        )
    )
    for label, key in (
        ("👁 حداقل بازدید", "min_views"),
        ("❤️ حداقل واکنش", "min_reactions"),
        ("↪️ حداقل فوروارد", "min_forwards"),
    ):
        value = int(cfg.get(key) or 0)
        kb.row(
            InlineKeyboardButton(
                text=f"{label}: {fa_num(value) if value else 'بی‌اهمیت'}",
                callback_data=f"ask:{key}:{task_id}",
            )
        )
    kb.row(InlineKeyboardButton(text="🔙 بازگشت", callback_data=f"set:filters:{task_id}"))
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


def configs_menu(task_id: int, cfg: dict) -> InlineKeyboardMarkup:
    """بازنویسی نام کانفیگ‌های پروکسی، در متن و در فایل."""
    kb = InlineKeyboardBuilder()
    kb.row(
        _flag(
            "بازنویسی نام کانفیگ‌های داخل متن",
            bool(cfg.get("rewrite_configs")),
            f"flag:rewrite_configs:{task_id}",
        )
    )
    kb.row(
        _flag(
            "بازنویسی داخل فایل‌های پیوست",
            bool(cfg.get("rewrite_files")),
            f"flag:rewrite_files:{task_id}",
        )
    )

    tag = (cfg.get("config_tag") or "").strip()
    fallback = (cfg.get("signature") or cfg.get("footer") or "").strip()
    if tag:
        label = f"🏷 نام روی کانفیگ‌ها: {tag[:24]}"
    elif fallback:
        label = f"🏷 نام: {fallback[:20]} (از امضا)"
    else:
        label = "🏷 نام روی کانفیگ‌ها: تعیین نشده"
    kb.row(InlineKeyboardButton(text=label, callback_data=f"ask:config_tag:{task_id}"))

    if cfg.get("rewrite_files"):
        kb.row(
            InlineKeyboardButton(
                text=f"📄 نام فایل: {(cfg.get('file_rename') or '{tag}')[:24]}",
                callback_data=f"ask:file_rename:{task_id}",
            )
        )
    kb.row(InlineKeyboardButton(text="🔙 بازگشت", callback_data=f"task:open:{task_id}"))
    return kb.as_markup()


def ai_menu(task_id: int, cfg: dict, *, balance: int = 0) -> InlineKeyboardMarkup:
    """قابلیت‌های هوش مصنوعی این کار.

    گزینه‌های هر قابلیت فقط وقتی نشان داده می‌شوند که خودش روشن باشد؛
    لحنِ بازنویسیِ خاموش چیزی برای تنظیم کردن ندارد و فقط منو را شلوغ
    می‌کند.
    """
    kb = InlineKeyboardBuilder()

    kb.row(
        InlineKeyboardButton(
            text=f"🤖 اعتبار باقی‌مانده: {fa_num(balance)}",
            callback_data="credits:open",
        )
    )

    kb.row(_flag("خلاصه‌سازی", bool(cfg.get("ai_summarize")), f"flag:ai_summarize:{task_id}"))
    if cfg.get("ai_summarize"):
        kb.row(
            InlineKeyboardButton(
                text=f"   ↳ در {fa_num(int(cfg.get('ai_sentences') or 2))} جمله",
                callback_data=f"aisent:{task_id}",
            )
        )

    kb.row(_flag("بازنویسی", bool(cfg.get("ai_rewrite")), f"flag:ai_rewrite:{task_id}"))
    if cfg.get("ai_rewrite"):
        style = str(cfg.get("ai_style") or "same")
        kb.row(
            InlineKeyboardButton(
                text=f"   ↳ لحن: {aiskills.STYLES.get(style, style)}",
                callback_data=f"aistyle:{task_id}",
            )
        )

    kb.row(_flag("ترجمه", bool(cfg.get("ai_translate")), f"flag:ai_translate:{task_id}"))
    if cfg.get("ai_translate"):
        lang = str(cfg.get("ai_language") or "en")
        kb.row(
            InlineKeyboardButton(
                text=f"   ↳ زبان: {aiskills.LANGUAGES.get(lang, lang)}",
                callback_data=f"ailang:{task_id}",
            )
        )

    kb.row(InlineKeyboardButton(text="🔙 بازگشت", callback_data=f"task:open:{task_id}"))
    return kb.as_markup()


def watermark_menu(task_id: int, cfg: dict) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.row(_flag("واترمارک", bool(cfg.get("watermark_enabled")), f"flag:watermark_enabled:{task_id}"))

    # ساده‌ترین راه: کاربر هرچه می‌خواهد می‌فرستد و از شش پیش‌نمایش انتخاب می‌کند
    kb.row(
        InlineKeyboardButton(
            text="🎨 ساخت با پیش‌نمایش (ساده‌ترین راه)",
            callback_data=f"wmpv:{task_id}",
        )
    )
    if cfg.get("watermark_logo") or (cfg.get("watermark_text") or "").strip():
        kb.row(
            InlineKeyboardButton(
                text="👁 پیش‌نمایش تنظیمات فعلی", callback_data=f"wmpvnow:{task_id}"
            )
        )
    kb.row(_divider("تنظیم دستی"))

    # نوع واترمارک: متن یا لوگوی تصویری
    kind = cfg.get("watermark_kind", "text")
    kb.row(
        InlineKeyboardButton(
            text=("🔘" if kind == "text" else "⚪️") + " ✏️ متن",
            callback_data=f"wmkind:text:{task_id}",
        ),
        InlineKeyboardButton(
            text=("🔘" if kind == "logo" else "⚪️") + " 🖼 لوگو",
            callback_data=f"wmkind:logo:{task_id}",
        ),
    )
    if kind == "logo":
        has_logo = bool(cfg.get("watermark_logo"))
        kb.row(
            InlineKeyboardButton(
                text="🔄 تعویض لوگو" if has_logo else "📎 آپلود لوگو",
                callback_data=f"wmlogo:{task_id}",
            )
        )
        if has_logo:
            kb.row(
                InlineKeyboardButton(text="🗑 حذف لوگو", callback_data=f"wmlogodel:{task_id}")
            )
    else:
        kb.row(
            InlineKeyboardButton(
                text="✏️ متن واترمارک", callback_data=f"ask:watermark_text:{task_id}"
            )
        )
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

    # خارج از ساعت کاری: دور ریختن یا نگه داشتن؟ فقط وقتی ساعت تنظیم
    # شده باشد معنی دارد، پس در حالت ۲۴ ساعته نشان داده نمی‌شود.
    if start != end:
        kb.row(
            _flag(
                "پست‌های خارج از ساعت را نگه دار",
                bool(cfg.get("hold_outside_hours")),
                f"flag:hold_outside_hours:{task_id}",
            )
        )

    gap = int(cfg.get("min_gap_seconds") or 0)
    kb.row(
        InlineKeyboardButton(
            text=(
                f"🚏 فاصله‌ی بین پست‌ها: {fa_num(gap)} ثانیه"
                if gap
                else "🚏 فاصله‌ی بین پست‌ها: بدون فاصله"
            ),
            callback_data=f"ask:min_gap_seconds:{task_id}",
        )
    )
    kb.row(
        _flag(
            "تأیید دستی پیش از انتشار",
            bool(cfg.get("approval")),
            f"flag:approval:{task_id}",
        )
    )
    kb.row(InlineKeyboardButton(text="🔙 بازگشت", callback_data=f"task:open:{task_id}"))
    return kb.as_markup()


def destinations_menu(task_id: int, primary: str, extras: list) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.row(
        InlineKeyboardButton(
            text=f"⭐️ {primary[:34]} (اصلی)", callback_data=f"dmain:{task_id}"
        )
    )
    for dest in extras:
        mark = "🟢" if dest.enabled else "🔴"
        has_own = "✍️" if (dest.overrides or {}) else "➕"
        kb.row(
            InlineKeyboardButton(
                text=f"{mark} {(dest.title or dest.ref)[:28]}",
                callback_data=f"dest:toggle:{dest.id}:{task_id}",
            ),
            InlineKeyboardButton(
                text=has_own, callback_data=f"dest:sig:{dest.id}:{task_id}"
            ),
            InlineKeyboardButton(text="🗑", callback_data=f"dest:del:{dest.id}:{task_id}"),
        )
    kb.row(InlineKeyboardButton(text="➕ افزودن مقصد", callback_data=f"dest:add:{task_id}"))
    kb.row(
        InlineKeyboardButton(
            text="✍️ = امضای اختصاصی دارد | ➕ = تعیین امضا", callback_data="noop"
        )
    )
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


PLAN_ICONS = {
    "trial": "🎁",
    "week": "🥉",
    "two_week": "🥈",
    "month": "🥇",
    "custom": "💎",
}


def plans_menu() -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    for plan in purchasable():
        icon = PLAN_ICONS.get(plan.code, "▫️")
        star = " ⭐️" if plan.code == POPULAR_CODE else ""
        kb.row(
            InlineKeyboardButton(
                text=f"{icon} {plan.title} · {plan.price_label}{star}",
                callback_data=f"plan:{plan.code}",
            )
        )
    kb.row(
        InlineKeyboardButton(text="📊 مقایسه‌ی طرح‌ها", callback_data="cmp:plans"),
        InlineKeyboardButton(text="🎫 خرید اعتبار", callback_data="credit:menu"),
    )
    return kb.as_markup()


def credits_menu(balances: dict[str, int]) -> InlineKeyboardMarkup:
    """فهرست بسته‌های اعتبار، با نمایش مانده‌ی فعلی."""
    kb = InlineKeyboardBuilder()
    for kind, (title, _desc, _default) in CREDIT_KINDS.items():
        price = credit_unit(kind)
        have = fa_num(balances.get(kind, 0))
        kb.row(
            InlineKeyboardButton(
                text=f"{title} · مانده {have} · هر واحد {toman(price)}",
                callback_data=f"credit:pick:{kind}",
            )
        )
    kb.row(InlineKeyboardButton(text="🔙 بازگشت به طرح‌ها", callback_data="credit:plans"))
    return kb.as_markup()


def credit_packs_menu(kind: str) -> InlineKeyboardMarkup:
    """انتخاب تعداد واحد اعتبار."""
    kb = InlineKeyboardBuilder()
    row: list[InlineKeyboardButton] = []
    for amount in CREDIT_PACKS:
        row.append(
            InlineKeyboardButton(
                text=f"{fa_num(amount)} عدد · {toman(credit_price(kind, amount))}",
                callback_data=f"credit:buy:{kind}:{amount}",
            )
        )
        if len(row) == 2:
            kb.row(*row)
            row = []
    if row:
        kb.row(*row)
    kb.row(
        InlineKeyboardButton(text="🔢 تعداد دلخواه", callback_data=f"credit:ask:{kind}")
    )
    kb.row(InlineKeyboardButton(text="🔙 بازگشت", callback_data="credit:menu"))
    return kb.as_markup()


def credit_offer_menu(kind: str) -> InlineKeyboardMarkup:
    """دکمه‌ی کوتاه «اعتبار بخر» برای جاهایی که قابلیت قفل است."""
    kb = InlineKeyboardBuilder()
    kb.row(
        InlineKeyboardButton(text="🎫 خرید اعتبار", callback_data=f"credit:pick:{kind}")
    )
    kb.row(InlineKeyboardButton(text="💳 دیدن طرح‌ها", callback_data="credit:plans"))
    return kb.as_markup()


def account_menu(
    logged_in: bool,
    has_pin: bool,
    *,
    pro: bool = False,
    digest: bool = False,
) -> InlineKeyboardMarkup:
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
    kb.row(
        InlineKeyboardButton(text="📊 سهمیه و اعتبار من", callback_data="acc:quota")
    )
    kb.row(
        InlineKeyboardButton(text="👛 کیف پول", callback_data="wal:home"),
        InlineKeyboardButton(text="🧾 گزارش فعالیت", callback_data="acc:logs"),
    )
    kb.row(
        InlineKeyboardButton(
            text="🧭 منوها: پیشرفته" if pro else "🧭 منوها: ساده",
            callback_data="acc:level",
        )
    )
    kb.row(
        InlineKeyboardButton(
            text=f'{i18n.t("account.digest")}: {on_off(digest)}',
            callback_data="acc:digest",
        )
    )
    kb.row(
        InlineKeyboardButton(text=i18n.t("lang.button"), callback_data="acc:lang")
    )
    return kb.as_markup()


def quota_menu() -> InlineKeyboardMarkup:
    """زیر صفحه‌ی «سهمیه و اعتبار من»."""
    kb = InlineKeyboardBuilder()
    kb.row(
        InlineKeyboardButton(text="🎫 خرید اعتبار", callback_data="credit:menu"),
        InlineKeyboardButton(text="⬆️ ارتقای طرح", callback_data="credit:plans"),
    )
    kb.row(InlineKeyboardButton(text="🔄 به‌روزرسانی", callback_data="acc:quota"))
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
