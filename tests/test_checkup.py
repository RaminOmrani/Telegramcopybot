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


class _WithPosts(_Client):
    """کلاینتی که آخرین پست مبدأ را در زمان دلخواه برمی‌گرداند."""

    def __init__(self, posted) -> None:
        self.posted = posted

    async def get_messages(self, entity, limit=1):
        from types import SimpleNamespace

        if self.posted is None:
            return []
        return [SimpleNamespace(date=self.posted)]


def _telegram_with_posts(monkeypatch, posted, *, entities=None):
    from telkap.services.userbot import manager

    client = _WithPosts(posted)

    async def ensure_client(user_id):
        return client

    async def resolve_entity(c, ref):
        return (entities or {"@source": _Entity(), "@dest": _Entity()}).get(ref)

    monkeypatch.setattr(manager, "ensure_client", ensure_client)
    monkeypatch.setattr(manager, "resolve_entity", staticmethod(resolve_entity))


async def _ready(db_module, monkeypatch, user_id: int = 5):
    from telkap.models import User

    async with db_module.get_session() as db:
        person = await db.get(User, user_id)
        if person is None:
            db.add(User(id=user_id, first_name="کاربر", session_enc="x"))
        else:
            person.session_enc = "x"
        await db.commit()
    await _plan_always_active(monkeypatch)


@pytest.mark.asyncio
async def test_a_quiet_source_is_not_reported_as_broken(tmp_path, monkeypatch):
    """<b>نیمی از سؤال‌هایی که پرسیده می‌شود همین است.</b>

    «سه روز است چیزی نیامده» اگر مبدأ هم سه روز ساکت بوده باشد، اصلاً
    خرابی نیست. بدون این مقایسه، هر بار باید حدس می‌زدیم.
    """
    from datetime import UTC, datetime, timedelta

    db_module, _ = await _setup(tmp_path, monkeypatch, settings={})
    try:
        from telkap.models import Task
        from telkap.services import checkup
        from telkap.services.userbot import manager

        manager._runtimes.clear()
        await _ready(db_module, monkeypatch)

        long_ago = datetime.now(UTC) - timedelta(days=3)
        task_id = await _make_task(db_module, 5, source_id=-555)
        async with db_module.get_session() as db:
            row = await db.get(Task, task_id)
            row.last_copy_at = long_ago
            await db.commit()

        _arm(5, -555)
        # مبدأ هم از همان موقع ساکت بوده
        _telegram_with_posts(monkeypatch, long_ago - timedelta(minutes=5))

        report = await checkup.check_user(5)
        mine = [item for item in report.tasks if item.task_id == task_id][0]

        assert mine.state == checkup.OK, mine.problems
        assert any("آخرین پست مبدأ" in note for note in mine.notes), mine.notes
    finally:
        manager._runtimes.clear()
        await db_module.close_db()


@pytest.mark.asyncio
async def test_a_source_that_posted_after_our_last_copy_is_flagged(tmp_path, monkeypatch):
    """و نیمه‌ی دیگر: مبدأ پست گذاشته و ما نگرفته‌ایم."""
    from datetime import UTC, datetime, timedelta

    db_module, _ = await _setup(tmp_path, monkeypatch, settings={})
    try:
        from telkap.models import Task
        from telkap.services import checkup
        from telkap.services.userbot import manager

        manager._runtimes.clear()
        await _ready(db_module, monkeypatch)

        now = datetime.now(UTC)
        task_id = await _make_task(db_module, 5, source_id=-556)
        async with db_module.get_session() as db:
            row = await db.get(Task, task_id)
            row.last_copy_at = now - timedelta(hours=6)
            await db.commit()

        _arm(5, -556)
        _telegram_with_posts(monkeypatch, now - timedelta(minutes=20))

        report = await checkup.check_user(5)
        mine = [item for item in report.tasks if item.task_id == task_id][0]

        assert mine.state == checkup.WARN, mine.problems
        assert any("بعد از آخرین کپی" in line for line in mine.problems)
    finally:
        manager._runtimes.clear()
        await db_module.close_db()


@pytest.mark.asyncio
async def test_a_task_that_never_copied_while_the_source_posts_is_broken(
    tmp_path, monkeypatch
):
    """<b>دقیقاً حالتی که در سرور واقعی دیدیم.</b>

    copied=0 و last_copy=None، در حالی که مبدأ پست دارد. این دیگر
    «مبدأ ساکت است» نیست.
    """
    from datetime import UTC, datetime, timedelta

    db_module, _ = await _setup(tmp_path, monkeypatch, settings={})
    try:
        from telkap.services import checkup
        from telkap.services.userbot import manager

        manager._runtimes.clear()
        await _ready(db_module, monkeypatch)

        task_id = await _make_task(db_module, 5, source_id=-557)
        _arm(5, -557)
        _telegram_with_posts(monkeypatch, datetime.now(UTC) - timedelta(minutes=10))

        report = await checkup.check_user(5)
        mine = [item for item in report.tasks if item.task_id == task_id][0]

        assert mine.state == checkup.BAD, mine.problems
        assert any("حتی یک پست کپی نکرده" in line for line in mine.problems)
    finally:
        manager._runtimes.clear()
        await db_module.close_db()
