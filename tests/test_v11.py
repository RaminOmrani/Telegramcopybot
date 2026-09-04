"""تست دور یازدهم: مسیریابی با کلمه‌ی کلیدی، صف تأیید، و زمان‌بندی پیشرفته."""
from __future__ import annotations

from datetime import timedelta

import pytest

from tests.test_copier import FakeClient, FakeManager, FakeMessage, _setup


# --------------------------------------------------- خواندن کلمه‌ها
def test_keywords_are_read_from_whatever_the_user_types():
    from telkap.services.routing import parse_words

    assert parse_words("فروش، تخفیف") == ["فروش", "تخفیف"]
    assert parse_words("فروش, تخفیف\nکد هدیه") == ["فروش", "تخفیف", "کد هدیه"]
    assert parse_words("a | b") == ["a", "b"]
    assert parse_words("Sale, SALE, sale") == ["sale"]     # تکراری حذف می‌شود
    assert parse_words("   ") == []


def test_keyword_list_has_a_sane_ceiling():
    from telkap.services.routing import MAX_WORDS, parse_words

    many = parse_words(",".join(f"w{i}" for i in range(MAX_WORDS + 20)))
    assert len(many) == MAX_WORDS


# ------------------------------------------------------- منطق مسیریابی
def test_destination_without_keywords_takes_everything():
    from telkap.services.routing import is_filtered, wants

    assert wants("هر چیزی", {}) is True
    assert is_filtered({}) is False


def test_only_words_let_matching_posts_through():
    from telkap.services.routing import wants

    cfg = {"route_words": ["تخفیف"]}
    assert wants("امروز تخفیف ویژه داریم", cfg) is True
    assert wants("خبر عادی", cfg) is False


def test_skip_words_beat_only_words():
    """کاربر گفته «این کلمه هرگز»؛ نباید با کلمه‌ی دیگری دور زده شود."""
    from telkap.services.routing import wants

    cfg = {"route_words": ["تخفیف"], "route_skip": ["تست"]}
    assert wants("تخفیف ویژه", cfg) is True
    assert wants("تخفیف ویژه — تست", cfg) is False


def test_matching_ignores_letter_case():
    from telkap.services.routing import wants

    assert wants("Big SALE today", {"route_words": ["sale"]}) is True


@pytest.mark.asyncio
async def test_each_destination_gets_only_the_posts_it_asked_for(tmp_path, monkeypatch):
    db_module, task_id = await _setup(tmp_path, monkeypatch, settings={})
    try:
        from telkap.models import Destination
        from telkap.services.copier import Copier

        async with db_module.get_session() as session:
            session.add(
                Destination(
                    task_id=task_id, chat_id=-2001, ref="@sale", title="تخفیف‌ها",
                    overrides={"route_words": ["تخفیف"]},
                )
            )
            session.add(
                Destination(
                    task_id=task_id, chat_id=-2002, ref="@clean", title="بدون تبلیغ",
                    overrides={"route_skip": ["تخفیف"]},
                )
            )
            await session.commit()

        client = FakeClient()
        copier = Copier(FakeManager(client))

        assert await copier.process(7, task_id, [FakeMessage(id=1, message="تخفیف ویژه")])
        # مقصد اصلی شرطی ندارد پس می‌گیرد؛ کانال تخفیف بله، کانال تمیز نه
        assert [r.target for r in client.sent] == [-1002, -2001]

        client.sent.clear()
        assert await copier.process(7, task_id, [FakeMessage(id=2, message="خبر عادی")])
        assert [r.target for r in client.sent] == [-1002, -2002]
    finally:
        await db_module.close_db()


@pytest.mark.asyncio
async def test_task_level_keywords_apply_to_the_main_destination(tmp_path, monkeypatch):
    db_module, task_id = await _setup(
        tmp_path, monkeypatch, settings={"route_words": ["مهم"]}
    )
    try:
        from telkap.services.copier import Copier

        client = FakeClient()
        copier = Copier(FakeManager(client))

        assert await copier.process(7, task_id, [FakeMessage(id=1, message="خبر مهم")])
        assert len(client.sent) == 1

        client.sent.clear()
        assert await copier.process(7, task_id, [FakeMessage(id=2, message="خبر عادی")]) is False
        assert client.sent == []
    finally:
        await db_module.close_db()


