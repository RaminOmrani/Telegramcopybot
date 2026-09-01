"""تست انتشار مطالب فید در کانال کاربر.

مسیر فید همان تصمیم‌گیرنده‌های مسیر تلگرامی را صدا می‌زند — فیلترها،
تبدیل متن، مسیریابی، تکراری‌ها. این تست‌ها می‌سنجند که آن اتصال‌ها
واقعاً برقرارند، نه اینکه فقط کد کنارشان نوشته شده.
"""
from __future__ import annotations

import pytest

from telkap.services import feedworker
from telkap.services.defaults import merged_settings
from telkap.services.feeds import FeedItem
from tests.test_copier import _setup


def item(
    *, title="تیتر خبر", summary="متن خبر", link="https://news.example/1", guid="g1"
) -> FeedItem:
    return FeedItem(
        guid=guid, title=title, summary=summary, link=link, image="", published=None
    )


# ── ساختن متن پست ────────────────────────────────────────────────────


def test_the_default_layout_is_title_body_link():
    text = feedworker.render(item(), merged_settings(None))

    assert text == "تیتر خبر\n\nمتن خبر\n\nhttps://news.example/1"


def test_the_default_template_carries_no_html():
    """کلاینت با parse_mode=None کار می‌کند.

    یعنی «&lt;b&gt;» واقعاً همان پنج نویسه در پست کاربر دیده می‌شود، نه
    متن پررنگ. اگر روزی الگوی پیش‌فرض HTML بگیرد، این تست می‌گیردش.
    """
    assert "<" not in feedworker.DEFAULT_TEMPLATE


def test_a_custom_template_is_used():
    cfg = merged_settings({"feed_template": "📰 {title}\n{link}"})

    assert feedworker.render(item(), cfg) == "📰 تیتر خبر\nhttps://news.example/1"


def test_a_missing_field_does_not_leave_a_hole():
    """آیتم بدون لینک نباید پستی با دو خط خالی ته آن بسازد."""
    text = feedworker.render(item(link=""), merged_settings(None))

    assert text == "تیتر خبر\n\nمتن خبر"
    assert not text.endswith("\n")


def test_a_long_summary_is_cut_at_a_word_boundary():
    cfg = merged_settings({"feed_summary_chars": 20})
    long = "یک " * 40

    text = feedworker.render(item(summary=long), cfg)

    assert "…" in text
    assert len(text.split("\n\n")[1]) <= 21


def test_zero_means_do_not_cut():
    cfg = merged_settings({"feed_summary_chars": 0})
    long = "طولانی " * 200

    assert "…" not in feedworker.render(item(summary=long), cfg)


def test_a_template_with_only_a_title_works():
    cfg = merged_settings({"feed_template": "{title}"})

    assert feedworker.render(item(), cfg) == "تیتر خبر"


# ── تکراری‌ها ────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_nothing_is_seen_in_a_fresh_job(tmp_path, monkeypatch):
    await _setup(tmp_path, monkeypatch, settings={})

    assert await feedworker.seen_keys(1, [10, 20, 30]) == set()


@pytest.mark.asyncio
async def test_seen_items_come_back_and_others_do_not(tmp_path, monkeypatch):
    db_module, task_id = await _setup(tmp_path, monkeypatch, settings={})
    from telkap.models import MessageMap

    async with db_module.get_session() as db:
        db.add(MessageMap(task_id=task_id, src_msg_id=10, dst_msg_id=1, dest_chat="@d"))
        await db.commit()

    assert await feedworker.seen_keys(task_id, [10, 20]) == {10}


@pytest.mark.asyncio
async def test_one_job_does_not_see_another_ones_items(tmp_path, monkeypatch):
    """دو کاربر که یک فید را دنبال می‌کنند نباید همدیگر را ساکت کنند."""
    db_module, task_id = await _setup(tmp_path, monkeypatch, settings={})
    from telkap.models import MessageMap

    async with db_module.get_session() as db:
        db.add(MessageMap(task_id=task_id, src_msg_id=10, dst_msg_id=1, dest_chat="@d"))
        await db.commit()

    assert await feedworker.seen_keys(task_id + 999, [10]) == set()


@pytest.mark.asyncio
async def test_an_empty_key_list_asks_the_database_nothing(tmp_path, monkeypatch):
    await _setup(tmp_path, monkeypatch, settings={})

    assert await feedworker.seen_keys(1, []) == set()


