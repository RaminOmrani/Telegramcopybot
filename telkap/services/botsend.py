"""فرستادن به مقصد با <b>خودِ ربات</b>، نه با اکانت کاربر.

<b>چرا این نصف، مهم‌ترین نصف است.</b>

تا امروز هر پستی با اکانتِ خودِ مشتری فرستاده می‌شد — یعنی برای هر کاری
باید اکانتش را وصل می‌کرد. و همان یک قدم، بزرگ‌ترین مانعِ فروش است:
«اکانتم را به یک ربات وصل کنم؟»

ولی سمت مقصد اصلاً اکانت لازم ندارد. اگر مشتری <b>خودِ ربات</b> را در
کانالش ادمین کند — همان کاری که با هر ربات دیگری می‌کند و برایش عادی
است — ربات می‌تواند مستقیم پست بگذارد. این ماژول همان راه است.

<b>و چرا جدا از موتور کپی.</b> موتور کپی با Telethon حرف می‌زند (MTProto)
و این با aiogram (Bot API). این دو نه فقط دو کتابخانه، که دو مدلِ
داده‌اند: entity ها نام‌های متفاوت دارند، دکمه‌ها شکل متفاوت، و خطاها
معنای متفاوت. قاطی کردنشان داخل موتور، همان موتوری که الان سالم کار
می‌کند را شکننده می‌کند. پس ترجمه یک‌جا انجام می‌شود: اینجا.

<b>آنچه اینجا نیست:</b> فرستادن رسانه. فایلی که اکانت سرویس دیده،
`file_id` قابل استفاده برای ربات ندارد و باید یا دانلود/آپلود شود یا از
کانال واسط رد شود. آن تصمیم جداست و اینجا نمی‌آید — متن و قالب‌بندی و
دکمه اول باید درست باشند.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field

log = logging.getLogger(__name__)

# نام کلاس Telethon → نوعِ Bot API.
#
# <b>هرچه اینجا نباشد بی‌صدا کنار گذاشته می‌شود</b>، و این عمدی است:
# یک entity ناشناخته یا خراب، قالب‌بندیِ کلِ پیام را به‌هم می‌ریزد یا
# اصلاً جلوی رفتنش را می‌گیرد. نداشتنِ یک زیرخط بهتر از نرفتنِ پست است.
_ENTITY_TYPES = {
    "MessageEntityBold": "bold",
    "MessageEntityItalic": "italic",
    "MessageEntityUnderline": "underline",
    "MessageEntityStrike": "strikethrough",
    "MessageEntitySpoiler": "spoiler",
    "MessageEntityCode": "code",
    "MessageEntityPre": "pre",
    "MessageEntityBlockquote": "blockquote",
    "MessageEntityTextUrl": "text_link",
    "MessageEntityUrl": "url",
    "MessageEntityEmail": "email",
    "MessageEntityPhone": "phone_number",
    "MessageEntityMention": "mention",
    "MessageEntityMentionName": "text_mention",
    "MessageEntityHashtag": "hashtag",
    "MessageEntityCashtag": "cashtag",
    "MessageEntityBotCommand": "bot_command",
    "MessageEntityBankCard": "bank_card",
    "MessageEntityCustomEmoji": "custom_emoji",
}

# سقفِ خودِ تلگرام برای متن یک پیام. بریدن اینجا بهتر از خطا گرفتن
# است، چون خطای «متن بلند است» پست را کامل از دست می‌دهد.
MAX_TEXT = 4096
MAX_CAPTION = 1024


# ------------------------------------------------------------- ترجمه‌ی متن


def to_bot_entities(entities) -> list:
    """entity های Telethon را به شکل Bot API درمی‌آورد.

    <b>آفست‌ها دست‌نخورده می‌مانند</b> و این درست است: هر دو دنیا بر حسب
    واحدهای UTF-16 می‌شمارند، همان‌طور که خودِ تلگرام می‌شمارد. تبدیل
    کردنشان — که وسوسه‌اش هست چون len() پایتون فرق دارد — دقیقاً همان
    جابه‌جاییِ قالب را می‌سازد که می‌خواهیم نباشد.
    """
    from aiogram.types import MessageEntity

    out: list = []
    for entity in entities or ():
        kind = _ENTITY_TYPES.get(type(entity).__name__)
        if kind is None:
            continue

        offset = int(getattr(entity, "offset", -1))
        length = int(getattr(entity, "length", 0))
        if offset < 0 or length <= 0:
            continue

        extra: dict = {}
        if kind == "text_link":
            url = getattr(entity, "url", None)
            if not url:
                continue
            extra["url"] = str(url)
        elif kind == "custom_emoji":
            emoji_id = getattr(entity, "document_id", None)
            if not emoji_id:
                continue
            # Bot API این را رشته می‌خواهد، MTProto عدد می‌دهد
            extra["custom_emoji_id"] = str(emoji_id)
        elif kind == "pre":
            language = getattr(entity, "language", None)
            if language:
                extra["language"] = str(language)
        elif kind == "text_mention":
            # بدون شناسه‌ی کاربر، این entity بی‌معناست
            user_id = getattr(entity, "user_id", None)
            if not user_id:
                continue
            extra["user"] = {"id": int(user_id), "is_bot": False, "first_name": ""}

        try:
            out.append(MessageEntity(type=kind, offset=offset, length=length, **extra))
        except Exception:
            log.debug("ترجمه‌ی entity %s ناموفق بود", kind, exc_info=True)
    return out


def to_bot_buttons(message):
    """دکمه‌های لینک‌دارِ پست مبدا، به شکل Bot API.

    فقط دکمه‌های URL؛ دکمه‌های callback به رباتِ مبدا وصل‌اند و در کانال
    دیگری کار نمی‌کنند — همان قاعده‌ای که در مسیر اکانت هم هست.
    """
    from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

    markup = getattr(message, "reply_markup", None)
    if markup is None:
        return None

    rows = []
    for row in getattr(markup, "rows", []) or []:
        built = []
        for button in getattr(row, "buttons", []) or []:
            url = getattr(button, "url", None)
            text = getattr(button, "text", None)
            if url and text:
                built.append(InlineKeyboardButton(text=str(text), url=str(url)))
        if built:
            rows.append(built)
    return InlineKeyboardMarkup(inline_keyboard=rows) if rows else None


def trim(text: str, limit: int = MAX_TEXT) -> str:
    """متنِ بلندتر از سقف تلگرام را می‌برد.

    <b>چرا بریدن بهتر از خطا خوردن است.</b> اگر متن از سقف رد شود،
    تلگرام کل پیام را رد می‌کند و پست از دست می‌رود. یک پستِ بریده
    چیزی است که کاربر می‌بیند و می‌تواند درباره‌اش تصمیم بگیرد؛ پستی
    که اصلاً نرفته، فقط یک خلأ است.
    """
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"


# --------------------------------------------------- آیا ربات اجازه دارد


@dataclass(slots=True)
class Verdict:
    """جواب «ربات می‌تواند در این کانال پست بگذارد؟»

    <b>چرا پیام آماده دارد.</b> این وارسی دقیقاً سرِ راهِ ساختنِ کار
    انجام می‌شود، جایی که کاربر منتظر است. «خطا» به دردش نمی‌خورد؛
    باید بداند دقیقاً چه کاری کند.
    """

    ok: bool = False
    code: str = ""
    message: str = ""

    def __bool__(self) -> bool:
        return self.ok


ADD_BOT = (
    "ربات هنوز در این کانال ادمین نیست.\n\n"
    "کانال را باز کنید ← «مدیریت کانال» ← «مدیران» ← «افزودن مدیر» ← "
    "ربات را اضافه کنید و دسترسی <b>«ارسال پیام»</b> را روشن بگذارید."
)


async def can_post(bot, chat_id) -> Verdict:
    """وارسی می‌کند ربات واقعاً می‌تواند در این مقصد پست بگذارد.

    <b>چرا پیش از ساختن کار، نه سرِ اولین پست.</b> اگر این وارسی نباشد،
    کار ساخته می‌شود، کاربر فکر می‌کند تمام است، و خرابی ساعت‌ها بعد و
    به‌شکل «هیچ پستی نیامد» خودش را نشان می‌دهد — بدترین شکل ممکن،
    چون شبیه خرابیِ سرویس است نه یک قدمِ جامانده.
    """
    try:
        me = await bot.get_me()
        member = await bot.get_chat_member(chat_id=chat_id, user_id=me.id)
    except Exception as exc:
        text = str(exc).lower()
        if "not found" in text or "member list is inaccessible" in text:
            return Verdict(code="absent", message=ADD_BOT)
        if "forbidden" in text or "kicked" in text or "bot was blocked" in text:
            return Verdict(code="absent", message=ADD_BOT)
        log.info("وارسی دسترسی ربات در %s نشد: %s", chat_id, exc)
        return Verdict(
            code="unknown",
            message=(
                "نتوانستیم بررسی کنیم ربات در این کانال دسترسی دارد یا نه. "
                "مطمئن شوید نشانی کانال درست است و ربات ادمینش هست."
            ),
        )

    status = str(getattr(member, "status", "") or "")
    if status == "creator":
        return Verdict(ok=True, code="creator", message="")
    if status != "administrator":
        return Verdict(code="absent", message=ADD_BOT)

    # در کانال، ادمین بودن کافی نیست — باید اجازه‌ی ارسال داشته باشد.
    # در گروه چنین دسترسیِ جدایی وجود ندارد و None یعنی «مربوط نیست».
    allowed = getattr(member, "can_post_messages", None)
    if allowed is False:
        return Verdict(
            code="no_post",
            message=(
                "ربات ادمین هست ولی اجازه‌ی <b>«ارسال پیام»</b> ندارد.\n\n"
                "در «مدیریت کانال ← مدیران» روی ربات بزنید و همان یک "
                "دسترسی را روشن کنید."
            ),
        )
    return Verdict(ok=True, code="admin", message="")


# ----------------------------------------------------------------- ارسال


@dataclass(slots=True)
class Sent:
    ids: list[int] = field(default_factory=list)

    def __bool__(self) -> bool:
        return bool(self.ids)


async def send_text(
    bot,
    chat_id,
    text: str,
    *,
    entities=None,
    buttons=None,
    silent: bool = False,
) -> Sent:
    """یک پیام متنی با همان قالب‌بندیِ مبدا می‌فرستد.

    `entities` همان entity های Telethon است؛ ترجمه همین‌جا انجام
    می‌شود تا صداکننده لازم نباشد دو دنیا را بشناسد.
    """
    body = trim(text)
    if not body.strip():
        return Sent()

    message = await bot.send_message(
        chat_id=chat_id,
        text=body,
        entities=to_bot_entities(entities) or None,
        reply_markup=buttons,
        disable_notification=silent,
        link_preview_options={"is_disabled": True},
    )
    return Sent(ids=[int(message.message_id)])
