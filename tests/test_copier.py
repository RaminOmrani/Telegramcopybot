"""تست موتور کپی با کلاینت و منیجر ساختگی."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field

import pytest


# --------------------------------------------------------------- ساختگی‌ها
@dataclass
class FakeMessage:
    id: int
    message: str = ""
    media: object | None = None
    fwd_from: object | None = None
    reply_markup: object | None = None
    grouped_id: int | None = None
    entities: list | None = None


@dataclass
class SentRecord:
    target: object
    text: str
    kind: str
    entities: object = None
    payload: object = None


@dataclass
class FakeSent:
    id: int


class FakeClient:
    def __init__(self) -> None:
        self.sent: list[SentRecord] = []
        self._next_id = 1000

    def _new_id(self) -> int:
        self._next_id += 1
        return self._next_id

    async def send_message(
        self, target, text, link_preview=True, buttons=None, formatting_entities=None
    ):
        self.sent.append(SentRecord(target, text, "text", formatting_entities))
        return FakeSent(self._new_id())

    async def send_file(
        self, target, files, caption=None, buttons=None, formatting_entities=None
    ):
        self.sent.append(
            SentRecord(target, caption or "", "file", formatting_entities, files)
        )
        count = len(files) if isinstance(files, list) else 1
        return [FakeSent(self._new_id()) for _ in range(count)]

    async def forward_messages(self, target, messages):
        self.sent.append(SentRecord(target, "<forward>", "forward"))
        return [FakeSent(self._new_id()) for _ in messages]

    async def edit_message(self, target, msg_id, text):
        self.sent.append(SentRecord(target, text, "edit"))

    async def delete_messages(self, target, ids):
        self.sent.append(SentRecord(target, str(ids), "delete"))


class FakeManager:
    def __init__(self, client: FakeClient) -> None:
        self.client = client
        self.reloaded: list[int] = []

    async def ensure_client(self, user_id: int):
        return self.client

    def tasks_for_chat(self, user_id: int, chat_id: int):
        return []

    async def reload_user(self, user_id: int) -> int:
        self.reloaded.append(user_id)
        return 0


@dataclass
class Notes:
    messages: list[tuple[int, str]] = field(default_factory=list)

    async def __call__(self, user_id: int, text: str) -> None:
        self.messages.append((user_id, text))


# ------------------------------------------------------------------ بستر
async def _setup(tmp_path, monkeypatch, *, settings: dict, rules: list = ()):
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{tmp_path}/copier.db")
    monkeypatch.setenv("DOWNLOAD_DIR", str(tmp_path / "dl"))

    import telkap.config as config

    config.settings = config.load_settings()

    from telkap import db as db_module
    from telkap.models import Rule, Task, User
    from telkap.services import subscription

    await db_module.init_db()
    async with db_module.get_session() as session:
        session.add(User(id=7, first_name="کاربر"))
        await session.commit()
    await subscription.grant(7, "month")

    async with db_module.get_session() as session:
        task = Task(
            user_id=7,
            title="کار",
            source_ref="@src",
            source_id=-1001,
            dest_ref="@dst",
            dest_id=-1002,
            settings=settings,
        )
        session.add(task)
        await session.commit()
        await session.refresh(task)
        task_id = task.id
        for kind, pattern, repl in rules:
            session.add(Rule(task_id=task_id, kind=kind, pattern=pattern, replacement=repl))
        await session.commit()
    return db_module, task_id


@pytest.mark.asyncio
async def test_copies_text_with_transforms(tmp_path, monkeypatch):
    db_module, task_id = await _setup(
        tmp_path,
        monkeypatch,
        settings={"remove_links": True, "footer": "@mychannel"},
        rules=[("replace", "الماس", "جواهر")],
    )
    try:
        from telkap.services.copier import Copier

        client = FakeClient()
        manager = FakeManager(client)
        copier = Copier(manager)

        msg = FakeMessage(id=5, message="فروش الماس در https://shop.example.com\n\n@srcchan")
        assert await copier.process(7, task_id, [msg]) is True

        assert len(client.sent) == 1
        record = client.sent[0]
        assert record.target == -1002
        assert "جواهر" in record.text
        assert "shop.example.com" not in record.text
        assert "@srcchan" not in record.text
        assert record.text.endswith("@mychannel")

        # شمارنده‌ی کار به‌روز شده است
        from telkap.models import Task

        async with db_module.get_session() as session:
            task = await session.get(Task, task_id)
            assert task.copied_count == 1
            assert task.skipped_count == 0
    finally:
        await db_module.close_db()


@pytest.mark.asyncio
async def test_blocked_post_is_not_sent(tmp_path, monkeypatch):
    db_module, task_id = await _setup(
        tmp_path, monkeypatch, settings={}, rules=[("block", "قمار", "")]
    )
    try:
        from telkap.models import Task
        from telkap.services.copier import Copier

        client = FakeClient()
        copier = Copier(FakeManager(client))

        assert await copier.process(7, task_id, [FakeMessage(id=1, message="سایت قمار")]) is False
        assert client.sent == []

        async with db_module.get_session() as session:
            task = await session.get(Task, task_id)
            assert task.skipped_count == 1
    finally:
        await db_module.close_db()


@pytest.mark.asyncio
async def test_duplicate_is_skipped(tmp_path, monkeypatch):
    db_module, task_id = await _setup(tmp_path, monkeypatch, settings={"skip_duplicates": True})
    try:
        from telkap.services.copier import Copier

        client = FakeClient()
        copier = Copier(FakeManager(client))

        assert await copier.process(7, task_id, [FakeMessage(id=1, message="خبر تکراری")]) is True
        # همان متن با آیدی متفاوت باید رد شود
        assert await copier.process(7, task_id, [FakeMessage(id=2, message="خبر تکراری")]) is False
        assert len(client.sent) == 1
    finally:
        await db_module.close_db()


@pytest.mark.asyncio
async def test_duplicates_allowed_when_disabled(tmp_path, monkeypatch):
    db_module, task_id = await _setup(tmp_path, monkeypatch, settings={"skip_duplicates": False})
    try:
        from telkap.services.copier import Copier

        client = FakeClient()
        copier = Copier(FakeManager(client))
        await copier.process(7, task_id, [FakeMessage(id=1, message="تکراری")])
        await copier.process(7, task_id, [FakeMessage(id=2, message="تکراری")])
        assert len(client.sent) == 2
    finally:
        await db_module.close_db()


@pytest.mark.asyncio
async def test_forward_mode_uses_forward_api(tmp_path, monkeypatch):
    db_module, task_id = await _setup(tmp_path, monkeypatch, settings={"mode": "forward"})
    try:
        from telkap.services.copier import Copier

        client = FakeClient()
        copier = Copier(FakeManager(client))
        assert await copier.process(7, task_id, [FakeMessage(id=1, message="متن")]) is True
        assert client.sent[0].kind == "forward"
    finally:
        await db_module.close_db()


@pytest.mark.asyncio
async def test_expired_subscription_pauses_task(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{tmp_path}/exp.db")
    monkeypatch.setenv("TRIAL_DAYS", "0")

    import telkap.config as config

    config.settings = config.load_settings()

    from telkap import db as db_module
    from telkap.models import Task, User
    from telkap.services.copier import Copier

    await db_module.init_db()
    try:
        async with db_module.get_session() as session:
            session.add(User(id=9, first_name="بی‌اشتراک"))
            await session.commit()
            task = Task(user_id=9, source_ref="@a", dest_ref="@b", settings={})
            session.add(task)
            await session.commit()
            await session.refresh(task)
            task_id = task.id

        client = FakeClient()
        manager = FakeManager(client)
        notes = Notes()
        copier = Copier(manager, notifier=notes)

        assert await copier.process(9, task_id, [FakeMessage(id=1, message="سلام")]) is False
        assert client.sent == []

        async with db_module.get_session() as session:
            task = await session.get(Task, task_id)
            assert task.enabled is False
            assert "اشتراک" in (task.last_error or "")

        assert notes.messages and notes.messages[0][0] == 9
        assert 9 in manager.reloaded
    finally:
        await db_module.close_db()


@pytest.mark.asyncio
async def test_media_kind_filter_blocks_video(tmp_path, monkeypatch):
    db_module, task_id = await _setup(
        tmp_path, monkeypatch, settings={"allowed_media": ["text", "photo"]}
    )
    try:
        from telkap.services.copier import Copier

        client = FakeClient()
        copier = Copier(FakeManager(client))

        # پیام بدون رسانه = متن، مجاز است
        assert await copier.process(7, task_id, [FakeMessage(id=1, message="متن")]) is True
        assert len(client.sent) == 1
    finally:
        await db_module.close_db()


@pytest.mark.asyncio
async def test_hourly_quota_blocks_extra_posts(tmp_path, monkeypatch):
    db_module, task_id = await _setup(tmp_path, monkeypatch, settings={
        "max_per_hour": 2, "skip_duplicates": False
    })
    try:
        from telkap.services.copier import Copier
        from telkap.services.ratelimit import hourly_quota

        hourly_quota.forget(task_id)
        client = FakeClient()
        copier = Copier(FakeManager(client))

        assert await copier.process(7, task_id, [FakeMessage(id=1, message="یک")]) is True
        assert await copier.process(7, task_id, [FakeMessage(id=2, message="دو")]) is True
        assert await copier.process(7, task_id, [FakeMessage(id=3, message="سه")]) is False
        assert len(client.sent) == 2
        hourly_quota.forget(task_id)
    finally:
        await db_module.close_db()


# ── تور ایمنی آلبوم ──────────────────────────────────────────────────
#
# پیام‌های یک آلبوم جدا جدا می‌رسند و تلethon آن‌ها را با یک تایمر
# نیم‌ثانیه‌ای کنار هم می‌گذارد. خودِ نویسنده‌ی تلethon در کد نوشته
# «این یک هک کثیف است»: کار تحویل با create_task رها می‌شود و کلاینت
# را با weakref نگه می‌دارد که خودش می‌گوید «ممکن است مرده باشد».
#
# تا امروز هندلر پیام تازه هر پیامِ گروه‌دار را دور می‌ریخت، پس برای
# آلبوم‌ها یک راه بیشتر نبود — و آن یک راه تضمینی نداشت.


class _Msg:
    def __init__(self, msg_id, group=None):
        self.id = msg_id
        self.grouped_id = group
        self.message = ""


class _Event:
    """رویداد ساختگی؛ همان چیزهایی که هندلر می‌خواند.

    عمداً <code>grouped_id</code> ندارد: هندلر باید آن را از خودِ پیام
    بخواند، نه از رویداد. تکیه بر واگذاریِ ضمنیِ صفت‌ها در تلethon
    یعنی اگر روزی عوض شود، هر آلبوم دوباره فرستاده می‌شود بی‌آنکه چیزی
    خطا بدهد.
    """

    def __init__(self, chat_id, message=None, messages=None):
        self.chat_id = chat_id
        self.message = message
        self.messages = messages or []


def _copier(monkeypatch):
    """یک Copier با _dispatch جایگزین‌شده تا فقط ببینیم چه رسید."""
    from telkap.services.copier import Copier

    copier = Copier(manager=None)
    got = []

    async def fake_dispatch(user_id, chat_id, messages):
        got.append((user_id, chat_id, [m.id for m in messages]))

    copier._dispatch = fake_dispatch
    return copier, got


@pytest.mark.asyncio
async def test_a_lone_message_goes_straight_through(monkeypatch):
    """پیام بدون گروه هیچ تأخیری نمی‌گیرد؛ کپی باید لحظه‌ای بماند."""
    copier, got = _copier(monkeypatch)
    handler = copier.make_new_message_handler(7)

    await handler(_Event(-100, message=_Msg(5)))

    assert got == [(7, -100, [5])]


@pytest.mark.asyncio
async def test_an_album_handled_normally_is_not_sent_twice(monkeypatch):
    """<b>مسیر عادی: هندلر آلبوم کارش را می‌کند.</b>

    تور ایمنی نباید همان آلبوم را دوباره بفرستد — پستِ تکراری در
    کانال مشتری از پستِ نرسیده هم بدتر است.
    """
    from telkap.services import copier as copier_mod

    monkeypatch.setattr(copier_mod, "ALBUM_SAFETY_SECONDS", 0.05)
    copier, got = _copier(monkeypatch)
    new_handler = copier.make_new_message_handler(7)
    album_handler = copier.make_album_handler(7)

    await new_handler(_Event(-100, message=_Msg(5, group=99)))
    await new_handler(_Event(-100, message=_Msg(6, group=99)))
    await album_handler(
        _Event(-100, messages=[_Msg(5, group=99), _Msg(6, group=99)])
    )
    await asyncio.sleep(0.2)

    assert got == [(7, -100, [5, 6])]


@pytest.mark.asyncio
async def test_an_album_the_handler_never_delivered_is_still_sent(monkeypatch):
    """<b>همان باگی که پست‌ها را گم می‌کرد.</b>

    اگر هندلر آلبوم نیاید — قطعیِ لحظه‌ای، وصل شدن دوباره، یا مردنِ
    weakref — قبلاً کل آلبوم برای همیشه گم می‌شد، بی‌هیچ خطایی و
    بی‌هیچ ردی در لاگ.
    """
    from telkap.services import copier as copier_mod

    monkeypatch.setattr(copier_mod, "ALBUM_SAFETY_SECONDS", 0.05)
    copier, got = _copier(monkeypatch)
    new_handler = copier.make_new_message_handler(7)

    await new_handler(_Event(-100, message=_Msg(6, group=99)))
    await new_handler(_Event(-100, message=_Msg(5, group=99)))
    await asyncio.sleep(0.2)

    # مرتب‌شده بر اساس آیدی، وگرنه ترتیب آلبوم در مقصد به هم می‌ریزد
    assert got == [(7, -100, [5, 6])]


@pytest.mark.asyncio
async def test_the_same_message_twice_is_only_kept_once(monkeypatch):
    """تلگرام گاهی یک به‌روزرسانی را دوباره می‌فرستد."""
    from telkap.services import copier as copier_mod

    monkeypatch.setattr(copier_mod, "ALBUM_SAFETY_SECONDS", 0.05)
    copier, got = _copier(monkeypatch)
    handler = copier.make_new_message_handler(7)

    await handler(_Event(-100, message=_Msg(5, group=99)))
    await handler(_Event(-100, message=_Msg(5, group=99)))
    await asyncio.sleep(0.2)

    assert got == [(7, -100, [5])]


@pytest.mark.asyncio
async def test_two_albums_at_once_do_not_mix(monkeypatch):
    """دو کانال، دو آلبوم، در یک لحظه — نباید در هم بروند."""
    from telkap.services import copier as copier_mod

    monkeypatch.setattr(copier_mod, "ALBUM_SAFETY_SECONDS", 0.05)
    copier, got = _copier(monkeypatch)
    handler = copier.make_new_message_handler(7)

    await handler(_Event(-100, message=_Msg(1, group=11)))
    await handler(_Event(-200, message=_Msg(2, group=22)))
    await handler(_Event(-100, message=_Msg(3, group=11)))
    await asyncio.sleep(0.2)

    assert sorted(got) == [(7, -200, [2]), (7, -100, [1, 3])] or sorted(got) == [
        (7, -100, [1, 3]),
        (7, -200, [2]),
    ]


@pytest.mark.asyncio
async def test_nothing_is_left_behind_in_memory(monkeypatch):
    """بافر و تایمر باید پاک شوند، وگرنه حافظه بی‌نهایت رشد می‌کند."""
    from telkap.services import copier as copier_mod

    monkeypatch.setattr(copier_mod, "ALBUM_SAFETY_SECONDS", 0.05)
    copier, _got = _copier(monkeypatch)
    handler = copier.make_new_message_handler(7)

    await handler(_Event(-100, message=_Msg(5, group=99)))
    await asyncio.sleep(0.2)

    assert copier._album_buffer == {}
    assert copier._album_timers == {}
