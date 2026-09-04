"""تست نگهداری ایموجی پریمیوم در مسیر کپی.

کاربر گزارش داد ایموجی پریمیوم ساده می‌شود. مسیر واقعی سالم بود؛ چیزی
که خراب بود پیام پیش‌نمایش بود که ادعا می‌کرد حفظشان می‌کند در حالی که
اصلاً entity ها را حمل نمی‌کرد.

پس اینجا دو چیز سنجیده می‌شود: اینکه بازنگاشتِ entity ها واقعاً ایموجی
پریمیوم را نگه می‌دارد، و اینکه تشخیص «این پست ایموجی پریمیوم دارد»
درست کار می‌کند.
"""
from __future__ import annotations

from telkap.services.transform import (
    apply_transforms,
    drop_custom_emoji,
    has_custom_emoji,
    remap_entities,
)


class MessageEntityCustomEmoji:
    """جای کلاس هم‌نامِ Telethon.

    <b>نام این کلاس خودش قرارداد است.</b> کد با نام کلاس تشخیص می‌دهد تا
    به Telethon وابسته نشود، پس نامِ دقیق تنها چیزی است که آن دو را به هم
    وصل می‌کند. تستِ پایین همین را با کلاس واقعی می‌سنجد.
    """

    def __init__(self, offset: int, length: int, document_id: int = 555):
        self.offset = offset
        self.length = length
        self.document_id = document_id


class Bold:
    def __init__(self, offset: int, length: int):
        self.offset = offset
        self.length = length


# ── قرارداد با Telethon ─────────────────────────────────────────────


def test_the_class_name_we_match_on_is_the_real_one():
    """کد ایموجی پریمیوم را با نام کلاس تشخیص می‌دهد، نه با import.

    این استقلال بهایی دارد: اگر Telethon روزی کلاس را عوض نام بدهد،
    تشخیص <b>بی‌صدا</b> از کار می‌افتد و ایموجی‌ها بدون هیچ خطایی ساده
    منتشر می‌شوند. این تست همان روز را می‌گیرد.
    """
    from telethon.tl.types import MessageEntityCustomEmoji as Real

    assert Real.__name__ == "MessageEntityCustomEmoji"
    assert has_custom_emoji([Real(offset=0, length=2, document_id=1)]) is True
    assert drop_custom_emoji([Real(offset=0, length=2, document_id=1)]) == []


# ── تشخیص ───────────────────────────────────────────────────────────


def test_a_post_with_premium_emoji_is_recognized():
    assert has_custom_emoji([MessageEntityCustomEmoji(0, 2)]) is True
    assert has_custom_emoji([Bold(0, 4), MessageEntityCustomEmoji(5, 2)]) is True


def test_a_post_without_them_is_not():
    assert has_custom_emoji([]) is False
    assert has_custom_emoji(None) is False
    assert has_custom_emoji([Bold(0, 4)]) is False


# ── بازنگاشت: ایموجی پریمیوم باید زنده بماند ────────────────────────


def test_premium_emoji_survive_a_header_being_added():
    """هدر فقط متن را جلو می‌برد؛ همه‌ی entity ها باید جابه‌جا شوند."""
    original = "سلام دنیا"
    result = apply_transforms(original, {"header": "خبر فوری"})
    entity = MessageEntityCustomEmoji(offset=0, length=4)

    mapped = remap_entities(original, result, [entity])

    assert mapped is not None
    assert len(mapped) == 1
    assert type(mapped[0]).__name__ == "MessageEntityCustomEmoji"
    assert mapped[0].document_id == 555
    # متن اصلی بعد از «خبر فوری\n\n» شروع می‌شود
    assert mapped[0].offset == len("خبر فوری\n\n")


def test_premium_emoji_outside_the_changed_part_survive():
    """امضای مبدا حذف می‌شود ولی ایموجیِ بالای متن نباید قربانی شود."""
    original = "متن اصلی اینجاست\n\n@sourcechannel"
    result = apply_transforms(original, {"remove_source_signature": True})
    entity = MessageEntityCustomEmoji(offset=0, length=4)

    mapped = remap_entities(original, result, [entity])

    assert mapped is not None
    assert mapped[0].offset == 0
    assert mapped[0].length == 4


def test_an_emoji_sitting_on_deleted_text_is_dropped():
    """اگر خودِ ایموجی روی متنِ حذف‌شده باشد، جایی برای رفتن ندارد.

    نگه داشتنش یعنی آفستِ اشتباه، و آفست اشتباه کل فرمت پیام را
    به‌هم می‌ریزد — بدتر از نداشتنش.
    """
    original = "متن اصلی\n\n@sourcechannel"
    result = apply_transforms(original, {"remove_source_signature": True})
    entity = MessageEntityCustomEmoji(offset=len("متن اصلی\n\n"), length=4)

    mapped = remap_entities(original, result, [entity])

    assert not mapped


def test_the_original_entity_is_never_mutated():
    """entity ها از خودِ پیام تلگرام می‌آیند؛ دست‌کاریشان روی تلاش
    مجدد و روی مقصدهای بعدی اثر می‌گذارد."""
    original = "سلام"
    result = apply_transforms(original, {"header": "خبر"})
    entity = MessageEntityCustomEmoji(offset=0, length=4)

    remap_entities(original, result, [entity])

    assert entity.offset == 0


# ── حالت بدون پریمیوم ───────────────────────────────────────────────


def test_dropping_keeps_every_other_format():
    """وقتی اکانت پریمیوم نیست، فقط ایموجی می‌رود — بولد و لینک می‌مانند."""
    kept = drop_custom_emoji([Bold(0, 4), MessageEntityCustomEmoji(5, 2), Bold(8, 3)])

    assert kept is not None
    assert len(kept) == 2
    assert all(type(e).__name__ == "Bold" for e in kept)


def test_dropping_reports_nothing_to_do():
    """None یعنی «ایموجی پریمیومی نبود»، پس صدازننده نباید دوباره
    بفرستد — وگرنه پیام دوبار منتشر می‌شود."""
    assert drop_custom_emoji([Bold(0, 4)]) is None
    assert drop_custom_emoji([]) is None
    assert drop_custom_emoji(None) is None


def test_a_post_that_is_only_premium_emoji_becomes_empty_not_none():
    """فهرست خالی با None فرق دارد: خالی یعنی «فرستادن بدون entity»،
    و None یعنی «کاری نکن»."""
    assert drop_custom_emoji([MessageEntityCustomEmoji(0, 2)]) == []
