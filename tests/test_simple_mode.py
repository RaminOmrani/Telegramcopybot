"""تست حالت ساده: ربات می‌فرستد، نه اکانت مشتری.

<b>چرا این حالت هست.</b> بزرگ‌ترین مانعِ فروش یک جمله است: «اکانتم را
به یک ربات وصل کنم؟». برای مبدأ عمومی لازم نیست — اکانت سرویسِ ما
می‌خواند و خودِ ربات، که مشتری ادمینش کرده، پست می‌گذارد.

<b>و چرا درزِ دو مسیر دقیقاً سرِ «ارسال» است.</b> هرچه پیش از آن نقطه
است — قواعد، فیلترها، امضا، تشخیص تکراری — برای هر دو حالت یکی است، و
هرچه بعدش می‌آید هم. اگر درز بالاتر بود، حالت ساده کم‌کم نسخه‌ی دومی
از همه‌ی آن‌ها می‌شد و هر امکانِ تازه باید دو بار نوشته می‌شد. این
تست‌ها همان اشتراک را نگه می‌دارند.
"""
from __future__ import annotations

import pytest

from tests.test_copier import FakeClient, FakeManager, FakeMessage, _setup


class _Result:
    def __init__(self, message_id: int = 900) -> None:
        self.message_id = message_id


class _Bot:
    """رباتی که فقط یادداشت می‌کند چه چیزی از آن خواسته شد."""

    def __init__(self) -> None:
        self.sent: list[dict] = []
        self.photos: list[dict] = []
        self.documents: list[dict] = []
        self.videos: list[dict] = []
        self.groups: list[dict] = []

    async def send_message(self, **kwargs):
        self.sent.append(kwargs)
        return _Result(900 + len(self.sent))

    async def send_photo(self, **kwargs):
        self.photos.append(kwargs)
        return _Result(950 + len(self.photos))

    async def send_document(self, **kwargs):
        self.documents.append(kwargs)
        return _Result(960 + len(self.documents))

    async def send_video(self, **kwargs):
        self.videos.append(kwargs)
        return _Result(970 + len(self.videos))

    @property
    def any_media(self) -> list[dict]:
        return self.photos + self.documents + self.videos

    async def send_media_group(self, **kwargs):
        self.groups.append(kwargs)
        return [_Result(980 + i) for i in range(len(kwargs["media"]))]


def _photo_media():
    """رسانه‌ی واقعیِ «عکس» — نه یک شیء دلخواه.

    `classify_media` روی شیء ناشناخته <b>document</b> برمی‌گرداند، پس
    تستی که با `object()` نوشته شود مسیرِ فایل را می‌سنجد نه مسیرِ عکس.
    """
    from telethon.tl.types import MessageMediaPhoto

    return MessageMediaPhoto(photo=None)


def _reader(monkeypatch, tmp_path, names: list[str]) -> list[str]:
    """<b>اکانت سرویسی که فایل را دانلود می‌کند.</b>

    فایل‌های واقعی روی دیسک ساخته می‌شوند تا هم آپلود معنا داشته باشد
    و هم بشود سنجید که بعدش پاک می‌شوند.
    """
    from telkap.services import copier as copier_module

    made: list[str] = []
    for name in names:
        path = tmp_path / name
        path.write_bytes(b"\x89PNG\r\n\x1a\n" + b"0" * 64)
        made.append(str(path))

    async def fake_reader(self, messages):
        return object()

    async def fake_download(self, client, messages):
        return list(made)

    monkeypatch.setattr(copier_module.Copier, "_reader_for", fake_reader)
    monkeypatch.setattr(copier_module.Copier, "_download_all", fake_download)
    return made


async def _simple_task(db_module, task_id: int) -> None:
    from telkap.models import Task
    from telkap.services import cache

    async with db_module.get_session() as db:
        task = await db.get(Task, task_id)
        task.mode = Task.MODE_SIMPLE
        await db.commit()
    cache.invalidate_task(task_id)


def _install(monkeypatch) -> _Bot:
    from telkap.services import alerts

    bot = _Bot()
    monkeypatch.setattr(alerts, "bot", lambda: bot)
    return bot


