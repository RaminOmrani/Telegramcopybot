"""تست پویشگرِ مبدأهای عمومی — موتورِ حالت ساده.

<b>چرا پویش.</b> برای کانالی که عضوش نیستیم تلگرام آپدیت نمی‌فرستد؛
اصلاً نمی‌داند به آن علاقه داریم. پس خودمان سر می‌زنیم. این با «جارو»
فرق دارد: جارو تورِ ایمنیِ یک مسیرِ دیگر است، این <b>خودِ مسیر</b>.

<b>و چرا سخت‌گیرترین تستِ اینجا درباره‌ی اولین اجراست.</b> اگر پویشگر
بار اول آنچه می‌بیند را بفرستد، کانال مشتری پر می‌شود از پست‌های
قدیمی — خرابی‌ای که پاک کردنش دستی است و اعتماد را همان لحظه می‌برد.
"""
from __future__ import annotations

import pytest

from tests.test_copier import FakeMessage, _setup


class _FakeClient:
    """کانالی با چند پست؛ iter_messages از جدید به قدیم می‌دهد."""

    def __init__(self, messages: list, error: Exception | None = None) -> None:
        self.messages = sorted(messages, key=lambda m: m.id, reverse=True)
        self.error = error

    def iter_messages(self, chat_id, limit=None):
        error = self.error
        items = self.messages[: limit or len(self.messages)]

        async def gen():
            if error is not None:
                raise error
            for message in items:
                yield message

        return gen()


class _RecordingCopier:
    def __init__(self) -> None:
        self.seen: list[tuple[int, int, list[int]]] = []
        self.vias: list[str] = []

    async def process(self, user_id, task_id, messages, **kwargs) -> bool:
        self.seen.append((user_id, task_id, [m.id for m in messages]))
        self.vias.append(kwargs.get("via", ""))
        return True


async def _simple(db_module, task_id: int, source_id: int = -1001) -> None:
    from telkap.models import Task

    async with db_module.get_session() as db:
        task = await db.get(Task, task_id)
        task.mode = Task.MODE_SIMPLE
        task.source_id = source_id
        await db.commit()


async def _account(db_module) -> int:
    from telkap.models import ServiceAccount

    async with db_module.get_session() as db:
        account = ServiceAccount(label="svc", session_enc="enc")
        db.add(account)
        await db.commit()
        await db.refresh(account)
        return account.id


def _wire(monkeypatch, client) -> None:
    from telkap.services import pool

    async def fake_client_for(account):
        return client

    monkeypatch.setattr(pool, "client_for", fake_client_for)


@pytest.mark.asyncio
async def test_the_very_first_poll_never_dumps_the_archive(tmp_path, monkeypatch):
    """<b>سخت‌گیرترین تستِ این ماژول.</b>

    کارِ تازه‌ای ساخته می‌شود و پویشگر بیست‌وپنج پستِ قدیمیِ کانال را
    می‌بیند. اگر بفرستدشان، کانال مشتری پر می‌شود از محتوای هفته‌ی
    پیش — و پاک کردنش دستی است.
    """
    db_module, task_id = await _setup(tmp_path, monkeypatch, settings={})
    try:
        from telkap.services.pubpoll import PublicPoller

        await _simple(db_module, task_id)
        await _account(db_module)

        client = _FakeClient([FakeMessage(id=i, message=f"قدیمی {i}") for i in range(1, 26)])
        _wire(monkeypatch, client)

        copier = _RecordingCopier()
        assert await PublicPoller(copier).run_once() == 0
        assert copier.seen == [], "آرشیو در کانال مشتری ریخته شد"
    finally:
        await db_module.close_db()


@pytest.mark.asyncio
async def test_after_the_first_poll_new_posts_do_go(tmp_path, monkeypatch):
    """و بلافاصله بعدش باید کار کند — وگرنه «هیچ‌وقت آرشیو نریز» به
    «هیچ‌وقت چیزی نفرست» تبدیل شده."""
    db_module, task_id = await _setup(tmp_path, monkeypatch, settings={})
    try:
        from telkap.services.pubpoll import PublicPoller

        await _simple(db_module, task_id)
        await _account(db_module)

        client = _FakeClient([FakeMessage(id=10, message="قدیمی")])
        _wire(monkeypatch, client)
        copier = _RecordingCopier()
        poller = PublicPoller(copier)

        await poller.run_once()            # اولین دیدار: فقط نشانه

        client.messages = sorted(
            [FakeMessage(id=10, message="قدیمی"), FakeMessage(id=11, message="تازه")],
            key=lambda m: m.id, reverse=True,
        )
        assert await poller.run_once() == 1
        assert [ids for _, _, ids in copier.seen] == [[11]]
    finally:
        await db_module.close_db()


