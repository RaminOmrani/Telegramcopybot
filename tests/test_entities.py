"""تست حفظ فرمت متن و ایموجی پریمیوم.

تلگرام آفست entity ها را بر حسب واحد UTF-16 می‌شمارد. ایموجی‌ها بیرون از
BMP هستند و دو واحد می‌گیرند؛ اگر با len() پایتون حساب شود، فرمت‌ها روی
نویسه‌ی اشتباه می‌افتند.
"""
from __future__ import annotations

from dataclasses import dataclass

import pytest

from telkap.services.transform import remap_entities, utf16_len


@dataclass
class FakeEntity:
    """جای MessageEntityBold / MessageEntityCustomEmoji و امثال آن‌ها."""

    offset: int
    length: int
    kind: str = "bold"


# ------------------------------------------------------------ utf16_len
def test_utf16_len_ascii_and_persian():
    assert utf16_len("abc") == 3
    assert utf16_len("سلام") == 4          # فارسی داخل BMP است
    assert utf16_len("") == 0


def test_utf16_len_counts_emoji_as_two():
    assert utf16_len("🔥") == 2             # بیرون از BMP
    assert utf16_len("a🔥b") == 4
    assert utf16_len("⚽️") == 2            # نویسه + variation selector


# -------------------------------------------------------- remap_entities
def test_no_entities_returns_none():
    assert remap_entities("متن", "متن", None) is None
    assert remap_entities("متن", "متن", []) is None


def test_unchanged_text_keeps_entities_as_is():
    entities = [FakeEntity(offset=0, length=4)]
    result = remap_entities("سلام", "سلام", entities)
    assert result == entities


def test_footer_only_does_not_shift():
    """فوتر به انتها اضافه می‌شود، پس آفست‌ها ثابت می‌مانند."""
    original = "خبر مهم"
    result = "خبر مهم\n\n@mychannel"
    entities = [FakeEntity(offset=0, length=3)]
    remapped = remap_entities(original, result, entities)
    assert remapped[0].offset == 0
    assert remapped[0].length == 3


def test_header_shifts_offsets():
    original = "خبر مهم"
    header = "🔥 داغ"
    result = f"{header}\n\n{original}"
    entities = [FakeEntity(offset=0, length=3)]
    remapped = remap_entities(original, result, entities)
    # «🔥 داغ» = ۲ (ایموجی) + ۱ (فاصله) + ۳ (داغ) = ۶، به‌علاوه‌ی دو خط جدید
    assert remapped[0].offset == utf16_len(f"{header}\n\n")
    assert remapped[0].offset == 8


def test_header_with_emoji_uses_utf16_not_python_len():
    """اگر با len() پایتون حساب می‌شد، این تست شکست می‌خورد."""
    original = "متن"
    header = "🔥🔥🔥"          # پایتون: ۳ نویسه | UTF-16: ۶ واحد
    result = f"{header}\n\n{original}"

    remapped = remap_entities(original, result, [FakeEntity(offset=0, length=3)])
    assert remapped[0].offset == 8       # 6 + 2 خط جدید
    assert remapped[0].offset != len(f"{header}\n\n")  # یعنی ۵ نمی‌شود


def test_original_entities_are_not_mutated():
    """شیء اصلی نباید دست بخورد؛ پیام مبدا در حافظه مشترک است."""
    entities = [FakeEntity(offset=0, length=3)]
    remap_entities("متن", "سر\n\nمتن", entities)
    assert entities[0].offset == 0


def test_multiple_entities_all_shift_equally():
    original = "یک دو سه"
    result = f"سر\n\n{original}"
    entities = [FakeEntity(offset=0, length=2), FakeEntity(offset=3, length=2)]
    remapped = remap_entities(original, result, entities)
    shift = utf16_len("سر\n\n")
    assert [e.offset for e in remapped] == [0 + shift, 3 + shift]