# ── اولین خواندن ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_the_first_read_publishes_nothing_but_marks_everything(
    tmp_path, monkeypatch
):
    """<b>مهم‌ترین قاعده‌ی این ماژول.</b>

    فید معمولاً ده‌ها مطلب گذشته دارد. بدون این، لحظه‌ی ساختن کار،
    کانال کاربر پر می‌شود و اکانتش هم محدود.
    """
    db_module, task_id = await _setup(tmp_path, monkeypatch, settings={})
    from telkap.models import MessageMap, Task
    from telkap.services import cache

    async with db_module.get_session() as db:
        task = await db.get(Task, task_id)
        task.source_kind = Task.SOURCE_RSS
        task.source_ref = "https://news.example/rss"
        await db.commit()
    cache.invalidate_task(task_id)

    items = [item(guid=f"g{i}", title=f"خبر {i}") for i in range(30)]

    async def fake_fetch(url, *, session=None):
        return items

    published = []
    monkeypatch.setattr(feedworker.feeds, "fetch", fake_fetch)
    monkeypatch.setattr(
        feedworker, "publish", lambda *a, **k: published.append(a) or True
    )

    snapshot = await cache.get_task(task_id)
    sent = await feedworker.check_task(snapshot)

    assert sent == 0
    assert published == []

    async with db_module.get_session() as db:
        from sqlalchemy import func, select

        marked = await db.scalar(
            select(func.count()).select_from(MessageMap).where(
                MessageMap.task_id == task_id
            )
        )
    assert marked == 30


@pytest.mark.asyncio
async def test_after_the_first_read_only_new_items_go_out(tmp_path, monkeypatch):
    db_module, task_id = await _setup(tmp_path, monkeypatch, settings={})
    from telkap.models import Task
    from telkap.services import cache

    async with db_module.get_session() as db:
        task = await db.get(Task, task_id)
        task.source_kind = Task.SOURCE_RSS
        task.source_ref = "https://news.example/rss"
        await db.commit()
    cache.invalidate_task(task_id)

    old = [item(guid=f"g{i}", title=f"قدیمی {i}") for i in range(3)]
    feed = list(old)

    async def fake_fetch(url, *, session=None):
        return feed

    published = []

    async def fake_publish(snapshot, entry, *, session=None):
        published.append(entry.title)
        return True

    monkeypatch.setattr(feedworker.feeds, "fetch", fake_fetch)
    monkeypatch.setattr(feedworker, "publish", fake_publish)
    monkeypatch.setattr(feedworker, "GAP_SECONDS", 0)

    snapshot = await cache.get_task(task_id)
    await feedworker.check_task(snapshot)         # دور اول: فقط علامت
    assert published == []

    feed.insert(0, item(guid="new", title="تازه"))
    await feedworker.check_task(snapshot)

    assert published == ["تازه"]


@pytest.mark.asyncio
async def test_items_are_published_oldest_first(tmp_path, monkeypatch):
    """فید تازه‌ترین را اول می‌دهد؛ کانال باید ترتیب طبیعی داشته باشد."""
    db_module, task_id = await _setup(tmp_path, monkeypatch, settings={})
    from telkap.models import Task
    from telkap.services import cache

    async with db_module.get_session() as db:
        task = await db.get(Task, task_id)
        task.source_kind = Task.SOURCE_RSS
        await db.commit()
    cache.invalidate_task(task_id)

    feed = [item(guid="seed", title="قدیمی")]

    async def fake_fetch(url, *, session=None):
        return feed

    published = []

    async def fake_publish(snapshot, entry, *, session=None):
        published.append(entry.title)
        return True

    monkeypatch.setattr(feedworker.feeds, "fetch", fake_fetch)
    monkeypatch.setattr(feedworker, "publish", fake_publish)
    monkeypatch.setattr(feedworker, "GAP_SECONDS", 0)

    snapshot = await cache.get_task(task_id)
    await feedworker.check_task(snapshot)

    feed[:0] = [item(guid="c", title="سوم"), item(guid="b", title="دوم")]
    await feedworker.check_task(snapshot)

    assert published == ["دوم", "سوم"]


@pytest.mark.asyncio
async def test_a_burst_is_capped_per_round(tmp_path, monkeypatch):
    """اگر فید ناگهان بیست مطلب بدهد، کانال یکجا پر نمی‌شود."""
    db_module, task_id = await _setup(tmp_path, monkeypatch, settings={})
    from telkap.models import Task
    from telkap.services import cache

    async with db_module.get_session() as db:
        task = await db.get(Task, task_id)
        task.source_kind = Task.SOURCE_RSS
        await db.commit()
    cache.invalidate_task(task_id)

    feed = [item(guid="seed", title="قدیمی")]

    async def fake_fetch(url, *, session=None):
        return feed

    published = []

    async def fake_publish(snapshot, entry, *, session=None):
        published.append(entry.title)
        return True

    monkeypatch.setattr(feedworker.feeds, "fetch", fake_fetch)
    monkeypatch.setattr(feedworker, "publish", fake_publish)
    monkeypatch.setattr(feedworker, "GAP_SECONDS", 0)

    snapshot = await cache.get_task(task_id)
    await feedworker.check_task(snapshot)

    feed[:0] = [item(guid=f"n{i}", title=f"تازه {i}") for i in range(20)]
    sent = await feedworker.check_task(snapshot)

    assert sent == feedworker.MAX_PER_CHECK


