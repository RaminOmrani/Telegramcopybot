"""پست‌هایی که نمی‌روند — و چیزهایی که نباید دوباره بروند.

<b>چرا این فایل.</b> ۳۱ اکتبر گزارش ۸۶ شکست نشان داد. همه یک پست بودند:
یک پست از نوعی که Telethonِ ما نمی‌شناسد (MessageMediaUnsupported).
پشتِ آن هفت ایراد بود که روی هم سوار شده بودند:

- نوعِ ناشناخته «فایل» حساب می‌شد و با فهرستِ خالی ارسالش امتحان می‌شد
  («Cannot use [] as file»)؛
- خطای دائمی چهار بار دیگر امتحان می‌شد؛
- جارو هر سه دقیقه همان پست را دوباره در صف می‌گذاشت (۸۶ بار)؛
- برای هر رهاشده یک پیام به صاحب کانال می‌رفت (۷۷ پیام)؛
- و در مسیرِ کناری: وقتی فقط یک فایل بود، پاک کردنِ آن بعد از یک ارسالِ
  <b>موفق</b> خطا می‌داد — و همان پست تا پنج بار در کانال تکرار می‌شد؛
- تلاش مجدد پست را به <b>همه‌ی</b> مقصدها دوباره می‌فرستاد؛
- و هر تلاش یک واحد سهمیه می‌سوزاند.

هر تست اینجا یکی از آنها را می‌سنجد، و پیش از اصلاح قرمز بوده است.
"""
from __future__ import annotations

from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from tests.test_copier import FakeClient, FakeManager, FakeMessage, FakeSent, Notes, _setup

# ------------------------------------------------------------------ بستر


def _photo():
    from telethon.tl.types import MessageMediaPhoto, Photo

    return MessageMediaPhoto(photo=Photo(
        id=1, access_hash=1, file_reference=b"", date=None, sizes=[], dc_id=2,
    ))


def _video():
    from telethon.tl.types import Document, DocumentAttributeVideo, MessageMediaDocument

    return MessageMediaDocument(document=Document(
        id=2, access_hash=1, file_reference=b"", date=None, mime_type="video/mp4",
        size=10, dc_id=2,
        attributes=[DocumentAttributeVideo(duration=1, w=10, h=10)],
    ))


def _unsupported():
    from telethon.tl.types import MessageMediaUnsupported

    return MessageMediaUnsupported()


# نوع‌هایی که تلگرام با مرجع نمی‌پذیرد. اینجا مستقل از کدِ زیرِ تست نوشته
# شده تا تست روی کدِ قدیمی هم همان رفتارِ واقعی را ببیند.
_NO_REFERENCE = {"MessageMediaUnsupported", "MessageMediaStory", "MessageMediaPaidMedia"}


