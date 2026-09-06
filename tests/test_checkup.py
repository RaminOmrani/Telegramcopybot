"""تست تشخیصِ «کدام کار کار نمی‌کند و چرا».

<b>چرا این فایل هست.</b> کاربر ده کار دارد و می‌گوید «فقط یکی‌شان کار
می‌کند». تا امروز هیچ راهی نبود که بفهمد کدام‌ها و چرا — چون کارِ
خراب هم در فهرست همان‌قدر سبز است. تشخیصی که خودش اشتباه بگوید از
نبودنش بدتر است، پس هر شاخه‌اش اینجا سنجیده می‌شود.
"""
from __future__ import annotations

import pytest

from tests.test_copier import _setup


class _Client:
    def is_connected(self) -> bool:
        return True


class _Entity:
    """کانالی که می‌شود یا نمی‌شود در آن پست گذاشت."""

    def __init__(self, *, left: bool = False, broadcast: bool = True, admin: bool = True):
        self.left = left
        self.broadcast = broadcast
        self.creator = False
        self.admin_rights = object() if admin else None


async def _make_task(db_module, user_id: int, **kw):
    from telkap.models import Task

    fields = {
        "user_id": user_id,
        "title": "کار",
        "source_ref": "@source",
        "source_id": -100,
        "dest_ref": "@dest",
        "dest_id": -200,
        "enabled": True,
    }
    fields.update(kw)
    async with db_module.get_session() as db:
        task = Task(**fields)
        db.add(task)
        await db.commit()
        await db.refresh(task)
        return task.id


def _arm(user_id: int, chat_id: int) -> None:
    """وانمود می‌کنیم روی این کانال هندلر داریم."""
    from telkap.services.userbot import UserRuntime, manager

    runtime = manager._runtimes.get(user_id)
    if runtime is None:
        runtime = UserRuntime(client=_Client())
        manager._runtimes[user_id] = runtime
    runtime.source_map.setdefault(chat_id, []).append(0)


def _fake_telegram(monkeypatch, *, entities: dict[str, object]):
    """resolve و اتصال را ساختگی می‌کنیم؛ تست به تلگرام وصل نمی‌شود."""
    from telkap.services.userbot import manager

    async def ensure_client(user_id):
        return _Client()

    async def resolve_entity(client, ref):
        return entities.get(ref)

    monkeypatch.setattr(manager, "ensure_client", ensure_client)
    monkeypatch.setattr(manager, "resolve_entity", staticmethod(resolve_entity))


async def _plan_always_active(monkeypatch):
    from telkap.plans import get_plan
    from telkap.services import subscription

    async def active(user_id):
        return get_plan("week") or object()

    monkeypatch.setattr(subscription, "active_plan_for", active)


@pytest.mark.asyncio
async def test_a_task_nobody_listens_to_is_called_out(tmp_path, monkeypatch):
    """<b>خاموش‌ترین خرابیِ ممکن.</b>

    کار روشن است، مبدأ و مقصد سر جایشان‌اند، ولی هیچ هندلری روی مبدأ
    نیست. پست‌ها می‌آیند و هیچ ردی نمی‌گذارند — نه کپی، نه رد، نه
    خطا. از بیرون شبیه «مبدا چیزی نگذاشته» است.
    """
    db_module, _ = await _setup(tmp_path, monkeypatch, settings={})
    try:
        from telkap.models import User
        from telkap.services import checkup
        from telkap.services.userbot import manager

        manager._runtimes.clear()
        async with db_module.get_session() as db:
            person = await db.get(User, 5)
            if person is None:
                db.add(User(id=5, first_name="کاربر", session_enc="x"))
            else:
                person.session_enc = "x"
            await db.commit()

        task_id = await _make_task(db_module, 5)
        _fake_telegram(monkeypatch, entities={"@source": _Entity(), "@dest": _Entity()})
        await _plan_always_active(monkeypatch)
        # عمداً _arm صدا زده نمی‌شود

        report = await checkup.check_user(5)
        mine = [item for item in report.tasks if item.task_id == task_id][0]

        assert mine.state == checkup.BAD
        assert any("گوش داده نمی‌شود" in line for line in mine.problems)
        assert mine.fixes, "تشخیص بدون راه‌حل فقط نگرانی است"
    finally:
        manager._runtimes.clear()
        await db_module.close_db()


