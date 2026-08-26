"""تست دور سیزدهم: تشخیص تکراری هوشمند، قالب‌ها، نمایش ساده، خلاصه و پیام هدفمند."""
from __future__ import annotations

import pytest

from tests.test_copier import FakeClient, FakeManager, FakeMessage, _setup


# ------------------------------------------------------- یکسان‌سازی متن
def test_signature_and_emoji_do_not_make_two_posts_different():
    """همان چیزی که بین سه نسخه‌ی یک خبر فرق می‌کند."""
    from telkap.services.dedupe import normalize

    one = "قیمت دلار امروز بالا رفت.\n\n@ChannelOne"
    two = "🔴 قیمت دلار امروز بالا رفت. 🔥\n\n@ChannelTwo"
    assert normalize(one) == normalize(two)


def test_arabic_and_persian_letters_are_treated_as_one():
    from telkap.services.dedupe import normalize

    assert normalize("قيمت كالا") == normalize("قیمت کالا")


def test_persian_and_english_digits_are_treated_as_one():
    from telkap.services.dedupe import normalize

    assert normalize("۱۲۰ هزار تومان") == normalize("120 هزار تومان")


def test_links_and_hashtags_are_dropped_before_comparing():
    from telkap.services.dedupe import normalize

    assert normalize("خبر مهم https://t.me/a #فوری") == "خبر مهم"


# --------------------------------------------------- سه سطح سخت‌گیری
def test_exact_mode_only_matches_identical_text():
    from telkap.services.dedupe import MODE_EXACT, mode_of, normalized_hash
    from telkap.services.filters import MessageFacts, content_hash

    def ex(t):
        return content_hash(MessageFacts(text=t, media_kind="text"))

    a = "خبر مهم امروز درباره‌ی بازار ارز"
    b = "خبر مهم امروز درباره‌ی بازار ارز 🔥"
    assert ex(a) != ex(b)
    assert normalized_hash("text", a) == normalized_hash("text", b)
    assert mode_of({"duplicate_mode": MODE_EXACT}) == MODE_EXACT


def test_an_unknown_mode_falls_back_to_the_safe_default():
    from telkap.services.dedupe import MODE_NORMALIZED, mode_of

    assert mode_of({}) == MODE_NORMALIZED
    assert mode_of({"duplicate_mode": "چیز عجیب"}) == MODE_NORMALIZED


def test_short_posts_fall_back_to_exact_matching():
    """«🔥» و «🔥🔥» هر دو خالی می‌شوند؛ نباید تکراری اعلام شوند."""
    from telkap.services.dedupe import normalized_hash

    assert normalized_hash("text", "🔥") != normalized_hash("text", "🔥🔥")
    assert normalized_hash("text", "سلام") != normalized_hash("text", "خداحافظ")


def test_similar_mode_catches_a_lightly_reworded_post():
    from telkap.services.dedupe import looks_similar, simhash

    a = "قیمت دلار امروز در بازار آزاد به ۱۲۰ هزار تومان رسید و کارشناسان نگران‌اند"
    b = "قیمت دلار امروز در بازار آزاد به ۱۲۰ هزار تومان رسید 🔥 جوین کنید"
    far = "تیم ملی فوتبال ایران در دیدار دوستانه مقابل حریف خود به پیروزی رسید"

    assert looks_similar(simhash(a), simhash(b)) is True
    assert looks_similar(simhash(a), simhash(far)) is False


def test_similarity_fingerprint_fits_in_the_database():
    """۶۴ بیتِ بی‌علامت در ستون INTEGER جا نمی‌شد."""
    from telkap.services.dedupe import simhash

    for text in ("یک متن نمونه برای آزمایش", "متن دیگری کاملاً متفاوت", ""):
        value = simhash(text)
        assert -(2**63) <= value < 2**63


def test_an_empty_fingerprint_never_matches():
    from telkap.services.dedupe import looks_similar, simhash

    assert simhash("") == 0
    assert looks_similar(0, simhash("چیزی")) is False


