"""تست مرحله‌ی هوش مصنوعی در مسیر کپی.

چیزی که اینجا مهم است پول است: اعتبار درست کم شود، و مهم‌تر — وقتی مدل
جواب نداد <b>برگردد</b>. کاربری که بابت کاری که انجام نشده پول داده،
پشتیبانی می‌زند.
"""
from __future__ import annotations

import pytest

from telkap.plans import CREDIT_AI
from telkap.services import ai, aipass, aiskills, credits


class Wallet:
    """کیف پول ساختگی، تا تست به دیتابیس نیاز نداشته باشد."""

    def __init__(self, start: int = 10):
        self.balance = start
        self.consumed = 0
        self.refunded = 0

    async def consume(self, user_id, kind, amount=1):
        assert kind == CREDIT_AI
        if self.balance < amount:
            return False
        self.balance -= amount
        self.consumed += amount
        return True

    async def add(self, user_id, kind, amount, *, note=""):
        self.balance += amount
        if amount > 0:
            self.refunded += amount
        return self.balance


@pytest.fixture
def wallet(monkeypatch):
    purse = Wallet()
    monkeypatch.setattr(credits, "consume", purse.consume)
    monkeypatch.setattr(credits, "add", purse.add)
    monkeypatch.setattr(ai, "configured", lambda: True)
    return purse


def _skill(text: str):
    async def run(*args, **kwargs):
        return aiskills.Outcome(text=text, usage=ai.Usage(prompt=50, completion=50))
    return run


async def _fails(*args, **kwargs):
    return None


# ── کاری که هوش مصنوعی ندارد، هیچ هزینه‌ای ندارد ────────────────────


@pytest.mark.asyncio
async def test_a_task_without_ai_touches_nothing(monkeypatch):
    """نه اعتباری کم می‌شود، نه سرویسی صدا زده می‌شود، نه دیتابیسی باز.

    اکثر کارها هوش مصنوعی ندارند؛ اگر این مسیر هزینه‌دار باشد، هزینه‌اش
    را همه می‌دهند.
    """
    async def explode(*args, **kwargs):
        raise AssertionError("نباید صدا زده می‌شد")

    monkeypatch.setattr(credits, "consume", explode)
    monkeypatch.setattr(ai, "configured", explode)

    result = await aipass.enhance("متن", {}, user_id=7)

    assert result.text == "متن"
    assert result.changed is False
    assert result.spent == 0


@pytest.mark.asyncio
async def test_without_a_key_the_text_passes_through(monkeypatch):
    monkeypatch.setattr(ai, "configured", lambda: False)

    async def explode(*args, **kwargs):
        raise AssertionError("بدون کلید نباید اعتبار کم شود")

    monkeypatch.setattr(credits, "consume", explode)

    result = await aipass.enhance("متن", {"ai_rewrite": True}, user_id=7)
    assert result.text == "متن"
    assert result.spent == 0


# ── مسیر عادی ───────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_one_operation_costs_one_unit(wallet, monkeypatch):
    monkeypatch.setattr(aiskills, "rewrite", _skill("متن تازه"))

    result = await aipass.enhance("متن اصلی", {"ai_rewrite": True}, user_id=7)

    assert result.text == "متن تازه"
    assert result.applied == [aipass.OP_REWRITE]
    assert result.spent == 1
    assert wallet.balance == 9


@pytest.mark.asyncio
async def test_operations_run_in_the_declared_order(wallet, monkeypatch):
    """ترتیب نتیجه را عوض می‌کند: خلاصه ← بازنویسی ← ترجمه."""
    seen: list[str] = []

    def watcher(name: str, out: str):
        async def run(text, **kwargs):
            seen.append(name)
            return aiskills.Outcome(text=out)
        return run

    monkeypatch.setattr(aiskills, "summarize", watcher("summarize", "خلاصه"))
    monkeypatch.setattr(aiskills, "rewrite", watcher("rewrite", "بازنویسی"))
    monkeypatch.setattr(aiskills, "translate", watcher("translate", "ترجمه"))

    result = await aipass.enhance(
        "متن",
        {"ai_translate": True, "ai_rewrite": True, "ai_summarize": True},
        user_id=7,
    )

    assert seen == ["summarize", "rewrite", "translate"]
    assert result.text == "ترجمه"
    assert result.spent == 3
    assert wallet.balance == 7


@pytest.mark.asyncio
async def test_each_step_feeds_the_next(wallet, monkeypatch):
    """ترجمه باید متنِ بازنویسی‌شده را بگیرد، نه متن اصلی."""
    got: list[str] = []

    async def rewrite(text, **kwargs):
        return aiskills.Outcome(text="بازنویسی‌شده")

    async def translate(text, **kwargs):
        got.append(text)
        return aiskills.Outcome(text="ترجمه‌شده")

    monkeypatch.setattr(aiskills, "rewrite", rewrite)
    monkeypatch.setattr(aiskills, "translate", translate)

    await aipass.enhance(
        "متن اصلی", {"ai_rewrite": True, "ai_translate": True}, user_id=7
    )

    assert got == ["بازنویسی‌شده"]


