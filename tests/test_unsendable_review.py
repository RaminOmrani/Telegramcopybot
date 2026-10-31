"""بازبینیِ دوم: چیزهایی که اصلاحِ «پست‌های نرفتنی» خودش خراب کرده بود.

هر تست یکی از یافته‌های بازبینی است. بیشترشان پیش از این اصلاح قرمز
بودند؛ آن‌هایی که نبودند (نگهبان‌اند) در توضیحشان گفته شده.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from tests.test_copier import FakeManager, FakeMessage, FakeSent, Notes, _setup
from tests.test_unsendable import (
    Telethonish,
    _due,
    _events,
    _forwarding,
    _restricted,
    _rows,
    _two_destinations,
    _unsupported,
)


def _dice():
    from telethon.tl.types import MessageMediaDice

    return MessageMediaDice(value=3, emoticon="🎲")


# ------------------------------------------ ۱. کپشن، پیش از فوروارد


@pytest.mark.asyncio
async def test_a_post_that_takes_no_caption_is_forwarded_without_editing(
    tmp_path, monkeypatch
):
    """<b>تاس، استوری، بازی، موقعیتِ زنده کپشن نمی‌گیرند.</b> کاری که امضا
    یا فوتر داشت، فوروارد را می‌فرستاد، بعد ویرایش رد می‌شد و پست را پاک
    می‌کردیم: هیچ‌کدام از این پست‌ها هرگز نمی‌رسید."""
    db_module, task_id = await _setup(
        tmp_path, monkeypatch, settings={"footer": "@mine"}
    )
    try:
        from telkap.services.copier import Copier

        flags: list = []
        _forwarding(monkeypatch, flags=flags)
        client = Telethonish()
        client.edit_error = RuntimeError("MEDIA_CAPTION_NOT_ALLOWED")

        post = FakeMessage(id=1, message="", media=_dice())
        assert await Copier(FakeManager(client)).process(7, task_id, [post]) is True

        assert client.deleted == [], "پستِ رسیده پاک شد"
        assert client.edits == []
        assert flags == [False]
    finally:
        await db_module.close_db()


@pytest.mark.asyncio
async def test_an_unchanged_caption_comes_along_with_the_forward(tmp_path, monkeypatch):
    """متنِ ما همان متنِ مبدأ است: کپشن را نینداز تا دوباره بگذاری‌اش.
    هر ویرایش برچسبِ «ویرایش‌شده» می‌گذارد و یک درخواستِ اضافه است."""
    db_module, task_id = await _setup(tmp_path, monkeypatch, settings={})
    try:
        from telkap.services.copier import Copier

        flags: list = []
        _forwarding(monkeypatch, flags=flags)
        client = Telethonish()

        post = FakeMessage(id=1, message="خبر", media=_unsupported())
        assert await Copier(FakeManager(client)).process(7, task_id, [post]) is True

        assert flags == [False]
        assert client.edits == []
    finally:
        await db_module.close_db()


@pytest.mark.asyncio
async def test_an_identical_caption_on_edit_is_still_a_success(tmp_path, monkeypatch):
    db_module, task_id = await _setup(
        tmp_path, monkeypatch, settings={"footer": "@mine"}
    )
    try:
        from telethon.errors import MessageNotModifiedError

        from telkap.services.copier import Copier

        _forwarding(monkeypatch)
        client = Telethonish()
        client.edit_error = MessageNotModifiedError(request=None)

        post = FakeMessage(id=1, message="خبر", media=_unsupported())
        assert await Copier(FakeManager(client)).process(7, task_id, [post]) is True
        assert client.deleted == []
    finally:
        await db_module.close_db()


# ------------------------------- ۲. فقط ایرادِ محتوا «نرفتنی» است


@pytest.mark.asyncio
async def test_a_network_error_while_forwarding_goes_to_the_retry_queue(
    tmp_path, monkeypatch
):
    """هر خطای فوروارد «نرفتنی» نامیده می‌شد. قطعیِ یک‌لحظه‌ای شبکه یعنی
    پستی که برای همیشه رها شد."""
    db_module, task_id = await _setup(tmp_path, monkeypatch, settings={})
    try:
        from telkap.models import RetryItem
        from telkap.services.copier import Copier

        _forwarding(monkeypatch, error=RuntimeError("Request timed out"))
        post = FakeMessage(id=1, message="خبر", media=_unsupported())
        await Copier(FakeManager(Telethonish())).process(7, task_id, [post])

        assert len(await _rows(db_module, RetryItem)) == 1
        assert await _events(db_module, "unsendable") == []
    finally:
        await db_module.close_db()


@pytest.mark.asyncio
async def test_a_banned_account_while_forwarding_is_not_hidden(tmp_path, monkeypatch):
    """بن شدنِ اکانت باید همه‌ی کارها را متوقف کند. «نرفتنی» نامیدنش یعنی
    کار ادامه می‌داد و صاحب کانال هیچ‌وقت نمی‌فهمید."""
    db_module, task_id = await _setup(tmp_path, monkeypatch, settings={})
    try:
        from telethon.errors import UserDeactivatedBanError

        from telkap.services.copier import Copier

        _forwarding(monkeypatch, error=UserDeactivatedBanError(request=None))
        post = FakeMessage(id=1, message="خبر", media=_unsupported())
        with pytest.raises(UserDeactivatedBanError):
            await Copier(FakeManager(Telethonish())).process(7, task_id, [post])
        assert await _events(db_module, "unsendable") == []
    finally:
        await db_module.close_db()


@pytest.mark.asyncio
async def test_a_flood_wait_while_forwarding_reaches_the_caller(tmp_path, monkeypatch):
    db_module, task_id = await _setup(tmp_path, monkeypatch, settings={})
    try:
        from telethon.errors import FloodWaitError

        from telkap.services.copier import Copier

        _forwarding(monkeypatch, error=FloodWaitError(request=None, capture=30))
        post = FakeMessage(id=1, message="خبر", media=_unsupported())
        with pytest.raises(FloodWaitError):
            await Copier(FakeManager(Telethonish())).process(7, task_id, [post])
    finally:
        await db_module.close_db()


def test_missing_media_rights_is_not_permanent():
    """«ارسال رسانه ممنوع» یعنی ادمین دسترسی را گرفته — با پس دادنش درست
    می‌شود. دائمی شمردنش یعنی هر پستِ رسانه‌ای در این فاصله بی‌صدا رها
    می‌شد و صفِ تلاش مجدد (که کار را بعد از ۶۰ شکست متوقف و خبر می‌کند)
    هیچ‌وقت چیزی نمی‌دید."""
    from telethon.errors import ChatSendMediaForbiddenError

    from telkap.services.copier import is_permanent

    assert not is_permanent(ChatSendMediaForbiddenError(request=None))


# ------------------------------------------------- ۳. آلبومِ نصفه


@pytest.mark.asyncio
async def test_a_half_forwarded_album_is_removed_and_tried_again(tmp_path, monkeypatch):
    """جوابِ تلگرام جای پیامی که نرسید None است. فیلتر کردنش یعنی آلبومی
    که یکی از عکس‌هایش نیست «رسید» ثبت می‌شد."""
    db_module, task_id = await _setup(tmp_path, monkeypatch, settings={})
    try:
        from telkap.models import MessageMap, RetryItem
        from telkap.services.copier import Copier

        _forwarding(monkeypatch, missing={2})
        client = Telethonish()
        album = [
            FakeMessage(id=1, message="", media=_unsupported(), grouped_id=5),
            FakeMessage(id=2, message="", media=_unsupported(), grouped_id=5),
        ]
        assert await Copier(FakeManager(client)).process(7, task_id, album) is False

        assert client.deleted == [(-1002, [9000])], "نیمه‌ی رسیده در کانال ماند"
        assert len(await _rows(db_module, RetryItem)) == 1
        assert await _rows(db_module, MessageMap) == []
        assert await _events(db_module, "unsendable") == []
    finally:
        await db_module.close_db()


@pytest.mark.asyncio
async def test_the_caption_lands_on_the_album_item_that_had_it(tmp_path, monkeypatch):
    """(نگهبان.) در آلبوم کپشن لزوماً روی اولی نیست."""
    db_module, task_id = await _setup(
        tmp_path, monkeypatch, settings={"footer": "@mine"}
    )
    try:
        from telkap.services.copier import Copier

        _forwarding(monkeypatch)
        client = Telethonish()
        album = [
            FakeMessage(id=1, message="", media=_unsupported(), grouped_id=5),
            FakeMessage(id=2, message="خبر", media=_unsupported(), grouped_id=5),
        ]
        assert await Copier(FakeManager(client)).process(7, task_id, album) is True
        assert [msg_id for _t, msg_id, _x in client.edits] == [9001]
    finally:
        await db_module.close_db()


@pytest.mark.asyncio
async def test_the_raw_forward_request_keeps_order_and_gaps():
    """خودِ درخواستِ خام — که تا امروز هیچ تستی اجرایش نکرده بود."""
    from telethon.tl.functions.messages import ForwardMessagesRequest

    from telkap.services.copier import Copier

    class Raw:
        def __init__(self) -> None:
            self.requests: list = []

        async def get_input_entity(self, ref):
            return ref

        async def __call__(self, request):
            self.requests.append(request)
            return "updates"

        def _get_response_message(self, request, result, to_peer):
            assert result == "updates" and to_peer == -1002
            return [FakeSent(50), None]

    client = Raw()
    messages = [SimpleNamespace(id=1, peer_id=-1001), SimpleNamespace(id=2, peer_id=-1001)]
    found = await Copier._forward_without_caption(
        client, -1002, messages, drop_captions=False
    )

    (request,) = client.requests
    assert isinstance(request, ForwardMessagesRequest)
    assert request.drop_author is True
    assert request.drop_media_captions is False
    assert request.id == [1, 2]
    assert found[1] is None, "جای پیامِ نرسیده حذف شد"


# ------------------------- ۴. جارو: پستِ رهاشده، ردشده، منتظرِ تأیید


@pytest.mark.asyncio
async def test_a_post_waiting_for_approval_is_not_published_by_the_sweeper(
    tmp_path, monkeypatch
):
    """<b>تأیید دستی سه دقیقه دوام داشت.</b> جارو پستِ منتظرِ تأیید را (که
    «نرفته» است) دوباره می‌داد؛ صف «از قبل هست» می‌گفت و پست بی‌تأیید
    منتشر می‌شد."""
    db_module, task_id = await _setup(tmp_path, monkeypatch, settings={"approval": True})
    try:
        from telkap.services.copier import Copier

        client = Telethonish()
        copier = Copier(FakeManager(client))
        post = FakeMessage(id=1, message="خبر")

        assert await copier.process(7, task_id, [post]) is False   # در صف
        assert await copier.process(7, task_id, [post]) is False   # دوباره، مثل جارو
        assert client.sent == [], "پستِ منتظرِ تأیید بی‌تأیید منتشر شد"
    finally:
        await db_module.close_db()


@pytest.mark.asyncio
async def test_the_sweeper_skips_posts_that_are_in_the_approval_queue(tmp_path, monkeypatch):
    db_module, task_id = await _setup(tmp_path, monkeypatch, settings={})
    try:
        from telkap.models import PendingPost
        from telkap.services.sweeper import Sweeper
        from tests.test_sweeper import _FakeClient, _FakeManager, _mark_sent, _RecordingCopier

        await _mark_sent(db_module, task_id, 100)
        async with db_module.get_session() as db:
            db.add(PendingPost(
                task_id=task_id, user_id=7, src_chat_id=-1001, src_msg_ids="101,102",
            ))
            await db.commit()

        client = _FakeClient([
            FakeMessage(id=100), FakeMessage(id=101, grouped_id=9),
            FakeMessage(id=102, grouped_id=9), FakeMessage(id=103),
        ])
        copier = _RecordingCopier()
        await Sweeper(_FakeManager(client, 7, -1001, [task_id]), copier).run_once()

        assert [ids for _task, ids in copier.seen] == [[103]]
    finally:
        await db_module.close_db()


@pytest.mark.asyncio
async def test_a_rejected_post_does_not_come_back(tmp_path, monkeypatch):
    """«رد» عملاً «بعداً دوباره بپرس» بود: پستِ ردشده هم «نرفته» است و جارو
    سه دقیقه بعد دوباره در صفِ تأیید می‌نشاندش."""
    db_module, task_id = await _setup(tmp_path, monkeypatch, settings={})
    try:
        from telkap.models import PendingPost
        from telkap.services import abandoned, pending

        one = await pending.hold(
            task_id=task_id, user_id=7, src_chat_id=-1001, message_ids=[5],
            reason=PendingPost.REASON_APPROVAL,
        )
        await pending.hold(
            task_id=task_id, user_id=7, src_chat_id=-1001, message_ids=[6, 7],
            reason=PendingPost.REASON_APPROVAL,
        )
        await pending.reject(one)
        await pending.drop_task(task_id, reason=PendingPost.REASON_APPROVAL)

        assert await abandoned.is_marked(task_id, 5)
        assert await abandoned.is_marked(task_id, 6)
        assert await _rows(db_module, PendingPost) == []
    finally:
        await db_module.close_db()


@pytest.mark.asyncio
async def test_a_post_the_retry_queue_gave_up_on_is_remembered(tmp_path, monkeypatch):
    """<b>هر ۸۰ دقیقه یک دور.</b> پستِ رهاشده نه در نگاشت بود نه در صف؛
    جارو پیدایش می‌کرد و هر چهار تلاش از نو شروع می‌شد."""
    db_module, task_id = await _setup(tmp_path, monkeypatch, settings={})
    try:
        from telkap.models import RetryItem
        from telkap.services import abandoned
        from telkap.services.copier import Copier
        from telkap.services.retry import MAX_ATTEMPTS, RetryWorker

        async with db_module.get_session() as db:
            item = RetryItem(
                task_id=task_id, user_id=7, src_chat_id=-1001, src_msg_ids="41,42",
                dest_chat="-1002", attempts=MAX_ATTEMPTS - 1,
            )
            db.add(item)
            await db.commit()
            await db.refresh(item)

        manager = FakeManager(Telethonish())
        await RetryWorker(manager, Copier(manager))._reschedule(item, "timeout")

        assert await abandoned.is_marked(task_id, 41)
    finally:
        await db_module.close_db()


@pytest.mark.asyncio
async def test_an_unsendable_post_is_remembered_across_a_restart(tmp_path, monkeypatch):
    """حافظه‌ی پروسه با هر آپدیت پاک می‌شد؛ حالا دیتابیس."""
    db_module, task_id = await _setup(tmp_path, monkeypatch, settings={})
    try:
        from telkap.services import abandoned
        from telkap.services.copier import Copier

        _forwarding(monkeypatch, error=_restricted())
        await Copier(FakeManager(Telethonish())).process(
            7, task_id, [FakeMessage(id=77, media=_unsupported())]
        )
        assert await abandoned.is_marked(task_id, 77)
    finally:
        await db_module.close_db()


# ---------------------------------------------------- ۵. حساب‌وکتاب


@pytest.mark.asyncio
async def test_a_retry_that_had_already_arrived_is_not_counted_again(tmp_path, monkeypatch):
    """کپیِ موفق بار اول شمرده شده؛ تلاشِ مجددی که می‌بیند پست رسیده بود،
    کپیِ تازه‌ای نیست."""
    db_module, task_id = await _setup(tmp_path, monkeypatch, settings={})
    try:
        from telkap.models import DailyStat, DeliveryTiming, MessageMap, RetryItem
        from telkap.services.copier import Copier
        from telkap.services.retry import RetryWorker

        async with db_module.get_session() as db:
            db.add(MessageMap(task_id=task_id, src_msg_id=7, dst_msg_id=70, dest_chat="-1002"))
            db.add(RetryItem(
                task_id=task_id, user_id=7, src_chat_id=-1001,
                src_msg_ids="7", dest_chat="-1002",
            ))
            await db.commit()
        await _due(db_module)

        client = Telethonish()
        client.messages[7] = FakeMessage(id=7, message="خبر")
        manager = FakeManager(client)
        await RetryWorker(manager, Copier(manager)).run_once()

        stats = await _rows(db_module, DailyStat, task_id=task_id)
        assert sum(s.copied for s in stats) == 0, "کپیِ قبلی دوباره شمرده شد"
        assert await _rows(db_module, DeliveryTiming) == []
        assert await _rows(db_module, RetryItem) == []
    finally:
        await db_module.close_db()


@pytest.mark.asyncio
async def test_each_destination_that_could_not_take_a_post_is_one_failure(
    tmp_path, monkeypatch
):
    """صفِ تلاش مجدد برای هر مقصد یک شکست می‌شمارد؛ «نرفتنی» هم باید همین
    را بکند، وگرنه یک عدد دو معنی دارد."""
    db_module, task_id = await _setup(tmp_path, monkeypatch, settings={})
    try:
        from telkap.models import DailyStat
        from telkap.services.copier import Copier

        await _two_destinations(db_module, task_id)
        _forwarding(monkeypatch, error=_restricted())
        await Copier(FakeManager(Telethonish())).process(
            7, task_id, [FakeMessage(id=1, media=_unsupported())]
        )
        stats = await _rows(db_module, DailyStat, task_id=task_id)
        assert sum(s.failed for s in stats) == 2
    finally:
        await db_module.close_db()


async def _used_messages() -> int:
    from telkap.plans import FEAT_MESSAGES
    from telkap.services import cache, entitlement

    ent = await cache.get_entitlement(7)
    return await entitlement.used(ent.subscription_id, FEAT_MESSAGES)


@pytest.mark.asyncio
async def test_a_duplicate_does_not_spend_the_message_quota(tmp_path, monkeypatch):
    db_module, task_id = await _setup(
        tmp_path, monkeypatch, settings={"skip_duplicates": True}
    )
    try:
        from telkap.services.copier import Copier

        copier = Copier(FakeManager(Telethonish()))
        assert await copier.process(7, task_id, [FakeMessage(id=1, message="همان خبر")])
        assert not await copier.process(7, task_id, [FakeMessage(id=2, message="همان خبر")])
        assert await _used_messages() == 1
    finally:
        await db_module.close_db()


@pytest.mark.asyncio
async def test_a_flood_wait_gives_the_quota_back(tmp_path, monkeypatch):
    db_module, task_id = await _setup(tmp_path, monkeypatch, settings={})
    try:
        from telethon.errors import FloodWaitError

        from telkap.services.copier import Copier

        client = Telethonish()
        client.raise_on_send = FloodWaitError(request=None, capture=30)
        with pytest.raises(FloodWaitError):
            await Copier(FakeManager(client)).process(7, task_id, [FakeMessage(id=1, message="خبر")])
        assert await _used_messages() == 0
    finally:
        await db_module.close_db()


@pytest.mark.asyncio
async def test_no_account_gives_the_quota_back(tmp_path, monkeypatch):
    db_module, task_id = await _setup(tmp_path, monkeypatch, settings={})
    try:
        from telkap.services.copier import Copier

        class NoClient(FakeManager):
            async def ensure_client(self, user_id):
                return None

        await Copier(NoClient(Telethonish())).process(7, task_id, [FakeMessage(id=1, message="خبر")])
        assert await _used_messages() == 0
    finally:
        await db_module.close_db()


@pytest.mark.asyncio
async def test_a_retry_for_a_removed_destination_says_so(tmp_path, monkeypatch):
    db_module, task_id = await _setup(tmp_path, monkeypatch, settings={})
    try:
        from telkap.models import RetryItem
        from telkap.services.copier import Copier
        from telkap.services.retry import RetryWorker

        async with db_module.get_session() as db:
            db.add(RetryItem(
                task_id=task_id, user_id=7, src_chat_id=-1001,
                src_msg_ids="7", dest_chat="-9999",
            ))
            await db.commit()
        await _due(db_module)

        client = Telethonish()
        client.messages[7] = FakeMessage(id=7, message="خبر")
        manager = FakeManager(client)
        await RetryWorker(manager, Copier(manager)).run_once()

        assert client.sent == []
        assert await _events(db_module, "retry_dropped")
    finally:
        await db_module.close_db()


@pytest.mark.asyncio
async def test_the_approval_notice_escapes_the_task_title(tmp_path, monkeypatch):
    db_module, task_id = await _setup(tmp_path, monkeypatch, settings={"approval": True})
    try:
        from telkap.models import Task
        from telkap.services import cache
        from telkap.services.copier import Copier

        async with db_module.get_session() as db:
            (await db.get(Task, task_id)).title = "Barça & <Real>"
            await db.commit()
        cache.invalidate_task(task_id)

        notes = Notes()
        await Copier(FakeManager(Telethonish()), notifier=notes).process(
            7, task_id, [FakeMessage(id=1, message="خبر")]
        )
        text = notes.messages[0][1]
        assert "&lt;Real&gt;" in text and "<Real>" not in text
    finally:
        await db_module.close_db()


def test_cleanup_takes_a_single_path_as_a_path_not_as_letters(tmp_path):
    """(نگهبان.) پستِ پنج‌باره از همین‌جا بود: رشته روی حرف‌هایش پیمایش
    می‌شد."""
    from telkap.services.copier import _discard

    one = tmp_path / "a.bin"
    one.write_bytes(b"x")
    _discard(str(one))
    _discard(None)
    _discard([str(tmp_path / "missing")])
    assert not one.exists()
