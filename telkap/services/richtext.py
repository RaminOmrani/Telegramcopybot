"""نگه داشتن قالب‌بندی متن‌هایی که خودِ کاربر می‌نویسد.

امضا، هدر و فوتر تا امروز فقط <b>متن خام</b> ذخیره می‌شدند. کاربری که
امضایش را با ایموجی پریمیوم می‌نوشت، آن را ساده در کانالش می‌دید — چون
ایموجی پریمیوم متن نیست، یک entity کنار متن است و ما دورش می‌ریختیم.

اینجا آن entity ها هنگام دریافت پیام برداشته و در تنظیمات ذخیره
می‌شوند، و هنگام ساختن پست دوباره روی جای درستشان می‌نشینند.

<b>دو دنیای متفاوت، دو شکل entity.</b> کاربر با ربات حرف می‌زند (Bot
API، از راه aiogram) ولی پست با اکانت کاربری فرستاده می‌شود (MTProto، از
راه Telethon). این دو نام‌های متفاوتی دارند: `custom_emoji` در برابر
`MessageEntityCustomEmoji`، و `custom_emoji_id` رشته‌ای در برابر
`document_id` عددی. این ماژول همان مترجم است.

<b>آفست‌ها بر حسب UTF-16 حساب می‌شوند</b>، همان‌طور که تلگرام می‌شمارد.
هر ایموجی دو واحد می‌گیرد، پس شمردن با len() پایتون قالب‌ها را جابه‌جا
می‌کند.
"""
from __future__ import annotations

import logging

from telkap.services.transform import utf16_len

log = logging.getLogger(__name__)

# کلیدهای متنی‌ای که قالب‌بندی‌شان نگه داشته می‌شود. هرکدام یک کلید
# همزاد با پسوند _entities در تنظیمات دارد.
RICH_KEYS = ("signature", "header", "footer")
SUFFIX = "_entities"

# نوعِ Bot API → نام کلاس Telethon. فقط چیزهایی که در یک امضا معنا
# دارند؛ چیزی که اینجا نباشد بی‌صدا کنار گذاشته می‌شود، که از فرستادن
# entity ناشناخته و خراب کردن کل پیام بهتر است.
_TYPES = {
    "custom_emoji": "MessageEntityCustomEmoji",
    "bold": "MessageEntityBold",
    "italic": "MessageEntityItalic",
    "underline": "MessageEntityUnderline",
    "strikethrough": "MessageEntityStrike",
    "spoiler": "MessageEntitySpoiler",
    "code": "MessageEntityCode",
    "text_link": "MessageEntityTextUrl",
}

MAX_SPANS = 40      # امضای معقول این‌قدر entity ندارد؛ سقف جلوی سوءاستفاده را می‌گیرد


def entities_key(key: str) -> str:
    return f"{key}{SUFFIX}"


def capture(entities, *, shift: int = 0) -> list[dict]:
    """entity های پیام کاربر را به شکل قابل ذخیره درمی‌آورد.

    `shift` تعداد واحدهای UTF-16 است که از ابتدای متن بریده شده — چون
    متن پیش از ذخیره strip می‌شود و بدون این تصحیح، همه‌ی آفست‌ها به
    اندازه‌ی فاصله‌های ابتدایی جابه‌جا می‌مانند.
    """
    spans: list[dict] = []
    for entity in entities or ():
        kind = getattr(entity, "type", None)
        if kind not in _TYPES:
            continue

        offset = int(getattr(entity, "offset", 0)) - shift
        length = int(getattr(entity, "length", 0))
        if offset < 0 or length <= 0:
            continue

        span = {"type": kind, "offset": offset, "length": length}
        if kind == "custom_emoji":
            emoji_id = getattr(entity, "custom_emoji_id", None)
            if not emoji_id:
                continue
            span["id"] = str(emoji_id)
        elif kind == "text_link":
            url = getattr(entity, "url", None)
            if not url:
                continue
            span["url"] = str(url)[:512]

        spans.append(span)
        if len(spans) >= MAX_SPANS:
            break
    return spans


def _build(span: dict, offset: int):
    """یک entity تلگرامی از روی شکل ذخیره‌شده.

    None یعنی این span قابل ساخت نبود و باید کنار گذاشته شود — یک
    entity خراب کل قالب‌بندی پیام را به‌هم می‌ریزد، پس نداشتنش بهتر است.
    """
    from telethon.tl import types

    name = _TYPES.get(span.get("type"))
    if name is None:
        return None
    cls = getattr(types, name, None)
    if cls is None:
        return None

    length = int(span.get("length") or 0)
    if length <= 0:
        return None

    try:
        if span["type"] == "custom_emoji":
            return cls(offset=offset, length=length, document_id=int(span["id"]))
        if span["type"] == "text_link":
            return cls(offset=offset, length=length, url=str(span["url"]))
        return cls(offset=offset, length=length)
    except (KeyError, TypeError, ValueError):
        log.debug("ساخت entity از %s ناموفق بود", span, exc_info=True)
        return None


def restore(spans, base_offset: int) -> list:
    """entity های ذخیره‌شده را روی جای تازه‌شان می‌نشاند.

    `base_offset` آفست UTF-16 جایی است که این متن در پست نهایی شروع
    می‌شود.
    """
    built = []
    for span in spans or ():
        if not isinstance(span, dict):
            continue
        try:
            offset = base_offset + int(span.get("offset") or 0)
        except (TypeError, ValueError):
            continue
        entity = _build(span, offset)
        if entity is not None:
            built.append(entity)
    return built


def place(result: str, piece: str, spans, *, from_end: bool) -> list:
    """متنِ ذخیره‌شده را در پست نهایی پیدا و entity هایش را می‌سازد.

    `from_end=True` برای امضا و فوتر است که ته پست می‌نشینند، و False
    برای هدر. جهت جست‌وجو مهم است: اگر همان عبارت جای دیگری هم در پست
    باشد، جست‌وجو از سمت درست به نمونه‌ی درست می‌رسد.

    فهرست خالی یعنی یا چیزی برای نشاندن نبود یا متن پیدا نشد — مثلاً
    وقتی «حذف ایموجی» امضا را عوض کرده. در آن حالت هیچ entity ای بهتر
    از entity روی جای اشتباه است.
    """
    if not spans or not piece:
        return []

    index = result.rfind(piece) if from_end else result.find(piece)
    if index < 0:
        return []
    return restore(spans, utf16_len(result[:index]))
