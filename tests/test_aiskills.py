"""تست قابلیت‌های هوش مصنوعی.

مهم‌ترین چیزی که اینجا سنجیده می‌شود «کیفیت پاسخ مدل» نیست — آن را
نمی‌شود در تست سنجید. چیزی که سنجیده می‌شود این است که:

۱. وقتی مدل تنظیم نشده یا خراب است، هیچ‌چیز نمی‌شکند.
۲. الگوی دو مرحله‌ای واقعاً مدل را صدا نمی‌زند وقتی لازم نیست — که کل
   استدلال اقتصادی این ماژول روی همین سوار است.
"""
from __future__ import annotations

import pytest

from telkap.services import ai, aiskills


class Recorder:
    """جای مدل می‌نشیند و می‌شمارد چند بار صدا زده شده."""

    def __init__(self, reply=None, parsed=None):
        self.calls = 0
        self.reply = reply
        self.parsed = parsed

    async def chat(self, *args, **kwargs):
        self.calls += 1
        return self.reply

    async def ask_json(self, *args, **kwargs):
        self.calls += 1
        return self.parsed, ai.Usage(prompt=40, completion=10)


@pytest.fixture
def model(monkeypatch):
    """مدلِ ساختگیِ تنظیم‌شده. هر تست پاسخش را خودش می‌گذارد."""
    rec = Recorder()
    monkeypatch.setattr(ai, "configured", lambda: True)
    monkeypatch.setattr(ai, "chat", rec.chat)
    monkeypatch.setattr(ai, "ask_json", rec.ask_json)
    return rec


# ── بدون مدل، هیچ‌چیز نمی‌شکند ──────────────────────────────────────


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "call",
    [
        lambda: aiskills.rewrite("سلام"),
        lambda: aiskills.translate("سلام"),
        lambda: aiskills.summarize("متن " * 80),
        lambda: aiskills.image_is_ad("https://example.com/a.jpg"),
        lambda: aiskills.route_topic("متن", ["خبر", "فروش"]),
    ],
)
async def test_without_a_key_every_skill_returns_none(monkeypatch, call):
    monkeypatch.setattr(ai, "configured", lambda: False)
    assert await call() is None


@pytest.mark.asyncio
async def test_without_a_key_the_ad_check_falls_back_to_the_score(monkeypatch):
    """تشخیص تبلیغ باید همیشه جواب بدهد، حتی بدون مدل.

    بقیه‌ی مهارت‌ها می‌توانند None بدهند چون اختیاری‌اند، ولی این یکی سر
    راه انتشار است — None یعنی پست معلق می‌ماند.
    """
    monkeypatch.setattr(ai, "configured", lambda: False)

    verdict = await aiskills.ad_verdict("متن مرزی", score=4, threshold=4)
    assert verdict.is_ad is True
    assert verdict.asked_model is False

    verdict = await aiskills.ad_verdict("متن مرزی", score=3, threshold=4)
    assert verdict.is_ad is False


@pytest.mark.asyncio
async def test_a_broken_model_does_not_raise(model):
    model.reply = None                      # سرویس قطع
    assert await aiskills.rewrite("سلام") is None
    assert await aiskills.translate("سلام") is None


# ── الگوی دو مرحله‌ای: مدل فقط در موارد مرزی ────────────────────────


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "score,expected",
    [
        (0, False),   # هیچ نشانه‌ای نیست — اکثر پست‌ها اینجایند
        (1, False),
        (2, False),
        (6, True),    # چند نشانه‌ی قوی کنار هم
        (9, True),
    ],
)
async def test_a_clear_score_never_calls_the_model(model, score, expected):
    """این تست همان استدلال هزینه است.

    اگر روزی کسی بازه‌ی قطعیت را عوض کند و مدل روی هر پست صدا زده شود،
    هزینه چند برابر می‌شود بدون اینکه چیزی خراب به نظر برسد. این تست
    همان تغییر را می‌گیرد.
    """
    verdict = await aiskills.ad_verdict("هر متنی", score=score, threshold=4)
    assert verdict.is_ad is expected
    assert verdict.asked_model is False
    assert model.calls == 0
    assert verdict.usage.total == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("score", [3, 4, 5])
async def test_a_borderline_score_asks_the_model(model, score):
    model.parsed = {"ad": True, "why": "شماره تماس دارد"}

    verdict = await aiskills.ad_verdict("متن مرزی", score=score, threshold=4)

    assert model.calls == 1
    assert verdict.asked_model is True
    assert verdict.is_ad is True
    assert verdict.reason == "شماره تماس دارد"
    assert verdict.usage.total == 50


@pytest.mark.asyncio
async def test_a_malformed_answer_falls_back_to_the_score(model):
    """مدل گاهی JSON بی‌ربط می‌دهد؛ نباید تصمیم را خراب کند."""
    model.parsed = {"unexpected": "shape"}

    verdict = await aiskills.ad_verdict("متن", score=5, threshold=4)

    assert verdict.is_ad is True           # ۵ ≥ ۴، همان تصمیم امتیازی
    assert verdict.asked_model is True
    assert verdict.usage.total == 50       # هزینه‌اش را دادیم، پس گزارش شود


# ── مسیریابی: موضوعِ ازخودساخته پذیرفته نمی‌شود ─────────────────────


@pytest.mark.asyncio
async def test_routing_returns_one_of_the_given_topics(model):
    model.parsed = {"topic": "فروش"}
    result = await aiskills.route_topic("تخفیف ویژه", ["خبر", "فروش"])
    assert result.text == "فروش"