@pytest.mark.asyncio
async def test_the_same_news_with_different_signatures_lands_once(tmp_path, monkeypatch):
    """سناریوی واقعی: یک خبر از سه کانال، هرکدام با امضای خودش."""
    db_module, task_id = await _setup(
        tmp_path,
        monkeypatch,
        settings={"skip_cross_duplicates": True, "duplicate_mode": "normalized"},
    )
    try:
        from telkap.models import Task
        from telkap.services import cache
        from telkap.services.copier import Copier

        async with db_module.get_session() as db:
            for index in (2, 3):
                db.add(
                    Task(
                        user_id=7, title=f"مبدا {index}",
                        source_ref=f"@src{index}", source_id=-1000 - index,
                        dest_ref="@dst", dest_id=-1002,
                        settings={
                            "skip_cross_duplicates": True,
                            "duplicate_mode": "normalized",
                        },
                    )
                )
            await db.commit()
            rows = await db.execute(
                Task.__table__.select().where(Task.user_id == 7)
            )
            ids = [row.id for row in rows]

        client = FakeClient()
        copier = Copier(FakeManager(client))
        news = "قیمت دلار امروز در بازار آزاد بالا رفت و به رکورد تازه‌ای رسید"

        for index, tid in enumerate(ids):
            cache.invalidate_task(tid)
            await copier.process(
                7, tid, [FakeMessage(id=index + 1, message=f"{news}\n\n@Channel{index}")]
            )

        assert len(client.sent) == 1        # فقط یکی رفت، نه سه تا
    finally:
        await db_module.close_db()


# --------------------------------------------------------------- قالب‌ها
def test_a_template_only_touches_the_keys_it_names():
    from telkap.services import templates
    from telkap.services.defaults import merged_settings

    cfg = merged_settings({"footer": "@MyChannel", "watermark_text": "من"})
    news = templates.get("news")
    out = templates.apply(cfg, news)

    assert out["remove_links"] is True          # از قالب
    assert out["footer"] == "@MyChannel"        # دست‌نخورده
    assert out["watermark_text"] == "من"        # دست‌نخورده


def test_a_template_reports_what_it_will_change():
    from telkap.services import templates
    from telkap.services.defaults import merged_settings

    cfg = merged_settings({})
    news = templates.get("news")
    assert "remove_links" in templates.changes(cfg, news)

    already = templates.apply(cfg, news)
    assert templates.changes(already, news) == []


def test_the_mirror_template_really_turns_everything_off():
    from telkap.services import templates
    from telkap.services.defaults import merged_settings

    noisy = merged_settings(
        {"remove_links": True, "block_ads": True, "footer": "چیزی"}
    )
    out = templates.apply(noisy, templates.get("mirror"))
    assert out["remove_links"] is False
    assert out["block_ads"] is False
    assert out["footer"] == ""


def test_every_template_only_uses_real_settings():
    """یک کلید اشتباه در قالب، بی‌صدا نادیده گرفته می‌شد."""
    from telkap.services import templates
    from telkap.services.defaults import DEFAULT_SETTINGS

    for template in templates.TEMPLATES:
        unknown = set(template.values) - set(DEFAULT_SETTINGS)
        assert not unknown, f"{template.code}: {unknown}"


def test_template_codes_are_unique():
    from telkap.services import templates

    codes = [t.code for t in templates.TEMPLATES]
    assert len(codes) == len(set(codes))


# ------------------------------------------------------- نمایش ساده
def test_the_simple_menu_hides_the_advanced_options():
    from telkap.keyboards import task_menu
    from telkap.models import Task

    task = Task(id=1, user_id=7, title="کار", source_ref="@a", dest_ref="@b")
    simple = [b.callback_data for r in task_menu(task).inline_keyboard for b in r]
    pro = [b.callback_data for r in task_menu(task, pro=True).inline_keyboard for b in r]

    assert "set:cfg:1" not in simple and "set:cfg:1" in pro
    assert "set:send:1" not in simple and "set:send:1" in pro
    assert "task:pro:1" in simple        # راه رفتن به حالت کامل هست

    # چیزهایی که در هر دو حالت باید باشند
    for essential in ("set:clean:1", "dest:list:1", "tpl:list:1", "task:del:1"):
        assert essential in simple and essential in pro


def test_the_pending_badge_shows_only_when_something_waits():
    from telkap.keyboards import task_menu
    from telkap.models import Task

    task = Task(id=1, user_id=7, title="کار", source_ref="@a", dest_ref="@b")
    quiet = [b.callback_data for r in task_menu(task).inline_keyboard for b in r]
    busy = [
        b.callback_data for r in task_menu(task, waiting=3).inline_keyboard for b in r
    ]
    assert "pend:list:1" not in quiet
    assert "pend:list:1" in busy


