"""تبدیل متن پست‌ها: جایگزینی، حذف لینک/هشتگ، امضا، هدر و فوتر.

این ماژول کاملاً خالص است (بدون وابستگی به تلگرام یا دیتابیس) تا
بتوان رفتار آن را مستقیم تست کرد.
"""
from __future__ import annotations

import copy
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

# --------------------------------------------------------------------------
# تشخیص پیام تبلیغاتی
#
# روش امتیازدهی است نه یادگیری ماشین: هر نشانه امتیازی دارد و اگر جمعِ
# امتیازها از آستانه‌ی حساسیت رد شود، پست تبلیغاتی شمرده می‌شود. نشانه‌های
# قوی‌تر (مثل «جهت تبلیغات») امتیاز بیشتری می‌گیرند تا یک واژه‌ی معمولی
# به‌تنهایی باعث رد شدن پست نشود.
# --------------------------------------------------------------------------

# نشانه‌های قطعی تبلیغ (امتیاز ۳)
AD_STRONG: tuple[str, ...] = (
    "جهت تبلیغ", "برای تبلیغ", "سفارش تبلیغ", "رزرو تبلیغ", "پذیرش تبلیغ",
    "تعرفه تبلیغات", "ادمین تبلیغات", "تبلیغات بنری", "تبادل و تبلیغ",
    "advertise here", "for ads", "sponsored post", "paid promotion",
)

# نشانه‌های تجاری (امتیاز ۲)
AD_COMMERCIAL: tuple[str, ...] = (
    "ثبت سفارش", "کد تخفیف", "لینک خرید", "خرید اشتراک", "همین حالا سفارش",
    "ارسال رایگان", "تخفیف ویژه", "فروش ویژه", "قیمت استثنایی", "شرایط اقساط",
    "مشاوره رایگان", "ظرفیت محدود", "فرصت محدود", "همکاری در فروش",
    "promo code", "discount code", "limited offer", "buy now", "order now",
)

# نشانه‌های ضعیف (امتیاز ۱) — به‌تنهایی کافی نیستند
AD_WEAK: tuple[str, ...] = (
    "تبلیغات", "تبليغات", "تخفیف", "قیمت", "خرید", "فروش", "سفارش",
    "دایرکت", "پیوی", "واتساپ", "اینستاگرام",
    "ads", "advertising", "sponsored", "promo",
)

# سازگاری با نسخه‌ی قبل
AD_KEYWORDS: tuple[str, ...] = AD_STRONG + AD_COMMERCIAL

# ارقام فارسی و عربی به لاتین، پیش از تطبیق الگوهای عددی.
# بدون این کار «۰۹۱۲…» با الگوی شماره تماس مطابقت نمی‌کند و در کانال‌های
# فارسی عملاً هیچ شماره‌ای تشخیص داده نمی‌شد.
DIGIT_MAP = str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789")


def normalize_digits(text: str) -> str:
    return (text or "").translate(DIGIT_MAP)


# شماره تماس ایران، و شماره کارت بانکی ۱۶ رقمی
PHONE_RE = re.compile(r"(?:\+?98|0)9\d{9}\b")
CARD_RE = re.compile(r"\b(?:\d{4}[\s-]?){3}\d{4}\b")
PRICE_RE = re.compile(r"\d{1,3}(?:[,،]\d{3})+\s*(?:تومان|ریال|هزار|میلیون)")

# آستانه‌ی امتیاز برای هر سطح حساسیت.
# نشانه‌ی قطعی به‌تنهایی امتیاز ۵ می‌گیرد تا در همه‌ی سطوح گرفته شود.
AD_THRESHOLDS = {"low": 5, "medium": 4, "high": 3}


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