@pytest.mark.asyncio
async def test_routing_rejects_a_topic_it_was_not_given(model):
    """مدل گاهی موضوعِ تازه می‌سازد.

    مسیریابی به کانالی که وجود ندارد از مسیریابی نکردن بدتر است، پس هر
    چیزی که در فهرست نباشد دور ریخته می‌شود — ولی هزینه‌اش گزارش می‌شود.
    """
    model.parsed = {"topic": "ورزشی"}
    result = await aiskills.route_topic("متن", ["خبر", "فروش"])
    assert result.text == ""
    assert result.usage.total == 50


@pytest.mark.asyncio
async def test_routing_needs_at_least_two_topics(model):
    """با یک موضوع، انتخابی در کار نیست — نباید هزینه بدهیم."""
    assert await aiskills.route_topic("متن", ["خبر"]) is None
    assert model.calls == 0


# ── خلاصه‌سازی متن کوتاه هزینه ندارد ────────────────────────────────


@pytest.mark.asyncio
async def test_short_text_is_not_summarized(model):
    """خلاصه‌ی یک پست سه‌خطی خودش است."""
    assert await aiskills.summarize("پست کوتاه") is None
    assert model.calls == 0


@pytest.mark.asyncio
async def test_long_text_is_summarized(model):
    model.reply = ai.Reply(text="خلاصه", usage=ai.Usage(prompt=300, completion=20))
    result = await aiskills.summarize("جمله‌ی طولانی. " * 40)
    assert result.text == "خلاصه"
    assert result.used_model is True


# ── تکراری معنایی: فقط بازه‌ی مرزی ──────────────────────────────────


def test_only_the_uncertain_band_needs_the_model():
    same = aiskills.dedupe.simhash("قیمت دلار امروز افزایش یافت")

    # فاصله‌ی صفر: قطعاً یکی‌اند، پرسیدن لازم نیست
    assert aiskills.needs_semantic_check(same, same) is False

    # فاصله‌ی خیلی زیاد: قطعاً دو چیز متفاوت‌اند
    assert aiskills.needs_semantic_check(0, (1 << 64) - 1) is False


def test_the_band_edges_are_exclusive_below_and_inclusive_above():
    """مرزها را صریح می‌سنجیم تا جابه‌جا شدنشان بی‌صدا نماند."""
    low = (1 << aiskills.UNSURE_LOW) - 1              # دقیقاً UNSURE_LOW بیت
    inside = (1 << (aiskills.UNSURE_LOW + 1)) - 1     # یکی بیشتر
    edge = (1 << aiskills.UNSURE_HIGH) - 1            # دقیقاً UNSURE_HIGH بیت
    over = (1 << (aiskills.UNSURE_HIGH + 1)) - 1      # یکی بیشتر

    assert aiskills.needs_semantic_check(0, low) is False
    assert aiskills.needs_semantic_check(0, inside) is True
    assert aiskills.needs_semantic_check(0, edge) is True
    assert aiskills.needs_semantic_check(0, over) is False


@pytest.mark.asyncio
async def test_same_story_picks_the_closest_candidate(monkeypatch):
    monkeypatch.setattr(ai, "configured", lambda: True)

    async def fake_embed(texts):
        # اولی متن تازه؛ نامزد دوم تقریباً همان بردار است
        return [[1.0, 0.0], [0.0, 1.0], [0.99, 0.14]]

    monkeypatch.setattr(ai, "embed", fake_embed)

    index, _ = await aiskills.same_story("خبر", ["بی‌ربط", "همان خبر"])
    assert index == 1


@pytest.mark.asyncio
async def test_same_story_returns_none_when_nothing_is_close(monkeypatch):
    monkeypatch.setattr(ai, "configured", lambda: True)

    async def fake_embed(texts):
        return [[1.0, 0.0], [0.0, 1.0]]

    monkeypatch.setattr(ai, "embed", fake_embed)

    index, _ = await aiskills.same_story("خبر", ["کاملاً بی‌ربط"])
    assert index is None


@pytest.mark.asyncio
async def test_same_story_survives_a_short_embedding_reply(monkeypatch):
    """اگر سرویس کمتر از تعداد خواسته‌شده بردار بدهد، نباید IndexError بدهد."""
    monkeypatch.setattr(ai, "configured", lambda: True)

    async def fake_embed(texts):
        return [[1.0, 0.0]]

    monkeypatch.setattr(ai, "embed", fake_embed)

    index, usage = await aiskills.same_story("خبر", ["الف", "ب"])
    assert index is None
    assert usage.total == 0


# ── بازنویسی ────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_rewrite_returns_the_model_text_and_its_cost(model):
    model.reply = ai.Reply(text="متن تازه", usage=ai.Usage(prompt=120, completion=60))
    result = await aiskills.rewrite("متن اصلی", style="friendly")
    assert result.text == "متن تازه"
    assert result.usage.total == 180
    assert result.used_model is True


@pytest.mark.asyncio
async def test_empty_text_costs_nothing(model):
    assert await aiskills.rewrite("   ") is None
    assert await aiskills.translate("") is None
    assert model.calls == 0


def test_every_style_and_language_has_a_label():
    """کلیدها در دیتابیس ذخیره می‌شوند؛ برچسب نداشتنشان یعنی منوی خالی."""
    assert aiskills.STYLES["same"]
    assert all(label for label in aiskills.STYLES.values())
    assert all(label for label in aiskills.LANGUAGES.values())
