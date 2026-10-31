"""تست منوی هوش مصنوعی.

اینجا سه چیز سنجیده می‌شود: دکمه فقط وقتی ظاهر شود که سرویس تنظیم است،
گزینه‌های هر قابلیت فقط وقتی که خودش روشن است، و چرخه‌ها روی هر مقدار
ناشناخته‌ای هم گیر نکنند.
"""
from __future__ import annotations

from telkap import keyboards
from telkap.handlers import settings as settings_handler
from telkap.models import Task
from telkap.services import ai, aiskills
from telkap.services.defaults import merged_settings


def _labels(markup) -> list[str]:
    return [btn.text for row in markup.inline_keyboard for btn in row]


def _callbacks(markup) -> list[str]:
    return [btn.callback_data for row in markup.inline_keyboard for btn in row]


def _task() -> Task:
    return Task(id=5, user_id=7, source_ref="@src", title="کار", enabled=True)


# ── دکمه‌ی ورود به پنل ──────────────────────────────────────────────


def test_the_button_is_hidden_without_a_key(monkeypatch):
    """دکمه‌ای که به بن‌بست می‌رسد بدتر از نبودنش است."""
    monkeypatch.setattr(ai, "configured", lambda: False)
    markup = keyboards.task_menu(_task(), pro=True)
    assert not any(cb == "set:ai:5" for cb in _callbacks(markup))


def test_the_button_appears_when_the_service_is_set_up(monkeypatch):
    monkeypatch.setattr(ai, "configured", lambda: True)
    markup = keyboards.task_menu(_task(), pro=True)
    assert "set:ai:5" in _callbacks(markup)


def test_the_button_stays_out_of_the_simple_menu(monkeypatch):
    """منوی ساده عمداً کوتاه است؛ این قابلیت پشت «پیشرفته» می‌ماند."""
    monkeypatch.setattr(ai, "configured", lambda: True)
    markup = keyboards.task_menu(_task(), pro=False)
    assert "set:ai:5" not in _callbacks(markup)


# ── خودِ پنل ────────────────────────────────────────────────────────


def test_options_are_hidden_until_their_feature_is_on():
    """لحنِ بازنویسیِ خاموش چیزی برای تنظیم ندارد و فقط منو را شلوغ می‌کند."""
    off = keyboards.ai_menu(5, merged_settings({}))
    assert "aistyle:5" not in _callbacks(off)
    assert "ailang:5" not in _callbacks(off)
    assert "aisent:5" not in _callbacks(off)

    on = keyboards.ai_menu(5, merged_settings({"ai_rewrite": True}))
    assert "aistyle:5" in _callbacks(on)
    assert "ailang:5" not in _callbacks(on)


def test_the_balance_is_shown_up_front():
    """کاربر نباید وقتی بفهمد اعتبار ندارد که پست‌ها بی‌تغییر رفته‌اند."""
    markup = keyboards.ai_menu(5, merged_settings({}), balance=42)
    assert any("۴۲" in label for label in _labels(markup))


def test_the_chosen_style_and_language_are_visible():
    cfg = merged_settings(
        {"ai_rewrite": True, "ai_style": "formal", "ai_translate": True, "ai_language": "ar"}
    )
    labels = " ".join(_labels(keyboards.ai_menu(5, cfg)))
    assert aiskills.STYLES["formal"] in labels
    assert aiskills.LANGUAGES["ar"] in labels


# ── چرخه‌ها ─────────────────────────────────────────────────────────


def test_cycles_advance_and_wrap():
    styles = list(aiskills.STYLES)
    assert settings_handler._cycle(styles, styles[0], "same") == styles[1]
    assert settings_handler._cycle(styles, styles[-1], "same") == styles[0]


def test_an_unknown_value_falls_back_instead_of_crashing():
    """مقدار قدیمی یا دستکاری‌شده نباید ValueError بدهد."""
    assert settings_handler._cycle(aiskills.STYLES, "چیز نامعلوم", "same") == "same"
    assert settings_handler._cycle(aiskills.LANGUAGES, None, "en") == "en"


# ── پیش‌فرض‌ها ──────────────────────────────────────────────────────


def test_every_ai_feature_starts_off():
    """هرکدام روی هر پست پول می‌برد، پس روشن بودنشان باید تصمیم کاربر باشد."""
    cfg = merged_settings({})
    assert cfg["ai_summarize"] is False
    assert cfg["ai_rewrite"] is False
    assert cfg["ai_translate"] is False


def test_the_default_style_and_language_are_real_options():
    cfg = merged_settings({})
    assert cfg["ai_style"] in aiskills.STYLES
    assert cfg["ai_language"] in aiskills.LANGUAGES


def test_each_ai_flag_knows_its_panel():
    """بدون این، فشردن کلید کاربر را به پنل اشتباه می‌برد."""
    for key in ("ai_summarize", "ai_rewrite", "ai_translate"):
        assert settings_handler.FLAG_PANEL[key] == "ai"


def test_the_panel_is_registered():
    title, builder = settings_handler.PANELS["ai"]
    assert "هوش مصنوعی" in title
    assert builder is keyboards.ai_menu
