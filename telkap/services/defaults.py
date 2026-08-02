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

    # --- رسانه ---
    "allowed_media": list(MEDIA_KINDS),
    "caption_only": False,      # فقط متن/کپشن بفرست، رسانه را نه
    "skip_media_over_mb": 0,    # 0 = بدون محدودیت

    # --- واترمارک ---
    "watermark_enabled": False,
    "watermark_text": "",
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
}


def merged_settings(stored: dict[str, Any] | None) -> dict[str, Any]:
    """تنظیمات ذخیره‌شده را روی پیش‌فرض‌ها سوار می‌کند."""
    data = dict(DEFAULT_SETTINGS)
    data["allowed_media"] = list(DEFAULT_SETTINGS["allowed_media"])
    if stored:
        for key, value in stored.items():
            if key in DEFAULT_SETTINGS:
                data[key] = value
    return data