class Telethonish(FakeClient):
    """جایی که مهم است، مثل Telethon رفتار می‌کند.

    - ارسال با مرجع را می‌شود رد کرد (کانالِ محافظت‌شده)؛
    - `send_file([])` همان TypeErrorِ Telethon را می‌دهد؛
    - دانلود فایلِ <b>واقعی</b> روی دیسک می‌سازد، تا بشود دید پاک شد یا نه؛
    - رسانه‌ای که چیزی برای دانلود ندارد None برمی‌گرداند، مثل Telethon.
    """

    def __init__(self, *, refuse_reference: bool = False) -> None:
        super().__init__()
        self.refuse_reference = refuse_reference
        self.fail_targets: set = set()
        self.raise_on_send: Exception | None = None
        self.undownloadable: set[int] = set()
        self.download_error_on: int | None = None
        self.downloads: list[Path] = []
        self.edits: list[tuple] = []
        self.deleted: list[tuple] = []
        self.edit_error: Exception | None = None
        self.messages: dict[int, FakeMessage] = {}

    def _gate(self, target) -> None:
        if self.raise_on_send is not None:
            raise self.raise_on_send
        if target in self.fail_targets:
            raise RuntimeError("Request timed out")

    async def send_message(
        self, target, text, link_preview=True, buttons=None, formatting_entities=None
    ):
        self._gate(target)
        return await super().send_message(
            target, text, link_preview=link_preview, buttons=buttons,
            formatting_entities=formatting_entities,
        )

    async def send_file(
        self, target, files, caption=None, buttons=None, formatting_entities=None,
        force_document=False,
    ):
        from telethon.errors import QuizCorrectAnswersEmptyError
        from telethon.tl.types import InputMediaPoll

        self._gate(target)
        if not files:
            # دقیقاً همان چیزی که Telethon می‌گوید (uploads.py)
            raise TypeError(f"Cannot use {files!r} as file")
        items = files if isinstance(files, list) else [files]
        for item in items:
            if isinstance(item, InputMediaPoll) and item.poll.quiz and not item.correct_answers:
                # همان قاعده‌ی سرور: آزمون بدون جوابِ درست ساخته نمی‌شود
                raise QuizCorrectAnswersEmptyError(request=None)
        by_reference = not all(isinstance(item, str | Path) for item in items)
        if by_reference and any(type(i).__name__ in _NO_REFERENCE for i in items):
            # مثل خودِ تلگرام: برای این نوع‌ها Telethon ورودیِ خالی می‌سازد
            # و سرور MEDIA_EMPTY می‌دهد
            from telethon.errors import MediaEmptyError

            raise MediaEmptyError(request=None)
        if by_reference and self.refuse_reference:
            raise RuntimeError("CHAT_FORWARDS_RESTRICTED")
        sent = await super().send_file(
            target, files, caption=caption, buttons=buttons,
            formatting_entities=formatting_entities,
        )
        # مثل Telethon: فایلِ تکی یک پیام برمی‌گرداند، فهرست یک فهرست
        return sent if isinstance(files, list) else sent[0]

    async def download_media(self, message, file=None):
        # <b>مستقل از کدِ زیرِ تست.</b> فیک نباید منطقِ تشخیص را از خودِ
        # ماژولی بگیرد که می‌سنجد — وگرنه روی کدِ قدیمی به‌جای ایرادِ
        # واقعی، خطای import می‌دهد و هیچ چیزی را ثابت نمی‌کند.
        media = getattr(message, "media", None)
        downloadable = (
            getattr(media, "photo", None) is not None
            or getattr(media, "document", None) is not None
        )
        if message.id == self.download_error_on:
            raise RuntimeError("connection reset while downloading")
        if message.id in self.undownloadable or not downloadable:
            return None
        path = Path(file) / f"m{message.id}.bin"
        path.write_bytes(b"x" * 8)
        self.downloads.append(path)
        return str(path)

    async def edit_message(self, target, msg_id, text, formatting_entities=None):
        if self.edit_error is not None:
            raise self.edit_error
        self.edits.append((target, msg_id, text))

    async def delete_messages(self, target, ids):
        self.deleted.append((target, list(ids)))

    async def get_messages(self, chat, ids=None):
        return [self.messages.get(i) for i in (ids or [])]

    def kinds_sent(self) -> list[str]:
        return [record.kind for record in self.sent]


def _forwarding(monkeypatch, *, error: Exception | None = None) -> list:
    """فورواردِ بی‌نام و بی‌کپشن — جایی که درخواستِ خامِ Telethon ساخته می‌شود."""
    from telkap.services import copier as copier_module

    calls: list = []

    async def fake(client, target, messages):
        calls.append((target, [m.id for m in messages]))
        if error is not None:
            raise error
        return [FakeSent(9000 + i) for i, _ in enumerate(messages)]

    # raising=False: روی کدِ قدیمی که این متد را ندارد هم تست اجرا می‌شود و
    # رفتارِ واقعی‌اش را نشان می‌دهد، نه AttributeError
    monkeypatch.setattr(
        copier_module.Copier, "_forward_without_caption", staticmethod(fake),
        raising=False,
    )
    return calls


async def _rows(db_module, model, **where):
    async with db_module.get_session() as db:
        query = select(model)
        for key, value in where.items():
            query = query.where(getattr(model, key) == value)
        return list((await db.execute(query)).scalars())


async def _events(db_module, event: str) -> list[str]:
    from telkap.models import ActivityLog

    return [row.detail for row in await _rows(db_module, ActivityLog, event=event)]


# --------------------------------------------- ۱. نوعِ ناشناخته: فوروارد