# ── پول: برگشت اعتبار ───────────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_failed_call_refunds_the_credit(wallet, monkeypatch):
    monkeypatch.setattr(aiskills, "rewrite", _fails)

    result = await aipass.enhance("متن", {"ai_rewrite": True}, user_id=7)

    assert result.text == "متن"
    assert result.spent == 0
    assert wallet.consumed == 1
    assert wallet.refunded == 1
    assert wallet.balance == 10


@pytest.mark.asyncio
async def test_an_empty_answer_also_refunds(wallet, monkeypatch):
    """خلاصه‌ی متن کوتاه None می‌دهد؛ نباید پولش گرفته شود."""
    monkeypatch.setattr(aiskills, "summarize", _skill("   "))

    result = await aipass.enhance("متن", {"ai_summarize": True}, user_id=7)

    assert result.spent == 0
    assert wallet.balance == 10


@pytest.mark.asyncio
async def test_a_raising_skill_does_not_stop_the_copy(wallet, monkeypatch):
    """کپی نباید بخوابد چون یک قابلیت اختیاری استثنا داد."""
    async def boom(*args, **kwargs):
        raise RuntimeError("سرویس خراب")

    monkeypatch.setattr(aiskills, "rewrite", boom)

    result = await aipass.enhance("متن", {"ai_rewrite": True}, user_id=7)

    assert result.text == "متن"
    assert wallet.balance == 10


@pytest.mark.asyncio
async def test_one_failure_does_not_cancel_the_others(wallet, monkeypatch):
    monkeypatch.setattr(aiskills, "rewrite", _fails)
    monkeypatch.setattr(aiskills, "translate", _skill("ترجمه"))

    result = await aipass.enhance(
        "متن", {"ai_rewrite": True, "ai_translate": True}, user_id=7
    )

    assert result.text == "ترجمه"
    assert result.applied == [aipass.OP_TRANSLATE]
    assert wallet.balance == 9          # یکی کم، یکی برگشت، یکی خرج


# ── اعتبار تمام‌شده ─────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_running_out_keeps_what_was_already_done(monkeypatch):
    purse = Wallet(start=1)
    monkeypatch.setattr(credits, "consume", purse.consume)
    monkeypatch.setattr(credits, "add", purse.add)
    monkeypatch.setattr(ai, "configured", lambda: True)
    monkeypatch.setattr(aiskills, "rewrite", _skill("بازنویسی‌شده"))
    monkeypatch.setattr(aiskills, "translate", _skill("ترجمه‌شده"))

    result = await aipass.enhance(
        "متن", {"ai_rewrite": True, "ai_translate": True}, user_id=7
    )

    # اولی انجام شد، دومی اعتبار نداشت — نتیجه‌ی نیمه‌کاره نگه داشته
    # می‌شود، نه اینکه همه‌چیز دور ریخته شود
    assert result.text == "بازنویسی‌شده"
    assert result.out_of_credit is True
    assert result.spent == 1


@pytest.mark.asyncio
async def test_no_credit_at_all_leaves_the_text_alone(monkeypatch):
    purse = Wallet(start=0)
    monkeypatch.setattr(credits, "consume", purse.consume)
    monkeypatch.setattr(credits, "add", purse.add)
    monkeypatch.setattr(ai, "configured", lambda: True)

    result = await aipass.enhance("متن", {"ai_rewrite": True}, user_id=7)

    assert result.text == "متن"
    assert result.out_of_credit is True
    assert result.changed is False


# ── گزارش ───────────────────────────────────────────────────────────


def test_summary_is_empty_when_nothing_happened():
    assert aipass.summary(aipass.Pass("متن")) == ""


def test_summary_names_what_was_done():
    result = aipass.Pass("متن", [aipass.OP_REWRITE, aipass.OP_TRANSLATE], spent=2)
    line = aipass.summary(result)
    assert "بازنویسی" in line and "ترجمه" in line and "۲" in line.replace("2", "۲")


def test_summary_says_when_credit_ran_out():
    result = aipass.Pass("متن", [aipass.OP_REWRITE], spent=1, out_of_credit=True)
    assert "تمام شد" in aipass.summary(result)


def test_wanted_follows_the_declared_order():
    cfg = {"ai_translate": True, "ai_summarize": True, "ai_rewrite": True}
    assert aipass.wanted(cfg) == list(aipass.ORDER)
    assert aipass.wanted({}) == []


def test_every_operation_has_a_label():
    assert all(aipass.LABELS[op] for op in aipass.ORDER)