@pytest.mark.asyncio
async def test_a_destination_can_override_the_task_keywords(tmp_path, monkeypatch):
    """مقصدی که شرط خودش را دارد، شرط کار را نادیده می‌گیرد."""
    db_module, task_id = await _setup(
        tmp_path, monkeypatch, settings={"route_words": ["مهم"]}
    )
    try:
        from telkap.models import Destination
        from telkap.services.copier import Copier

        async with db_module.get_session() as session:
            session.add(
                Destination(
                    task_id=task_id, chat_id=-2001, ref="@all", title="همه",
                    overrides={"route_words": []},
                )
            )
            await session.commit()

        client = FakeClient()
        copier = Copier(FakeManager(client))
        assert await copier.process(7, task_id, [FakeMessage(id=1, message="خبر عادی")])
        assert [r.target for r in client.sent] == [-2001]   # فقط همان مقصد
    finally:
        await db_module.close_db()


# ---------------------------------------------------------- صف تأیید
@pytest.mark.asyncio
async def test_approval_holds_the_post_instead_of_sending_it(tmp_path, monkeypatch):
    db_module, task_id = await _setup(tmp_path, monkeypatch, settings={"approval": True})
    try:
        from telkap.models import PendingPost
        from telkap.services import pending
        from telkap.services.copier import Copier

        client = FakeClient()
        notes: list[tuple[int, str]] = []

        async def notify(user_id, text, markup=None):
            notes.append((user_id, text))

        copier = Copier(FakeManager(client), notifier=notify)
        assert await copier.process(7, task_id, [FakeMessage(id=1, message="خبر تازه")]) is False
        assert client.sent == []      # هیچ‌چیز منتشر نشد

        queued = await pending.listing(7, reason=PendingPost.REASON_APPROVAL)
        assert len(queued) == 1
        assert queued[0].preview == "خبر تازه"
        assert notes and "منتظر تأیید" in notes[0][1]
    finally:
        await db_module.close_db()


@pytest.mark.asyncio
async def test_a_released_post_is_not_held_again(tmp_path, monkeypatch):
    """بدون این، پستِ تأییدشده دوباره در صف می‌افتاد و هرگز منتشر نمی‌شد."""
    db_module, task_id = await _setup(tmp_path, monkeypatch, settings={"approval": True})
    try:
        from telkap.models import PendingPost
        from telkap.services import pending
        from telkap.services.copier import Copier

        client = FakeClient()
        copier = Copier(FakeManager(client))
        assert await copier.process(
            7, task_id, [FakeMessage(id=1, message="خبر")],
            released=PendingPost.REASON_APPROVAL
        ) is True
        assert len(client.sent) == 1
        assert await pending.listing(7) == []
    finally:
        await db_module.close_db()


@pytest.mark.asyncio
async def test_the_same_post_is_not_queued_twice(tmp_path, monkeypatch):
    db_module, task_id = await _setup(tmp_path, monkeypatch, settings={"approval": True})
    try:
        from telkap.services import pending
        from telkap.services.copier import Copier

        client = FakeClient()
        copier = Copier(FakeManager(client))
        message = FakeMessage(id=1, message="خبر")
        await copier.process(7, task_id, [message])
        await copier.process(7, task_id, [message])

        assert len(await pending.listing(7)) == 1
    finally:
        await db_module.close_db()


@pytest.mark.asyncio
async def test_filtered_posts_never_reach_the_approval_queue(tmp_path, monkeypatch):
    """صفِ تأیید نباید با پست‌هایی پر شود که فیلترها ردشان کرده‌اند."""
    db_module, task_id = await _setup(
        tmp_path, monkeypatch, settings={"approval": True, "block_with_links": True}
    )
    try:
        from telkap.services import pending
        from telkap.services.copier import Copier

        copier = Copier(FakeManager(FakeClient()))
        await copier.process(
            7, task_id, [FakeMessage(id=1, message="اینجا ببینید https://x.com")]
        )
        assert await pending.listing(7) == []
    finally:
        await db_module.close_db()


