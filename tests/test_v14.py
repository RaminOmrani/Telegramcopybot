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


def test_every_language_has_a_name_and_a_working_menu():
    """زبانی که در فهرست باشد ولی منویش خالی درآید، بدتر از نبودنش است."""
    from telkap import i18n
    from telkap.keyboards import main_menu

    for code in i18n.LANGS:
        assert code in i18n.LANG_NAMES, code
        buttons = [b.text for row in main_menu(code).keyboard for b in row]
        assert len(buttons) == 8, code
        assert all(text.strip() for text in buttons), code


def test_each_language_menu_is_actually_distinct():
    """اگر ترجمه‌ای جا بیفتد، منو بی‌صدا فارسی می‌ماند."""
    from telkap import i18n
    from telkap.keyboards import main_menu

    def first(code: str) -> str:
        return main_menu(code).keyboard[0][0].text

    seen = {first(code) for code in i18n.LANGS}
    assert len(seen) == len(i18n.LANGS)


def test_the_welcome_screen_works_in_every_language():
    from telkap import i18n
    from telkap.handlers.start import welcome

    persian = welcome("fa")
    for code in i18n.LANGS:
        text = welcome(code)
        assert len(text) > 200, code
        if code != "fa":
            assert text != persian, code


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


def test_the_first_question_is_understandable_without_persian():
    """کسی که فارسی نمی‌داند هم باید بفهمد از او چه می‌پرسند."""
    from telkap.handlers.language import ask_text

    default = ask_text()
    assert "زبان خود را انتخاب کنید" in default
    assert "Choose your language" in default      # پلِ انگلیسی

    # وقتی زبانی حدس زده شده، همان به‌علاوه‌ی انگلیسی
    russian = ask_text("ru")
    assert "Выберите язык" in russian
    assert "Choose your language" in russian
    assert "زبان خود را انتخاب کنید" not in russian


def test_the_language_question_does_not_become_a_wall_of_text():
    """با نُه زبان، نوشتن پرسش به همه‌شان دیوار متن می‌ساخت."""
    from telkap import i18n
    from telkap.handlers.language import ask_text

    assert len(ask_text().splitlines()) <= 2
    for code in i18n.LANGS:
        assert len(ask_text(code).splitlines()) <= 2, code
    # انگلیسی خودش یک خط می‌ماند، نه دو خط تکراری
    assert len(ask_text("en").splitlines()) == 1


# ----------------------------------------------------- راهنمای چندزبانه
def test_an_untranslated_guide_section_stays_persian():
    """راهنما باید ناقص بماند، نه خالی."""
    from telkap.handlers import guide

    # «filters» هنوز ترجمه نشده
    assert "فیلترها" in guide._body("filters", "en")


def test_a_translated_guide_section_uses_the_chosen_language():
    from telkap.handlers import guide

    assert "Quick start" in guide._body("start", "en")
    assert "Быстрый старт" in guide._body("start", "ru")
    assert "Tez boshlash" in guide._body("start", "uz")


def test_the_guide_home_and_menu_follow_the_language():
    from telkap.handlers import guide

    assert "Bot guide" in guide._home("en")
    titles = [b.text for r in guide._menu("en").as_markup().inline_keyboard for b in r]
    assert "🚀 Quick start" in titles


def test_the_guide_language_comes_from_the_context():
    """میدل‌ور زبان را می‌گذارد؛ هندلرها لازم نیست پاسش بدهند."""
    from telkap import i18n
    from telkap.handlers import guide

    i18n.set_current("ru")
    try:
        assert "Быстрый старт" in guide._body("start")
    finally:
        i18n.set_current(i18n.DEFAULT)


def test_translated_guide_sections_are_real_translations():
    """بخشی که ترجمه‌اش با فارسی یکی باشد، یعنی ترجمه نشده."""
    from telkap import guide_texts, i18n

    for key, entry in guide_texts.BODIES.items():
        for lang in i18n.LANGS:
            if lang == i18n.DEFAULT:
                continue
            text = entry.get(lang)
            assert text, f"{key}/{lang} خالی است"
            assert len(text) > 100, f"{key}/{lang} خیلی کوتاه است"


def test_every_translated_section_has_a_button_title():
    """بخشی که متنش ترجمه شده ولی دکمه‌اش فارسی مانده، ناجور دیده می‌شود."""
    from telkap import guide_texts, i18n

    for key in guide_texts.BODIES:
        if key == "home":
            continue
        for lang in i18n.LANGS:
            if lang == i18n.DEFAULT:
                continue
            assert guide_texts.title(key, lang), f"عنوان {key}/{lang} نیست"


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
