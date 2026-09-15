"""تست فرستادن با خودِ ربات (نصفِ مقصد).

<b>چرا این مسیر ساخته شد.</b> تا امروز هر پستی با اکانتِ خودِ مشتری
فرستاده می‌شد، یعنی برای هر کاری باید اکانتش را وصل می‌کرد — و همان
یک قدم بزرگ‌ترین مانعِ فروش است. سمت مقصد اصلاً اکانت لازم ندارد:
ادمین کردنِ ربات در کانال، کاری است که هرکسی با هر رباتی می‌کند.

<b>و چرا تستش دقیق است.</b> اینجا یک مترجم بین دو دنیاست — Telethon و
Bot API — و خطای مترجم بی‌صداست: پست می‌رود، فقط قالب‌بندی‌اش جابه‌جا
شده، یا لینکی که باید کلیک‌شدنی باشد متنِ خام است. چنین خطایی را هیچ
لاگی نشان نمی‌دهد؛ فقط مشتری می‌بیند و چیزی نمی‌گوید.
"""
from __future__ import annotations

import pytest
from telethon.tl import types

from telkap.services import botsend

# ------------------------------------------------------- ترجمه‌ی قالب‌بندی


def test_the_common_formats_survive_the_trip():
    got = botsend.to_bot_entities([
        types.MessageEntityBold(offset=0, length=4),
        types.MessageEntityItalic(offset=5, length=3),
        types.MessageEntityStrike(offset=9, length=2),
        types.MessageEntitySpoiler(offset=12, length=6),
    ])
    assert [e.type for e in got] == ["bold", "italic", "strikethrough", "spoiler"]
    assert [(e.offset, e.length) for e in got] == [(0, 4), (5, 3), (9, 2), (12, 6)]


def test_offsets_are_never_recomputed():
    """<b>تله‌ای که وسوسه‌اش همیشه هست.</b>

    len() پایتون با شمارشِ تلگرام فرق دارد و آدم وسوسه می‌شود
    «تصحیحش» کند. ولی هر دو دنیا بر حسب واحدهای UTF-16 می‌شمارند —
    همان‌طور که خودِ تلگرام. دست زدن به آفست‌ها دقیقاً همان جابه‌جاییِ
    قالب را می‌سازد که می‌خواستیم نباشد، و روی متن فارسیِ پر از ایموجی
    خودش را نشان می‌دهد.
    """
    # آفست ۱۰ یعنی «بعد از پنج ایموجی»، نه «بعد از ده نویسه‌ی پایتون»
    got = botsend.to_bot_entities([types.MessageEntityBold(offset=10, length=4)])
    assert (got[0].offset, got[0].length) == (10, 4)


def test_a_link_keeps_its_address():
    got = botsend.to_bot_entities([
        types.MessageEntityTextUrl(offset=0, length=5, url="https://example.com"),
    ])
    assert got[0].type == "text_link"
    assert got[0].url == "https://example.com"


def test_a_premium_emoji_id_becomes_a_string():
    """<b>یک تفاوتِ کوچک که کل پیام را رد می‌کند.</b>

    MTProto شناسه‌ی ایموجی را عدد می‌دهد و Bot API رشته می‌خواهد.
    فرستادن عدد، خطای اعتبارسنجی می‌دهد و پست اصلاً نمی‌رود.
    """
    got = botsend.to_bot_entities([
        types.MessageEntityCustomEmoji(offset=0, length=2, document_id=5368324170671202286),
    ])
    assert got[0].custom_emoji_id == "5368324170671202286"
    assert isinstance(got[0].custom_emoji_id, str)


def test_a_broken_entity_is_dropped_not_sent():
    """<b>چرا بی‌صدا کنار گذاشته می‌شود و خطا نمی‌دهد.</b>

    یک entity خراب باعث می‌شود تلگرام <b>کل پیام</b> را رد کند. از
    دست دادن یک زیرخط، در برابر از دست دادن پست، معامله‌ی روشنی است.
    """
    got = botsend.to_bot_entities([
        types.MessageEntityTextUrl(offset=0, length=5, url=""),      # بدون نشانی
        types.MessageEntityBold(offset=-1, length=3),                # آفست منفی
        types.MessageEntityItalic(offset=2, length=0),               # طول صفر
        types.MessageEntityBold(offset=0, length=4),                 # این یکی سالم
    ])
    assert [e.type for e in got] == ["bold"]