@pytest.mark.asyncio
async def test_an_unknown_post_type_is_forwarded_with_our_caption_not_theirs(
    tmp_path, monkeypatch
):
    """<b>خودِ پستِ ۳۱ اکتبر.</b>

    `MessageMediaUnsupported` یعنی تلگرام نوعی اضافه کرده که Telethonِ ما
    نمی‌شناسد. نه ارسال با مرجع کار می‌کند نه دانلود؛ ولی سرورِ تلگرام
    می‌تواند فورواردش کند. بی‌نام (تا «فورواردشده از» نخورد) و
    <b>بی‌کپشن</b> — کپشنِ مبدأ لینک و امضای کانالِ دیگری را دارد — و بعد
    کپشنِ خودمان، با همه‌ی تبدیل‌ها، رویش.
    """
    db_module, task_id = await _setup(
        tmp_path, monkeypatch, settings={"footer": "@mine"}
    )
    try:
        from telkap.models import MessageMap, RetryItem
        from telkap.services.copier import Copier

        forwards = _forwarding(monkeypatch)
        client = Telethonish()
        copier = Copier(FakeManager(client))
        post = FakeMessage(id=165031, message="خبر @rival", media=_unsupported())

        assert await copier.process(7, task_id, [post]) is True

        assert forwards == [(-1002, [165031])]
        assert len(client.edits) == 1, "کپشنِ خودمان روی پست ننشست"
        assert "@mine" in client.edits[0][2]
        assert await _rows(db_module, RetryItem) == [], "به صف تلاش مجدد رفت"
        assert await _rows(db_module, MessageMap, task_id=task_id), "نگاشت ثبت نشد"
    finally:
        await db_module.close_db()


@pytest.mark.asyncio
async def test_a_post_that_cannot_even_be_forwarded_is_dropped_once_not_retried(
    tmp_path, monkeypatch
):
    """<b>کانالِ محافظت‌شده فوروارد را هم نمی‌پذیرد.</b> آن‌وقت واقعاً راهی
    نیست — و تکرار هم راهی نمی‌سازد.

    تا امروز: صف، چهار تلاشِ دیگر در یک ساعت و ربع، و یک پیام برای هر
    پست. حالا: یک رویداد، یک شکست، هیچ صف.
    """
    db_module, task_id = await _setup(tmp_path, monkeypatch, settings={})
    try:
        from telkap.models import DailyStat, RetryItem
        from telkap.services.copier import Copier

        _forwarding(monkeypatch, error=RuntimeError("CHAT_FORWARDS_RESTRICTED"))
        copier = Copier(FakeManager(Telethonish()))

        post = FakeMessage(id=1, message="خبر", media=_unsupported())
        assert await copier.process(7, task_id, [post]) is False

        assert await _rows(db_module, RetryItem) == [], "خطای دائمی به صف رفت"
        assert len(await _events(db_module, "unsendable")) == 1
        stats = await _rows(db_module, DailyStat, task_id=task_id)
        assert sum(s.failed for s in stats) == 1, "شکست درست شمرده نشد"
    finally:
        await db_module.close_db()


@pytest.mark.asyncio
async def test_the_owner_hears_about_it_once_a_day_not_once_a_post(tmp_path, monkeypatch):
    """<b>۷۷ پیامِ «ارسال نشد» در یک روز.</b> کانالی که مرتب از یک نوعِ
    نرفتنی پست می‌گذارد، با این قاعده روزی یک خبر می‌گیرد نه صد تا."""
    db_module, task_id = await _setup(tmp_path, monkeypatch, settings={})
    try:
        from telkap.services.copier import Copier

        _forwarding(monkeypatch, error=RuntimeError("CHAT_FORWARDS_RESTRICTED"))
        notes = Notes()
        copier = Copier(FakeManager(Telethonish()), notifier=notes)

        for msg_id in (1, 2, 3):
            await copier.process(
                7, task_id, [FakeMessage(id=msg_id, message="", media=_unsupported())]
            )

        assert len(notes.messages) == 1, f"{len(notes.messages)} پیام برای یک نوع"
    finally:
        await db_module.close_db()


@pytest.mark.asyncio
async def test_if_our_caption_cannot_be_set_the_forwarded_copy_is_removed(
    tmp_path, monkeypatch
):
    """<b>نه نصفه.</b> پستِ فورواردشده بی‌کپشن است. اگر کپشنِ ما رویش
    ننشست، پستی در کانال مانده که متنش جا مانده — مشتری این را می‌بیند."""
    db_module, task_id = await _setup(
        tmp_path, monkeypatch, settings={"footer": "@mine"}
    )
    try:
        from telkap.services.copier import Copier

        _forwarding(monkeypatch)
        client = Telethonish()
        client.edit_error = RuntimeError("MESSAGE_EDIT_TIME_EXPIRED")
        copier = Copier(FakeManager(client))

        post = FakeMessage(id=1, message="خبر", media=_unsupported())
        assert await copier.process(7, task_id, [post]) is False
        assert client.deleted == [(-1002, [9000])], "پستِ بی‌کپشن در کانال ماند"
    finally:
        await db_module.close_db()


