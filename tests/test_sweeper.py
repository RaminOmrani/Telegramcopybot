"""تست تورِ ایمنیِ مبدأها.

<b>چرا این ماژول هست.</b> کل سیستم روی یک فرض بنا بود: تلگرام هر پست
تازه را به‌صورت آپدیت می‌فرستد. وقتی آن فرض بشکند هیچ نشانه‌ای ندارد —
اتصال برقرار، هندلرها ثبت، تماس‌های خروجی سالم، و هیچ پستی نیاید.

<b>و چرا تستش سخت‌گیر است.</b> ابزاری که برای «نرسیدن پست» ساخته شده،
اگر خودش پستی را دوباره بفرستد بدتر از نبودنش است: کانال مشتری پر
می‌شود از تکرار. پس دو چیز جدا سنجیده می‌شود — پستِ جامانده گرفته
می‌شود، و پستی که قبلاً رفته <b>هرگز</b> دوباره نمی‌رود.
"""
from __future__ import annotations

import pytest

from tests.test_copier import FakeMessage, _setup


class _FakeClient:
    """کانالی با چند پست؛ iter_messages از جدید به قدیم می‌دهد."""

    def __init__(self, messages: list) -> None:
        self.messages = sorted(messages, key=lambda m: m.id, reverse=True)

    def iter_messages(self, chat_id, limit=None):
        items = self.messages[: limit or len(self.messages)]

        async def gen():
            for message in items:
                yield message

        return gen()


class _FakeManager:
    def __init__(self, client, user_id: int, chat_id: int, task_ids: list[int]) -> None:
        self.client = client
        self._runtimes = {user_id: object()}
        self._chat_id = chat_id
        self._task_ids = task_ids

    def listening_chats(self, user_id: int) -> list[int]:
        return [self._chat_id]

    def tasks_for_chat(self, user_id: int, chat_id: int) -> list[int]:
        return list(self._task_ids) if chat_id == self._chat_id else []

    async def ensure_client(self, user_id: int):
        return self.client


class _RecordingCopier:
    def __init__(self) -> None:
        self.seen: list[tuple[int, list[int]]] = []

    async def process(self, user_id: int, task_id: int, messages) -> bool:
        self.seen.append((task_id, [m.id for m in messages]))
        return True


async def _mark_sent(db_module, task_id: int, src_msg_id: int) -> None:
    from telkap.models import MessageMap

    async with db_module.get_session() as db:
        db.add(
            MessageMap(
                task_id=task_id,
                src_msg_id=src_msg_id,
                dst_msg_id=src_msg_id * 10,
                dest_chat="-1002",
            )
        )
        await db.commit()


@pytest.mark.asyncio
async def test_a_post_whose_update_never_arrived_is_picked_up(tmp_path, monkeypatch):
    """<b>کاری که این ماژول برایش ساخته شده.</b>"""
    db_module, task_id = await _setup(tmp_path, monkeypatch, settings={})
    try:
        from telkap.services.sweeper import Sweeper

        await _mark_sent(db_module, task_id, 100)

        client = _FakeClient([
            FakeMessage(id=100, message="قدیمی"),
            FakeMessage(id=101, message="جامانده"),
            FakeMessage(id=102, message="جامانده‌ی دوم"),
        ])
        copier = _RecordingCopier()
        sweeper = Sweeper(_FakeManager(client, 7, -1001, [task_id]), copier)

        await sweeper.run_once()

        assert [ids for _task, ids in copier.seen] == [[101], [102]], copier.seen
    finally:
        await db_module.close_db()


@pytest.mark.asyncio
async def test_a_post_that_already_went_is_never_sent_twice(tmp_path, monkeypatch):
    """<b>بدترین شکستِ ممکنِ این ابزار.</b>

    ابزاری که برای «نرسیدن پست» ساخته شده، اگر تکراری بفرستد کانال
    مشتری را خراب می‌کند — و آن خرابی دیده می‌شود، برخلاف پستِ نرسیده.
    """
    db_module, task_id = await _setup(tmp_path, monkeypatch, settings={})
    try:
        from telkap.services.sweeper import Sweeper

        for msg_id in (100, 101, 102):
            await _mark_sent(db_module, task_id, msg_id)

        client = _FakeClient([
            FakeMessage(id=100), FakeMessage(id=101), FakeMessage(id=102),
        ])
        copier = _RecordingCopier()
        sweeper = Sweeper(_FakeManager(client, 7, -1001, [task_id]), copier)

        await sweeper.run_once()

        assert copier.seen == [], copier.seen
    finally:
        await db_module.close_db()


@pytest.mark.asyncio
async def test_a_channel_with_no_history_is_left_alone(tmp_path, monkeypatch):
    """<b>ریختنِ آرشیو یک کانال در مقصد، بدترین کاری است که می‌شود کرد.</b>

    کاری که تا حالا هیچ پستی نفرستاده، نمی‌دانیم از کجایش «تازه»
    است. کپی گذشته کارِ خودش را دارد و کاربر خودش شروعش می‌کند.
    """
    db_module, task_id = await _setup(tmp_path, monkeypatch, settings={})
    try:
        from telkap.services.sweeper import Sweeper

        client = _FakeClient([FakeMessage(id=n) for n in range(1, 40)])
        copier = _RecordingCopier()
        sweeper = Sweeper(_FakeManager(client, 7, -1001, [task_id]), copier)

        await sweeper.run_once()

        assert copier.seen == [], "نباید آرشیو کانال ریخته شود"
    finally:
        await db_module.close_db()


