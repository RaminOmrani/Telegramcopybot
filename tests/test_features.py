"""تست قابلیت‌های جدید: زمان‌بندی، دکمه‌ها، چند مقصد، صف تلاش مجدد،
آمار روزانه، پرداخت و یادآوری انقضا."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import timedelta

import pytest

from tests.test_copier import FakeClient, FakeManager, FakeMessage, _setup


# --------------------------------------------------------------- زمان‌بندی
@pytest.mark.parametrize(
    "start,end,hour,expected",
    [
        (0, 0, 3, True),      # برابر بودن یعنی ۲۴ ساعته
        (8, 23, 12, True),
        (8, 23, 2, False),
        (8, 23, 23, False),   # پایان شامل نمی‌شود
        (8, 23, 8, True),     # شروع شامل می‌شود
        (22, 6, 23, True),    # بازه‌ی گذرنده از نیمه‌شب
        (22, 6, 3, True),
        (22, 6, 12, False),
    ],
)
def test_active_hours(start, end, hour, expected):
    from telkap.services.copier import within_active_hours

    cfg = {"active_from_hour": start, "active_to_hour": end}
    assert within_active_hours(cfg, hour=hour) is expected


# ----------------------------------------------------------------- دکمه‌ها
class FakeUrlButton:
    def __init__(self, text, url):
        self.text, self.url = text, url


class FakeCallbackButton:
    def __init__(self, text):
        self.text = text


@dataclass
class FakeRow:
    buttons: list


@dataclass
class FakeMarkup:
    rows: list


def test_extract_buttons_keeps_only_urls(monkeypatch):
    from telethon.tl.types import KeyboardButtonUrl

    from telkap.services import copier

    # دکمه‌ی URL ساختگی را به‌عنوان نوع واقعی جا می‌زنیم
    monkeypatch.setattr(copier, "KeyboardButtonUrl", FakeUrlButton)

    message = FakeMessage(id=1)
    message.reply_markup = FakeMarkup(
        rows=[
            FakeRow(buttons=[FakeUrlButton("سایت", "https://example.com")]),
            FakeRow(buttons=[FakeCallbackButton("رأی")]),  # باید نادیده گرفته شود
        ]
    )
    rows = copier.extract_buttons(message)
    assert rows is not None
    assert len(rows) == 1  # ردیف دکمه‌ی callback حذف شده
    assert KeyboardButtonUrl is not None  # ایمپورت واقعی هنوز معتبر است


def test_extract_buttons_none_when_no_markup():
    from telkap.services.copier import extract_buttons

    assert extract_buttons(FakeMessage(id=1)) is None


# ------------------------------------------------------------- چند مقصد
@pytest.mark.asyncio
async def test_copies_to_multiple_destinations(tmp_path, monkeypatch):
    db_module, task_id = await _setup(tmp_path, monkeypatch, settings={})
    try:
        from telkap.models import Destination
        from telkap.services.copier import Copier

        async with db_module.get_session() as session:
            session.add(Destination(task_id=task_id, chat_id=-2001, ref="@b", title="دو"))
            session.add(Destination(task_id=task_id, chat_id=-2002, ref="@c", title="سه"))
            await session.commit()

        client = FakeClient()
        copier = Copier(FakeManager(client))
        assert await copier.process(7, task_id, [FakeMessage(id=1, message="خبر")]) is True

        targets = [record.target for record in client.sent]
        assert targets == [-1002, -2001, -2002]
    finally:
        await db_module.close_db()


@pytest.mark.asyncio
async def test_disabled_destination_is_skipped(tmp_path, monkeypatch):
    db_module, task_id = await _setup(tmp_path, monkeypatch, settings={})
    try:
        from telkap.models import Destination
        from telkap.services.copier import Copier

        async with db_module.get_session() as session:
            session.add(
                Destination(task_id=task_id, chat_id=-2001, ref="@b", enabled=False)
            )
            await session.commit()

        client = FakeClient()
        copier = Copier(FakeManager(client))
        await copier.process(7, task_id, [FakeMessage(id=1, message="خبر")])
        assert [r.target for r in client.sent] == [-1002]
    finally:
        await db_module.close_db()


@pytest.mark.asyncio
async def test_out_of_hours_is_skipped(tmp_path, monkeypatch):
    db_module, task_id = await _setup(
        tmp_path, monkeypatch, settings={"active_from_hour": 8, "active_to_hour": 9}
    )
    try:
        from telkap.services import copier as copier_module
        from telkap.services.copier import Copier

        # ساعت محلی را بیرون از بازه ثابت می‌کنیم
        monkeypatch.setattr(copier_module, "local_hour", lambda *a, **k: 15)

        client = FakeClient()
        copier = Copier(FakeManager(client))
        assert await copier.process(7, task_id, [FakeMessage(id=1, message="خبر")]) is False
        assert client.sent == []
    finally:
        await db_module.close_db()


# --------------------------------------------------------- صف تلاش مجدد
class BrokenClient(FakeClient):
    async def send_message(self, target, text, link_preview=True, buttons=None):
        raise RuntimeError("شبکه قطع است")


@pytest.mark.asyncio
async def test_failed_send_is_queued_for_retry(tmp_path, monkeypatch):
    db_module, task_id = await _setup(tmp_path, monkeypatch, settings={})
    try:
        from sqlalchemy import select

        from telkap.models import DailyStat, RetryItem
        from telkap.services.copier import Copier

        copier = Copier(FakeManager(BrokenClient()))
        assert await copier.process(7, task_id, [FakeMessage(id=42, message="خبر")]) is False

        async with db_module.get_session() as session:
            rows = await session.execute(select(RetryItem))
            items = list(rows.scalars())
            assert len(items) == 1
            assert items[0].message_ids == [42]
            assert items[0].task_id == task_id
            assert "شبکه" in items[0].last_error

            stat = (
                await session.execute(select(DailyStat).where(DailyStat.task_id == task_id))
            ).scalar_one()
            assert stat.failed == 1
    finally:
        await db_module.close_db()


@pytest.mark.asyncio
async def test_retry_worker_resends_and_clears_queue(tmp_path, monkeypatch):
    db_module, task_id = await _setup(tmp_path, monkeypatch, settings={})
    try:
        from sqlalchemy import select

        from telkap.models import RetryItem
        from telkap.services.copier import Copier
        from telkap.services.retry import RetryWorker

        # اول با کلاینت خراب صف را پر می‌کنیم
        broken = FakeManager(BrokenClient())
        await Copier(broken).process(7, task_id, [FakeMessage(id=42, message="خبر")])

        # حالا کلاینت سالم، و پیام مبدا دوباره در دسترس است
        client = FakeClient()

        class Manager(FakeManager):
            async def ensure_client(self, user_id):
                return client

        client.get_messages = _fake_get_messages  # type: ignore[attr-defined]
        manager = Manager(client)
        copier = Copier(manager)

        async with db_module.get_session() as session:
            item = (await session.execute(select(RetryItem))).scalar_one()
            item.next_try_at = item.created_at - timedelta(seconds=1)
            await session.commit()

        worker = RetryWorker(manager, copier)
        assert await worker.run_once() == 1

        async with db_module.get_session() as session:
            assert list((await session.execute(select(RetryItem))).scalars()) == []
        assert len(client.sent) == 1
    finally:
        await db_module.close_db()


async def _fake_get_messages(chat_id, ids=None):
    return [FakeMessage(id=i, message="خبر") for i in (ids or [])]


@pytest.mark.asyncio
async def test_retry_drops_item_when_source_deleted(tmp_path, monkeypatch):
    db_module, task_id = await _setup(tmp_path, monkeypatch, settings={})
    try:
        from sqlalchemy import select

        from telkap.models import RetryItem
        from telkap.services.copier import Copier
        from telkap.services.retry import RetryWorker

        await Copier(FakeManager(BrokenClient())).process(
            7, task_id, [FakeMessage(id=42, message="خبر")]
        )

        client = FakeClient()

        async def gone(chat_id, ids=None):
            return []

        client.get_messages = gone  # type: ignore[attr-defined]
        manager = FakeManager(client)

        async with db_module.get_session() as session:
            item = (await session.execute(select(RetryItem))).scalar_one()
            item.next_try_at = item.created_at - timedelta(seconds=1)
            await session.commit()

        worker = RetryWorker(manager, Copier(manager))
        assert await worker.run_once() == 0

        async with db_module.get_session() as session:
            assert list((await session.execute(select(RetryItem))).scalars()) == []
    finally:
        await db_module.close_db()


# ------------------------------------------------------------ آمار روزانه
@pytest.mark.asyncio
async def test_daily_stats_accumulate(tmp_path, monkeypatch):
    db_module, task_id = await _setup(
        tmp_path, monkeypatch, settings={"skip_duplicates": False}
    )
    try:
        from sqlalchemy import select

        from telkap.models import DailyStat
        from telkap.services.copier import Copier

        copier = Copier(FakeManager(FakeClient()))
        await copier.process(7, task_id, [FakeMessage(id=1, message="یک")])
        await copier.process(7, task_id, [FakeMessage(id=2, message="دو")])

        async with db_module.get_session() as session:
            stat = (
                await session.execute(select(DailyStat).where(DailyStat.task_id == task_id))
            ).scalar_one()
            assert stat.copied == 2
            assert stat.skipped == 0
    finally:
        await db_module.close_db()


# ---------------------------------------------------------------- پرداخت
@pytest.mark.asyncio
async def test_payment_flow_activates_subscription(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{tmp_path}/pay.db")
    monkeypatch.setenv("TRIAL_DAYS", "0")

    import telkap.config as config

    config.settings = config.load_settings()

    from telkap import db as db_module
    from telkap.models import PaymentRequest, User
    from telkap.services import payments, subscription

    await db_module.init_db()
    try:
        async with db_module.get_session() as session:
            session.add(User(id=77, first_name="خریدار"))
            await session.commit()

        assert await subscription.active_plan_for(77) is None

        request = await payments.create_request(77, "month")
        assert request is not None
        assert await payments.awaiting_receipt(77) is not None
        assert await payments.pending_requests() == []  # هنوز رسیدی ندارد

        await payments.attach_receipt(request.id, "FILEID", "photo")
        assert await payments.awaiting_receipt(77) is None
        assert len(await payments.pending_requests()) == 1

        approved, sub = await payments.approve(request.id, admin_id=1)
        assert approved.status == PaymentRequest.STATUS_APPROVED
        assert sub is not None
        plan = await subscription.active_plan_for(77)
        assert plan is not None and plan.code == "month"

        # تأیید دوباره نباید اشتراک اضافی بدهد
        again, _ = await payments.approve(request.id, admin_id=1)
        assert again is None
        assert await subscription.remaining_days(77) == 30
    finally:
        await db_module.close_db()


@pytest.mark.asyncio
async def test_payment_reject_does_not_grant(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{tmp_path}/pay2.db")
    monkeypatch.setenv("TRIAL_DAYS", "0")

    import telkap.config as config

    config.settings = config.load_settings()

    from telkap import db as db_module
    from telkap.models import User
    from telkap.services import payments, subscription

    await db_module.init_db()
    try:
        async with db_module.get_session() as session:
            session.add(User(id=88, first_name="رد"))
            await session.commit()

        request = await payments.create_request(88, "week")
        await payments.attach_receipt(request.id, "FILE", "photo")
        rejected = await payments.reject(request.id, admin_id=1, reason="نامعتبر")

        assert rejected.status == "rejected"
        assert await subscription.active_plan_for(88) is None
        assert await payments.pending_requests() == []
    finally:
        await db_module.close_db()


# ------------------------------------------------------- یادآوری انقضا
@dataclass
class Recorder:
    sent: list = field(default_factory=list)

    async def __call__(self, user_id: int, text: str) -> None:
        self.sent.append((user_id, text))


@pytest.mark.asyncio
async def test_expiry_reminder_sent_once(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{tmp_path}/rem.db")
    monkeypatch.setenv("TRIAL_DAYS", "0")

    import telkap.config as config

    config.settings = config.load_settings()

    from telkap import db as db_module
    from telkap.models import Subscription, User, utcnow
    from telkap.services import reminders

    await db_module.init_db()
    try:
        async with db_module.get_session() as session:
            session.add(User(id=99, first_name="نزدیک انقضا"))
            await session.commit()
            # ۲.۵ روز مانده → آستانه‌ی ۳ روز
            session.add(
                Subscription(
                    user_id=99,
                    plan_code="month",
                    expires_at=utcnow() + timedelta(days=2, hours=12),
                )
            )
            await session.commit()

        notes = Recorder()
        assert await reminders.run_once(notes) == 1
        assert notes.sent and notes.sent[0][0] == 99
        assert "تمام می‌شود" in notes.sent[0][1]

        # اجرای دوباره نباید پیام تکراری بفرستد
        assert await reminders.run_once(notes) == 0
        assert len(notes.sent) == 1
    finally:
        await db_module.close_db()


@pytest.mark.asyncio
async def test_no_reminder_when_far_from_expiry(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{tmp_path}/rem2.db")
    monkeypatch.setenv("TRIAL_DAYS", "0")

    import telkap.config as config

    config.settings = config.load_settings()

    from telkap import db as db_module
    from telkap.models import Subscription, User, utcnow
    from telkap.services import reminders

    await db_module.init_db()
    try:
        async with db_module.get_session() as session:
            session.add(User(id=100, first_name="خیلی مانده"))
            await session.commit()
            session.add(
                Subscription(
                    user_id=100, plan_code="month", expires_at=utcnow() + timedelta(days=20)
                )
            )
            await session.commit()

        notes = Recorder()
        assert await reminders.run_once(notes) == 0
        assert notes.sent == []
    finally:
        await db_module.close_db()


# ------------------------------------------------------------ پشتیبان‌گیری
@pytest.mark.asyncio
async def test_backup_creates_file(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{tmp_path}/bk.db")

    import telkap.config as config

    config.settings = config.load_settings()
    monkeypatch.setattr(config, "BASE_DIR", tmp_path)

    from telkap import db as db_module
    from telkap.services import backup

    monkeypatch.setattr(backup, "BASE_DIR", tmp_path)

    await db_module.init_db()
    try:
        path = backup.make_backup()
        assert path is not None and path.exists()
        assert path.stat().st_size > 0
    finally:
        await db_module.close_db()