# ------------------------------------ ۲. پاک کردن، بعد از یک ارسالِ موفق


@pytest.mark.asyncio
async def test_a_single_photo_reupload_goes_once_and_its_file_is_deleted(
    tmp_path, monkeypatch
):
    """<b>پستِ پنج‌باره.</b>

    وقتی ارسال با مرجع رد می‌شد و فقط یک فایل دانلود شده بود، فهرست به
    یک رشته تبدیل می‌شد. ارسال <b>موفق</b> بود؛ بعد حلقه‌ی پاک‌سازی روی
    حرف‌های مسیر می‌چرخید و به «/» که می‌رسید خطا می‌داد. آن خطا جای
    نتیجه را می‌گرفت: پست شکست حساب می‌شد، دوباره می‌رفت، و تا پنج بار
    در کانال تکرار می‌شد — بی‌آنکه نگاشتی ثبت شود. فایل هم می‌ماند.
    """
    db_module, task_id = await _setup(tmp_path, monkeypatch, settings={})
    try:
        from telkap.models import MessageMap, RetryItem
        from telkap.services.copier import Copier

        client = Telethonish(refuse_reference=True)
        copier = Copier(FakeManager(client))
        post = FakeMessage(id=5, message="عکس", media=_photo())

        assert await copier.process(7, task_id, [post]) is True

        uploads = [r for r in client.sent if r.kind == "file" and isinstance(r.payload, str)]
        assert len(uploads) == 1, "پست بیش از یک بار رفت"
        assert await _rows(db_module, RetryItem) == [], "ارسالِ موفق به صف تلاش مجدد رفت"
        assert await _rows(db_module, MessageMap, task_id=task_id), "نگاشت ثبت نشد"
        assert client.downloads and not any(p.exists() for p in client.downloads), (
            "فایل دانلودشده روی دیسک ماند"
        )
    finally:
        await db_module.close_db()


@pytest.mark.asyncio
async def test_an_album_with_a_missing_item_is_never_sent_half(tmp_path, monkeypatch):
    """آلبومی که یکی از عکس‌هایش دانلود نشد، تا امروز ناقص فرستاده می‌شد
    و «رسید» ثبت می‌شد — هیچ‌وقت هم درست نمی‌شد. حالا یا کامل، یا فوروارد."""
    db_module, task_id = await _setup(tmp_path, monkeypatch, settings={})
    try:
        from telkap.services.copier import Copier

        forwards = _forwarding(monkeypatch)
        client = Telethonish(refuse_reference=True)
        client.undownloadable = {2}
        copier = Copier(FakeManager(client))
        album = [
            FakeMessage(id=1, message="آلبوم", media=_photo(), grouped_id=9),
            FakeMessage(id=2, message="", media=_photo(), grouped_id=9),
        ]

        await copier.process(7, task_id, album)

        partial = [
            r for r in client.sent if r.kind == "file"
            and (isinstance(r.payload, str) or (
                isinstance(r.payload, list) and all(isinstance(p, str) for p in r.payload)
                and len(r.payload) < 2
            ))
        ]
        assert partial == [], "آلبومِ ناقص فرستاده شد"
        assert forwards == [(-1002, [1, 2])], "سراغ تنها راهِ باقی‌مانده نرفت"
    finally:
        await db_module.close_db()


@pytest.mark.asyncio
async def test_a_download_that_breaks_midway_leaves_no_files_behind(tmp_path, monkeypatch):
    db_module, _ = await _setup(tmp_path, monkeypatch, settings={})
    try:
        from telkap.services.copier import Copier

        client = Telethonish()
        client.download_error_on = 2
        copier = Copier(FakeManager(client))
        album = [FakeMessage(id=1, media=_photo()), FakeMessage(id=2, media=_photo())]

        with pytest.raises(RuntimeError):
            await copier._download_all(client, album)

        assert client.downloads, "اصلاً چیزی دانلود نشد؛ تست چیزی نمی‌سنجد"
        assert not any(p.exists() for p in client.downloads), "فایلِ نیمه‌کاره ماند"
    finally:
        await db_module.close_db()


