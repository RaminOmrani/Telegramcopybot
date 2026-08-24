"""تست دور چهارم: امضای اختصاصی هر مقصد و مدیریت اشتراک از پنل ادمین."""
from __future__ import annotations

import pytest

from tests.test_copier import FakeClient, FakeManager, FakeMessage, _setup


async def _add_destination(db_module, task_id: int, ref: str, chat_id: int, overrides: dict):
    from telkap.models import Destination
    from telkap.services import cache

    async with db_module.get_session() as session:
        session.add(
            Destination(
                task_id=task_id,
                chat_id=chat_id,
                ref=ref,
                title=ref,
                overrides=overrides,
            )
        )
        await session.commit()
    cache.invalidate_task(task_id)


# ------------------------------------------------ امضای اختصاصی هر مقصد
@pytest.mark.asyncio
async def test_each_destination_gets_its_own_footer(tmp_path, monkeypatch):
    """یک مبدا، دو مقصد، دو امضای متفاوت."""
    db_module, task_id = await _setup(tmp_path, monkeypatch, settings={"footer": "@first"})
    try:
        from telkap.services.copier import Copier

        await _add_destination(db_module, task_id, "@second", -1003, {"footer": "@second"})

        client = FakeClient()
        copier = Copier(FakeManager(client))
        await copier.process(7, task_id, [FakeMessage(id=1, message="خبر مهم")])

        by_target = {record.target: record.text for record in client.sent}
        assert by_target[-1002].endswith("@first")
        assert by_target[-1003].endswith("@second")
        # متن اصلی در هر دو دست‌نخورده مانده است
        assert all(text.startswith("خبر مهم") for text in by_target.values())
    finally:
        await db_module.close_db()


@pytest.mark.asyncio
async def test_destination_without_overrides_uses_task_settings(tmp_path, monkeypatch):
    db_module, task_id = await _setup(tmp_path, monkeypatch, settings={"footer": "@shared"})
    try:
        from telkap.services.copier import Copier

        await _add_destination(db_module, task_id, "@second", -1003, {})

        client = FakeClient()
        copier = Copier(FakeManager(client))
        await copier.process(7, task_id, [FakeMessage(id=1, message="خبر")])

        texts = [record.text for record in client.sent]
        assert len(texts) == 2
        assert all(text == "خبر\n\n@shared" for text in texts)
    finally:
        await db_module.close_db()


@pytest.mark.asyncio
async def test_destination_override_can_add_header(tmp_path, monkeypatch):
    db_module, task_id = await _setup(tmp_path, monkeypatch, settings={})
    try:
        from telkap.services.copier import Copier

        await _add_destination(db_module, task_id, "@vip", -1003, {"header": "🔥 ویژه"})

        client = FakeClient()
        copier = Copier(FakeManager(client))
        await copier.process(7, task_id, [FakeMessage(id=1, message="متن")])

        by_target = {record.target: record.text for record in client.sent}
        assert by_target[-1002] == "متن"
        assert by_target[-1003].startswith("🔥 ویژه")
    finally:
        await db_module.close_db()


# ---------------------------------------------- کم و زیاد کردن روز اشتراک
@pytest.mark.asyncio
async def test_adjust_days_extends_and_shortens(tmp_path, monkeypatch):
    db_module, _task_id = await _setup(tmp_path, monkeypatch, settings={})
    try:
        from telkap.services import subscription

        before = await subscription.remaining_days(7)
        assert before > 0

        await subscription.adjust_days(7, 10, admin_id=1)
        assert await subscription.remaining_days(7) == before + 10

        await subscription.adjust_days(7, -5, admin_id=1)
        assert await subscription.remaining_days(7) == before + 5
    finally:
        await db_module.close_db()


@pytest.mark.asyncio
async def test_adjust_days_never_goes_below_now(tmp_path, monkeypatch):
    """کم کردن بیش از اندازه، اشتراک را تمام می‌کند نه اینکه به گذشته ببرد."""
    db_module, _task_id = await _setup(tmp_path, monkeypatch, settings={})
    try:
        from telkap.services import subscription

        await subscription.adjust_days(7, -9999, admin_id=1)
        assert await subscription.active_subscription(7) is None
        assert await subscription.remaining_days(7) == 0
    finally:
        await db_module.close_db()


@pytest.mark.asyncio
async def test_adjust_days_without_subscription_returns_none(tmp_path, monkeypatch):
    db_module, _task_id = await _setup(tmp_path, monkeypatch, settings={})
    try:
        from telkap.models import User
        from telkap.services import subscription

        async with db_module.get_session() as session:
            session.add(User(id=99, first_name="بدون اشتراک"))
            await session.commit()

        assert await subscription.adjust_days(99, 30, admin_id=1) is None
    finally:
        await db_module.close_db()


@pytest.mark.asyncio
async def test_revoke_ends_subscription_and_clears_cache(tmp_path, monkeypatch):
    db_module, task_id = await _setup(tmp_path, monkeypatch, settings={})
    try:
        from telkap.services import cache, subscription

        assert await cache.get_plan(7) is not None
        assert await cache.get_task(task_id) is not None

        count = await subscription.revoke(7, admin_id=1)
        assert count == 1
        assert await subscription.active_subscription(7) is None
        # کش کاربر باطل شده تا کپی بعدی متوقف شود
        assert cache.stats()["tasks"] == 0
        assert await cache.get_plan(7) is None
    finally:
        await db_module.close_db()


@pytest.mark.asyncio
async def test_revoke_stops_copying(tmp_path, monkeypatch):
    db_module, task_id = await _setup(tmp_path, monkeypatch, settings={})
    try:
        from telkap.services import subscription
        from telkap.services.copier import Copier

        client = FakeClient()
        copier = Copier(FakeManager(client))
        assert await copier.process(7, task_id, [FakeMessage(id=1, message="اول")]) is True

        await subscription.revoke(7, admin_id=1)
        assert await copier.process(7, task_id, [FakeMessage(id=2, message="دوم")]) is False
        assert len(client.sent) == 1
    finally:
        await db_module.close_db()
