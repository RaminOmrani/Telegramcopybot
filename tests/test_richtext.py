"""تست حفظ قالب‌بندیِ متن‌هایی که خودِ کاربر می‌نویسد.

کاربر گزارش داد: «پست‌ها با ایموجی پریمیوم فوروارد شدند، اما امضایی که
با ایموجی پریمیوم زدم ساده شد.» هر دو نیمه‌ی این جمله درست بود و علتشان
یکی نبود — متنِ کپی‌شده entity هایش را داشت، ولی امضا از ابتدا فقط
<b>متن خام</b> ذخیره شده بود.

اینجا همان مسیرِ ذخیره تا انتشار سنجیده می‌شود: برداشتن entity از پیام
کاربر، ماندنش در تنظیمات، و نشستنش روی جای درست در پست نهایی.
"""
from __future__ import annotations

from telkap.services import richtext
from telkap.services.copier import with_own_entities
from telkap.services.defaults import DEFAULT_SETTINGS, merged_settings
from telkap.services.transform import apply_transforms, utf16_len


class BotEntity:
    """شکل entity در سمت Bot API (چیزی که aiogram تحویل می‌دهد).

    عمداً کلاس ساده است نه کلاس aiogram: این ماژول قرار است با هر چیزی
    که attribute های درست را دارد کار کند.
    """

    def __init__(self, type_: str, offset: int, length: int, **extra):
        self.type = type_
        self.offset = offset
        self.length = length
        for key, value in extra.items():
            setattr(self, key, value)


def premium(offset: int, length: int = 2, emoji_id: str = "5368324170671202286"):
    return BotEntity("custom_emoji", offset, length, custom_emoji_id=emoji_id)


# ── برداشتن از پیام کاربر ────────────────────────────────────────────


def test_premium_emoji_survives_capture():
    spans = richtext.capture([premium(0)])

    assert spans == [
        {"type": "custom_emoji", "offset": 0, "length": 2, "id": "5368324170671202286"},
    ]


def test_unknown_kinds_are_dropped_not_stored():
    """entity ای که نمی‌دانیم چطور بسازیمش نباید ذخیره شود.

    یک entity خرابِ نیمه‌ساخته کل قالب‌بندی پیام را به‌هم می‌ریزد؛
    نداشتنش بهتر است.
    """
    spans = richtext.capture([
        BotEntity("mention", 0, 5),
        BotEntity("pre", 6, 4, language="py"),
        premium(11),
    ])

    assert [span["type"] for span in spans] == ["custom_emoji"]


def test_custom_emoji_without_an_id_is_useless():
    assert richtext.capture([BotEntity("custom_emoji", 0, 2)]) == []


def test_text_link_without_a_url_is_useless():
    assert richtext.capture([BotEntity("text_link", 0, 4)]) == []


def test_capture_is_capped():
    """سقف، جلوی پیامی را می‌گیرد که با هزار entity ساخته شده."""
    spans = richtext.capture([premium(i * 2) for i in range(richtext.MAX_SPANS + 30)])

    assert len(spans) == richtext.MAX_SPANS


def test_capture_shifts_offsets_by_what_strip_removed():
    """متن پیش از ذخیره strip می‌شود، پس آفست‌ها باید عقب بیایند."""
    raw = "  ✅ کانال من"
    shift = utf16_len(raw) - utf16_len(raw.lstrip())

    spans = richtext.capture([premium(2, length=1)], shift=shift)

    assert spans[0]["offset"] == 0


def test_an_entity_left_inside_the_stripped_part_is_dropped():
    """قالب‌بندی روی فاصله‌ی ابتدایی، بعد از strip جایی ندارد."""
    assert richtext.capture([BotEntity("bold", 0, 2)], shift=2) == []


# ── ساختن دوباره ─────────────────────────────────────────────────────


def test_restore_builds_a_real_telethon_entity():
    from telethon.tl.types import MessageEntityCustomEmoji

    built = richtext.restore(richtext.capture([premium(0)]), base_offset=10)

    assert len(built) == 1
    assert isinstance(built[0], MessageEntityCustomEmoji)
    assert built[0].offset == 10
    assert built[0].length == 2
    assert built[0].document_id == 5368324170671202286


def test_restore_handles_every_supported_kind():
    entities = [
        BotEntity("bold", 0, 2),
        BotEntity("italic", 2, 2),
        BotEntity("underline", 4, 2),
        BotEntity("strikethrough", 6, 2),
        BotEntity("spoiler", 8, 2),
        BotEntity("code", 10, 2),
        BotEntity("text_link", 12, 2, url="https://example.com"),
        premium(14),
    ]

    built = richtext.restore(richtext.capture(entities), base_offset=0)

    assert len(built) == len(entities)
    assert [type(item).__name__ for item in built] == [
        richtext._TYPES[entity.type] for entity in entities
    ]


def test_restore_ignores_junk_instead_of_raising():
    """تنظیمات JSON خام‌اند و ممکن است دستی دستکاری شده باشند."""
    built = richtext.restore(
        ["نه دیکشنری", {"type": "bold"}, {"type": "custom_emoji", "id": "نه عدد",
                                          "offset": 0, "length": 2}],
        base_offset=0,
    )

    assert built == []


# ── پیدا کردن جای متن در پست نهایی ───────────────────────────────────


def test_place_finds_the_signature_at_the_end():
    signature = "🔥 کانال من"
    result = f"متن پست\n\n{signature}"
    spans = richtext.capture([premium(0)])

    built = richtext.place(result, signature, spans, from_end=True)

    assert len(built) == 1
    assert built[0].offset == utf16_len("متن پست\n\n")