@pytest.mark.asyncio
async def test_a_simple_task_posts_with_the_bot_not_the_account(tmp_path, monkeypatch):
    """<b>کلِ نکته‌ی این حالت.</b>"""
    db_module, task_id = await _setup(tmp_path, monkeypatch, settings={})
    try:
        from telkap.services.copier import Copier

        await _simple_task(db_module, task_id)
        bot = _install(monkeypatch)

        client = FakeClient()
        copier = Copier(FakeManager(client))
        assert await copier.process(7, task_id, [FakeMessage(id=5, message="سلام")])

        assert len(bot.sent) == 1, "ربات چیزی نفرستاد"
        assert bot.sent[0]["text"] == "سلام"
        assert client.sent == [], "اکانت مشتری هم فرستاد — یعنی پست دوتا شد"
    finally:
        await db_module.close_db()


class _NoAccountManager:
    """مدیری که هیچ کلاینتی ندارد — یعنی وضعیتِ <b>واقعیِ</b> هر مشتریِ
    حالت ساده، که اصلاً اکانتی وصل نکرده."""

    async def ensure_client(self, user_id):
        return None

    def tasks_for_chat(self, user_id, chat_id):
        return []

    async def reload_user(self, user_id):
        return 0


@pytest.mark.asyncio
async def test_a_customer_with_no_account_at_all_still_gets_their_posts(
    tmp_path, monkeypatch
):
    """<b>اشکالی که تست‌های خودم نگرفته بودند.</b>

    موتور پیش از هر کاری کلاینتِ مشتری را می‌گرفت و اگر نبود، کار را
    با «اکانت کاربری متصل نیست» متوقف می‌کرد. یعنی اولین کارِ حالت
    ساده، سرِ اولین پست، خاموش می‌شد — کلِ حالتی که برای «اکانت
    نمی‌خواهیم» ساخته شد، دقیقاً به نبودنِ اکانت گیر می‌کرد.

    تست‌های قبلی‌ام نگرفتندش چون مدیرِ ساختگی‌شان همیشه یک کلاینت
    برمی‌گرداند — یعنی شرطی را می‌سنجیدند که در واقعیت هیچ‌وقت
    برقرار نیست.
    """
    db_module, task_id = await _setup(tmp_path, monkeypatch, settings={})
    try:
        from telkap.models import Task
        from telkap.services.copier import Copier

        await _simple_task(db_module, task_id)
        bot = _install(monkeypatch)

        copier = Copier(_NoAccountManager())
        assert await copier.process(7, task_id, [FakeMessage(id=5, message="سلام")])
        assert len(bot.sent) == 1

        async with db_module.get_session() as db:
            task = await db.get(Task, task_id)
        assert task.enabled, f"کار متوقف شد: {task.last_error}"
    finally:
        await db_module.close_db()


@pytest.mark.asyncio
async def test_a_full_task_without_an_account_is_still_paused(tmp_path, monkeypatch):
    """و نگهبانِ طرفِ دیگر: در حالت کامل، نبودنِ اکانت واقعاً یک خرابی
    است و باید همان‌طور که بود گزارش شود."""
    db_module, task_id = await _setup(tmp_path, monkeypatch, settings={})
    try:
        from telkap.models import Task
        from telkap.services.copier import Copier

        copier = Copier(_NoAccountManager())
        assert not await copier.process(7, task_id, [FakeMessage(id=5, message="سلام")])

        async with db_module.get_session() as db:
            task = await db.get(Task, task_id)
        assert not task.enabled
        assert "اکانت" in (task.last_error or "")
    finally:
        await db_module.close_db()


@pytest.mark.asyncio
async def test_a_full_task_still_goes_through_the_account(tmp_path, monkeypatch):
    """<b>نگهبانِ کارهای امروز.</b>

    شش کارِ در حال اجرا همه `full` هستند. اگر درزِ تازه به آن‌ها نشت
    کند، همه با هم سراغ رباتی می‌روند که در مقصدشان ادمین نیست.
    """
    db_module, task_id = await _setup(tmp_path, monkeypatch, settings={})
    try:
        from telkap.services.copier import Copier

        bot = _install(monkeypatch)
        client = FakeClient()
        copier = Copier(FakeManager(client))
        assert await copier.process(7, task_id, [FakeMessage(id=5, message="سلام")])

        assert len(client.sent) == 1
        assert bot.sent == []
    finally:
        await db_module.close_db()