def test_cleanup_can_never_raise(tmp_path):
    """این تابع در finally بعد از یک ارسالِ <b>موفق</b> صدا می‌شود. هر
    خطایی از آن یعنی پستی که رفته «شکست» حساب شود."""
    from telkap.services.copier import _discard

    folder = tmp_path / "a-directory"
    folder.mkdir()
    _discard([str(folder)])                 # IsADirectoryError — بلعیده
    _discard(str(tmp_path / "x" / "y.jpg"))  # رشته، نه فهرست — روی حرف‌ها نمی‌چرخد
    _discard(None)


# --------------------------------------- ۳. خطای دائمی و موقت


@pytest.mark.asyncio
async def test_a_permanent_error_is_not_retried(tmp_path, monkeypatch):
    db_module, task_id = await _setup(tmp_path, monkeypatch, settings={})
    try:
        from telethon.errors import MediaEmptyError

        from telkap.models import RetryItem
        from telkap.services.copier import Copier

        client = Telethonish()
        client.raise_on_send = MediaEmptyError(request=None)
        copier = Copier(FakeManager(client))

        await copier.process(7, task_id, [FakeMessage(id=1, message="خبر")])

        assert await _rows(db_module, RetryItem) == []
        assert await _events(db_module, "unsendable")
    finally:
        await db_module.close_db()


@pytest.mark.asyncio
async def test_a_temporary_error_still_goes_to_the_retry_queue(tmp_path, monkeypatch):
    """<b>نگهبانِ طرفِ دیگر</b> (پیش از اصلاح هم سبز بود): اگر هر خطایی
    دائمی شمرده شود، یک قطعیِ چندثانیه‌ای شبکه پست را برای همیشه می‌برد."""
    db_module, task_id = await _setup(tmp_path, monkeypatch, settings={})
    try:
        from telkap.models import RetryItem
        from telkap.services.copier import Copier

        client = Telethonish()
        client.raise_on_send = RuntimeError("Request timed out")
        copier = Copier(FakeManager(client))

        await copier.process(7, task_id, [FakeMessage(id=1, message="خبر")])

        assert len(await _rows(db_module, RetryItem)) == 1
    finally:
        await db_module.close_db()


def test_a_cold_cache_is_not_mistaken_for_a_permanent_error():
    """«این کانال در کش نیست» با گرم شدنِ کش درست می‌شود — همان خانواده‌ای
    که حالت ساده را بعد از هر ری‌استارت می‌خواباند. دائمی شمردنش یعنی دور
    ریختنِ پست‌هایی که چند دقیقه بعد می‌رفتند."""
    from telkap.services.copier import is_permanent

    assert not is_permanent(ValueError("Could not find the input entity for PeerChannel"))
    assert is_permanent(TypeError("Cannot use [] as file"))


# ------------------------------------------------------------- ۴. جارو


@pytest.mark.asyncio
async def test_the_sweeper_does_not_queue_a_waiting_post_again(tmp_path, monkeypatch):
    """<b>۸۶ بار.</b> پستِ ناموفق نه در نگاشت است و نه زیرِ نشانه، پس جارو
    هر سه دقیقه دوباره پیدایش می‌کرد و یک آیتمِ تازه در صف می‌گذاشت."""
    db_module, task_id = await _setup(tmp_path, monkeypatch, settings={})
    try:
        from telkap.models import RetryItem
        from telkap.services.sweeper import Sweeper
        from tests.test_sweeper import _FakeClient, _FakeManager, _mark_sent, _RecordingCopier

        await _mark_sent(db_module, task_id, 100)
        async with db_module.get_session() as db:
            db.add(RetryItem(
                task_id=task_id, user_id=7, src_chat_id=-1001,
                src_msg_ids="101", dest_chat="-1002",
            ))
            await db.commit()

        client = _FakeClient([FakeMessage(id=100), FakeMessage(id=101)])
        copier = _RecordingCopier()
        await Sweeper(_FakeManager(client, 7, -1001, [task_id]), copier).run_once()

        assert copier.seen == [], "پستی که در صف است دوباره فرستاده شد"
    finally:
        await db_module.close_db()


