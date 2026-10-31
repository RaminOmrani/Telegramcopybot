"""تست دور هشتم: سلامت اکانت، واکنش به محدودیت تلگرام، و پشتیبان بیرونی."""
from __future__ import annotations

import pytest

from tests.test_copier import FakeClient, FakeManager, FakeMessage, _setup


# ------------------------------------------------------ تشخیص خطاها
def test_every_limit_error_gets_its_own_diagnosis():
    from telethon.errors import (
        AuthKeyUnregisteredError,
        FloodWaitError,
        PeerFloodError,
        UserDeactivatedBanError,
    )

    from telkap.services import health

    flood = health.classify(FloodWaitError(request=None))
    assert flood.state == health.STATE_FLOOD
    assert not flood.fatal          # موقت است، نباید کار را متوقف کند

    peer = health.classify(PeerFloodError(request=None))
    assert peer.state == health.STATE_PEER_FLOOD
    assert peer.fatal
    assert "SpamBot" in peer.message

    banned = health.classify(UserDeactivatedBanError(request=None))
    assert banned.state == health.STATE_BANNED and banned.fatal

    revoked = health.classify(AuthKeyUnregisteredError(request=None))
    assert revoked.state == health.STATE_REVOKED and revoked.fatal

    # خطای بی‌ربط نباید به‌عنوان محدودیت تعبیر شود
    assert health.classify(ValueError("چیز دیگری")).state == health.STATE_OK


def test_flood_wait_message_states_the_wait():
    from telethon.errors import FloodWaitError

    from telkap.services import health

    exc = FloodWaitError(request=None)
    exc.seconds = 7200
    assert "۲ ساعت" in health.classify(exc).message.replace("2", "۲")


# ------------------------------------------------------ ثبت و اطلاع
@pytest.mark.asyncio
async def test_user_is_told_once_not_on_every_post(tmp_path, monkeypatch):
    db_module, _ = await _setup(tmp_path, monkeypatch, settings={})
    try:
        from telethon.errors import PeerFloodError

        from telkap.services import health

        notes: list[tuple[int, str]] = []

        async def notifier(user_id, text):
            notes.append((user_id, text))

        diagnosis = health.classify(PeerFloodError(request=None))
        assert await health.record(7, diagnosis, notifier=notifier) is True
        # همان وضعیت دوباره نباید کاربر را دوباره بیدار کند
        assert await health.record(7, diagnosis, notifier=notifier) is False
        assert len(notes) == 1
        assert await health.state_of(7) == health.STATE_PEER_FLOOD
    finally:
        await db_module.close_db()


@pytest.mark.asyncio
async def test_temporary_limits_do_not_bother_the_user(tmp_path, monkeypatch):
    """FloodWait عادی است؛ ثبت می‌شود ولی پیامی نمی‌رود."""
    db_module, _ = await _setup(tmp_path, monkeypatch, settings={})
    try:
        from telethon.errors import FloodWaitError

        from telkap.services import health

        notes: list = []

        async def notifier(user_id, text):
            notes.append(text)

        exc = FloodWaitError(request=None)
        exc.seconds = 30
        await health.record(7, health.classify(exc), notifier=notifier)
        assert notes == []
        assert await health.state_of(7) == health.STATE_FLOOD
    finally:
        await db_module.close_db()


@pytest.mark.asyncio
async def test_reconnecting_clears_the_problem(tmp_path, monkeypatch):
    db_module, _ = await _setup(tmp_path, monkeypatch, settings={})
    try:
        from telethon.errors import PeerFloodError

        from telkap.services import health

        await health.record(7, health.classify(PeerFloodError(request=None)))
        assert await health.state_of(7) != health.STATE_OK

        await health.clear(7)
        assert await health.state_of(7) == health.STATE_OK
    finally:
        await db_module.close_db()


@pytest.mark.asyncio
async def test_admin_summary_counts_only_connected_accounts(tmp_path, monkeypatch):
    db_module, _ = await _setup(tmp_path, monkeypatch, settings={})
    try:
        from telethon.errors import PeerFloodError

        from telkap.models import User
        from telkap.services import health

        # کاربر ۷ اکانت متصل ندارد، پس در آمار سلامت نمی‌آید
        await health.record(7, health.classify(PeerFloodError(request=None)))
        assert await health.summary() == {}

        async with db_module.get_session() as db:
            db.add(User(id=11, first_name="متصل", session_enc="x"))
            await db.commit()
        await health.record(11, health.classify(PeerFloodError(request=None)))

        assert (await health.summary()).get(health.STATE_PEER_FLOOD) == 1
        assert [u.id for u in await health.unhealthy()] == [11]
    finally:
        await db_module.close_db()


# --------------------------------------------- واکنش موتور کپی
class FloodClient(FakeClient):
    """کلاینتی که تلگرام محدودش کرده است."""

    async def send_message(self, *args, **kwargs):
        from telethon.errors import PeerFloodError

        raise PeerFloodError(request=None)


