"""مسیریابی پست‌ها بین مقصدها بر اساس کلمه‌ی کلیدی.

تا امروز هر پست به همه‌ی مقصدهای یک کار می‌رفت. برای کسی که یک کانال
عمومی دارد و یک کانال VIP، یا محتوایش چند دسته دارد، این یعنی باید به
ازای هر دسته یک کار جدا بسازد و همان مبدا را چند بار بخواند.

اینجا هر مقصد می‌تواند بگوید چه پستی را می‌خواهد و چه پستی را نه. اگر
چیزی تنظیم نشده باشد رفتار قبلی سر جایش است: همه‌چیز به همه‌جا می‌رود.
"""
from __future__ import annotations

# جداکننده‌های مجاز بین کلمه‌ها؛ کاربر لازم نیست قالب خاصی یاد بگیرد
_SEPARATORS = (",", "،", "\n", "|")

MAX_WORDS = 30
MAX_WORD_LEN = 40


def parse_words(raw: str) -> list[str]:
    """متن کاربر را به فهرست کلمه‌ها تبدیل می‌کند."""
    text = raw or ""
    for sep in _SEPARATORS:
        text = text.replace(sep, ",")
    words: list[str] = []
    for part in text.split(","):
        word = part.strip().lower()
        if word and word not in words:
            words.append(word[:MAX_WORD_LEN])
    return words[:MAX_WORDS]


def _has_any(text: str, words) -> bool:
    lowered = (text or "").lower()
    return any(word in lowered for word in words if word)


def wants(text: str, cfg: dict) -> bool:
    """آیا این مقصد این پست را می‌خواهد؟

    `route_skip` بر `route_words` مقدم است: اگر کاربر گفته «این کلمه هرگز»،
    نباید کلمه‌ی دیگری آن را دور بزند.
    """
    skip = cfg.get("route_skip") or []
    if skip and _has_any(text, skip):
        return False
    only = cfg.get("route_words") or []
    if only and not _has_any(text, only):
        return False
    return True


def is_filtered(cfg: dict) -> bool:
    """آیا اصلاً مسیریابی‌ای روی این مقصد تنظیم شده؟"""
    return bool(cfg.get("route_words") or cfg.get("route_skip"))


def describe(cfg: dict) -> str:
    """توضیح کوتاه فارسی برای نمایش در منو."""
    only = cfg.get("route_words") or []
    skip = cfg.get("route_skip") or []
    if not only and not skip:
        return "همه‌ی پست‌ها"
    parts = []
    if only:
        parts.append("فقط با: " + "، ".join(only[:4]) + ("…" if len(only) > 4 else ""))
    if skip:
        parts.append("بدون: " + "، ".join(skip[:4]) + ("…" if len(skip) > 4 else ""))
    return " | ".join(parts)