@pytest.mark.asyncio
async def test_a_post_is_never_sent_twice(tmp_path, monkeypatch):
    db_module, task_id = await _setup(tmp_path, monkeypatch, settings={})
    try:
        from telkap.services.pubpoll import PublicPoller

        await _simple(db_module, task_id)
        await _account(db_module)

        client = _FakeClient([FakeMessage(id=10, message="قدیمی")])
        _wire(monkeypatch, client)
        copier = _RecordingCopier()
        poller = PublicPoller(copier)
        await poller.run_once()

        client.messages = sorted(
            [FakeMessage(id=10), FakeMessage(id=11, message="تازه")],
            key=lambda m: m.id, reverse=True,
        )
        await poller.run_once()
        await poller.run_once()

        assert len(copier.seen) == 1, "همان پست دو بار رفت"
    finally:
        await db_module.close_db()


@pytest.mark.asyncio
async def test_the_post_is_credited_to_the_task_owner(tmp_path, monkeypatch):
    """<b>سهمیه و گزارش به همین بند است.</b>

    نسخه‌ی اول صاحبِ کار را از یک کشِ جداگانه می‌خواند که باید دستی
    تازه می‌شد. کشِ کهنه یعنی پست به حسابِ کاربرِ اشتباه — یا کاربر
    صفر، که یعنی هیچ‌کس.
    """
    db_module, task_id = await _setup(tmp_path, monkeypatch, settings={})
    try:
        from telkap.services.pubpoll import PublicPoller

        await _simple(db_module, task_id)
        await _account(db_module)

        client = _FakeClient([FakeMessage(id=10)])
        _wire(monkeypatch, client)
        copier = _RecordingCopier()
        poller = PublicPoller(copier)
        await poller.run_once()

        client.messages = [FakeMessage(id=11), FakeMessage(id=10)]
        await poller.run_once()

        assert copier.seen[0][0] == 7, "پست به حساب صاحبِ کار نخورد"
    finally:
        await db_module.close_db()


@pytest.mark.asyncio
async def test_one_broken_post_does_not_block_everything_behind_it(
    tmp_path, monkeypatch
):
    """<b>انتخابی که هر دو طرفش هزینه دارد.</b>

    اگر نشانه پشتِ پستی که ارسالش شکسته بماند، هر دقیقه همان یکی
    دوباره تلاش می‌شود و <b>همه‌ی پست‌های بعدی پشتش گیر می‌کنند</b> —
    همان حلقه‌ی بی‌پایانی که یک بار گرفتارش شدیم، فقط این بار کلِ
    کانال را هم می‌خواباند.

    پس نشانه جلو می‌رود. هزینه‌اش این است که تلاشِ مجدد باید <b>داخل
    موتور کپی</b> انجام شود، نه اینجا: موتور خودش شکستِ ارسال را در
    صف می‌گذارد. چیزی که اینجا می‌ترکد یعنی اشکالِ برنامه، و آن باید
    در لاگ فریاد بزند نه اینکه تا ابد تکرار شود.
    """
    db_module, task_id = await _setup(tmp_path, monkeypatch, settings={})
    try:
        from sqlalchemy import select

        from telkap.models import SourceLease
        from telkap.services.pubpoll import PublicPoller

        await _simple(db_module, task_id)
        await _account(db_module)

        client = _FakeClient([FakeMessage(id=10)])
        _wire(monkeypatch, client)

        class _Exploding(_RecordingCopier):
            async def process(self, *args, **kwargs):
                raise RuntimeError("مقصد جواب نداد")

        poller = PublicPoller(_Exploding())
        await poller.run_once()          # نشانه روی ۱۰

        client.messages = [FakeMessage(id=11), FakeMessage(id=10)]
        await poller.run_once()          # ارسال ۱۱ می‌ترکد

        async with db_module.get_session() as db:
            lease = await db.scalar(select(SourceLease))
        # نشانه جلو می‌رود (وگرنه هر دقیقه همان پستِ خراب دوباره تلاش
        # می‌شود و بقیه پشتش می‌مانند) — ولی پست در صف تلاش مجدد
        # موتور کپی می‌نشیند، نه اینکه ناپدید شود.
        assert lease.seen_msg_id == 11
    finally:
        await db_module.close_db()


@pytest.mark.asyncio
async def test_an_album_stays_together(tmp_path, monkeypatch):
    db_module, task_id = await _setup(tmp_path, monkeypatch, settings={})
    try:
        from telkap.services.pubpoll import PublicPoller

        await _simple(db_module, task_id)
        await _account(db_module)

        client = _FakeClient([FakeMessage(id=10)])
        _wire(monkeypatch, client)
        copier = _RecordingCopier()
        poller = PublicPoller(copier)
        await poller.run_once()

        client.messages = [
            FakeMessage(id=13, grouped_id=99),
            FakeMessage(id=12, grouped_id=99),
            FakeMessage(id=11, grouped_id=99),
            FakeMessage(id=10),
        ]
        await poller.run_once()

        assert len(copier.seen) == 1, "آلبوم تکه‌تکه رفت"
        assert copier.seen[0][2] == [11, 12, 13]
    finally:
        await db_module.close_db()