def test_place_counts_in_utf16_not_python_characters():
    """ایموجی در شمارش تلگرام دو واحد است و در پایتون یک کاراکتر.

    اگر با len() بشماریم، هر ایموجیِ پیش از امضا آن را یک واحد جلو
    می‌اندازد و ایموجی پریمیوم روی حرف بغلی می‌نشیند.
    """
    body = "خبر 🎉🎉 تازه"
    signature = "🔥 کانال"
    result = f"{body}\n\n{signature}"

    built = richtext.place(result, signature, richtext.capture([premium(0)]),
                           from_end=True)

    assert built[0].offset == utf16_len(f"{body}\n\n")
    assert built[0].offset == len(f"{body}\n\n") + 2   # دو ایموجی، هرکدام یکی بیشتر


def test_place_prefers_the_last_copy_for_a_signature():
    """اگر همان عبارت در متن پست هم آمده باشد، امضا آنِ آخری است."""
    signature = "کانال من"
    result = f"به {signature} خوش آمدید\n\n{signature}"

    built = richtext.place(result, signature, richtext.capture([premium(0)]),
                           from_end=True)

    assert built[0].offset == utf16_len(f"به {signature} خوش آمدید\n\n")


def test_place_prefers_the_first_copy_for_a_header():
    header = "کانال من"
    result = f"{header}\n\nمتن پست\n\n{header}"

    built = richtext.place(result, header, richtext.capture([premium(0)]),
                           from_end=False)

    assert built[0].offset == 0


def test_place_gives_up_when_the_text_was_changed():
    """«حذف ایموجی» ممکن است امضا را عوض کند.

    آن‌وقت هیچ entity ای بهتر از entity روی جای اشتباه است — قالب‌بندیِ
    جابه‌جا شده پیام را خراب نشان می‌دهد.
    """
    assert richtext.place("متن پست\n\nکانال", "🔥 کانال من",
                          richtext.capture([premium(0)]), from_end=True) == []


def test_place_needs_something_to_place():
    assert richtext.place("متن", "", richtext.capture([premium(0)]), from_end=True) == []
    assert richtext.place("متن", "امضا", [], from_end=True) == []


# ── ماندگاری در تنظیمات ──────────────────────────────────────────────


def test_the_entity_keys_are_real_settings():
    """کلیدی که در DEFAULT_SETTINGS نباشد، merged_settings دورش می‌ریزد.

    یعنی امضا ذخیره می‌شد ولی دفعه‌ی بعد که کار خوانده می‌شد،
    قالب‌بندی‌اش ناپدید می‌شد — دقیقاً همان باگی که می‌خواهیم نباشد.
    """
    for key in richtext.RICH_KEYS:
        assert richtext.entities_key(key) in DEFAULT_SETTINGS

    spans = richtext.capture([premium(0)])
    cfg = merged_settings({"signature": "🔥 من", "signature_entities": spans})

    assert cfg["signature_entities"] == spans


def test_each_task_gets_its_own_list():
    cfg_one = merged_settings(None)
    cfg_two = merged_settings(None)
    cfg_one["signature_entities"].append({"type": "bold", "offset": 0, "length": 1})

    assert cfg_two["signature_entities"] == []


# ── مسیر کامل: از تنظیمات تا پست ─────────────────────────────────────


def test_the_signature_reaches_the_post_with_its_premium_emoji():
    """همان چیزی که کاربر گزارش داد، سرتاسر مسیر."""
    from telethon.tl.types import MessageEntityCustomEmoji

    cfg = merged_settings({
        "signature": "🔥 کانال من",
        "signature_entities": richtext.capture([premium(0)]),
        "remove_source_signature": False,
    })
    result = apply_transforms("خبر تازه", cfg)

    entities = with_own_entities(result, cfg, [])

    assert len(entities) == 1
    assert isinstance(entities[0], MessageEntityCustomEmoji)
    assert result == "خبر تازه\n\n🔥 کانال من"
    assert entities[0].offset == utf16_len("خبر تازه\n\n")
    assert entities[0].length == 2   # ایموجی در شمارش تلگرام دو واحد است


def test_header_footer_and_signature_all_land_sorted():
    """تلگرام entity های مرتب‌نشده را نمی‌پذیرد."""
    cfg = merged_settings({
        "header": "سرتیتر",
        "header_entities": richtext.capture([BotEntity("bold", 0, 6)]),
        "footer": "پانوشت",
        "footer_entities": richtext.capture([BotEntity("italic", 0, 6)]),
        "signature": "امضا",
        "signature_entities": richtext.capture([premium(0, length=4)]),
        "remove_source_signature": False,
    })
    result = apply_transforms("متن", cfg)

    entities = with_own_entities(result, cfg, [])
    offsets = [entity.offset for entity in entities]

    assert len(entities) == 3
    assert offsets == sorted(offsets)


def test_own_entities_merge_with_the_copied_ones_in_order():
    from telethon.tl.types import MessageEntityBold

    cfg = merged_settings({
        "signature": "امضا",
        "signature_entities": richtext.capture([premium(0, length=4)]),
        "remove_source_signature": False,
    })
    result = apply_transforms("متن اصلی", cfg)
    copied = MessageEntityBold(offset=0, length=3)

    entities = with_own_entities(result, cfg, [copied])
    offsets = [entity.offset for entity in entities]

    assert entities[0] is copied
    assert offsets == sorted(offsets)


def test_nothing_stored_means_nothing_changed():
    """کاربری که امضای ساده دارد نباید هیچ تفاوتی ببیند."""
    cfg = merged_settings({"signature": "کانال من"})
    original = ["هرچه که باشد"]

    assert with_own_entities("متن\n\nکانال من", cfg, original) is original