def ad_score(text: str) -> tuple[int, list[str]]:
    """امتیاز تبلیغاتی بودن متن، به‌همراه فهرست نشانه‌های پیداشده.

    برگرداندن دلایل باعث می‌شود کاربر در «تست تنظیمات» و لاگ ببیند
    چرا پستی تبلیغاتی تشخیص داده شده است.
    """
    if not text:
        return 0, []

    lowered = text.lower()
    # الگوهای عددی روی نسخه‌ی یکسان‌شده اجرا می‌شوند
    digits = normalize_digits(text)
    score = 0
    reasons: list[str] = []

    for keyword in AD_STRONG:
        if keyword in lowered:
            score += 5  # نشانه‌ی قطعی، به‌تنهایی از همه‌ی آستانه‌ها رد می‌شود
            reasons.append(f"«{keyword}»")
            break

    commercial = [kw for kw in AD_COMMERCIAL if kw in lowered]
    if commercial:
        score += 2 * min(len(commercial), 2)
        reasons.append("، ".join(f"«{kw}»" for kw in commercial[:2]))

    weak = [kw for kw in AD_WEAK if kw in lowered]
    if weak:
        score += min(len(weak), 3)
        reasons.append("، ".join(f"«{kw}»" for kw in weak[:3]))

    if PHONE_RE.search(digits):
        score += 2
        reasons.append("شماره تماس")
    if CARD_RE.search(digits):
        score += 4  # شماره کارت در یک پست خبری تقریباً همیشه یعنی تبلیغ
        reasons.append("شماره کارت")
    if PRICE_RE.search(digits):
        score += 2
        reasons.append("قیمت")

    links = len(URL_RE.findall(text))
    if links >= 2:
        score += 2
        reasons.append(f"{links} لینک")
    elif links == 1:
        score += 1

    mentions = len(MENTION_RE.findall(text))
    if mentions >= 2:
        score += 1
        reasons.append(f"{mentions} آیدی")

    return score, reasons


def utf16_len(text: str) -> int:
    """طول متن بر حسب واحدهای UTF-16.

    تلگرام آفست entity ها را با این واحد می‌شمارد، نه با نویسه‌ی پایتون.
    ایموجی‌ها (از جمله ایموجی پریمیوم) بیرون از BMP هستند و دو واحد
    می‌گیرند؛ اگر با len() پایتون حساب کنیم، فرمت‌ها جابه‌جا می‌شوند.
    """
    return len(text.encode("utf-16-le")) // 2


def remap_entities(original: str, result: str, entities):
    """entity های متن اصلی را با متن تغییریافته هماهنگ می‌کند.

    فقط وقتی می‌شود مطمئن بود که متن اصلی دست‌نخورده داخل نتیجه باشد —
    یعنی تنها هدر یا فوتر اضافه شده. در این حالت آفست‌ها به اندازه‌ی
    متن اضافه‌شده‌ی ابتدایی جابه‌جا می‌شوند.

    اگر متن از وسط عوض شده باشد (جایگزینی کلمه، حذف لینک و…) آفست‌ها
    دیگر معتبر نیستند و None برمی‌گردد تا فرمت غلط اعمال نشود.
    """
    if not entities or not original:
        return None

    index = result.find(original)
    if index < 0:
        return None  # متن از وسط تغییر کرده است

    shift = utf16_len(result[:index])
    if shift == 0:
        return list(entities)

    shifted = []
    for entity in entities:
        clone = copy.copy(entity)
        clone.offset = entity.offset + shift
        shifted.append(clone)
    return shifted


def looks_like_ad(text: str, sensitivity: str = "medium") -> bool:
    """آیا این متن تبلیغاتی است؟

    `sensitivity`: low (فقط تبلیغ‌های آشکار) | medium | high (سخت‌گیرانه)
    """
    threshold = AD_THRESHOLDS.get(sensitivity, AD_THRESHOLDS["medium"])
    score, _ = ad_score(text)
    return score >= threshold


def ad_reason(text: str) -> str:
    """توضیح خوانا از اینکه چرا متن تبلیغاتی شمرده شد."""
    score, reasons = ad_score(text)
    if not reasons:
        return f"امتیاز {score}"
    return f"امتیاز {score} — {'، '.join(reasons)}"
