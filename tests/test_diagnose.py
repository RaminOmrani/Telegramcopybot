"""تست تشخیص: چرا این کار پست نمی‌زند.

<b>چرا این فایل هست.</b> گزارشِ اول یک ادعای اثبات‌نشده می‌کرد —
وقتی هیچ رکوردی نداشت می‌گفت «مبدا چیزی منتشر نکرده». این را
نمی‌دانست، فقط نمی‌دیدش. و در اولین برخورد با واقعیت غلط از آب
درآمد: کانال نُه پست زده بود و هیچ‌کدام به ربات نرسیده بود.

پس تست‌های اینجا بیش از هر چیز می‌سنجند که گزارش <b>چیزی را که
نمی‌داند ادعا نکند</b>.
"""
from __future__ import annotations

import pytest

from telkap.models import Destination, MessageMap, Task, User
from telkap.services import diagnose
from tests.test_copier import _setup


class _Msg:
    def __init__(self, msg_id):
        self.id = msg_id


class _Client:
    def __init__(self, latest_id=None, error=None):
        self.latest_id = latest_id
        self.error = error

    async def get_messages(self, target, limit=1):
        if self.error:
            raise RuntimeError(self.error)
        return [_Msg(self.latest_id)] if self.latest_id else []


async def _task(db_module, **kwargs):
    fields = {
        "user_id": 7,
        "source_ref": "@src",
        "source_id": -1001234,
        "dest_ref": "@dst",
        "title": "کانال آزمایشی",
        "enabled": True,
    }
    fields.update(kwargs)
    async with db_module.get_session() as db:
        if await db.get(User, fields["user_id"]) is None:
            db.add(User(id=fields["user_id"], first_name="ر"))
            await db.commit()
        task = Task(**fields)
        db.add(task)
        await db.commit()
        await db.refresh(task)
        db.add(Destination(task_id=task.id, ref="@dst", enabled=True))
        await db.commit()
        return task


def _fake_manager(monkeypatch, *, connected=True, listening=True, client=None):
    from telkap.services.userbot import manager

    monkeypatch.setattr(manager, "is_connected", lambda uid: connected)
    monkeypatch.setattr(manager, "is_listening", lambda uid, cid: listening)
    monkeypatch.setattr(manager, "get_client", lambda uid: client)


@pytest.mark.asyncio
async def test_posts_that_never_arrived_are_counted(tmp_path, monkeypatch):
    """<b>همان چیزی که ربات اشتباه گفت.</b>

    مبدا نُه پست جلوتر رفته و ما هیچ‌کدام را ندیده‌ایم. گزارش قبلی
    می‌گفت «مبدا چیزی منتشر نکرده»؛ حالا عددش را می‌گوید.
    """
    db_module, _ = await _setup(tmp_path, monkeypatch, settings={})
    task = await _task(db_module)
    async with db_module.get_session() as db:
        db.add(
            MessageMap(task_id=task.id, src_msg_id=1180, dest_chat="@dst", dst_msg_id=1)
        )
        await db.commit()

    _fake_manager(monkeypatch, listening=False, client=_Client(latest_id=1189))

    report = await diagnose.task_report(task.id)

    assert report.missed == 9
    assert any("۹" in p or "9" in p for p in report.problems)


@pytest.mark.asyncio
async def test_a_channel_we_do_not_listen_to_is_reported(tmp_path, monkeypatch):
    """<b>بدترین حالت، چون هیچ ردی نمی‌گذارد.</b>

    کانالی که در فهرست گوش دادن نباشد، پیام‌هایش به هیچ کاری نمی‌رسند
    و نه کپی ثبت می‌شود نه رد نه خطا — از بیرون دقیقاً شبیه «مبدا
    ساکت است».
    """
    db_module, _ = await _setup(tmp_path, monkeypatch, settings={})
    task = await _task(db_module)
    _fake_manager(monkeypatch, listening=False, client=_Client(latest_id=5))

    report = await diagnose.task_report(task.id)

    assert report.listening is False
    assert any("گوش نمی‌دهیم" in p for p in report.problems)


@pytest.mark.asyncio
async def test_a_disconnected_account_is_reported(tmp_path, monkeypatch):
    db_module, _ = await _setup(tmp_path, monkeypatch, settings={})
    task = await _task(db_module)
    _fake_manager(monkeypatch, connected=False, client=None)

    report = await diagnose.task_report(task.id)

    assert any("وصل نیست" in p for p in report.problems)


@pytest.mark.asyncio
async def test_being_unable_to_read_the_source_is_itself_a_finding(
    tmp_path, monkeypatch
):
    """<b>نرسیدن به مبدا یک یافته است، نه یک خطای بی‌اهمیت.</b>

    تلگرام پست‌های کانال را فقط به اعضایش می‌فرستد. اکانتی که عضو
    نیست هیچ‌وقت چیزی نمی‌گیرد — و این دقیقاً همان چیزی است که کاربر
    باید بداند.
    """
    db_module, _ = await _setup(tmp_path, monkeypatch, settings={})
    task = await _task(db_module)
    _fake_manager(monkeypatch, client=_Client(error="CHANNEL_PRIVATE"))

    report = await diagnose.task_report(task.id)

    assert "CHANNEL_PRIVATE" in report.probe_error
    assert any("عضو" in p for p in report.problems)


@pytest.mark.asyncio
async def test_a_healthy_task_claims_nothing_it_cannot_know(tmp_path, monkeypatch):
    """وقتی همه‌چیز سالم است، گزارش نباید مشکلی از خودش بسازد."""
    db_module, _ = await _setup(tmp_path, monkeypatch, settings={})
    task = await _task(db_module)
    async with db_module.get_session() as db:
        db.add(
            MessageMap(task_id=task.id, src_msg_id=50, dest_chat="@dst", dst_msg_id=1)
        )
        await db.commit()

    _fake_manager(monkeypatch, client=_Client(latest_id=50))

    report = await diagnose.task_report(task.id)

    assert report.missed == 0
    assert report.connected and report.listening
    assert not any("نرسیده" in p for p in report.problems)


@pytest.mark.asyncio
async def test_an_rss_task_is_not_probed_over_telegram(tmp_path, monkeypatch):
    """فید مبدا تلگرامی ندارد که آخرین پستش را بپرسیم."""
    db_module, _ = await _setup(tmp_path, monkeypatch, settings={})
    task = await _task(
        db_module, source_kind=Task.SOURCE_RSS, source_ref="https://x.example/feed"
    )

    async def explode(*args, **kwargs):
        raise AssertionError("نباید از تلگرام پرسیده می‌شد")

    client = _Client()
    client.get_messages = explode
    _fake_manager(monkeypatch, client=client)

    report = await diagnose.task_report(task.id)

    assert report.source_last_id == 0


@pytest.mark.asyncio
async def test_a_missing_task_gives_nothing(tmp_path, monkeypatch):
    await _setup(tmp_path, monkeypatch, settings={})

    assert await diagnose.task_report(9999) is None
