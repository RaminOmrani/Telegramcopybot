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
    """راهنما باید ناقص بماند، نه خالی.

    ترجمه‌ی یک بخش را موقتاً برمی‌داریم به‌جای اینکه به بخشی که هنوز ترجمه
    نشده تکیه کنیم؛ وگرنه این تست با کامل شدن ترجمه‌ها بی‌معنا می‌شد.
    """
    from telkap import guide_texts
    from telkap.handlers import guide

    original = guide_texts.BODIES
    guide_texts.BODIES = {k: v for k, v in original.items() if k != "filters"}
    try:
        assert "فیلترها" in guide._body("filters", "en")
    finally:
        guide_texts.BODIES = original


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


def test_the_whole_guide_is_translated_into_every_language():
    """هر بخشی که به راهنما اضافه شود باید ترجمه‌اش هم بیاید."""
    from telkap import guide_texts, i18n
    from telkap.handlers.guide import SECTIONS

    assert [key for key in SECTIONS if key not in guide_texts.BODIES] == []
    assert [key for key in SECTIONS if key not in guide_texts.TITLES] == []
    for lang in i18n.LANGS:
        if lang != i18n.DEFAULT:
            assert guide_texts.coverage(lang) == len(guide_texts.BODIES), lang


def test_no_section_is_left_with_an_unfilled_placeholder():
    """جای خالیِ پرنشده یعنی کاربر «{{table}}» را روی صفحه می‌بیند."""
    import re

    from telkap import i18n
    from telkap.handlers import guide
    from telkap.handlers.guide import SECTIONS

    for key in SECTIONS:
        for lang in i18n.LANGS:
            left = re.findall(r"\{\{\w+\}\}", guide._body(key, lang))
            assert left == [], f"{key}/{lang}: {left}"


def test_no_translation_leaks_a_foreign_alphabet():
    """جمله‌ای از یک زبان که اشتباهی در متن زبان دیگر جا مانده باشد.

    موقع نوشتن نُه ترجمه کنار هم، پیش می‌آید که یک کلمه از ترجمه‌ی قبلی
    سر جایش بماند. اگر خط‌ها فرق داشته باشند، همین‌جا گیر می‌افتد.
    """
    import re

    from telkap import guide_texts

    scripts = [
        ("سیریلیک", re.compile(r"[Ѐ-ӿ]"), {"ru"}),
        ("عربی", re.compile(r"[؀-ۿ]"), {"ar", "fa"}),
        ("دیوناگری", re.compile(r"[ऀ-ॿ]"), {"hi"}),
        ("شرق آسیا", re.compile(r"[　-鿿]"), set()),
    ]
    leaks = [
        (key, lang, name, "".join(pattern.findall(text)[:12]))
        for key, entry in guide_texts.BODIES.items()
        for lang, text in entry.items()
        for name, pattern, allowed in scripts
        if lang not in allowed and pattern.search(text)
    ]
    assert leaks == []


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


# ------------------------------------------- جدول طرح‌ها به هر زبان
def test_the_plan_table_is_built_in_the_readers_language():
    from telkap.handlers import guide

    english = guide._plans_table("en")
    assert "7-day plan" in english
    assert "toman" in english
    assert "129,000" in english          # ارقام لاتین، نه فارسی
    assert "۱۲۹" not in english

    persian = guide._plans_table("fa")
    assert "اشتراک ۷ روزه" in persian
    assert "۱۲۹،۰۰۰ تومان" in persian


def test_the_plan_table_is_grammatical_at_every_count():
    """«۱ days» و «None watermarks» همان‌قدر بد است که ترجمه‌ی غلط."""
    from telkap.handlers import guide

    assert "1 day" in guide._plans_table("en")     # نه «1 days»
    assert "1 days" not in guide._plans_table("en")
    assert "7 days" in guide._plans_table("en")

    russian = guide._plans_table("ru")
    assert "1 день" in russian
    assert "7 дней" in russian
    assert "1 дней" not in russian

    # واحد پیش از عدد می‌آید، پس مفرد و جمع اصلاً درگیر نمی‌شوند
    assert "jobs: 1" in guide._plans_table("en")
    assert "watermarks: None" in guide._plans_table("en")


def test_the_arabic_plural_follows_its_own_rule():
    """عربی برای ۳ تا ۱۰ جمع قِلّه می‌گیرد و بعد از آن مفردِ منصوب."""
    from telkap import i18n

    assert i18n.plural(1, "unit.days", "ar") == "يوم"
    assert i18n.plural(7, "unit.days", "ar") == "أيام"
    assert i18n.plural(30, "unit.days", "ar") == "يوماً"


def test_a_language_without_plurals_gets_one_form():
    from telkap import i18n

    for code in ("fa", "tr", "uz", "id"):
        assert i18n.plural(1, "unit.days", code) == i18n.plural(30, "unit.days", code)


def test_thousands_are_separated_the_local_way():
    from telkap import i18n

    assert i18n.num(129_000, "ru") == "129 000"     # روسی با فاصله
    assert i18n.num(129_000, "pt") == "129.000"     # پرتغالی با نقطه
    assert i18n.num(129_000, "en") == "129,000"


def test_a_plan_renamed_by_the_admin_keeps_its_name():
    """ترجمه‌ی ما نباید انتخاب ادمین را بی‌صدا کنار بزند."""
    from dataclasses import replace

    from telkap import plans
    from telkap.handlers import guide

    renamed = replace(plans.WEEK, title="اشتراک ویژه‌ی نوروز")
    assert guide._plan_title(renamed, "en") == "اشتراک ویژه‌ی نوروز"
    # دست‌نخورده که باشد، ترجمه می‌شود
    assert guide._plan_title(plans.WEEK, "en") == "7-day plan"


def test_an_unlimited_quota_reads_as_words_not_a_negative_number():
    from telkap.handlers import guide

    assert guide._quota(-1, "en") == "Unlimited"
    assert guide._quota(0, "en") == "None"
    assert guide._quota(1500, "en") == "1,500"
    assert guide._quota(-1, "fa") == "نامحدود"


# --------------------------------------- جای خالی در متن‌های ترجمه‌شده
def test_a_placeholder_is_filled_in_the_translated_guide():
    from telkap import guide_texts

    guide_texts.BODIES["_test"] = {"en": "price: {{p}} · left: {{gone}}"}
    try:
        assert guide_texts.body("_test", "en", p="9") == "price: 9 · left: {{gone}}"
    finally:
        guide_texts.BODIES.pop("_test")


def test_the_proxy_filename_braces_survive_substitution():
    """متن «کانفیگ پروکسی» خودش {tag} و {name} دارد و باید سالم بماند."""
    from telkap import guide_texts

    guide_texts.BODIES["_test"] = {"en": "{tag}_{name} — {{who}}"}
    try:
        assert guide_texts.body("_test", "en", who="Ali") == "{tag}_{name} — Ali"
    finally:
        guide_texts.BODIES.pop("_test")


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