@pytest.mark.asyncio
async def test_the_sweeper_leaves_alone_a_post_known_to_be_unsendable(tmp_path, monkeypatch):
    db_module, task_id = await _setup(tmp_path, monkeypatch, settings={})
    try:
        from telkap.services.sweeper import Sweeper
        from tests.test_sweeper import _FakeClient, _FakeManager, _mark_sent, _RecordingCopier

        await _mark_sent(db_module, task_id, 100)

        class _GaveUp(_RecordingCopier):
            def gave_up(self, task, msg_id):
                return msg_id == 101

        client = _FakeClient([FakeMessage(id=100), FakeMessage(id=101), FakeMessage(id=102)])
        copier = _GaveUp()
        await Sweeper(_FakeManager(client, 7, -1001, [task_id]), copier).run_once()

        assert [ids for _task, ids in copier.seen] == [[102]]
    finally:
        await db_module.close_db()


# ---------------------------------------------------- ۵. تلاش مجدد


async def _two_destinations(db_module, task_id: int) -> None:
    from telkap.models import Destination
    from telkap.services import cache

    async with db_module.get_session() as db:
        db.add(Destination(task_id=task_id, chat_id=-2001, ref="@b", title="دو"))
        await db.commit()
    cache.invalidate_task(task_id)


async def _due(db_module) -> None:
    from telkap.models import RetryItem, utcnow

    async with db_module.get_session() as db:
        for item in (await db.execute(select(RetryItem))).scalars():
            item.next_try_at = utcnow() - timedelta(seconds=1)
        await db.commit()


@pytest.mark.parametrize("skip_duplicates", [True, False])
@pytest.mark.asyncio
async def test_a_retry_goes_only_to_the_destination_that_failed(
    tmp_path, monkeypatch, skip_duplicates
):
    """<b>دو خرابی با یک علت.</b> تلاش مجدد به همه‌ی مقصدها می‌رفت:
    - با «تکراری‌ها رد شوند» (پیش‌فرض)، مقصدِ سالم پست را داشت، پس کلِ
      تلاش «تکراری» تشخیص داده و بی‌صدا دور ریخته می‌شد — مقصدِ خراب هرگز
      پست را نمی‌گرفت؛
    - بدونش، مقصدِ سالم همان پست را دوباره می‌گرفت.
    """
    db_module, task_id = await _setup(
        tmp_path, monkeypatch, settings={"skip_duplicates": skip_duplicates}
    )
    try:
        from telkap.services.copier import Copier
        from telkap.services.retry import RetryWorker

        await _two_destinations(db_module, task_id)
        client = Telethonish()
        manager = FakeManager(client)
        copier = Copier(manager)
        post = FakeMessage(id=7, message="خبر")
        client.messages[7] = post

        client.fail_targets = {-2001}
        await copier.process(7, task_id, [post])
        client.fail_targets = set()
        await _due(db_module)

        await RetryWorker(manager, copier).run_once()

        sends = [r.target for r in client.sent if r.kind == "text"]
        assert sends.count(-1002) == 1, "مقصدِ سالم پست را دوباره گرفت"
        assert sends.count(-2001) == 1, "مقصدِ خراب هیچ‌وقت پست را نگرفت"
    finally:
        await db_module.close_db()


@pytest.mark.asyncio
async def test_a_retry_that_had_already_arrived_is_not_sent_again(tmp_path, monkeypatch):
    """اگر یک تلاشِ قبلی در واقع رسیده بود (مثلاً ارسال موفق و خطایی بعدش)،
    دوباره فرستادنش یعنی پستِ تکراری."""
    db_module, task_id = await _setup(tmp_path, monkeypatch, settings={})
    try:
        from telkap.models import MessageMap, RetryItem
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

        assert client.sent == [], "پستی که رسیده بود دوباره رفت"
        assert await _rows(db_module, RetryItem) == []
    finally:
        await db_module.close_db()