@pytest.mark.asyncio
async def test_an_album_goes_as_one_piece(tmp_path, monkeypatch):
    """آلبومی که تکه‌تکه برود، در مقصد چند پستِ بی‌ربط می‌شود."""
    db_module, task_id = await _setup(tmp_path, monkeypatch, settings={})
    try:
        from telkap.services.sweeper import Sweeper

        await _mark_sent(db_module, task_id, 100)

        client = _FakeClient([
            FakeMessage(id=100),
            FakeMessage(id=101, grouped_id=55),
            FakeMessage(id=102, grouped_id=55),
            FakeMessage(id=103, grouped_id=55),
        ])
        copier = _RecordingCopier()
        sweeper = Sweeper(_FakeManager(client, 7, -1001, [task_id]), copier)

        await sweeper.run_once()

        assert [ids for _task, ids in copier.seen] == [[101, 102, 103]], copier.seen
    finally:
        await db_module.close_db()


@pytest.mark.asyncio
async def test_a_disabled_task_is_skipped(tmp_path, monkeypatch):
    """کارِ خاموش ممکن است هنوز در نقشه‌ی هندلرها باشد."""
    db_module, task_id = await _setup(tmp_path, monkeypatch, settings={})
    try:
        from telkap.models import Task
        from telkap.services.sweeper import Sweeper

        await _mark_sent(db_module, task_id, 100)
        async with db_module.get_session() as db:
            row = await db.get(Task, task_id)
            row.enabled = False
            await db.commit()

        client = _FakeClient([FakeMessage(id=100), FakeMessage(id=101)])
        copier = _RecordingCopier()
        sweeper = Sweeper(_FakeManager(client, 7, -1001, [task_id]), copier)

        await sweeper.run_once()

        assert copier.seen == []
    finally:
        await db_module.close_db()


async def _second_task(db_module) -> int:
    from telkap.models import Task

    async with db_module.get_session() as db:
        task = Task(
            user_id=7, title="کار دوم",
            source_ref="@src", source_id=-1001,
            dest_ref="@other", dest_id=-1003,
            settings={},
        )
        db.add(task)
        await db.commit()
        await db.refresh(task)
        return task.id


@pytest.mark.asyncio
async def test_a_task_that_lags_behind_its_neighbour_still_catches_up(
    tmp_path, monkeypatch
):
    """<b>باگی که تستِ قبلی نمی‌گرفت و همین‌جا پیدا شد.</b>

    دو کار روی یک مبدأ، یکی جلوتر و یکی عقب‌مانده. نسخه‌ی اول یک
    نشانه‌ی مشترک برای کل مبدأ می‌گرفت — بزرگ‌ترینشان — و کارِ
    عقب‌مانده <b>برای همیشه</b> نادیده می‌ماند. یعنی همان چیزی که این
    ماژول قرار بود درستش کند.
    """
    db_module, ahead = await _setup(tmp_path, monkeypatch, settings={})
    try:
        from telkap.services.sweeper import Sweeper

        behind = await _second_task(db_module)
        await _mark_sent(db_module, ahead, 102)     # جلوتر
        await _mark_sent(db_module, behind, 100)    # عقب‌مانده

        client = _FakeClient([
            FakeMessage(id=100), FakeMessage(id=101),
            FakeMessage(id=102), FakeMessage(id=103),
        ])
        copier = _RecordingCopier()
        sweeper = Sweeper(_FakeManager(client, 7, -1001, [ahead, behind]), copier)

        await sweeper.run_once()

        got = sorted(copier.seen)
        # کارِ عقب‌مانده هر سه پستِ جامانده‌اش را می‌گیرد
        assert (behind, [101]) in got, got
        assert (behind, [102]) in got, got
        assert (behind, [103]) in got, got
        # و کارِ جلوتر فقط همان یکی که واقعاً تازه است — نه ۱۰۱ و ۱۰۲
        assert [ids for task, ids in got if task == ahead] == [[103]], got
    finally:
        await db_module.close_db()


@pytest.mark.asyncio
async def test_the_map_is_consulted_even_above_the_watermark(tmp_path, monkeypatch):
    """<b>نگهبانِ دوم، برای وقتی که نشانه کافی نیست.</b>

    پستی که بالاتر از نشانه است ولی جدولِ نگاشت می‌گوید قبلاً رفته —
    مثلاً چون نشانه از یک مقصد دیگر آمده — نباید دوباره برود.
    """
    db_module, task_id = await _setup(tmp_path, monkeypatch, settings={})
    try:
        from telkap.services.sweeper import Sweeper

        await _mark_sent(db_module, task_id, 100)
        # ۱۰۲ رفته ولی ۱۰۱ نه؛ نشانه ۱۰۲ می‌شود، پس فقط ۱۰۳ می‌ماند
        await _mark_sent(db_module, task_id, 102)

        client = _FakeClient([
            FakeMessage(id=100), FakeMessage(id=101),
            FakeMessage(id=102), FakeMessage(id=103),
        ])
        copier = _RecordingCopier()
        sweeper = Sweeper(_FakeManager(client, 7, -1001, [task_id]), copier)

        await sweeper.run_once()

        assert [ids for _t, ids in copier.seen] == [[103]], copier.seen
    finally:
        await db_module.close_db()
