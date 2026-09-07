"""تست یادآوری انقضای مشتری به نماینده.

<b>چرا این کار حساس است.</b> نگرانی هر نماینده یک جمله است: «مشتریِ من
مستقیم از شما بخرد و دیگر سراغ من نیاید». آن اتفاق وقتی می‌افتد که
اشتراک مشتری تمام شود و نماینده خبر نداشته باشد.

ولی همین کار اگر بد انجام شود، بدتر از نبودنش است: نماینده‌ای که هر
روز بیست اعلان تکراری می‌گیرد، بعد از دو روز هیچ‌کدام را باز نمی‌کند
— و آن یکی که مهم بود هم گم می‌شود.
"""
from __future__ import annotations

from datetime import timedelta

import pytest

from tests.test_copier import _setup


class _Inbox:
    def __init__(self) -> None:
        self.sent: list[tuple[int, str]] = []

    async def __call__(self, user_id: int, text: str, markup=None) -> None:
        self.sent.append((user_id, text))


async def _customer(db_module, user_id: int, owner: int, *, days: float, name="مشتری"):
    from telkap.models import Subscription, User, utcnow

    async with db_module.get_session() as db:
        if await db.get(User, owner) is None:
            db.add(User(id=owner, first_name="نماینده", is_reseller=True))
        person = await db.get(User, user_id)
        if person is None:
            person = User(id=user_id, first_name=name)
            db.add(person)
        person.owned_by = owner
        db.add(
            Subscription(
                user_id=user_id,
                plan_code="week",
                expires_at=utcnow() + timedelta(days=days),
            )
        )
        await db.commit()


@pytest.mark.asyncio
async def test_the_reseller_hears_before_the_subscription_ends(tmp_path, monkeypatch):
    db_module, _ = await _setup(tmp_path, monkeypatch, settings={})
    try:
        from telkap.services import agentnudge

        await _customer(db_module, 501, owner=50, days=3, name="نزدیک")
        inbox = _Inbox()

        assert await agentnudge.run_once(inbox) == 1
        assert len(inbox.sent) == 1
        who, text = inbox.sent[0]
        assert who == 50
        assert "نزدیک" in text
        assert "501" in text
    finally:
        await db_module.close_db()


@pytest.mark.asyncio
async def test_a_customer_that_is_not_close_is_left_alone(tmp_path, monkeypatch):
    """اعلانِ زودهنگام همان‌قدر بی‌فایده است که اعلانِ دیرهنگام."""
    db_module, _ = await _setup(tmp_path, monkeypatch, settings={})
    try:
        from telkap.services import agentnudge

        await _customer(db_module, 502, owner=50, days=40)
        inbox = _Inbox()

        assert await agentnudge.run_once(inbox) == 0
        assert inbox.sent == []
    finally:
        await db_module.close_db()


@pytest.mark.asyncio
async def test_twenty_customers_make_one_message_not_twenty(tmp_path, monkeypatch):
    """<b>تفاوت یک ابزار با یک مزاحم.</b>"""
    db_module, _ = await _setup(tmp_path, monkeypatch, settings={})
    try:
        from telkap.services import agentnudge

        for index in range(20):
            await _customer(db_module, 600 + index, owner=51, days=2, name=f"م{index}")
        inbox = _Inbox()

        await agentnudge.run_once(inbox)

        assert len(inbox.sent) == 1
        assert "۲۰" in inbox.sent[0][1]
    finally:
        await db_module.close_db()


@pytest.mark.asyncio
async def test_the_same_subscription_is_never_announced_twice(tmp_path, monkeypatch):
    """چهار بار در روز اجرا می‌شود؛ بدون این، چهار بار هم خبر می‌داد."""
    db_module, _ = await _setup(tmp_path, monkeypatch, settings={})
    try:
        from telkap.services import agentnudge

        await _customer(db_module, 503, owner=52, days=4)
        inbox = _Inbox()

        assert await agentnudge.run_once(inbox) == 1
        assert await agentnudge.run_once(inbox) == 0
        assert await agentnudge.run_once(inbox) == 0
        assert len(inbox.sent) == 1
    finally:
        await db_module.close_db()


@pytest.mark.asyncio
async def test_each_reseller_only_hears_about_their_own(tmp_path, monkeypatch):
    """<b>نشتِ اینجا یعنی دادنِ فهرست مشتری یک نماینده به نماینده‌ی دیگر.</b>"""
    db_module, _ = await _setup(tmp_path, monkeypatch, settings={})
    try:
        from telkap.services import agentnudge

        await _customer(db_module, 504, owner=53, days=2, name="مالِ پنجاه‌وسه")
        await _customer(db_module, 505, owner=54, days=2, name="مالِ پنجاه‌وچهار")
        inbox = _Inbox()

        await agentnudge.run_once(inbox)
        got = dict(inbox.sent)

        assert "مالِ پنجاه‌وسه" in got[53]
        assert "مالِ پنجاه‌وچهار" not in got[53]
        assert "مالِ پنجاه‌وچهار" in got[54]
        assert "مالِ پنجاه‌وسه" not in got[54]
    finally:
        await db_module.close_db()


@pytest.mark.asyncio
async def test_a_customer_who_renewed_is_not_reported_as_expiring(tmp_path, monkeypatch):
    """<b>کسی که سه بار تمدید کرده سه ردیف اشتراک دارد.</b>

    بدون برداشتنِ تازه‌ترین، همان مشتری با اشتراکِ قدیمیِ منقضی‌شده در
    فهرست می‌آمد — و نماینده سراغ کسی می‌رفت که همین دیروز تمدید کرده.
    """
    db_module, _ = await _setup(tmp_path, monkeypatch, settings={})
    try:
        from telkap.services import agentnudge

        await _customer(db_module, 506, owner=55, days=2, name="تمدیدکرده")
        await _customer(db_module, 506, owner=55, days=60, name="تمدیدکرده")
        inbox = _Inbox()

        assert await agentnudge.run_once(inbox) == 0
        assert inbox.sent == []
    finally:
        await db_module.close_db()


@pytest.mark.asyncio
async def test_an_expired_customer_is_reported_once_then_dropped(tmp_path, monkeypatch):
    """یادآوریِ همیشگی دیگر یادآوری نیست؛ سرزنش است."""
    db_module, _ = await _setup(tmp_path, monkeypatch, settings={})
    try:
        from telkap.services import agentnudge

        await _customer(db_module, 507, owner=56, days=-1, name="تمام‌شده")
        inbox = _Inbox()

        await agentnudge.run_once(inbox)
        assert len(inbox.sent) == 1
        assert "تمام شد" in inbox.sent[0][1]

        # و مشتریِ خیلی قدیمی اصلاً گفته نمی‌شود
        await _customer(db_module, 508, owner=57, days=-30, name="خیلی قدیمی")
        inbox2 = _Inbox()
        await agentnudge.run_once(inbox2)
        assert all("خیلی قدیمی" not in text for _who, text in inbox2.sent)
    finally:
        await db_module.close_db()