@pytest.mark.asyncio
async def test_peer_flood_pauses_every_task_and_warns_once(tmp_path, monkeypatch):
    db_module, task_id = await _setup(tmp_path, monkeypatch, settings={})
    try:
        from telkap.models import Task
        from telkap.services import health
        from telkap.services.copier import Copier

        notes: list[tuple[int, str]] = []

        async def notifier(user_id, text):
            notes.append((user_id, text))

        copier = Copier(FakeManager(FloodClient()), notifier=notifier)
        # _dispatch مسیر واقعی است: خطا از process بالا می‌آید و مرکزی
        # رسیدگی می‌شود
        monkeypatch.setattr(
            copier.manager, "tasks_for_chat", lambda uid, chat: [task_id]
        )
        await copier._dispatch(7, 123, [FakeMessage(id=1, message="سلام")])

        async with db_module.get_session() as db:
            assert (await db.get(Task, task_id)).enabled is False

        assert await health.state_of(7) == health.STATE_PEER_FLOOD
        assert notes and "محدود" in notes[0][1]
    finally:
        await db_module.close_db()


@pytest.mark.asyncio
async def test_ordinary_failures_do_not_pause_anything(tmp_path, monkeypatch):
    """خطای معمولی باید به صف تلاش مجدد برود، نه توقف کار."""
    db_module, task_id = await _setup(tmp_path, monkeypatch, settings={})
    try:
        from telkap.models import Task
        from telkap.services import health
        from telkap.services.copier import Copier

        class BrokenClient(FakeClient):
            async def send_message(self, *args, **kwargs):
                raise RuntimeError("شبکه قطع شد")

        copier = Copier(FakeManager(BrokenClient()))
        monkeypatch.setattr(
            copier.manager, "tasks_for_chat", lambda uid, chat: [task_id]
        )
        await copier._dispatch(7, 123, [FakeMessage(id=1, message="سلام")])

        async with db_module.get_session() as db:
            assert (await db.get(Task, task_id)).enabled is True
        assert await health.state_of(7) == health.STATE_OK
    finally:
        await db_module.close_db()


# ------------------------------------------------ اثر انگشت کلاینت
def test_client_does_not_announce_itself_as_a_bot():
    """نام دستگاه هم به کاربر و هم به تلگرام نشان داده می‌شود."""
    from telkap.config import get_settings

    cfg = get_settings()
    for value in (cfg.device_model, cfg.system_version, cfg.app_version):
        assert value
        assert "bot" not in value.lower()
        assert "copy" not in value.lower()


# ---------------------------------------------------- پشتیبان‌گیری
@pytest.mark.asyncio
async def test_backup_is_written_and_compresses_well(tmp_path, monkeypatch):
    db_module, _ = await _setup(tmp_path, monkeypatch, settings={})
    try:
        from telkap.services import backup

        path = backup.make_backup()
        assert path is not None and path.exists()

        packed = backup.compress(path)
        assert packed is not None and packed.exists()
        assert packed.suffix == ".gz"
        assert packed.stat().st_size < path.stat().st_size
    finally:
        await db_module.close_db()


@pytest.mark.asyncio
async def test_offsite_upload_is_skipped_when_no_channel_is_set(tmp_path, monkeypatch):
    db_module, _ = await _setup(tmp_path, monkeypatch, settings={})
    try:
        from telkap.services import backup

        path, offsite = await backup.run_once(bot=None)
        assert path is not None and path.exists()
        assert offsite is False        # کانالی تنظیم نشده، ولی نسخه‌ی محلی ساخته شد
    finally:
        await db_module.close_db()


@pytest.mark.asyncio
async def test_offsite_upload_sends_the_file(tmp_path, monkeypatch):
    db_module, _ = await _setup(tmp_path, monkeypatch, settings={})
    try:
        from telkap.services import backup

        sent: list = []

        class FakeBot:
            async def send_document(self, chat, document, **kwargs):
                sent.append((chat, kwargs.get("caption", "")))

        path = backup.make_backup()
        assert await backup.send_offsite(FakeBot(), path, to="-1001234567890") is True
        assert sent and sent[0][0] == -1001234567890
        assert "پشتیبان" in sent[0][1]
    finally:
        await db_module.close_db()


@pytest.mark.asyncio
async def test_oversized_backup_is_refused_not_truncated(tmp_path, monkeypatch):
    db_module, _ = await _setup(tmp_path, monkeypatch, settings={})
    try:
        from telkap.services import backup

        big = tmp_path / "big.db"
        big.write_bytes(b"x" * 32)
        monkeypatch.setattr(backup, "MAX_UPLOAD_BYTES", 8)

        class FakeBot:
            async def send_document(self, *a, **k):
                raise AssertionError("نباید ارسال می‌شد")

        assert await backup.send_offsite(FakeBot(), big, to="-100123") is False
    finally:
        await db_module.close_db()