# ── خطای فید ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_broken_feed_is_recorded_not_raised(tmp_path, monkeypatch):
    """کاربر باید دلیل را در «کار» ببیند، نه اینکه بی‌صدا هیچ نیاید."""
    db_module, task_id = await _setup(tmp_path, monkeypatch, settings={})
    from telkap.models import Task
    from telkap.services import cache
    from telkap.services.feeds import FeedError

    async with db_module.get_session() as db:
        task = await db.get(Task, task_id)
        task.source_kind = Task.SOURCE_RSS
        await db.commit()
    cache.invalidate_task(task_id)

    async def fake_fetch(url, *, session=None):
        raise FeedError("سرور فید پاسخ ۴۰۴ داد.")

    monkeypatch.setattr(feedworker.feeds, "fetch", fake_fetch)

    snapshot = await cache.get_task(task_id)
    assert await feedworker.check_task(snapshot) == 0

    async with db_module.get_session() as db:
        task = await db.get(Task, task_id)
        assert "۴۰۴" in task.last_error


# ── فقط کارهای فید ───────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_telegram_jobs_are_never_polled_as_feeds(tmp_path, monkeypatch):
    """کار تلگرامی نباید در فهرست فیدها بیاید — source_ref آنجا آیدی است."""
    await _setup(tmp_path, monkeypatch, settings={})
    feedworker._last_check.clear()

    assert await feedworker._due_tasks() == []


@pytest.mark.asyncio
async def test_a_feed_job_is_polled(tmp_path, monkeypatch):
    db_module, task_id = await _setup(tmp_path, monkeypatch, settings={})
    from telkap.models import Task

    async with db_module.get_session() as db:
        task = await db.get(Task, task_id)
        task.source_kind = Task.SOURCE_RSS
        await db.commit()
    feedworker._last_check.clear()

    assert await feedworker._due_tasks() == [task_id]


@pytest.mark.asyncio
async def test_a_disabled_feed_job_is_left_alone(tmp_path, monkeypatch):
    db_module, task_id = await _setup(tmp_path, monkeypatch, settings={})
    from telkap.models import Task

    async with db_module.get_session() as db:
        task = await db.get(Task, task_id)
        task.source_kind = Task.SOURCE_RSS
        task.enabled = False
        await db.commit()
    feedworker._last_check.clear()

    assert await feedworker._due_tasks() == []


@pytest.mark.asyncio
async def test_a_feed_is_not_read_again_right_away(tmp_path, monkeypatch):
    """بدون این، هر دورِ پنج‌دقیقه‌ای به سرور خبرگزاری فشار می‌آورد."""
    db_module, task_id = await _setup(tmp_path, monkeypatch, settings={})
    import asyncio

    from telkap.models import Task

    async with db_module.get_session() as db:
        task = await db.get(Task, task_id)
        task.source_kind = Task.SOURCE_RSS
        await db.commit()

    feedworker._last_check.clear()
    feedworker._last_check[task_id] = asyncio.get_running_loop().time()

    assert await feedworker._due_tasks() == []


# ── تشخیص فید از کانال ───────────────────────────────────────────────


def test_a_web_address_is_treated_as_a_feed():
    from telkap.handlers.tasks import looks_like_feed

    assert looks_like_feed("https://example.com/rss") is True
    assert looks_like_feed("http://news.example/feed.xml") is True


def test_a_telegram_link_is_never_a_feed():
    """لینک تلگرام هم URL است؛ «http دارد» به‌تنهایی کافی نیست."""
    from telkap.handlers.tasks import looks_like_feed

    for ref in (
        "https://t.me/channel",
        "http://telegram.me/channel",
        "https://t.me/joinchat/AAA",
        "https://telegram.dog/channel",
    ):
        assert looks_like_feed(ref) is False, ref


def test_a_username_or_numeric_id_is_never_a_feed():
    from telkap.handlers.tasks import looks_like_feed

    for ref in ("@channel", "channel", "-1001234567890", "1234567890"):
        assert looks_like_feed(ref) is False, ref