@pytest.mark.asyncio
async def test_a_retry_does_not_charge_the_message_quota_again(tmp_path, monkeypatch):
    """پستی که هرگز نرفت پنج واحد سهمیه می‌خورد: یکی برای هر تلاش."""
    db_module, task_id = await _setup(tmp_path, monkeypatch, settings={})
    try:
        from telkap.services.copier import Copier
        from telkap.services.retry import RetryWorker

        client = Telethonish()
        manager = FakeManager(client)
        copier = Copier(manager)
        charged: list[int] = []
        original = copier._message_quota_ok

        async def counting(*args, **kwargs):
            charged.append(1)
            return await original(*args, **kwargs)

        copier._message_quota_ok = counting
        post = FakeMessage(id=7, message="خبر")
        client.messages[7] = post
        client.fail_targets = {-1002}
        await copier.process(7, task_id, [post])
        client.fail_targets = set()
        await _due(db_module)
        await RetryWorker(manager, copier).run_once()

        assert len(charged) == 1, f"سهمیه {len(charged)} بار برداشته شد"
    finally:
        await db_module.close_db()


@pytest.mark.asyncio
async def test_a_retry_after_active_hours_still_delivers(tmp_path, monkeypatch):
    """پستی که در ساعت فعال آمده و ارسالش شکست خورده، اگر تلاشِ دوباره‌اش
    بعد از ساعت فعال برسد، تا امروز «خارج از ساعت» رد و دور ریخته می‌شد."""
    db_module, task_id = await _setup(tmp_path, monkeypatch, settings={})
    try:
        from telkap.services import copier as copier_module
        from telkap.services.copier import Copier
        from telkap.services.retry import RetryWorker

        client = Telethonish()
        manager = FakeManager(client)
        copier = Copier(manager)
        post = FakeMessage(id=7, message="خبر")
        client.messages[7] = post
        client.fail_targets = {-1002}
        await copier.process(7, task_id, [post])

        client.fail_targets = set()
        monkeypatch.setattr(copier_module, "within_active_hours", lambda cfg: False)
        await _due(db_module)
        await RetryWorker(manager, copier).run_once()

        assert [r.target for r in client.sent if r.kind == "text"] == [-1002]
    finally:
        await db_module.close_db()


@pytest.mark.asyncio
async def test_the_quota_comes_back_when_a_post_went_nowhere(tmp_path, monkeypatch):
    db_module, task_id = await _setup(tmp_path, monkeypatch, settings={})
    try:
        from telkap.plans import FEAT_MESSAGES
        from telkap.services import copier as copier_module
        from telkap.services.copier import Copier

        _forwarding(monkeypatch, error=RuntimeError("CHAT_FORWARDS_RESTRICTED"))
        returned: list = []
        real_release = copier_module.entitlement.release

        async def spy(user_id, grant, sub_id):
            if grant is not None:
                returned.append(grant.feature)
            return await real_release(user_id, grant, sub_id)

        monkeypatch.setattr(copier_module.entitlement, "release", spy)
        copier = Copier(FakeManager(Telethonish()))
        await copier.process(7, task_id, [FakeMessage(id=1, media=_unsupported())])

        assert FEAT_MESSAGES in returned, "سهمیه‌ی پستی که نرفت برنگشت"
    finally:
        await db_module.close_db()


@pytest.mark.asyncio
async def test_the_give_up_notice_escapes_the_error(tmp_path, monkeypatch):
    """پیام با HTML فرستاده می‌شود؛ یک «<» در خطای تلگرام کلِ پیام را
    می‌انداخت — یعنی صاحب کانال اصلاً خبردار نمی‌شد."""
    db_module, task_id = await _setup(tmp_path, monkeypatch, settings={})
    try:
        from telkap.models import RetryItem
        from telkap.services.copier import Copier
        from telkap.services.retry import MAX_ATTEMPTS, RetryWorker

        async with db_module.get_session() as db:
            item = RetryItem(
                task_id=task_id, user_id=7, src_chat_id=-1001, src_msg_ids="1",
                dest_chat="-1002", attempts=MAX_ATTEMPTS - 1,
            )
            db.add(item)
            await db.commit()
            await db.refresh(item)

        notes = Notes()
        manager = FakeManager(Telethonish())
        worker = RetryWorker(manager, Copier(manager), notifier=notes)
        await worker._reschedule(item, "<b>broken</b> & more")

        text = notes.messages[0][1]
        assert "<b>broken</b>" not in text and "&lt;b&gt;" in text
    finally:
        await db_module.close_db()


def test_the_approval_list_escapes_post_text():
    """«Barça & Real» در متنِ پست کافی بود تا فهرستِ تأیید باز نشود."""
    from telkap.handlers.approvals import _entry

    line = _entry(SimpleNamespace(preview="Barça & Real <3"), 1)
    assert "&amp;" in line and "&lt;3" in line


# ------------------------------------------------------------- ۶. نظرسنجی


