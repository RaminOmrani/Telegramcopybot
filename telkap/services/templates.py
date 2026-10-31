"""قالب‌های آماده‌ی تنظیمات.

ربات ده‌ها گزینه دارد و کاربر تازه نمی‌داند از کجا شروع کند. قالب یعنی
«من یک کانال خبری دارم» و بعد همه‌ی تنظیم‌های منطقی یک‌جا اعمال شوند.

قالب فقط نقطه‌ی شروع است: بعدش هر گزینه‌ای را می‌شود دستی عوض کرد و
هیچ‌چیز قفل نمی‌ماند. برای همین هم فقط کلیدهایی که در قالب آمده‌اند
تغییر می‌کنند و بقیه‌ی تنظیمات کاربر دست‌نخورده می‌ماند.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from telkap.services.defaults import DEFAULT_SETTINGS


@dataclass(frozen=True, slots=True)
class Template:
    code: str
    title: str
    summary: str          # یک خط، برای فهرست
    detail: str           # توضیح کامل، پیش از اعمال
    values: dict[str, Any] = field(default_factory=dict)


TEMPLATES: tuple[Template, ...] = (
    Template(
        code="news",
        title="📰 کانال خبری",
        summary="بدون لینک و تبلیغ، با امضای خودتان",
        detail=(
            "لینک‌ها و امضای کانال مبدا حذف می‌شوند، فیلتر تبلیغات روشن "
            "می‌شود و ویرایش‌های مبدا هم منتقل می‌گردند.\n\n"
            "<i>یادتان باشد بعدش «🖋 امضای جایگزین» خودتان را بگذارید.</i>"
        ),
        values={
            "remove_links": True,
            "remove_source_signature": True,
            "block_ads": True,
            "ad_sensitivity": "medium",
            "sync_edits": True,
            "skip_duplicates": True,
            "duplicate_mode": "normalized",
        },
    ),
    Template(
        code="proxy",
        title="🧩 کانال پروکسی",
        summary="نام کانفیگ‌ها با نام کانال شما عوض می‌شود",
        detail=(
            "بازنویسی نام کانفیگ‌ها در متن و در فایل‌های پیوست روشن "
            "می‌شود. لینک‌ها حذف <b>نمی‌شوند</b> چون خودِ کانفیگ لینک است.\n\n"
            "<i>نام روی کانفیگ‌ها از «امضا» برداشته می‌شود؛ اگر امضا "
            "ندارید، در «🧩 کانفیگ پروکسی» یک نام بگذارید.</i>"
        ),
        values={
            "rewrite_configs": True,
            "rewrite_files": True,
            "remove_links": False,
            "remove_source_signature": True,
            "copy_buttons": True,
            "skip_duplicates": True,
        },
    ),
    Template(
        code="shop",
        title="🛍 کانال فروشگاهی",
        summary="همه‌چیز با دکمه‌ها و لینک‌ها",
        detail=(
            "دکمه‌های شیشه‌ای و لینک‌ها حفظ می‌شوند و ویرایش قیمت در مبدا "
            "به کانال شما هم می‌رسد — همان چیزی که برای فروش لازم است."
        ),
        values={
            "copy_buttons": True,
            "remove_links": False,
            "sync_edits": True,
            "sync_deletes": True,
            "block_ads": False,
        },
    ),
    Template(
        code="curated",
        title="✅ گلچین با تأیید شما",
        summary="هیچ پستی بدون تأیید شما منتشر نمی‌شود",
        detail=(
            "هر پست در صف می‌نشیند تا خودتان ✅ یا ❌ بزنید. فیلتر تبلیغات "
            "هم روشن می‌شود تا صف شلوغ نشود.\n\n"
            "<i>اگر روزی حوصله‌ی تأیید نداشتید، از «🕐 زمان‌بندی» "
            "خاموشش کنید.</i>"
        ),
        values={
            "approval": True,
            "block_ads": True,
            "ad_sensitivity": "high",
            "skip_duplicates": True,
        },
    ),
    Template(
        code="best",
        title="🔥 فقط پست‌های پرطرفدار",
        summary="یک ساعت صبر، بعد فقط آنهایی که گرفته‌اند",
        detail=(
            "ربات یک ساعت صبر می‌کند، بعد پست را دوباره می‌خواند و اگر "
            "حداقل ۵۰۰ بازدید گرفته باشد کپی می‌کند.\n\n"
            "<i>عدد بازدید را در «🚦 فیلترها ← 📈 فیلتر تعامل» متناسب با "
            "اندازه‌ی کانال مبدا تنظیم کنید.</i>"
        ),
        values={
            "engagement_wait_minutes": 60,
            "min_views": 500,
            "skip_duplicates": True,
            "duplicate_mode": "normalized",
        },
    ),
    Template(
        code="mirror",
        title="🪞 آینه‌ی کامل",
        summary="عیناً همان مبدا، بدون هیچ تغییری",
        detail=(
            "همه‌ی فیلترها و پاک‌سازی‌ها خاموش می‌شوند و ویرایش و حذف هم "
            "منتقل می‌گردد. مناسب وقتی می‌خواهید کانال دوم دقیقاً کپی "
            "کانال اول باشد."
        ),
        values={
            "remove_links": False,
            "remove_hashtags": False,
            "remove_mentions": False,
            "remove_emails": False,
            "remove_emoji": False,
            "remove_source_signature": False,
            "block_ads": False,
            "block_forwarded": False,
            "block_with_links": False,
            "block_with_buttons": False,
            "copy_buttons": True,
            "sync_edits": True,
            "sync_deletes": True,
            "header": "",
            "footer": "",
            "signature": "",
        },
    ),
)

BY_CODE = {template.code: template for template in TEMPLATES}


def get(code: str) -> Template | None:
    return BY_CODE.get(code)


def apply(cfg: dict[str, Any], template: Template) -> dict[str, Any]:
    """قالب را روی تنظیمات فعلی سوار می‌کند و نسخه‌ی تازه را برمی‌گرداند.

    کلیدهایی که قالب نامشان را نبرده دست‌نخورده می‌مانند — قالب یعنی
    «این چند تا را درست کن»، نه «همه‌چیز را از نو بساز».
    """
    merged = dict(cfg)
    for key, value in template.values.items():
        if key in DEFAULT_SETTINGS:
            merged[key] = list(value) if isinstance(value, list) else value
    return merged


def changes(cfg: dict[str, Any], template: Template) -> list[str]:
    """کلیدهایی که واقعاً عوض می‌شوند — برای نشان دادن پیش از اعمال."""
    return [
        key
        for key, value in template.values.items()
        if key in DEFAULT_SETTINGS and cfg.get(key) != value
    ]
