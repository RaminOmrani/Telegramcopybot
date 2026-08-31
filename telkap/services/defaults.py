"""تنظیمات پیش‌فرض هر کار کپی.

تنظیمات در ستون JSON ذخیره می‌شوند تا افزودن گزینه‌ی جدید نیازی به
مهاجرت دیتابیس نداشته باشد؛ مقادیر غایب از این جدول پر می‌شوند.
"""
from __future__ import annotations

from typing import Any

MEDIA_KINDS = (
    "text",
    "photo",
    "video",
    "animation",
    "audio",
    "voice",
    "document",
    "sticker",
    "poll",
    "video_note",
)

DEFAULT_SETTINGS: dict[str, Any] = {
    # --- شیوه ارسال ---
    "mode": "copy",             # copy = بدون برچسب فوروارد | forward = با برچسب
    "delay_seconds": 0,         # تأخیر پیش از ارسال
    "max_per_hour": 0,          # 0 = نامحدود

    # --- پاک‌سازی متن ---
    "remove_links": False,
    "remove_hashtags": False,
    "remove_mentions": False,
    "remove_emails": False,
    "remove_emoji": False,
    "remove_source_signature": True,   # حذف آیدی/امضای کانال مبدا از انتهای پست
    "strip_empty_lines": True,

    # --- افزودن متن ---
    "header": "",               # متن ابتدای پست
    "footer": "",               # متن انتهای پست (مثلاً آیدی کانال شما)
    "signature": "",            # جایگزین امضای مبدا
    # قالب‌بندی همان سه متن بالا — بولد، لینک و مهم‌تر از همه ایموجی
    # پریمیوم. ایموجی پریمیوم متن نیست، یک entity کنار متن است؛ بدون
    # این سه کلید، امضای پریمیوم ساده می‌شد. ساختارشان در richtext.py.
    "header_entities": [],
    "footer_entities": [],
    "signature_entities": [],

    # --- رسانه ---
    "allowed_media": list(MEDIA_KINDS),
    "caption_only": False,      # فقط متن/کپشن بفرست، رسانه را نه
    "skip_media_over_mb": 0,    # 0 = بدون محدودیت

    # --- واترمارک ---
    "watermark_enabled": False,
    "watermark_kind": "text",   # text | logo
    "watermark_text": "",
    "watermark_logo": "",       # مسیر فایل لوگوی آپلودشده
    "watermark_position": "bottom-right",   # 5 حالت: 4 گوشه + center
    "watermark_opacity": 60,    # 0..100
    "watermark_size": 4,        # درصدی از عرض تصویر (1..20)

    # --- فیلترها ---
    "block_ads": False,         # فیلتر هوشمند پیام‌های تبلیغاتی
    "ad_sensitivity": "medium",  # low | medium | high
    "block_forwarded": False,   # پست‌هایی که خودشان فوروارد هستند کپی نشوند
    "block_with_links": False,  # هر پستی که لینک دارد رد شود
    "block_with_buttons": False,
    "min_length": 0,
    "max_length": 0,            # 0 = نامحدود
    "skip_duplicates": True,

    # --- مخصوص گروه‌ها ---
    "skip_bots": False,        # پیام‌های ربات‌ها کپی نشوند
    "skip_replies": False,     # پیام‌های پاسخ (reply) کپی نشوند

    # --- همگام‌سازی ---
    "sync_edits": True,
    "sync_deletes": False,

    # --- زمان‌بندی ---
    # ساعت فعال بودن کار (به وقت محلی تعیین‌شده در TIMEZONE_OFFSET).
    # اگر شروع و پایان برابر باشند، یعنی ۲۴ ساعته.
    "active_from_hour": 0,
    "active_to_hour": 0,

    # --- دکمه‌های شیشه‌ای ---
    "copy_buttons": False,   # دکمه‌های زیر پست هم کپی شوند

    # --- مسیریابی با کلمه‌ی کلیدی ---
    # روی هر مقصد هم می‌شود جداگانه تنظیم کرد و همین‌ها را بازنویسی کند.
    "route_words": [],       # خالی = همه‌چیز برود؛ وگرنه فقط پستِ دارای این کلمه‌ها
    "route_skip": [],        # پستی که یکی از این کلمه‌ها را دارد نرود

    # --- صف تأیید و زمان‌بندی پیشرفته ---
    "approval": False,          # پیش از انتشار، خودتان تأیید کنید
    "hold_outside_hours": False,  # خارج از ساعت فعال، به‌جای دور ریختن نگه دار
    "min_gap_seconds": 0,       # حداقل فاصله بین دو انتشار (۰ = بدون فاصله)

    # --- فیلتر تعامل ---
    # پستِ تازه هنوز بازدید ندارد، پس تصمیم باید عقب بیفتد: پست نگه داشته
    # می‌شود، بعد از این چند دقیقه دوباره خوانده و آن‌وقت سنجیده می‌شود.
    "engagement_wait_minutes": 0,   # ۰ = فیلتر تعامل خاموش
    "min_views": 0,
    "min_reactions": 0,
    "min_forwards": 0,

    # --- تکراری بین چند مبدا ---
    # «جلوگیری از پست تکراری» فقط داخل همان کار کار می‌کند. این یکی
    # می‌پرسد «آیا همین محتوا قبلاً به همین کانال رفته؟» — حتی اگر از کار
    # و مبدای دیگری آمده باشد.
    "skip_cross_duplicates": False,
    # چطور «تکراری» تشخیص داده شود: exact | normalized | similar
    "duplicate_mode": "normalized",
    # فقط در حالت similar. روی متن‌های فارسی واقعی، دو نسخه‌ی یک خبر
    # فاصله‌ی ۱۰ تا ۱۳ بیت می‌گیرند و دو خبر متفاوت بالای ۲۰؛ پس ۸۰٪
    # (یعنی سقف ۱۳ بیت) درست بین این دو می‌نشیند.
    "similarity_percent": 80,

    # --- کانفیگ‌های پروکسی ---
    "rewrite_configs": False,   # نام کانفیگ‌های داخل متن را عوض کن
    "rewrite_files": False,     # نام داخل فایل‌های پیوست را هم عوض کن
    "config_tag": "",           # نام تازه؛ خالی = از امضا یا فوتر برداشته می‌شود
    "file_rename": "{tag}",     # الگوی نام فایل تازه؛ {tag} و {name} پذیرفته است

    # --- هوش مصنوعی ---
    # هر سه پیش‌فرض خاموش‌اند و هرکدام روی هر پست یک واحد اعتبار می‌برند،
    # پس روشن شدنشان باید تصمیم خودِ کاربر باشد نه پیش‌فرض ما.
    "ai_summarize": False,
    "ai_rewrite": False,
    "ai_translate": False,
    "ai_style": "same",         # لحن بازنویسی؛ کلیدهای aiskills.STYLES
    "ai_language": "en",        # زبان ترجمه؛ کلیدهای aiskills.LANGUAGES
    "ai_sentences": 2,          # خلاصه در چند جمله
}


def merged_settings(stored: dict[str, Any] | None) -> dict[str, Any]:
    """تنظیمات ذخیره‌شده را روی پیش‌فرض‌ها سوار می‌کند.

    فهرست‌ها کپی می‌شوند، وگرنه همه‌ی کارها یک شیء مشترک می‌گرفتند و
    ویرایش تنظیمات یک کار روی بقیه هم اثر می‌گذاشت.
    """
    data = {
        key: list(value) if isinstance(value, list) else value
        for key, value in DEFAULT_SETTINGS.items()
    }
    if stored:
        for key, value in stored.items():
            if key in DEFAULT_SETTINGS:
                data[key] = list(value) if isinstance(value, list) else value
    return data