def test_an_unknown_entity_does_not_break_the_rest():
    class _Alien:
        offset, length = 0, 3

    got = botsend.to_bot_entities([_Alien(), types.MessageEntityBold(offset=4, length=2)])
    assert [e.type for e in got] == ["bold"]


def test_no_entities_is_not_an_error():
    assert botsend.to_bot_entities(None) == []
    assert botsend.to_bot_entities([]) == []


# ----------------------------------------------------------------- دکمه‌ها


class _Btn:
    def __init__(self, text="", url=None) -> None:
        self.text, self.url = text, url


class _Row:
    def __init__(self, buttons) -> None:
        self.buttons = buttons


class _Markup:
    def __init__(self, rows) -> None:
        self.rows = rows


class _Msg:
    def __init__(self, markup=None) -> None:
        self.reply_markup = markup


def test_link_buttons_are_rebuilt():
    markup = botsend.to_bot_buttons(_Msg(_Markup([
        _Row([_Btn("سایت ما", "https://example.com")]),
    ])))
    assert markup is not None
    assert markup.inline_keyboard[0][0].text == "سایت ما"


def test_callback_buttons_are_left_behind():
    """دکمه‌ی callback به رباتِ مبدا وصل است و در کانال دیگری فقط یک
    دکمه‌ی خراب می‌شود — همان قاعده‌ای که در مسیر اکانت هم داریم."""
    markup = botsend.to_bot_buttons(_Msg(_Markup([
        _Row([_Btn("رأی بده", None), _Btn("سایت", "https://example.com")]),
    ])))
    assert len(markup.inline_keyboard[0]) == 1


def test_a_post_without_buttons_gets_no_keyboard():
    assert botsend.to_bot_buttons(_Msg()) is None
    assert botsend.to_bot_buttons(_Msg(_Markup([_Row([_Btn("x", None)])]))) is None


# ------------------------------------------------------------- سقف تلگرام


def test_a_too_long_text_is_trimmed_not_rejected():
    """<b>بریدن بهتر از خطا خوردن است.</b>

    متنِ بلندتر از سقف، کل پیام را رد می‌کند و پست از دست می‌رود.
    پستِ بریده چیزی است که کاربر می‌بیند؛ پستی که نرفته، فقط یک خلأ
    است که بعداً باید دنبال علتش گشت.
    """
    cut = botsend.trim("ا" * 5000)
    assert len(cut) <= botsend.MAX_TEXT
    assert cut.endswith("…")


def test_a_normal_text_is_untouched():
    assert botsend.trim("سلام") == "سلام"


# ------------------------------------------- آیا ربات اجازه‌ی ارسال دارد


class _Me:
    id = 777


class _Member:
    def __init__(self, status, can_post=None) -> None:
        self.status = status
        self.can_post_messages = can_post


class _Bot:
    def __init__(self, member=None, error: Exception | None = None) -> None:
        self._member, self._error = member, error
        self.sent: list[dict] = []

    async def get_me(self):
        return _Me()

    async def get_chat_member(self, chat_id, user_id):
        if self._error is not None:
            raise self._error
        return self._member

    async def send_message(self, **kwargs):
        self.sent.append(kwargs)

        class _Result:
            message_id = 4242

        return _Result()


@pytest.mark.asyncio
async def test_an_admin_with_posting_rights_is_allowed():
    assert await botsend.can_post(_Bot(_Member("administrator", True)), -100)


@pytest.mark.asyncio
async def test_the_channel_owner_is_allowed():
    assert await botsend.can_post(_Bot(_Member("creator")), -100)


@pytest.mark.asyncio
async def test_a_group_where_the_flag_does_not_apply_is_allowed():
    """در گروه، دسترسیِ جداگانه‌ی «ارسال پیام» وجود ندارد و مقدارش None
    می‌آید. اگر None را «اجازه ندارد» بخوانیم، هر گروهی رد می‌شود."""
    assert await botsend.can_post(_Bot(_Member("administrator", None)), -100)


@pytest.mark.asyncio
async def test_an_admin_without_posting_rights_is_told_exactly_what_to_turn_on():
    verdict = await botsend.can_post(_Bot(_Member("administrator", False)), -100)
    assert not verdict
    assert verdict.code == "no_post"
    assert "ارسال پیام" in verdict.message


@pytest.mark.asyncio
async def test_a_plain_member_is_not_enough():
    verdict = await botsend.can_post(_Bot(_Member("member")), -100)
    assert not verdict
    assert verdict.code == "absent"