def _quiz(correct: bool):
    from telethon.tl.types import (
        MessageMediaPoll,
        Poll,
        PollAnswer,
        PollAnswerVoters,
        PollResults,
        TextWithEntities,
    )

    poll = Poll(
        id=1, question=TextWithEntities(text="کدام؟", entities=[]),
        answers=[
            PollAnswer(text=TextWithEntities(text="الف", entities=[]), option=b"0"),
            PollAnswer(text=TextWithEntities(text="ب", entities=[]), option=b"1"),
        ],
        quiz=True,
    )
    results = PollResults(results=[
        PollAnswerVoters(option=b"1", voters=3, correct=correct),
    ])
    return MessageMediaPoll(poll=poll, results=results)


@pytest.mark.asyncio
async def test_a_quiz_whose_answer_we_cannot_see_is_reported_not_lost_silently(
    tmp_path, monkeypatch
):
    """جوابِ درستِ آزمون را تلگرام فقط به رأی‌دهنده نشان می‌دهد. تا امروز
    آزمون بدون جواب فرستاده می‌شد، رد می‌شد، و پست بی‌هیچ ردی گم می‌شد."""
    db_module, task_id = await _setup(tmp_path, monkeypatch, settings={})
    try:
        from telkap.services.copier import Copier

        copier = Copier(FakeManager(Telethonish()))
        await copier.process(7, task_id, [FakeMessage(id=1, media=_quiz(correct=False))])

        assert await _events(db_module, "unsendable"), "پست بی‌صدا گم شد"
    finally:
        await db_module.close_db()


@pytest.mark.asyncio
async def test_a_quiz_keeps_its_correct_answer(tmp_path, monkeypatch):
    db_module, task_id = await _setup(tmp_path, monkeypatch, settings={})
    try:
        from telkap.services.copier import Copier

        client = Telethonish()
        copier = Copier(FakeManager(client))
        assert await copier.process(
            7, task_id, [FakeMessage(id=1, media=_quiz(correct=True))]
        )
        poll = client.sent[0].payload
        assert poll.correct_answers == [b"1"]
    finally:
        await db_module.close_db()


# ---------------------------------------------------------- ۷. حالت ساده


@pytest.mark.asyncio
async def test_simple_mode_says_so_when_a_post_cannot_be_copied(tmp_path, monkeypatch):
    """در حالت ساده فوروارد ممکن نیست (ربات عضوِ مبدأ نیست). تا امروز
    نوعِ ناشناخته «فایل» حساب و هر چه دانلود شده بود فرستاده می‌شد — یا
    اگر چیزی نبود، پست بی‌صدا ناپدید می‌شد."""
    db_module, task_id = await _setup(tmp_path, monkeypatch, settings={})
    try:
        from telkap.services.copier import Copier
        from tests.test_simple_mode import _install, _NoAccountManager, _reader, _simple_task

        await _simple_task(db_module, task_id)
        bot = _install(monkeypatch)
        _reader(monkeypatch, tmp_path, ["junk.bin"])

        copier = Copier(_NoAccountManager())
        post = FakeMessage(id=1, message="خبر", media=_unsupported())
        assert await copier.process(7, task_id, [post]) is False

        assert bot.any_media == [] and bot.sent == [], "چیزِ بی‌ربطی فرستاده شد"
        assert await _events(db_module, "unsendable"), "پست بی‌صدا ناپدید شد"
    finally:
        await db_module.close_db()


@pytest.mark.asyncio
async def test_a_mixed_album_keeps_each_items_kind(tmp_path):
    """آلبومِ عکس و ویدیو با کلاسِ اولی ساخته می‌شد — ویدیو مثل عکس می‌رفت."""
    from aiogram.types import InputMediaPhoto, InputMediaVideo

    from telkap.services import botsend

    files = []
    for name in ("a.jpg", "b.mp4"):
        path = tmp_path / name
        path.write_bytes(b"x")
        files.append(str(path))

    groups: list = []

    class _Bot:
        async def send_media_group(self, **kwargs):
            groups.append(kwargs["media"])
            return [SimpleNamespace(message_id=1), SimpleNamespace(message_id=2)]

    await botsend.send_media(_Bot(), -1, "photo", files, kinds=["photo", "video"])

    assert [type(m) for m in groups[0]] == [InputMediaPhoto, InputMediaVideo]
