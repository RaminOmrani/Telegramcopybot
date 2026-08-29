"""تست دور شانزدهم: لایه‌ی هوش مصنوعی."""
from __future__ import annotations

import pytest

from tests.test_copier import _setup


# --------------------------------------------------- خاموش بودن پیش‌فرض
def _with(monkeypatch, **changes):
    """تنظیمات با چند مقدار عوض‌شده. Settings فریز است، پس نسخه‌ی تازه."""
    from dataclasses import replace

    from telkap import config

    monkeypatch.setattr(config, "settings", replace(config.get_settings(), **changes))


def test_nothing_happens_without_a_key(monkeypatch):
    """تا کلید نباشد، هیچ قابلیتی نباید ظاهر شود."""
    from telkap.services import ai

    _with(monkeypatch, ai_api_key="")
    assert ai.configured() is False


def test_configured_needs_both_key_and_address(monkeypatch):
    from telkap.services import ai

    _with(monkeypatch, ai_api_key="k", ai_base_url="")
    assert ai.configured() is False

    _with(monkeypatch, ai_api_key="k", ai_base_url="https://api.avalai.ir/v1")
    assert ai.configured() is True


@pytest.mark.asyncio
async def test_a_call_without_a_key_returns_nothing_instead_of_raising(monkeypatch):
    """خرابیِ مدل نباید به‌صورت استثنا از لایه بیرون بزند."""
    from telkap.services import ai

    _with(monkeypatch, ai_api_key="")
    assert await ai.chat("سلام") is None
    assert await ai.embed_one("سلام") is None
    parsed, usage = await ai.ask_json("سلام")
    assert parsed is None
    assert usage.total == 0


# ------------------------------------------------------ انتخاب مدل
def test_each_job_gets_its_own_model(monkeypatch):
    """مدل کوچک برای دسته‌بندی و مدل اصلی برای بازنویسی — همین‌جا جدا می‌شوند."""
    from telkap.services import ai

    _with(
        monkeypatch,
        ai_model_small="کوچک",
        ai_model_main="اصلی",
        ai_model_embed="بردار",
    )
    assert ai.model_for(ai.ROLE_SMALL) == "کوچک"
    assert ai.model_for(ai.ROLE_MAIN) == "اصلی"
    assert ai.model_for(ai.ROLE_EMBED) == "بردار"
    # نقش ناشناخته نباید صفحه را خالی کند؛ ارزان‌ترین گزینه می‌ماند
    assert ai.model_for("چیز دیگری") == "کوچک"


# ------------------------------------------------ بیرون کشیدن JSON
def test_json_is_pulled_out_of_a_chatty_answer():
    """مدل معمولاً دور JSON توضیح می‌نویسد یا در ```json می‌پیچدش."""
    import json
    import re

    from telkap.services.ai import _JSON_BLOCK

    messy = 'البته! این هم جواب:\n```json\n{"ad": true, "score": 8}\n```\nموفق باشید.'
    match = _JSON_BLOCK.search(messy)
    assert match is not None
    assert json.loads(match.group(0)) == {"ad": True, "score": 8}

    assert _JSON_BLOCK.search("هیچ JSON ای اینجا نیست") is None
    assert isinstance(_JSON_BLOCK, re.Pattern)


# ---------------------------------------------------------- شباهت
def test_two_identical_vectors_are_fully_similar():
    from telkap.services.ai import similarity

    assert similarity([1.0, 0.0, 0.0], [1.0, 0.0, 0.0]) == pytest.approx(1.0)


def test_two_unrelated_vectors_are_not_similar():
    from telkap.services.ai import similarity

    assert similarity([1.0, 0.0], [0.0, 1.0]) == pytest.approx(0.0)


def test_similarity_ignores_how_long_the_vectors_are():
    """بردار بلندتر با همان جهت، همان معنا را دارد."""
    from telkap.services.ai import similarity

    assert similarity([1.0, 1.0], [5.0, 5.0]) == pytest.approx(1.0)


def test_a_broken_vector_scores_zero_instead_of_crashing():
    from telkap.services.ai import similarity

    assert similarity([], [1.0]) == 0.0
    assert similarity([1.0, 2.0], [1.0]) == 0.0        # طول‌ها نمی‌خوانند
    assert similarity([0.0, 0.0], [1.0, 1.0]) == 0.0   # بردار صفر


def test_similarity_never_leaves_its_range():
    """عدد بیرون از ۰ تا ۱ یعنی آستانه‌های تشخیص تکراری بی‌معنا می‌شوند."""
    from telkap.services.ai import similarity

    for pair in ([[-1.0, 0.0], [1.0, 0.0]], [[3.0, -2.0], [-3.0, 2.0]]):
        assert 0.0 <= similarity(*pair) <= 1.0


# ------------------------------------------------------ اعتبار هوش مصنوعی
def test_ai_is_a_sellable_credit_like_the_others():
    from telkap.plans import CREDIT_AI, CREDIT_KINDS, credit_unit

    assert CREDIT_AI in CREDIT_KINDS
    title, _explain, price = CREDIT_KINDS[CREDIT_AI]
    assert "هوش مصنوعی" in title
    assert price > 0
    assert credit_unit(CREDIT_AI) == price


@pytest.mark.asyncio
async def test_ai_credit_is_bought_and_spent_like_any_other(tmp_path, monkeypatch):
    db_module, _ = await _setup(tmp_path, monkeypatch, settings={})
    try:
        from telkap.plans import CREDIT_AI
        from telkap.services import credits

        # کاربر ۷ را خودِ _setup ساخته است
        assert await credits.balance(7, CREDIT_AI) == 0
        await credits.add(7, CREDIT_AI, 10, note="خرید آزمایشی")
        assert await credits.balance(7, CREDIT_AI) == 10

        assert await credits.consume(7, CREDIT_AI, 3) is True
        assert await credits.balance(7, CREDIT_AI) == 7

        # بیشتر از مانده برداشته نمی‌شود و مانده هم دست نمی‌خورد
        assert await credits.consume(7, CREDIT_AI, 100) is False
        assert await credits.balance(7, CREDIT_AI) == 7
    finally:
        await db_module.close_db()


@pytest.mark.asyncio
async def test_all_three_credit_kinds_show_up_together(tmp_path, monkeypatch):
    db_module, _ = await _setup(tmp_path, monkeypatch, settings={})
    try:
        from telkap.plans import CREDIT_AI, CREDIT_HISTORY, CREDIT_WATERMARK
        from telkap.services import credits

        assert set(await credits.balances(7)) == {
            CREDIT_WATERMARK,
            CREDIT_HISTORY,
            CREDIT_AI,
        }
    finally:
        await db_module.close_db()
