"""تست صف تلاش مجدد — جایی که یک حلقه‌ی بی‌پایان پنهان شده بود.

<b>چطور پیدا شد.</b> خلاصه‌ی روزانه‌ی یک کاربر واقعی این بود:

    کپی‌شده: ۶۶ ✅ · ردشده با فیلترها: ۲ · ناموفق: ۲۰۲۹ ⚠️

دو هزار شکست در برابر شصت‌وشش موفقیت، آن هم برای کانالی که روزی چند
ده پست دارد. عددی که با «گاهی شبکه قطع می‌شود» جور درنمی‌آید.

<b>علت.</b> کارگرِ صف، <code>copier.process()</code> را صدا می‌زد. اگر
ارسال باز هم شکست می‌خورد، خودِ <code>process</code> یک آیتم
<b>تازه</b> در صف می‌گذاشت — با شمارنده‌ی تلاشِ صفر — و کارگر بعدش
آیتمِ قدیمی را دور می‌ریخت، چون «False» را «فیلترها جلویش را گرفتند»
می‌خواند. یعنی هر دور، آیتم نو می‌شد و <code>MAX_ATTEMPTS</code>
هیچ‌وقت نمی‌رسید: یک پستِ نرفتنی، هر دقیقه، تا ابد.

و هزینه‌اش فقط یک عدد در گزارش نبود — هر تلاش یک تماس با تلگرام بود،
که اکانت را به محدودیت می‌رساند و <b>کارهای سالمِ دیگر</b> را هم
می‌خواباند.
"""
from __future__ import annotations

import pytest

from tests.test_copier import FakeClient, FakeManager, Notes, _setup


class _AlwaysFails(FakeClient):
    """کلاینتی که ارسالش همیشه شکست می‌خورد — مثل مقصدی که دسترسی ندارد."""

    def __init__(self) -> None:
        super().__init__()
        self.attempts = 0

    async def send_message(self, *args, **kwargs):
        self.attempts += 1
        raise RuntimeError("ارسال ممکن نشد")

    async def send_file(self, *args, **kwargs):
        self.attempts += 1
        raise RuntimeError("ارسال ممکن نشد")

    async def get_messages(self, chat_id, ids=None):
        from tests.test_copier import FakeMessage

        return [FakeMessage(id=i, message="سلام") for i in (ids or [])]


async def _queue_size(db_module, task_id: int) -> int:
    from sqlalchemy import func, select

    from telkap.models import RetryItem

    async with db_module.get_session() as db:
        return int(
            await db.scalar(
                select(func.count(RetryItem.id)).where(RetryItem.task_id == task_id)
            )
            or 0
        )


async def _failed_today(db_module, task_id: int) -> int:
    from sqlalchemy import func, select

    from telkap.models import DailyStat

    async with db_module.get_session() as db:
        return int(
            await db.scalar(
                select(func.coalesce(func.sum(DailyStat.failed), 0)).where(
                    DailyStat.task_id == task_id
                )
            )
            or 0
        )


@pytest.mark.asyncio
async def test_a_post_that_never_sends_is_eventually_given_up_on(tmp_path, monkeypatch):
    """<b>قلبِ ماجرا.</b>

    یک پست که هیچ‌وقت نمی‌رود باید بعد از چند تلاش رها شود و کاربر
    خبردار گردد — نه اینکه تا ابد هر دقیقه دوباره امتحان شود.
    """
    db_module, task_id = await _setup(tmp_path, monkeypatch, settings={})
    try:
        from telkap.models import RetryItem, utcnow
        from telkap.services.copier import Copier
        from telkap.services.retry import MAX_ATTEMPTS, RetryWorker

        client = _AlwaysFails()
        manager = FakeManager(client)
        notes = Notes()
        copier = Copier(manager, notifier=notes)
        worker = RetryWorker(manager, copier, notifier=notes)

        async with db_module.get_session() as db:
            db.add(
                RetryItem(
                    task_id=task_id,
                    user_id=7,
                    src_chat_id=-1001,
                    src_msg_ids="1",
                    dest_chat="-1002",
                    next_try_at=utcnow(),
                )
            )
            await db.commit()

        # هر دور، آیتم‌های سررسیدشده را بی‌درنگ سررسید می‌کنیم تا
        # چند تلاش را بدون انتظارِ واقعی بسنجیم
        for _ in range(MAX_ATTEMPTS + 3):
            await worker.run_once()
            async with db_module.get_session() as db:
                for row in (await db.execute(__import__("sqlalchemy").select(RetryItem))).scalars():
                    row.next_try_at = utcnow()
                await db.commit()

        assert await _queue_size(db_module, task_id) == 0, "صف باید خالی شده باشد"
        assert any("ارسال نشد" in text for _who, text in notes.messages), notes.messages
    finally:
        await db_module.close_db()