@pytest.mark.asyncio
async def test_the_rules_still_run_in_simple_mode(tmp_path, monkeypatch):
    """<b>چرا درز پایین است و نه بالا.</b>

    اگر حالت ساده مسیر خودش را داشت، اولین چیزی که از دست می‌رفت
    همین‌ها بود — و کاربر فقط می‌دید که «تنظیماتم اعمال نمی‌شود».
    """
    db_module, task_id = await _setup(
        tmp_path, monkeypatch,
        settings={"remove_links": True, "footer": "@mychannel"},
        rules=[("replace", "الماس", "جواهر")],
    )
    try:
        from telkap.services.copier import Copier

        await _simple_task(db_module, task_id)
        bot = _install(monkeypatch)

        copier = Copier(FakeManager(FakeClient()))
        await copier.process(7, task_id, [
            FakeMessage(id=5, message="فروش الماس در https://shop.example.com"),
        ])

        body = bot.sent[0]["text"]
        assert "جواهر" in body, "قاعده‌ی جایگزینی اجرا نشد"
        assert "shop.example.com" not in body, "حذف لینک اجرا نشد"
        assert "@mychannel" in body, "فوتر اضافه نشد"
    finally:
        await db_module.close_db()


@pytest.mark.asyncio
async def test_a_photo_really_goes_as_a_photo(tmp_path, monkeypatch):
    """<b>رسانه از راهِ دانلود و آپلود می‌رود — و به شکلِ درست.</b>

    `file_id` در تلگرام به همان رباتی گره خورده که آن را دیده؛ فایلی
    که اکانت سرویس در مبدأ می‌بیند برای ربات ما شناسه‌ی قابل
    استفاده‌ای ندارد. پس خودِ بایت‌ها رد می‌شوند.

    و نوعش باید حفظ شود: عکسی که به‌صورت «فایل» برود، در کانال مشتری
    به‌جای تصویر یک پیوستِ قابل دانلود دیده می‌شود.

    (نسخه‌ی اولِ این تست `object()` را رسانه می‌داد، که <b>document</b>
    دسته‌بندی می‌شود — یعنی اسمش «عکس» بود و مسیرِ فایل را می‌سنجید.)
    """
    db_module, task_id = await _setup(tmp_path, monkeypatch, settings={})
    try:
        from telkap.services.copier import Copier

        await _simple_task(db_module, task_id)
        bot = _install(monkeypatch)
        _reader(monkeypatch, tmp_path, ["shot.jpg"])

        photo = FakeMessage(id=5, message="کپشن عکس", media=_photo_media())
        copier = Copier(FakeManager(FakeClient()))
        assert await copier.process(7, task_id, [photo])

        assert bot.photos, f"عکس به‌شکل عکس نرفت (documents={len(bot.documents)})"
        assert bot.photos[0]["caption"] == "کپشن عکس"
        assert bot.sent == [], "به‌جای عکس، متنِ تنها رفت"
    finally:
        await db_module.close_db()


@pytest.mark.asyncio
async def test_a_failed_download_sends_nothing_and_locks_nothing(
    tmp_path, monkeypatch
):
    """<b>نه نصفه.</b>

    وسوسه‌ی طبیعی وقتی دانلود شکست می‌خورد این است که «حداقل کپشن را
    بفرستیم» — ولی آن پست، عکسِ بدونِ عکس است: برای مخاطب بی‌معناست و
    برای مشتری هم معلوم نیست چرا.

    بدتر از آن: در جدول نگاشت «فرستاده شد» ثبت می‌شود، یعنی همان پست
    <b>هیچ‌وقت</b> دیگر درست فرستاده نمی‌شود. یک نیمه‌کاری که خودش را
    برای همیشه قفل می‌کند.
    """
    db_module, task_id = await _setup(tmp_path, monkeypatch, settings={})
    try:
        from sqlalchemy import select

        from telkap.models import MessageMap
        from telkap.services.copier import Copier

        await _simple_task(db_module, task_id)
        bot = _install(monkeypatch)
        _reader(monkeypatch, tmp_path, [])          # دانلود چیزی برنگرداند

        photo = FakeMessage(id=5, message="کپشن عکس", media=_photo_media())
        copier = Copier(FakeManager(FakeClient()))
        await copier.process(7, task_id, [photo])

        assert bot.sent == [] and bot.any_media == [], "کپشنِ تنها به‌جای عکس رفت"

        async with db_module.get_session() as db:
            rows = list((await db.execute(select(MessageMap))).scalars())
        assert rows == [], "پستِ نرفته «فرستاده شد» ثبت شد و برای همیشه قفل می‌شود"
    finally:
        await db_module.close_db()