@pytest.mark.asyncio
async def test_a_bot_that_is_not_there_gets_the_how_to_add_it_message():
    """<b>مهم‌ترین پیامِ این ماژول.</b>

    این همان لحظه‌ای است که کاربر منتظر است و کار هنوز ساخته نشده.
    «خطا» به دردش نمی‌خورد — باید دقیقاً بداند کدام دکمه را بزند،
    وگرنه همین‌جا رها می‌کند.
    """
    error = Exception("Bad Request: chat not found")
    verdict = await botsend.can_post(_Bot(error=error), -100)
    assert not verdict
    assert verdict.code == "absent"
    assert "مدیران" in verdict.message and "ارسال پیام" in verdict.message


@pytest.mark.asyncio
async def test_an_unclear_failure_never_claims_everything_is_fine():
    """<b>سکوتِ خوش‌بینانه بدترین جواب است.</b>

    اگر وارسی به نتیجه نرسد و ما «اجازه دارد» برگردانیم، کار ساخته
    می‌شود و خرابی ساعت‌ها بعد به‌شکل «هیچ پستی نیامد» پیدایش می‌شود —
    که شبیه خرابیِ سرویس است، نه یک قدمِ جامانده.
    """
    verdict = await botsend.can_post(_Bot(error=Exception("gateway timeout")), -100)
    assert not verdict
    assert verdict.code == "unknown"


# ------------------------------------------------------------ خودِ ارسال


@pytest.mark.asyncio
async def test_sending_passes_the_translated_format_along():
    bot = _Bot()
    result = await botsend.send_text(
        bot, -100, "سلام دنیا",
        entities=[types.MessageEntityBold(offset=0, length=4)],
    )
    assert result and result.ids == [4242]

    call = bot.sent[0]
    assert call["text"] == "سلام دنیا"
    assert call["entities"][0].type == "bold"
    # پیش‌نمایش لینک باید خاموش باشد، مثل مسیر اکانت
    assert call["link_preview_options"] == {"is_disabled": True}


@pytest.mark.asyncio
async def test_an_empty_post_is_not_sent_at_all():
    """تلگرام پیام خالی را رد می‌کند و آن خطا در لاگ شبیه یک خرابیِ
    واقعی به نظر می‌رسد."""
    bot = _Bot()
    assert not await botsend.send_text(bot, -100, "   ")
    assert bot.sent == []


# ------------------------------------------------- رنگِ دکمه‌ها (Bot API 9.4)


def test_only_the_three_real_styles_are_used():
    """<b>مقدارِ اشتباه، کل پیام را رد می‌کند.</b>

    تلگرام فقط سه مقدار می‌پذیرد. یک «red» یا «green» به‌جای
    «danger»/«success» خطای اعتبارسنجی می‌دهد و پیام اصلاً نمی‌رود —
    یعنی یک تایپوی رنگ، دکمه را نه بی‌رنگ که <b>ناموجود</b> می‌کند.
    """
    from telkap import keyboards

    assert {keyboards.DANGER, keyboards.GO, keyboards.CALM} == {
        "danger", "success", "primary"
    }


def test_a_plain_confirm_is_not_red():
    """<b>اگر همه‌ی تأییدها قرمز شوند، قرمز دیگر چیزی نمی‌گوید.</b>"""
    from telkap.keyboards import confirm

    markup = confirm("yes", "no")
    assert markup.inline_keyboard[0][0].style is None


def test_a_destructive_confirm_is_red():
    """حذف برگشت‌ناپذیر است؛ دکمه‌ی قرمز همان مکثِ نیم‌ثانیه‌ای را
    می‌سازد که فرقِ «حذف کردم» و «اشتباهی حذف شد» است."""
    from telkap.keyboards import DANGER, confirm

    markup = confirm("yes", "no", danger=True)
    assert markup.inline_keyboard[0][0].style == DANGER
    # «خیر» رنگ نمی‌گیرد — رنگ برای کاری است که باید مکث بیاورد
    assert markup.inline_keyboard[0][1].style is None


def test_the_library_actually_supports_colours():
    """<b>نگهبانِ ارتقا.</b>

    این فیلد در Bot API 9.4 آمده و aiogram قدیمی‌تر از ۳٫۳۱ ندارَدش.
    اگر روزی کسی کتابخانه را پایین ببرد، همه‌ی دکمه‌های رنگی بی‌صدا
    خطای اعتبارسنجی می‌دهند — و پیام‌ها نمی‌روند.
    """
    from aiogram.types import InlineKeyboardButton, KeyboardButton

    assert "style" in InlineKeyboardButton.model_fields
    assert "style" in KeyboardButton.model_fields