@pytest.mark.asyncio
async def test_the_delivery_is_tagged_as_simple_not_as_a_broken_stream(
    tmp_path, monkeypatch
):
    """<b>چرا برچسبش جدا از «جارو» است.</b>

    `sweep_share` ساخته شد که بگوید «آپدیت‌ها نمی‌رسند» — یک خرابی.
    در حالت ساده آپدیتی در کار نیست و پویش خودِ طراحی است؛ اگر در
    همان کاسه بریزد، آن هشدار با رشدِ حالت ساده بی‌معنا می‌شود.
    """
    db_module, task_id = await _setup(tmp_path, monkeypatch, settings={})
    try:
        from telkap.models import DeliveryTiming
        from telkap.services.pubpoll import PublicPoller

        await _simple(db_module, task_id)
        await _account(db_module)

        client = _FakeClient([FakeMessage(id=10)])
        _wire(monkeypatch, client)
        copier = _RecordingCopier()
        poller = PublicPoller(copier)
        await poller.run_once()

        client.messages = [FakeMessage(id=11), FakeMessage(id=10)]
        await poller.run_once()

        assert copier.vias == [DeliveryTiming.VIA_SIMPLE]
        assert DeliveryTiming.VIA_SIMPLE not in DeliveryTiming.VIA_LIVE
    finally:
        await db_module.close_db()


@pytest.mark.asyncio
async def test_a_full_mode_task_is_left_alone(tmp_path, monkeypatch):
    """پویشگر نباید به کارهای اکانت‌دار دست بزند؛ وگرنه هر پست دو بار
    می‌رود — یک بار با اکانت، یک بار با ربات."""
    db_module, task_id = await _setup(tmp_path, monkeypatch, settings={})
    try:
        from telkap.services.pubpoll import PublicPoller

        await _account(db_module)
        _wire(monkeypatch, _FakeClient([FakeMessage(id=10)]))

        copier = _RecordingCopier()
        assert await PublicPoller(copier).run_once() == 0
        assert copier.seen == []
    finally:
        await db_module.close_db()


@pytest.mark.asyncio
async def test_a_flood_wait_parks_the_account_instead_of_hammering_it(
    tmp_path, monkeypatch
):
    db_module, task_id = await _setup(tmp_path, monkeypatch, settings={})
    try:
        from telethon.errors import FloodWaitError

        from telkap.models import ServiceAccount
        from telkap.services.pubpoll import PublicPoller

        await _simple(db_module, task_id)
        account_id = await _account(db_module)

        error = FloodWaitError(request=None)
        error.seconds = 300
        _wire(monkeypatch, _FakeClient([], error=error))

        await PublicPoller(_RecordingCopier()).run_once()

        async with db_module.get_session() as db:
            account = await db.get(ServiceAccount, account_id)
        assert account.state == ServiceAccount.STATE_FLOOD
        assert account.quiet_until is not None
    finally:
        await db_module.close_db()


@pytest.mark.asyncio
async def test_with_no_service_account_the_admin_is_told(tmp_path, monkeypatch):
    """<b>ساکت‌ترین خرابیِ ممکن.</b>

    بدون خواننده، کارهای حالت ساده هیچ پستی نمی‌فرستند — و مشتری هیچ
    خطایی نمی‌بیند، فقط کانالش خالی می‌ماند. از بیرون شبیه «سرویس کند
    است» به نظر می‌رسد، نه شبیه «زیرساخت نداریم». لاگ کافی نیست: کسی
    دنبال لاگ نمی‌گردد وقتی نمی‌داند چیزی خراب است.

    و حلقه هم نباید بمیرد، وگرنه اضافه کردنِ اکانت هم دیگر کمکی
    نمی‌کند.
    """
    db_module, task_id = await _setup(tmp_path, monkeypatch, settings={})
    try:
        from telkap.services import alerts
        from telkap.services.pubpoll import PublicPoller

        await _simple(db_module, task_id)
        alerts.reset()
        said: list[str] = []

        async def fake_send(text, **kwargs):
            said.append(text)
            return 1

        monkeypatch.setattr(alerts, "send", fake_send)

        copier = _RecordingCopier()
        assert await PublicPoller(copier).run_once() == 0
        assert said, "هیچ‌کس خبردار نشد که خواننده‌ای نداریم"
        assert "/pool" in said[0], "گفته نشد چه کار باید کرد"
    finally:
        await db_module.close_db()
