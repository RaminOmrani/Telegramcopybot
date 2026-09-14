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


class _Bot:
    """رباتی که فقط یادداشت می‌کند چه چیزی از آن خواسته شد."""

    def __init__(self) -> None:
        self.sent: list[dict] = []

    async def send_message(self, **kwargs):
        self.sent.append(kwargs)

        class _Result:
            message_id = 900 + len(self.sent)

        return _Result()


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
async def test_a_media_post_is_skipped_whole_not_sent_as_bare_text(
    tmp_path, monkeypatch
):
    """<b>مهم‌ترین تستِ این مرحله.</b>

    مسیر رسانه هنوز ساخته نشده. وسوسه‌ی طبیعی این است که «فعلاً
    کپشن را بفرستیم» — ولی آن پست، عکسِ بدونِ عکس است: برای مخاطب
    بی‌معناست و برای مشتری هم معلوم نیست چرا.

    بدتر از آن: در جدول نگاشت «فرستاده شد» ثبت می‌شود، یعنی وقتی
    مسیر رسانه ساخته شد، این پست‌ها <b>هیچ‌وقت</b> درست فرستاده
    نمی‌شوند. یک نیمه‌کاری که خودش را برای همیشه قفل می‌کند.
    """
    db_module, task_id = await _setup(tmp_path, monkeypatch, settings={})
    try:
        from sqlalchemy import select

        from telkap.models import MessageMap
        from telkap.services.copier import Copier

        await _simple_task(db_module, task_id)
        bot = _install(monkeypatch)

        photo = FakeMessage(id=5, message="کپشن عکس", media=object())
        copier = Copier(FakeManager(FakeClient()))
        await copier.process(7, task_id, [photo])

        assert bot.sent == [], "کپشنِ تنها به‌جای عکس رفت"

        async with db_module.get_session() as db:
            rows = list((await db.execute(select(MessageMap))).scalars())
        assert rows == [], "پستِ نرفته «فرستاده شد» ثبت شد و برای همیشه قفل می‌شود"
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