@pytest.mark.asyncio
async def test_an_album_keeps_its_caption_on_the_first_item_only(
    tmp_path, monkeypatch
):
    """تلگرام کپشن را فقط از اولین آیتم می‌خواند؛ گذاشتنش روی همه یعنی
    متن زیر هر عکس تکرار شود."""
    db_module, task_id = await _setup(tmp_path, monkeypatch, settings={})
    try:
        from telkap.services.copier import Copier

        await _simple_task(db_module, task_id)
        bot = _install(monkeypatch)
        _reader(monkeypatch, tmp_path, ["a.jpg", "b.jpg", "c.jpg"])

        album = [
            FakeMessage(id=5, message="کپشن آلبوم", media=_photo_media(), grouped_id=1),
            FakeMessage(id=6, media=_photo_media(), grouped_id=1),
            FakeMessage(id=7, media=_photo_media(), grouped_id=1),
        ]
        copier = Copier(FakeManager(FakeClient()))
        assert await copier.process(7, task_id, album)

        assert len(bot.groups) == 1
        media = bot.groups[0]["media"]
        assert len(media) == 3
        captions = [getattr(item, "caption", None) for item in media]
        assert captions[0] == "کپشن آلبوم"
        assert captions[1:] == [None, None], "کپشن زیر هر عکس تکرار شد"
    finally:
        await db_module.close_db()


@pytest.mark.asyncio
async def test_an_album_is_sent_without_buttons(tmp_path, monkeypatch):
    """<b>نگهبانِ آینده، نه اشکالِ امروز.</b>

    تلگرام روی `send_media_group` صفحه‌کلید قبول نمی‌کند: فرستادنِ
    دکمه‌ها همراه آلبوم یعنی خطا، و خطا یعنی <b>هیچ‌کدام</b> از عکس‌ها
    نروند.

    کدِ امروز اصلاً دکمه‌ای به آن مسیر نمی‌دهد، پس این تست همین حالا
    قرمز نمی‌شود — کارش این است که اگر روزی کسی «برای کامل بودن»
    `reply_markup` را اضافه کرد، همان‌جا جلویش را بگیرد. کار با
    دکمه‌های روشن اجرا می‌شود تا مسیر واقعاً از همان‌جا رد شود.
    """
    db_module, task_id = await _setup(
        tmp_path, monkeypatch, settings={"copy_buttons": True}
    )
    try:
        from telkap.services.copier import Copier

        await _simple_task(db_module, task_id)
        bot = _install(monkeypatch)
        _reader(monkeypatch, tmp_path, ["a.jpg", "b.jpg"])

        album = [
            FakeMessage(id=5, message="متن", media=_photo_media(), grouped_id=1),
            FakeMessage(id=6, media=_photo_media(), grouped_id=1),
        ]
        copier = Copier(FakeManager(FakeClient()))
        assert await copier.process(7, task_id, album)

        assert "reply_markup" not in bot.groups[0]
    finally:
        await db_module.close_db()


@pytest.mark.asyncio
async def test_downloaded_files_are_cleaned_up(tmp_path, monkeypatch):
    """<b>دیسکِ سرور بی‌نهایت نیست.</b>

    هر پستِ رسانه‌دار یک فایل روی دیسک می‌گذارد. با چند ده کار و چند
    صد پست در روز، فایل‌های جامانده دیسک را پر می‌کنند — و وقتی پر
    شود، <b>همه‌چیز</b> می‌خوابد، نه فقط حالت ساده.
    """
    db_module, task_id = await _setup(tmp_path, monkeypatch, settings={})
    try:
        from telkap.services.copier import Copier

        await _simple_task(db_module, task_id)
        _install(monkeypatch)
        paths = _reader(monkeypatch, tmp_path, ["shot.jpg"])

        photo = FakeMessage(id=5, message="کپشن", media=_photo_media())
        copier = Copier(FakeManager(FakeClient()))
        await copier.process(7, task_id, [photo])

        from pathlib import Path

        assert not Path(paths[0]).exists(), "فایل دانلودشده روی دیسک ماند"
    finally:
        await db_module.close_db()