# ------------------------------------------------------ خلاصه‌ی روزانه
def test_a_quiet_day_is_not_worth_a_message():
    from telkap.services.digest import Summary

    assert Summary().worth_sending is False
    assert Summary(skipped=50).worth_sending is False       # فقط رد شده
    assert Summary(copied=1).worth_sending is True
    assert Summary(failed=1).worth_sending is True
    assert Summary(stopped=["کار من"]).worth_sending is True
    assert Summary(days_left=2).worth_sending is True       # هشدار انقضا
    assert Summary(days_left=20).worth_sending is False


def test_the_summary_mentions_what_matters():
    from telkap.services.digest import Summary, render

    text = render(Summary(copied=12, failed=2, waiting=3, stopped=["کانال خبر"]))
    assert "کپی‌شده" in text and "ناموفق" in text
    assert "منتظر تأیید" in text
    assert "کانال خبر" in text


@pytest.mark.asyncio
async def test_the_digest_goes_only_to_those_who_asked(tmp_path, monkeypatch):
    db_module, task_id = await _setup(tmp_path, monkeypatch, settings={})
    try:
        from telkap.models import DailyStat, Task, User
        from telkap.services import digest

        async with db_module.get_session() as db:
            db.add(User(id=8, first_name="مشترک خلاصه", daily_digest=True))
            other = Task(
                user_id=8, title="کار او", source_ref="@s", dest_ref="@d"
            )
            db.add(other)
            await db.commit()
            await db.refresh(other)
            db.add(DailyStat(task_id=other.id, user_id=8, day="2026-01-01", copied=5))
            db.add(DailyStat(task_id=task_id, user_id=7, day="2026-01-01", copied=9))
            await db.commit()

        got: list[int] = []

        async def notify(user_id, _text, _markup=None):
            got.append(user_id)

        assert await digest.run_once(notify, day="2026-01-01") == 1
        assert got == [8]        # کاربر ۷ روشنش نکرده بود
    finally:
        await db_module.close_db()


@pytest.mark.asyncio
async def test_a_banned_user_gets_no_digest(tmp_path, monkeypatch):
    db_module, task_id = await _setup(tmp_path, monkeypatch, settings={})
    try:
        from telkap.models import DailyStat, User
        from telkap.services import digest

        async with db_module.get_session() as db:
            db.add(
                User(id=8, first_name="مسدود", daily_digest=True, is_banned=True)
            )
            await db.commit()
            db.add(DailyStat(task_id=task_id, user_id=8, day="2026-01-01", copied=5))
            await db.commit()

        got: list[int] = []

        async def notify(user_id, _text, _markup=None):
            got.append(user_id)

        assert await digest.run_once(notify, day="2026-01-01") == 0
        assert got == []
    finally:
        await db_module.close_db()


# ------------------------------------------------------- پیام هدفمند
@pytest.mark.asyncio
async def test_each_audience_holds_the_right_people(tmp_path, monkeypatch):
    db_module, _ = await _setup(tmp_path, monkeypatch, settings={})
    try:
        from telkap.models import PaymentRequest, User
        from telkap.services import audience

        # کاربر ۷ از _setup: اشتراک فعال دارد و یک کار ساخته
        async with db_module.get_session() as db:
            db.add(User(id=8, first_name="خریدِ گذشته"))
            db.add(User(id=9, first_name="تازه‌وارد"))
            db.add(User(id=10, first_name="وصل ولی بی‌کار", session_enc="x"))
            db.add(User(id=11, first_name="مسدود", is_banned=True))
            await db.commit()
            # ۸ قبلاً خریده ولی اشتراکش تمام شده
            db.add(
                PaymentRequest(
                    user_id=8, plan_code="month", amount_toman=1,
                    status=PaymentRequest.STATUS_APPROVED,
                )
            )
            await db.commit()

        assert set(await audience.members(audience.ACTIVE)) == {7}
        assert set(await audience.members(audience.EXPIRED)) == {8}
        assert set(await audience.members(audience.NEVER)) == {9, 10}
        assert set(await audience.members(audience.LINKED)) == {10}
        assert set(await audience.members(audience.IDLE)) == {10}

        # مسدودها در هیچ گروهی نیستند
        for segment in audience.SEGMENTS:
            assert 11 not in await audience.members(segment.code)
    finally:
        await db_module.close_db()


@pytest.mark.asyncio
async def test_audience_sizes_match_the_member_lists(tmp_path, monkeypatch):
    db_module, _ = await _setup(tmp_path, monkeypatch, settings={})
    try:
        from telkap.services import audience

        counts = await audience.sizes()
        for segment in audience.SEGMENTS:
            assert counts[segment.code] == len(await audience.members(segment.code))
    finally:
        await db_module.close_db()