@pytest.mark.asyncio
async def test_turning_approval_off_clears_only_its_own_queue(tmp_path, monkeypatch):
    db_module, task_id = await _setup(tmp_path, monkeypatch, settings={})
    try:
        from telkap.models import PendingPost
        from telkap.services import pending

        await pending.hold(
            task_id=task_id, user_id=7, src_chat_id=-1001, message_ids=[1],
            reason=PendingPost.REASON_APPROVAL, text="یکی",
        )
        await pending.hold(
            task_id=task_id, user_id=7, src_chat_id=-1001, message_ids=[2],
            reason=PendingPost.REASON_SCHEDULE, text="دو",
        )

        cleared = await pending.drop_task(
            task_id, reason=PendingPost.REASON_APPROVAL
        )
        assert cleared == 1
        remaining = await pending.listing(7)
        assert [row.reason for row in remaining] == [PendingPost.REASON_SCHEDULE]
    finally:
        await db_module.close_db()


@pytest.mark.asyncio
async def test_forgotten_approvals_are_eventually_dropped(tmp_path, monkeypatch):
    db_module, task_id = await _setup(tmp_path, monkeypatch, settings={})
    try:
        from telkap.models import PendingPost, utcnow
        from telkap.services import pending

        item = await pending.hold(
            task_id=task_id, user_id=7, src_chat_id=-1001, message_ids=[1],
            reason=PendingPost.REASON_APPROVAL, text="قدیمی",
        )
        async with db_module.get_session() as db:
            row = await db.get(PendingPost, item.id)
            row.created_at = utcnow() - timedelta(hours=200)
            await db.commit()

        assert await pending.prune() == 1
        assert await pending.listing(7) == []
    finally:
        await db_module.close_db()


# ------------------------------------------------- زمان‌بندی پیشرفته
def test_next_window_open_lands_on_the_start_hour():
    from telkap.config import get_settings
    from telkap.services.copier import next_window_open

    offset = get_settings().timezone_offset
    moment = next_window_open({"active_from_hour": 8, "active_to_hour": 20})
    local = moment + timedelta(hours=offset)
    assert local.hour == 8 and local.minute == 0


def test_a_round_the_clock_task_never_waits():
    from telkap.models import utcnow
    from telkap.services.copier import next_window_open

    moment = next_window_open({"active_from_hour": 0, "active_to_hour": 0})
    assert moment <= utcnow() + timedelta(seconds=1)


@pytest.mark.asyncio
async def test_posts_outside_working_hours_are_kept_when_asked(tmp_path, monkeypatch):
    db_module, task_id = await _setup(tmp_path, monkeypatch, settings={})
    try:
        from telkap.models import PendingPost, Task
        from telkap.services import cache, pending
        from telkap.services.copier import Copier, local_hour

        # بازه‌ای که قطعاً الان بسته است
        now = local_hour()
        closed = {
            "active_from_hour": (now + 2) % 24,
            "active_to_hour": (now + 3) % 24,
            "hold_outside_hours": True,
        }
        async with db_module.get_session() as db:
            task = await db.get(Task, task_id)
            task.settings = closed
            await db.commit()
        cache.invalidate_task(task_id)

        client = FakeClient()
        copier = Copier(FakeManager(client))
        assert await copier.process(7, task_id, [FakeMessage(id=1, message="شبانه")]) is False
        assert client.sent == []

        queued = await pending.listing(7, reason=PendingPost.REASON_SCHEDULE)
        assert len(queued) == 1
        assert queued[0].release_at is not None
    finally:
        await db_module.close_db()


@pytest.mark.asyncio
async def test_without_the_toggle_out_of_hours_posts_are_still_dropped(
    tmp_path, monkeypatch
):
    """رفتار قبلی باید دست‌نخورده بماند تا کسی غافلگیر نشود."""
    db_module, task_id = await _setup(tmp_path, monkeypatch, settings={})
    try:
        from telkap.models import Task
        from telkap.services import cache, pending
        from telkap.services.copier import Copier, local_hour

        now = local_hour()
        async with db_module.get_session() as db:
            task = await db.get(Task, task_id)
            task.settings = {
                "active_from_hour": (now + 2) % 24,
                "active_to_hour": (now + 3) % 24,
            }
            await db.commit()
        cache.invalidate_task(task_id)

        copier = Copier(FakeManager(FakeClient()))
        assert await copier.process(7, task_id, [FakeMessage(id=1, message="شبانه")]) is False
        assert await pending.listing(7) == []
    finally:
        await db_module.close_db()