@pytest.mark.asyncio
async def test_a_healthy_task_is_not_flagged(tmp_path, monkeypatch):
    """اگر تشخیص روی کارِ سالم هم زنگ بزند، هیچ‌کس جدی‌اش نمی‌گیرد."""
    db_module, _ = await _setup(tmp_path, monkeypatch, settings={})
    try:
        from telkap.models import User
        from telkap.services import checkup
        from telkap.services.userbot import manager

        manager._runtimes.clear()
        async with db_module.get_session() as db:
            person = await db.get(User, 5)
            if person is None:
                db.add(User(id=5, first_name="کاربر", session_enc="x"))
            else:
                person.session_enc = "x"
            await db.commit()

        task_id = await _make_task(db_module, 5, source_id=-321)
        _arm(5, -321)
        _fake_telegram(monkeypatch, entities={"@source": _Entity(), "@dest": _Entity()})
        await _plan_always_active(monkeypatch)

        report = await checkup.check_user(5)
        mine = [item for item in report.tasks if item.task_id == task_id][0]

        assert mine.state == checkup.OK, mine.problems
        assert mine.problems == []
    finally:
        manager._runtimes.clear()
        await db_module.close_db()


@pytest.mark.asyncio
async def test_losing_admin_in_the_destination_is_caught(tmp_path, monkeypatch):
    """<b>خرابی‌ای که در دیتابیس ما هیچ ردی ندارد.</b>

    دسترسی ادمین در کانال مقصد گرفته می‌شود و ما تا اولین ارسالِ
    شکست‌خورده خبردار نمی‌شویم. تشخیصی که فقط جدول‌ها را بخواند
    دقیقاً همین را از دست می‌دهد.
    """
    db_module, _ = await _setup(tmp_path, monkeypatch, settings={})
    try:
        from telkap.models import User
        from telkap.services import checkup
        from telkap.services.userbot import manager

        manager._runtimes.clear()
        async with db_module.get_session() as db:
            person = await db.get(User, 5)
            if person is None:
                db.add(User(id=5, first_name="کاربر", session_enc="x"))
            else:
                person.session_enc = "x"
            await db.commit()

        task_id = await _make_task(db_module, 5, source_id=-777)
        _arm(5, -777)
        _fake_telegram(monkeypatch, entities={
            "@source": _Entity(),
            "@dest": _Entity(admin=False),
        })
        await _plan_always_active(monkeypatch)

        report = await checkup.check_user(5)
        mine = [item for item in report.tasks if item.task_id == task_id][0]

        assert mine.state == checkup.BAD
        assert any("اجازه‌ی ارسال" in line for line in mine.problems)
    finally:
        manager._runtimes.clear()
        await db_module.close_db()


@pytest.mark.asyncio
async def test_a_vanished_source_is_named(tmp_path, monkeypatch):
    """کانال مبدأ حذف شده یا اکانت از آن بیرون افتاده."""
    db_module, _ = await _setup(tmp_path, monkeypatch, settings={})
    try:
        from telkap.models import User
        from telkap.services import checkup
        from telkap.services.userbot import manager

        manager._runtimes.clear()
        async with db_module.get_session() as db:
            person = await db.get(User, 5)
            if person is None:
                db.add(User(id=5, first_name="کاربر", session_enc="x"))
            else:
                person.session_enc = "x"
            await db.commit()

        task_id = await _make_task(db_module, 5, source_id=-888)
        _arm(5, -888)
        _fake_telegram(monkeypatch, entities={"@dest": _Entity()})  # مبدأ نیست
        await _plan_always_active(monkeypatch)

        report = await checkup.check_user(5)
        mine = [item for item in report.tasks if item.task_id == task_id][0]

        assert mine.state == checkup.BAD
        assert any("مبدأ" in line and "پیدا نشد" in line for line in mine.problems)
    finally:
        manager._runtimes.clear()
        await db_module.close_db()