@pytest.mark.asyncio
async def test_a_failed_retry_does_not_create_a_second_queue_item(tmp_path, monkeypatch):
    """<b>دقیقاً همان چیزی که حلقه را می‌ساخت.</b>

    یک آیتم می‌رود تو، یک آیتم باید بیرون بیاید — نه دو تا، و نه
    یکی با شمارنده‌ی صفرشده.
    """
    db_module, task_id = await _setup(tmp_path, monkeypatch, settings={})
    try:
        from telkap.models import RetryItem, utcnow
        from telkap.services.copier import Copier
        from telkap.services.retry import RetryWorker

        client = _AlwaysFails()
        manager = FakeManager(client)
        copier = Copier(manager, notifier=Notes())
        worker = RetryWorker(manager, copier, notifier=Notes())

        async with db_module.get_session() as db:
            db.add(
                RetryItem(
                    task_id=task_id, user_id=7, src_chat_id=-1001,
                    src_msg_ids="1", dest_chat="-1002", next_try_at=utcnow(),
                )
            )
            await db.commit()

        await worker.run_once()

        assert await _queue_size(db_module, task_id) == 1, "نباید آیتم دومی ساخته شود"

        async with db_module.get_session() as db:
            row = (
                await db.execute(__import__("sqlalchemy").select(RetryItem))
            ).scalars().first()
        assert row.attempts == 1, "شمارنده‌ی تلاش باید بالا رفته باشد، نه صفر بماند"
    finally:
        await db_module.close_db()


@pytest.mark.asyncio
async def test_a_failed_retry_is_not_counted_as_a_brand_new_failure(tmp_path, monkeypatch):
    """<b>عددی که ما را به این باگ رساند.</b>

    تلاشِ ناموفق نباید دوباره در آمار «ناموفق» بنشیند؛ وگرنه یک پست
    به‌تنهایی هزاران شکست می‌سازد و گزارش روزانه بی‌معنی می‌شود.
    """
    db_module, task_id = await _setup(tmp_path, monkeypatch, settings={})
    try:
        from telkap.models import RetryItem, utcnow
        from telkap.services.copier import Copier
        from telkap.services.retry import RetryWorker

        client = _AlwaysFails()
        manager = FakeManager(client)
        copier = Copier(manager, notifier=Notes())
        worker = RetryWorker(manager, copier, notifier=Notes())

        async with db_module.get_session() as db:
            db.add(
                RetryItem(
                    task_id=task_id, user_id=7, src_chat_id=-1001,
                    src_msg_ids="1", dest_chat="-1002", next_try_at=utcnow(),
                )
            )
            await db.commit()

        before = await _failed_today(db_module, task_id)
        await worker.run_once()
        after = await _failed_today(db_module, task_id)

        assert after == before, f"شمارنده‌ی ناموفق از {before} به {after} رفت"
    finally:
        await db_module.close_db()


@pytest.mark.asyncio
async def test_a_task_whose_queue_keeps_growing_is_stopped(tmp_path, monkeypatch):
    """<b>مقصدِ خراب باید کار را بخواباند، نه تلگرام را بکوبد.</b>

    وقتی دسترسی ارسال گرفته شده، هر پستِ تازه‌ی مبدا یک آیتم دیگر
    اضافه می‌کند و هیچ‌کدام نمی‌روند. ادامه دادن اکانت را به محدودیت
    می‌رساند — و آن‌وقت کارهای سالمِ دیگر هم می‌خوابند.
    """
    db_module, task_id = await _setup(tmp_path, monkeypatch, settings={})
    try:
        from telkap.models import RetryItem, Task, utcnow
        from telkap.services.copier import MAX_RETRY_QUEUE, Copier

        notes = Notes()
        copier = Copier(FakeManager(_AlwaysFails()), notifier=notes)

        async with db_module.get_session() as db:
            for index in range(MAX_RETRY_QUEUE):
                db.add(
                    RetryItem(
                        task_id=task_id, user_id=7, src_chat_id=-1001,
                        src_msg_ids=str(index), dest_chat="-1002",
                        next_try_at=utcnow(),
                    )
                )
            await db.commit()

        await copier._enqueue_retry(
            task_id, 7, -1001, [999], "-1002", "دسترسی ارسال نیست"
        )

        async with db_module.get_session() as db:
            task = await db.get(Task, task_id)
        assert task.enabled is False, "کار باید متوقف شده باشد"

        # و صف بزرگ‌تر نشده
        assert await _queue_size(db_module, task_id) == MAX_RETRY_QUEUE
        assert any("متوقف شد" in text for _who, text in notes.messages), notes.messages
    finally:
        await db_module.close_db()