def test_entity_on_replaced_word_is_dropped():
    """entity ای که دقیقاً روی متن عوض‌شده افتاده، آفستش بی‌اعتبار است."""
    original = "قیمت الماس بالا رفت"
    result = "قیمت جواهر بالا رفت"     # جایگزینی کلمه
    assert remap_entities(original, result, [FakeEntity(offset=5, length=5)]) is None


def test_removed_link_drops_its_own_entity():
    original = "ببینید https://example.com الان"
    result = "ببینید  الان"
    assert remap_entities(original, result, [FakeEntity(offset=7, length=19)]) is None


# ------------------- تغییر متن نباید entity های بی‌ربط را از بین ببرد
def test_entity_before_replacement_is_kept():
    """متن از وسط عوض شده، ولی entity جای دیگری است و باید بماند."""
    original = "خبر فوری: قیمت الماس بالا رفت"
    result = "خبر فوری: قیمت جواهر بالا رفت"
    bold = FakeEntity(offset=0, length=8)          # «خبر فوری»
    remapped = remap_entities(original, result, [bold])
    assert remapped is not None
    assert remapped[0].offset == 0
    assert remapped[0].length == 8


def test_entity_after_replacement_shifts_correctly():
    original = "قیمت الماس بالا رفت"
    result = "قیمت جواهر گران بالا رفت"            # جایگزین بلندتر است
    bold = FakeEntity(offset=11, length=4)         # «بالا» در متن اصلی
    assert original[11:15] == "بالا"
    remapped = remap_entities(original, result, [bold])
    assert remapped is not None
    assert result[remapped[0].offset:remapped[0].offset + remapped[0].length] == "بالا"


def test_custom_emoji_survives_signature_replacement():
    """سناریوی واقعی: امضای کانال مبدا عوض می‌شود، ایموجی پریمیوم بالای متن است.

    پیش از این، هر تغییری در متن باعث می‌شد همه‌ی entity ها حذف شوند و
    ایموجی پریمیوم به نویسه‌ی جایگزین ساده‌اش تبدیل شود.
    """
    original = "✔️ مصدومیت جدی نیست\n\n@source_channel"
    result = "✔️ مصدومیت جدی نیست\n\n@my_channel"
    # ایموجی پریمیوم روی «✔️» در ابتدای متن
    custom = FakeEntity(offset=0, length=utf16_len("✔️"), kind="custom_emoji")
    remapped = remap_entities(original, result, [custom])
    assert remapped is not None
    assert remapped[0].kind == "custom_emoji"
    assert remapped[0].offset == 0
    assert remapped[0].length == utf16_len("✔️")


def test_emoji_before_change_keeps_utf16_offsets():
    """ایموجی پیش از محل تغییر: آفست باید بر حسب UTF-16 بماند، نه نویسه."""
    original = "🔥🔥 قیمت الماس"
    result = "🔥🔥 قیمت جواهر"
    custom = FakeEntity(offset=2, length=2, kind="custom_emoji")   # ایموجی دوم
    remapped = remap_entities(original, result, [custom])
    assert remapped is not None
    assert remapped[0].offset == 2
    assert remapped[0].length == 2


def test_entities_split_across_a_change_keep_the_safe_ones():
    original = "الف الماس ب"
    result = "الف جواهر ب"
    keep_before = FakeEntity(offset=0, length=3)     # «الف»
    on_change = FakeEntity(offset=4, length=5)       # «الماس»
    keep_after = FakeEntity(offset=10, length=1)     # «ب»
    remapped = remap_entities(original, result, [keep_before, on_change, keep_after])
    assert remapped is not None
    assert len(remapped) == 2
    texts = [result[e.offset:e.offset + e.length] for e in remapped]
    assert texts == ["الف", "ب"]


def test_completely_different_text_drops_everything():
    assert remap_entities("یک متن", "چیز دیگری کاملاً", [FakeEntity(offset=0, length=2)]) is None


def test_header_and_footer_together():
    original = "بدنه"
    result = f"سر\n\n{original}\n\nپا"
    remapped = remap_entities(original, result, [FakeEntity(offset=0, length=4)])
    assert remapped[0].offset == utf16_len("سر\n\n")