@pytest.mark.asyncio
async def test_minimum_gap_spaces_posts_out_instead_of_dropping_them(
    tmp_path, monkeypatch
):
    db_module, task_id = await _setup(
        tmp_path, monkeypatch, settings={"min_gap_seconds": 600}
    )
    try:
        from telkap.models import PendingPost
        from telkap.services import pending
        from telkap.services.copier import Copier

        client = FakeClient()
        copier = Copier(FakeManager(client))

        # اولی فوراً می‌رود چون این کار تا حالا چیزی نفرستاده
        assert await copier.process(7, task_id, [FakeMessage(id=1, message="یک")]) is True
        assert len(client.sent) == 1

        # دومی و سومی باید نوبت بگیرند، نه اینکه دور ریخته شوند
        assert await copier.process(7, task_id, [FakeMessage(id=2, message="دو")]) is False
        assert await copier.process(7, task_id, [FakeMessage(id=3, message="سه")]) is False
        assert len(client.sent) == 1

        queued = await pending.listing(7, reason=PendingPost.REASON_SCHEDULE)
        assert len(queued) == 2
        # هر کدام ۱۰ دقیقه بعد از قبلی — نه هر دو در یک لحظه
        first, second = queued[0].release_at, queued[1].release_at
        assert (second - first).total_seconds() == pytest.approx(600, abs=5)
    finally:
        await db_module.close_db()


@pytest.mark.asyncio
async def test_no_gap_means_no_waiting(tmp_path, monkeypatch):
    db_module, task_id = await _setup(tmp_path, monkeypatch, settings={})
    try:
        from telkap.services import pending
        from telkap.services.copier import Copier

        client = FakeClient()
        copier = Copier(FakeManager(client))
        for index in range(3):
            assert await copier.process(
                7, task_id, [FakeMessage(id=index + 1, message=f"خبر {index}")]
            ) is True
        assert len(client.sent) == 3
        assert await pending.listing(7) == []
    finally:
        await db_module.close_db()


@pytest.mark.asyncio
async def test_only_due_posts_are_released(tmp_path, monkeypatch):
    db_module, task_id = await _setup(tmp_path, monkeypatch, settings={})
    try:
        from telkap.models import PendingPost, utcnow
        from telkap.services import pending

        await pending.hold(
            task_id=task_id, user_id=7, src_chat_id=-1001, message_ids=[1],
            reason=PendingPost.REASON_SCHEDULE, text="رسیده",
            release_at=utcnow() - timedelta(minutes=1),
        )
        await pending.hold(
            task_id=task_id, user_id=7, src_chat_id=-1001, message_ids=[2],
            reason=PendingPost.REASON_SCHEDULE, text="هنوز نه",
            release_at=utcnow() + timedelta(hours=2),
        )
        # منتظر تأیید هرگز خودبه‌خود آزاد نمی‌شود
        await pending.hold(
            task_id=task_id, user_id=7, src_chat_id=-1001, message_ids=[3],
            reason=PendingPost.REASON_APPROVAL, text="منتظر تأیید",
        )

        ready = await pending.due()
        assert [row.preview for row in ready] == ["رسیده"]
    finally:
        await db_module.close_db()


# ------------------------------------------------------------- خلاصه
def test_a_post_without_text_still_gets_a_readable_label():
    from telkap.services.pending import summarize

    assert summarize("", "photo") == "🖼 عکس بدون متن"
    assert summarize("   ", "poll") == "📊 نظرسنجی"
    assert summarize("سلام دنیا", "photo") == "سلام دنیا"


def test_task_settings_do_not_share_one_list():
    """بدون کپی کردن فهرست‌ها، ویرایش یک کار روی بقیه هم اثر می‌گذاشت."""
    from telkap.services.defaults import merged_settings

    one = merged_settings({})
    two = merged_settings({})
    one["route_words"].append("تخفیف")
    assert two["route_words"] == []
    assert one["allowed_media"] is not two["allowed_media"]