@pytest.mark.asyncio
async def test_caption_only_still_works_with_media(tmp_path, monkeypatch):
    """اگر کاربر خودش خواسته باشد فقط متن برود، رسانه اصلاً موضوع
    نیست و نباید رد شود."""
    db_module, task_id = await _setup(
        tmp_path, monkeypatch, settings={"caption_only": True}
    )
    try:
        from telkap.services.copier import Copier

        await _simple_task(db_module, task_id)
        bot = _install(monkeypatch)

        photo = FakeMessage(id=5, message="کپشن عکس", media=object())
        copier = Copier(FakeManager(FakeClient()))
        await copier.process(7, task_id, [photo])

        assert len(bot.sent) == 1
        assert bot.sent[0]["text"] == "کپشن عکس"
    finally:
        await db_module.close_db()


@pytest.mark.asyncio
async def test_without_a_running_bot_the_failure_is_loud(tmp_path, monkeypatch):
    """<b>سکوت اینجا یعنی پستِ گم‌شده‌ی بی‌ردّ.</b>

    اگر ربات بالا نباشد و ما بی‌صدا رد شویم، پست نه می‌رود نه در صفِ
    تلاش مجدد می‌نشیند — فقط ناپدید می‌شود.
    """
    db_module, task_id = await _setup(tmp_path, monkeypatch, settings={})
    try:
        from telkap.services import alerts
        from telkap.services.copier import Copier

        await _simple_task(db_module, task_id)
        monkeypatch.setattr(alerts, "bot", lambda: None)

        copier = Copier(FakeManager(FakeClient()))
        # پست نباید «موفق» شمرده شود
        assert not await copier.process(7, task_id, [FakeMessage(id=5, message="سلام")])

        # و مهم‌تر: باید در صف تلاش مجدد بنشیند. «موفق نبود» به‌تنهایی
        # کافی نیست — پستی که نه رفته و نه در صف است، فقط ناپدید شده.
        from sqlalchemy import select

        from telkap.models import RetryItem

        async with db_module.get_session() as db:
            queued = list((await db.execute(select(RetryItem))).scalars())
        assert queued, "پست نه رفت و نه در صف تلاش مجدد نشست"
    finally:
        await db_module.close_db()


# ------------------------------------- جدایی کاملِ دو مسیر در سمتِ اکانت


@pytest.mark.asyncio
async def test_a_simple_task_is_never_wired_to_the_customers_account(
    tmp_path, monkeypatch
):
    """<b>مشتری‌ای که هم اکانت دارد هم کارِ ساده.</b>

    این حالت کاملاً عادی است: کسی که قبلاً اکانتش را وصل کرده، بعداً
    یک کارِ ساده هم می‌سازد. اگر آن کار روی اکانتش هم ثبت شود، دو چیز
    خراب می‌شود:

    ۱) رفتارِ حالت ساده به این بند می‌شود که طرف تصادفاً اکانت دارد
       یا نه — یعنی دو مشتری با یک تنظیمات، دو تجربه‌ی متفاوت.

    ۲) <b>و مهم‌تر:</b> جارو همان مبدأ را برمی‌دارد و پست‌ها با برچسب
       «sweep» ثبت می‌شوند به‌جای «simple». آن‌وقت `sweep_share` — که
       تنها نشانه‌ی خرابیِ آپدیت‌هاست و دیروز ۹۲٪ بودنش خرابی را لو
       داد — با رشدِ حالت ساده بالا می‌رود و هشدارش بی‌معنا می‌شود.
    """
    db_module, task_id = await _setup(tmp_path, monkeypatch, settings={})
    try:
        from telkap.services.userbot import manager

        await _simple_task(db_module, task_id)

        class _Runtime:
            def __init__(self) -> None:
                self.handlers: list = []
                self.source_map: dict = {}

        client = FakeClient()
        monkeypatch.setattr(
            manager, "ensure_client", lambda user_id: _returns(client)
        )
        manager._runtimes[7] = _Runtime()
        manager.bind_copier(object())

        active = await manager.reload_user(7)

        assert active == 0, "کارِ ساده روی اکانت مشتری ثبت شد"
        assert manager.tasks_for_chat(7, -1001) == []
    finally:
        manager._runtimes.pop(7, None)
        await db_module.close_db()


def _returns(value):
    async def _inner():
        return value

    return _inner()