def test_custom_emoji_entity_survives_footer():
    """سناریوی واقعی: پست با ایموجی پریمیوم و فوتر اضافه‌شده."""
    original = "🏅 نتیجه بازی"
    result = f"{original}\n\n@mychannel"
    # ایموجی پریمیوم در آفست ۰ به طول ۲ واحد UTF-16
    custom = FakeEntity(offset=0, length=2, kind="custom_emoji")
    remapped = remap_entities(original, result, [custom])
    assert remapped[0].offset == 0
    assert remapped[0].length == 2
    assert remapped[0].kind == "custom_emoji"


# ------------------------------------------- عبور entity از موتور کپی
@pytest.mark.asyncio
async def test_copier_passes_entities_when_only_footer_added(tmp_path, monkeypatch):
    """فوتر متن اصلی را دست نمی‌زند، پس فرمت باید حفظ شود."""
    from tests.test_copier import FakeClient, FakeManager, FakeMessage, _setup

    db_module, task_id = await _setup(
        tmp_path, monkeypatch, settings={"footer": "@mychannel"}
    )
    try:
        from telkap.services.copier import Copier

        client = FakeClient()
        copier = Copier(FakeManager(client))

        bold = FakeEntity(offset=0, length=3)
        msg = FakeMessage(id=1, message="خبر مهم ورزشی", entities=[bold])
        assert await copier.process(7, task_id, [msg]) is True

        record = client.sent[0]
        assert record.entities is not None
        assert record.entities[0].offset == 0      # فوتر آفست را جابه‌جا نمی‌کند
        assert record.text.endswith("@mychannel")
    finally:
        await db_module.close_db()


@pytest.mark.asyncio
async def test_copier_drops_entities_when_text_modified(tmp_path, monkeypatch):
    """با جایگزینی کلمه، آفست‌ها بی‌اعتبار می‌شوند و نباید فرستاده شوند."""
    from tests.test_copier import FakeClient, FakeManager, FakeMessage, _setup

    db_module, task_id = await _setup(
        tmp_path, monkeypatch, settings={}, rules=[("replace", "الماس", "جواهر")]
    )
    try:
        from telkap.services.copier import Copier

        client = FakeClient()
        copier = Copier(FakeManager(client))

        msg = FakeMessage(
            id=1, message="قیمت الماس", entities=[FakeEntity(offset=6, length=5)]
        )
        assert await copier.process(7, task_id, [msg]) is True
        assert client.sent[0].entities is None
        assert "جواهر" in client.sent[0].text
    finally:
        await db_module.close_db()


@pytest.mark.asyncio
async def test_copier_keeps_custom_emoji_when_signature_replaced(tmp_path, monkeypatch):
    """گزارش کاربر: آیدی مبدا با آیدی خودش عوض می‌شد و ایموجی پریمیوم می‌پرید."""
    from tests.test_copier import FakeClient, FakeManager, FakeMessage, _setup

    db_module, task_id = await _setup(
        tmp_path,
        monkeypatch,
        settings={"remove_source_signature": True, "signature": "@my_channel"},
    )
    try:
        from telkap.services.copier import Copier

        client = FakeClient()
        copier = Copier(FakeManager(client))

        custom = FakeEntity(offset=0, length=2, kind="custom_emoji")
        msg = FakeMessage(
            id=1,
            message="🏅 مصدومیت جدی نیست\n\n@source_channel",
            entities=[custom],
        )
        assert await copier.process(7, task_id, [msg]) is True

        record = client.sent[0]
        assert record.entities is not None, "ایموجی پریمیوم نباید حذف شود"
        assert record.entities[0].kind == "custom_emoji"
        assert record.text.startswith("🏅")
        assert record.text.endswith("@my_channel")
        assert "@source_channel" not in record.text
        # entity باید دقیقاً روی همان ایموجی بیفتد
        start = record.entities[0].offset
        assert utf16_len(record.text[:1]) == 2 and start == 0
    finally:
        await db_module.close_db()


