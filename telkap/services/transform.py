"""تبدیل متن پست‌ها: جایگزینی، حذف لینک/هشتگ، امضا، هدر و فوتر.

این ماژول کاملاً خالص است (بدون وابستگی به تلگرام یا دیتابیس) تا
بتوان رفتار آن را مستقیم تست کرد. `configs` هم به همین دلیل خالص نگه
داشته شده و اینجا وارد می‌شود.
"""
from __future__ import annotations

import copy
import re
from bisect import bisect_left
from collections.abc import Iterable
from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import Any

from telkap.services import configs

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


def config_tag(settings: dict[str, Any]) -> str:
    """نامی که روی کانفیگ‌ها می‌نشیند.

    اگر کاربر تگ جدا ننوشته باشد از امضا و بعد فوتر استفاده می‌شود — چون
    معمولاً همان آیدی کانال است و دوباره نوشتنش فقط یک جای دیگر برای
    قدیمی شدن می‌سازد.
    """
    for key in ("config_tag", "signature", "footer"):
        value = (settings.get(key) or "").strip()
        if value:
            return value
    return ""


# نشانه‌ای که در متن واقعی پیدا نمی‌شود و هیچ‌کدام از الگوهای پاک‌سازی
# آن را نمی‌گیرند
_SHELTER = "\x00cfg{}\x00"


def _shelter_configs(text: str) -> tuple[str, list[str]]:
    kept: list[str] = []

    def stash(match) -> str:
        kept.append(match.group(0))
        return _SHELTER.format(len(kept) - 1)

    return configs.LINK_RE.sub(stash, text), kept


def _restore_configs(text: str, kept: list[str]) -> str:
    for index, link in enumerate(kept):
        text = text.replace(_SHELTER.format(index), link)
    return text


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

    # ۲.۵) بازنویسی نام کانفیگ‌های پروکسی
    if settings.get("rewrite_configs"):
        text, _ = configs.rewrite_text(text, config_tag(settings))

    # کانفیگ‌ها از دست پاک‌سازی‌ها کنار گذاشته می‌شوند: «حذف لینک‌ها»
    # وگرنه خودِ کانفیگ را می‌خورد و «حذف ایموجی» نامش را خراب می‌کند.
    text, sheltered = _shelter_configs(text)

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

    text = _restore_configs(text, sheltered)

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


def _utf16_offsets(text: str) -> list[int]:
    """آفست UTF-16 هر نویسه، به‌علاوه‌ی طول کل در انتها.

    خروجی طولش len(text) + 1 است، پس offsets[i] یعنی آفست شروع نویسه‌ی i
    و offsets[len(text)] یعنی طول کل متن.
    """
    offsets = [0] * (len(text) + 1)
    position = 0
    for index, char in enumerate(text):
        offsets[index] = position
        position += 2 if ord(char) > 0xFFFF else 1
    offsets[len(text)] = position
    return offsets


def _char_index(offsets: list[int], utf16_offset: int) -> int | None:
    """آفست UTF-16 را به شماره‌ی نویسه‌ی پایتون برمی‌گرداند.

    اگر آفست وسط یک جفت جانشین (ایموجی) بیفتد None برمی‌گردد؛ چنین
    entity ای معتبر نیست و باید کنار گذاشته شود.
    """
    index = bisect_left(offsets, utf16_offset)
    if index >= len(offsets) or offsets[index] != utf16_offset:
        return None
    return index


def remap_entities(original: str, result: str, entities):
    """entity های متن اصلی را با متن تغییریافته هماهنگ می‌کند.

    entity ها همان چیزی هستند که بولد، ایتالیک، لینک، اسپویلر و
    **ایموجی پریمیوم** را می‌سازند. اگر همراه متن نروند، ایموجی پریمیوم
    به نویسه‌ی جایگزین ساده‌اش تبدیل می‌شود و بقیه‌ی فرمت‌ها از بین می‌روند.

    دو مسیر دارد:

    ۱. اگر متن اصلی دست‌نخورده داخل نتیجه باشد (فقط هدر یا فوتر اضافه شده)،
       همه‌ی آفست‌ها به یک اندازه جابه‌جا می‌شوند.
    ۲. اگر متن از وسط عوض شده باشد (جایگزینی کلمه، حذف لینک، حذف امضای
       مبدا)، بخش‌های دست‌نخورده با difflib پیدا می‌شوند و هر entity که
       کاملاً داخل یکی از آن‌ها باشد به جای تازه‌اش منتقل می‌گردد. فقط
       entity هایی حذف می‌شوند که خودشان روی متنِ تغییریافته افتاده‌اند.

    آفست‌ها بر حسب واحد UTF-16 حساب می‌شوند، همان‌طور که تلگرام می‌شمارد.
    """
    if not entities or not original:
        return None

    index = result.find(original)
    if index >= 0:
        shift = utf16_len(result[:index])
        if shift == 0:
            return list(entities)
        shifted = []
        for entity in entities:
            clone = copy.copy(entity)
            clone.offset = entity.offset + shift
            shifted.append(clone)
        return shifted

    # متن از وسط تغییر کرده؛ بخش‌های مشترک را پیدا می‌کنیم
    src_offsets = _utf16_offsets(original)
    dst_offsets = _utf16_offsets(result)
    blocks = [
        block
        for block in SequenceMatcher(None, original, result, autojunk=False).get_matching_blocks()
        if block.size
    ]
    if not blocks:
        return None

    mapped = []
    for entity in entities:
        length = getattr(entity, "length", 0) or 0
        if length <= 0:
            continue
        start = _char_index(src_offsets, entity.offset)
        end = _char_index(src_offsets, entity.offset + length)
        if start is None or end is None:
            continue
        for block in blocks:
            if block.a <= start and end <= block.a + block.size:
                new_start = block.b + (start - block.a)
                new_end = block.b + (end - block.a)
                clone = copy.copy(entity)
                clone.offset = dst_offsets[new_start]
                clone.length = dst_offsets[new_end] - dst_offsets[new_start]
                mapped.append(clone)
                break
    return mapped or None


def drop_custom_emoji(entities):
    """ایموجی‌های پریمیوم را از فهرست entity ها بیرون می‌کشد.

    ارسال ایموجی پریمیوم فقط با اکانت پریمیوم ممکن است؛ اگر تلگرام
    ارسال را رد کند، همان پیام بدون این entity ها دوباره فرستاده می‌شود
    تا دست‌کم بولد و لینک و بقیه‌ی فرمت‌ها از دست نروند.

    مقایسه با نام کلاس انجام می‌شود تا این ماژول به Telethon وابسته نشود.
    """
    if not entities:
        return None
    kept = [e for e in entities if type(e).__name__ != "MessageEntityCustomEmoji"]
    if len(kept) == len(entities):
        return None  # ایموجی پریمیومی نبود، چیزی تغییر نمی‌کند
    return kept or []


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
