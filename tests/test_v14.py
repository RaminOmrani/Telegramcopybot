"""تست دور چهاردهم: چندزبانه شدن رابط کاربری."""
from __future__ import annotations

import pytest

from tests.test_copier import _setup


# ------------------------------------------------------- انتخاب زبان
def test_only_supported_languages_are_accepted():
    from telkap import i18n

    assert i18n.normalize("en") == "en"
    assert i18n.normalize("EN") == "en"
    assert i18n.normalize("en-US") == "en"      # تلگرام گاهی اینطور می‌دهد
    assert i18n.normalize("de") == i18n.DEFAULT  # زبان پشتیبانی‌نشده
    assert i18n.normalize(None) == i18n.DEFAULT
    assert i18n.normalize("") == i18n.DEFAULT


def test_telegram_language_is_only_a_suggestion():
    from telkap import i18n

    assert i18n.pick("ar") == "ar"
    assert i18n.pick("fr") == i18n.DEFAULT      # ناشناخته → فارسی


# --------------------------------------------------------- ترجمه‌ها
def test_a_missing_translation_falls_back_to_persian():
    from telkap import i18n

    # کلیدی که فقط فارسی دارد
    i18n.CATALOG["_test.only_fa"] = {"fa": "فقط فارسی"}
    try:
        assert i18n.t("_test.only_fa", "en") == "فقط فارسی"
        assert i18n.t("_test.only_fa", "fa") == "فقط فارسی"
        assert i18n.has("_test.only_fa", "en") is False
    finally:
        i18n.CATALOG.pop("_test.only_fa")


def test_an_unknown_key_is_visible_rather_than_silent():
    """کلید جاافتاده باید در چشم بیاید، نه اینکه صفحه خالی شود."""
    from telkap import i18n

    assert i18n.t("nope.not.here") == "nope.not.here"


def test_the_core_journey_is_translated_everywhere():
    """هر کلیدی که کاربر خارجی می‌بیند باید هر سه زبان را داشته باشد."""
    from telkap import i18n

    missing = [
        (key, lang)
        for key, entry in i18n.CATALOG.items()
        for lang in i18n.LANGS
        if lang != i18n.DEFAULT and not entry.get(lang) and key != "lang.partial"
    ]
    assert missing == []


def test_every_entry_has_the_persian_fallback():
    from telkap import i18n

    assert [key for key, entry in i18n.CATALOG.items() if not entry.get("fa")] == [
        "lang.partial"
    ]


def test_placeholders_are_filled_in():
    from telkap import i18n

    i18n.CATALOG["_test.greet"] = {"fa": "سلام {name}", "en": "Hello {name}"}
    try:
        assert i18n.t("_test.greet", "en", name="Ali") == "Hello Ali"
        assert i18n.t("_test.greet", "fa", name="علی") == "سلام علی"
    finally:
        i18n.CATALOG.pop("_test.greet")


# ------------------------------------------------------------- اعداد
def test_numbers_follow_the_language():
    from telkap import i18n

    assert i18n.num(1234, "en") == "1,234"
    assert i18n.num(1234, "fa") == "۱،۲۳۴"
    assert i18n.num(1234, "ar") == "١٬٢٣٤"


# ------------------------------------------------------ زبان جاری
def test_the_current_language_can_be_set_and_read():
    from telkap import i18n

    i18n.set_current("en")
    try:
        assert i18n.current() == "en"
        assert i18n.t("menu.help") == "📚 Guide"      # بدون پاس دادن زبان
    finally:
        i18n.set_current(i18n.DEFAULT)


# --------------------------------------------------------- منوی اصلی
def test_the_main_menu_speaks_the_chosen_language():
    from telkap.keyboards import main_menu

    fa = [b.text for row in main_menu("fa").keyboard for b in row]
    en = [b.text for row in main_menu("en").keyboard for b in row]
    ar = [b.text for row in main_menu("ar").keyboard for b in row]

    assert "📚 راهنما" in fa
    assert "📚 Guide" in en
    assert "📚 الدليل" in ar
    assert len(fa) == len(en) == len(ar) == 8


def test_button_filters_accept_every_language():
    """کاربری که زبانش را عوض کرده ممکن است هنوز دکمه‌ی قدیمی را ببیند."""
    from telkap.keyboards import menu_texts

    texts = menu_texts("menu.account")
    assert "👤 حساب کاربری" in texts
    assert "👤 Account" in texts
    assert "👤 الحساب" in texts


def test_the_welcome_screen_is_translated():
    from telkap.handlers.start import welcome

    assert "Telegram Content Copier" in welcome("en")
    assert "بوت نسخ محتوى تلجرام" in welcome("ar")
    assert "ربات کپی محتوای تلگرام" in welcome("fa")


def test_the_language_picker_marks_the_current_one():
    from telkap.handlers.language import picker

    rows = [b.text for row in picker("en").as_markup().inline_keyboard for b in row]
    assert any(text.startswith("🔘") and "English" in text for text in rows)
    assert sum(1 for text in rows if text.startswith("🔘")) == 1


def test_the_first_question_is_asked_in_every_language():
    """کسی که فارسی نمی‌داند هم باید بفهمد از او چه می‌پرسند."""
    from telkap.handlers.language import ask_text

    everyone = ask_text()
    assert "زبان خود را انتخاب کنید" in everyone
    assert "Choose your language" in everyone
    assert "اختر لغتك" in everyone


# ------------------------------------------------- ذخیره‌ی زبان کاربر
@pytest.mark.asyncio
async def test_the_chosen_language_is_remembered(tmp_path, monkeypatch):
    db_module, _ = await _setup(tmp_path, monkeypatch, settings={})
    try:
        from telkap import i18n

        i18n.reset_cache()
        assert await i18n.language_of(7) == "fa"     # پیش‌فرض

        await i18n.set_language(7, "en")
        assert await i18n.language_of(7) == "en"

        # از دیتابیس هم همان درمی‌آید، پس ری‌استارت چیزی را از بین نمی‌برد
        i18n.reset_cache()
        assert await i18n.language_of(7) == "en"
    finally:
        i18n.reset_cache()
        i18n.set_current(i18n.DEFAULT)
        await db_module.close_db()


@pytest.mark.asyncio
async def test_an_unknown_user_falls_back_to_their_telegram_language(
    tmp_path, monkeypatch
):
    db_module, _ = await _setup(tmp_path, monkeypatch, settings={})
    try:
        from telkap import i18n

        i18n.reset_cache()
        assert await i18n.language_of(999, fallback="ar") == "ar"
        assert await i18n.language_of(998, fallback="zz") == "fa"
    finally:
        i18n.reset_cache()
        await db_module.close_db()


@pytest.mark.asyncio
async def test_an_invalid_language_is_never_stored(tmp_path, monkeypatch):
    db_module, _ = await _setup(tmp_path, monkeypatch, settings={})
    try:
        from telkap import i18n
        from telkap.models import User

        i18n.reset_cache()
        await i18n.set_language(7, "klingon")
        async with db_module.get_session() as db:
            user = await db.get(User, 7)
        assert user.language == i18n.DEFAULT
    finally:
        i18n.reset_cache()
        i18n.set_current(i18n.DEFAULT)
        await db_module.close_db()