@pytest.mark.asyncio
async def test_copier_retries_without_custom_emoji_for_non_premium(tmp_path, monkeypatch):
    """اگر تلگرام ایموجی پریمیوم را قبول نکند، پیام بدون آن‌ها می‌رود."""
    # اینجا از نوع‌های واقعی Telethon استفاده می‌شود تا تشخیص «ایموجی پریمیوم»
    # با همان نام کلاسی سنجیده شود که در اجرای واقعی می‌آید
    from telethon.errors import PremiumAccountRequiredError
    from telethon.tl.types import MessageEntityBold, MessageEntityCustomEmoji

    from tests.test_copier import FakeClient, FakeManager, FakeMessage, _setup

    db_module, task_id = await _setup(tmp_path, monkeypatch, settings={})
    try:
        from telkap.services.copier import Copier

        class PickyClient(FakeClient):
            """اولین ارسالِ دارای ایموجی پریمیوم را رد می‌کند."""

            async def send_message(self, target, text, **kwargs):
                entities = kwargs.get("formatting_entities") or []
                if any(isinstance(e, MessageEntityCustomEmoji) for e in entities):
                    raise PremiumAccountRequiredError(request=None)
                return await super().send_message(target, text, **kwargs)

        warnings: list[tuple[int, str]] = []

        async def notifier(user_id, text):
            warnings.append((user_id, text))

        client = PickyClient()
        copier = Copier(FakeManager(client), notifier=notifier)

        msg = FakeMessage(
            id=1,
            message="🏅 خبر",
            entities=[
                MessageEntityCustomEmoji(offset=0, length=2, document_id=555),
                MessageEntityBold(offset=3, length=3),
            ],
        )
        assert await copier.process(7, task_id, [msg]) is True

        record = client.sent[0]
        types = [type(e).__name__ for e in (record.entities or [])]
        assert types == ["MessageEntityBold"]   # بقیه‌ی فرمت‌ها حفظ شده‌اند
        assert warnings and "پریمیوم" in warnings[0][1]
    finally:
        await db_module.close_db()


@pytest.mark.asyncio
async def test_premium_emoji_kept_when_telegram_accepts_it(tmp_path, monkeypatch):
    """اکانت پریمیوم: ایموجی همراه شناسه‌ی سندش دست‌نخورده می‌رسد."""
    from telethon.tl.types import MessageEntityCustomEmoji

    from tests.test_copier import FakeClient, FakeManager, FakeMessage, _setup

    db_module, task_id = await _setup(
        tmp_path, monkeypatch, settings={"footer": "@my_channel"}
    )
    try:
        from telkap.services.copier import Copier

        client = FakeClient()
        copier = Copier(FakeManager(client))

        msg = FakeMessage(
            id=1,
            message="🏅 خبر",
            entities=[MessageEntityCustomEmoji(offset=0, length=2, document_id=555)],
        )
        assert await copier.process(7, task_id, [msg]) is True

        entity = client.sent[0].entities[0]
        assert isinstance(entity, MessageEntityCustomEmoji)
        assert entity.document_id == 555     # بدون این، ایموجی ساده می‌شود
        assert entity.offset == 0
    finally:
        await db_module.close_db()


@pytest.mark.asyncio
async def test_single_media_not_wrapped_in_list(tmp_path, monkeypatch):
    """تک‌رسانه باید تکی برود، نه به‌صورت آلبومِ یک‌تایی."""
    from tests.test_copier import FakeClient, FakeManager, FakeMessage, _setup

    db_module, task_id = await _setup(tmp_path, monkeypatch, settings={})
    try:
        from telkap.services import copier as copier_module
        from telkap.services.copier import Copier

        monkeypatch.setattr(copier_module, "classify_media", lambda m: "photo")

        client = FakeClient()
        copier = Copier(FakeManager(client))

        media = object()
        msg = FakeMessage(id=1, message="عکس خبری", media=media)
        assert await copier.process(7, task_id, [msg]) is True

        record = client.sent[0]
        assert record.kind == "file"
        assert record.payload is media          # نه [media]
        assert record.text == "عکس خبری"
    finally:
        await db_module.close_db()
