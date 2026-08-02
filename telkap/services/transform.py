"""تبدیل متن پست‌ها: جایگزینی، حذف لینک/هشتگ، امضا، هدر و فوتر.

این ماژول کاملاً خالص است (بدون وابستگی به تلگرام یا دیتابیس) تا
بتوان رفتار آن را مستقیم تست کرد.
"""
from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

URL_RE = re.compile(
    r"""(?xi)
    \b(
        (?:https?://|www\.)[^\s<>()"']+
        |
        (?:t\.me|telegram\.me)/[^\s<>()"']+
    )
    """
)
MENTION_RE = re.compile(r"(?<![\w@])@[A-Za-z][A-Za-z0-9_]{3,31}\b")
HASHTAG_RE = re.compile(r"(?<![\w#])#[^\s#]+")
EMAIL_RE = re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b")
EMOJI_RE = re.compile(
    "["
    "\U0001F300-\U0001FAFF"
    "\U00002700-\U000027BF"
    "\U0001F1E6-\U0001F1FF"
    "\U00002600-\U000026FF"
    "\U0000FE0F"
    "]+",
    flags=re.UNICODE,
)
MULTI_BLANK_RE = re.compile(r"\n{3,}")

# خطوطی که معمولاً امضای کانال مبدا هستند: فقط آیدی/لینک (+ چند ایموجی یا خط تزئینی)
_SIGNATURE_LINE_RE = re.compile(
    r"""(?xi)
    ^\s*
    (?:[\W_]*)                                   # ایموجی یا نویسه‌های تزئینی
    (?:
        @[A-Za-z][A-Za-z0-9_]{3,31}
        | (?:https?://)?(?:t\.me|telegram\.me)/[A-Za-z0-9_+/]+
    )
    (?:[\W_]*)
    \s*$
    """
)

# عبارت‌های رایج تبلیغاتی برای فیلتر هوشمند
AD_KEYWORDS: tuple[str, ...] = (
    "تبلیغات", "تبليغات", "جهت تبلیغ", "سفارش تبلیغ", "رزرو تبلیغ",
    "تعرفه تبلیغات", "ادمین تبلیغات", "پذیرش تبلیغ", "برای تبلیغ",
    "ثبت سفارش", "خرید اشتراک", "لینک خرید", "کد تخفیف",
    "ads", "advertise", "advertising", "sponsored", "promo code",
)


@dataclass(slots=True)
class TransformResult:
    text: str
    changed: bool


def _apply_replacements(text: str, rules: Iterable[RuleLike]) -> str:
    for rule in rules:
        if not rule.enabled or not rule.pattern:
            continue
        if rule.kind == "replace":
            text = text.replace(rule.pattern, rule.replacement)
        elif rule.kind == "regex":
            try:
                text = re.sub(rule.pattern, rule.replacement, text)
            except re.error:
                # الگوی نامعتبر کاربر نباید کل کپی را متوقف کند
                continue
    return text


class RuleLike:
    """پروتکل ساده‌ی قاعده — مدل `Rule` دیتابیس با آن سازگار است."""

    kind: str
    pattern: str
    replacement: str
    enabled: bool


def strip_signature(text: str, *, replacement: str = "") -> str:
    """امضای انتهایی کانال مبدا (خطوطی که فقط آیدی/لینک هستند) را حذف می‌کند."""
    lines = text.split("\n")
    while lines and (not lines[-1].strip() or _SIGNATURE_LINE_RE.match(lines[-1])):
        removed_blank = not lines[-1].strip()
        lines.pop()
        if not removed_blank:
            break  # فقط یک بلوک امضا از انتها حذف می‌شود
    result = "\n".join(lines).rstrip()
    if replacement:
        result = f"{result}\n\n{replacement}" if result else replacement
    return result


def tidy(text: str) -> str:
    text = MULTI_BLANK_RE.sub("\n\n", text)
    return "\n".join(line.rstrip() for line in text.split("\n")).strip()


def apply_transforms(
    text: str,
    settings: dict[str, Any],
    rules: Iterable[RuleLike] = (),
) -> str:
    """خط لوله‌ی کامل تبدیل متن یک پست."""
    if text is None:
        text = ""

    # ۱) قواعد کاربر (جایگزینی کلمه و regex) پیش از حذف‌ها اجرا می‌شوند
    text = _apply_replacements(text, rules)

    # ۲) حذف امضای مبدا / جایگزینی با امضای کاربر
    if settings.get("remove_source_signature"):
        text = strip_signature(text, replacement=settings.get("signature", ""))
    elif settings.get("signature"):
        text = f"{text}\n\n{settings['signature']}" if text else settings["signature"]

    # ۳) پاک‌سازی‌ها
    if settings.get("remove_links"):
        text = URL_RE.sub("", text)
    if settings.get("remove_mentions"):
        text = MENTION_RE.sub("", text)
    if settings.get("remove_hashtags"):
        text = HASHTAG_RE.sub("", text)
    if settings.get("remove_emails"):
        text = EMAIL_RE.sub("", text)
    if settings.get("remove_emoji"):
        text = EMOJI_RE.sub("", text)

    # ۴) هدر و فوتر
    header = (settings.get("header") or "").strip()
    footer = (settings.get("footer") or "").strip()
    parts = [part for part in (header, text.strip(), footer) if part]
    text = "\n\n".join(parts)

    if settings.get("strip_empty_lines", True):
        text = tidy(text)
    return text


def looks_like_ad(text: str) -> bool:
    """تشخیص ساده و محافظه‌کارانه‌ی پیام تبلیغاتی."""
    if not text:
        return False
    lowered = text.lower()
    hits = sum(1 for kw in AD_KEYWORDS if kw in lowered)
    if hits >= 2:
        return True
    # یک کلیدواژه‌ی تبلیغاتی همراه با لینک یا آیدی
    return hits >= 1 and bool(URL_RE.search(text) or MENTION_RE.search(text))
