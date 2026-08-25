"""تست دور پنجم: سقف پیام روزانه، اعتبارهای مصرفی، طرح‌ها و عضویت اجباری."""
from __future__ import annotations

import pytest

from tests.test_copier import FakeClient, FakeManager, FakeMessage, _setup


# ------------------------------------------------------------- طرح‌ها
def test_every_plan_is_self_consistent():
    from telkap.plans import FEAT_HISTORY, FEAT_WATERMARK, PLANS, PURCHASABLE, UNLIMITED

    for plan in PLANS.values():
        assert plan.days > 0
        assert plan.max_tasks >= 1
        assert plan.max_destinations >= 1
        assert plan.daily_messages > 0 or plan.daily_messages == UNLIMITED
        assert plan.extra_destinations == plan.max_destinations - 1
        # داشتن قابلیت و داشتن سهمیه باید با هم بخوانند
        for feature in (FEAT_WATERMARK, FEAT_HISTORY):
            assert plan.has(feature) == (plan.quota(feature) != 0), (
                f"{plan.code}: {feature}"
            )
    # طرح‌های قابل خرید باید قیمت داشته باشند و آزمایشی نباشند
    for plan in PURCHASABLE:
        assert plan.price_toman > 0
        assert plan.code != "trial"


def test_plan_prices_and_quota_match_the_price_list():
    from telkap.plans import CUSTOM, MONTH, TWO_WEEK, UNLIMITED, WEEK

    assert (WEEK.price_toman, TWO_WEEK.price_toman) == (129_000, 229_000)
    assert (MONTH.price_toman, CUSTOM.price_toman) == (429_000, 890_000)
    assert MONTH.daily_messages == 4_000
    assert CUSTOM.daily_messages == UNLIMITED
    assert CUSTOM.daily_label == "نامحدود"


def test_watermark_and_history_quotas_match_the_price_list():
    from telkap.plans import CUSTOM, MONTH, TRIAL, TWO_WEEK, UNLIMITED, WEEK

    assert [p.watermark_daily for p in (WEEK, TWO_WEEK, MONTH)] == [10, 20, 50]
    assert [p.history_daily for p in (TWO_WEEK, MONTH)] == [50, 100]
    assert WEEK.history_daily == 0              # در این طرح نیست
    assert (TRIAL.watermark_daily, TRIAL.history_daily) == (0, 0)
    assert CUSTOM.watermark_daily == UNLIMITED
    assert CUSTOM.history_daily == UNLIMITED
    assert TRIAL.watermark_label == "ندارد"
    assert MONTH.history_label == "۱۰۰"


def test_higher_plans_are_never_worse():
    """هر طرح بالاتر باید در همه‌ی سقف‌ها دست‌کم برابر طرح پایین‌تر باشد."""
    from telkap.plans import MONTH, TRIAL, TWO_WEEK, WEEK

    ladder = [TRIAL, WEEK, TWO_WEEK, MONTH]
    for lower, higher in zip(ladder, ladder[1:], strict=False):
        assert higher.max_tasks >= lower.max_tasks
        assert higher.max_destinations >= lower.max_destinations
        assert higher.daily_messages >= lower.daily_messages
        assert higher.watermark_daily >= lower.watermark_daily
        assert higher.history_daily >= lower.history_daily
        assert lower.features <= higher.features


def test_credit_price_scales_with_quantity():
    from telkap.plans import CREDIT_HISTORY, CREDIT_WATERMARK, credit_price

    assert credit_price(CREDIT_WATERMARK, 100) == 100_000
    assert credit_price(CREDIT_HISTORY, 250) == 250_000
    assert credit_price(CREDIT_WATERMARK, 0) == 0
    assert credit_price("چیز نامعتبر", 10) == 0


# --------------------------------------------------- سقف پیام روزانه
@pytest.mark.asyncio
async def test_daily_quota_stops_copying_after_the_limit(tmp_path, monkeypatch):
    db_module, task_id = await _setup(tmp_path, monkeypatch, settings={})
    try:
        from telkap.services import cache
        from telkap.services.copier import Copier
        from telkap.services.ratelimit import daily_quota

        daily_quota.forget(7)

        # پلن کاربر را به یک سقف کوچک محدود می‌کنیم
        plan = await cache.get_plan(7)
        from dataclasses import replace

        monkeypatch.setattr(
            cache, "get_plan", lambda uid: _async(replace(plan, daily_messages=2))
        )

        warnings: list[tuple[int, str]] = []

        async def notifier(user_id, text):
            warnings.append((user_id, text))

        client = FakeClient()
        copier = Copier(FakeManager(client), notifier=notifier)

        assert await copier.process(7, task_id, [FakeMessage(id=1, message="یک")]) is True
        assert await copier.process(7, task_id, [FakeMessage(id=2, message="دو")]) is True
        # سومی باید رد شود
        assert await copier.process(7, task_id, [FakeMessage(id=3, message="سه")]) is False
        assert len(client.sent) == 2
        assert warnings and "سقف روزانه" in warnings[0][1]
    finally:
        daily_quota.forget(7)
        await db_module.close_db()


@pytest.mark.asyncio
async def test_unlimited_plan_has_no_daily_ceiling(tmp_path, monkeypatch):
    db_module, task_id = await _setup(tmp_path, monkeypatch, settings={})
    try:
        from dataclasses import replace

        from telkap.plans import UNLIMITED
        from telkap.services import cache
        from telkap.services.copier import Copier
        from telkap.services.ratelimit import daily_quota

        daily_quota.forget(7)
        plan = await cache.get_plan(7)
        monkeypatch.setattr(
            cache, "get_plan", lambda uid: _async(replace(plan, daily_messages=UNLIMITED))
        )

        client = FakeClient()
        copier = Copier(FakeManager(client))
        for index in range(6):
            assert await copier.process(
                7, task_id, [FakeMessage(id=index + 1, message=f"پیام {index}")]
            ) is True
        assert len(client.sent) == 6
    finally:
        daily_quota.forget(7)
        await db_module.close_db()


def test_daily_quota_counter_rolls_over_at_midnight():
    from telkap.services.ratelimit import DailyQuota

    quota = DailyQuota()
    assert quota.allow(1, "2026-01-01", limit=1) is True
    assert quota.allow(1, "2026-01-01", limit=1) is False
    # روز تازه، شمارنده از صفر
    assert quota.allow(1, "2026-01-02", limit=1) is True


def test_daily_quota_seed_survives_restart():
    """پس از ری‌استارت، مصرف امروز از دیتابیس بازخوانی می‌شود."""
    from telkap.services.ratelimit import DailyQuota

    quota = DailyQuota()
    assert quota.is_seeded(5, "2026-01-01") is False
    quota.seed(5, "2026-01-01", used=9)
    assert quota.is_seeded(5, "2026-01-01") is True
    assert quota.remaining(5, "2026-01-01", limit=10) == 1
    assert quota.allow(5, "2026-01-01", limit=10) is True
    assert quota.allow(5, "2026-01-01", limit=10) is False


def _async(value):
    """یک coroutine ساده که همان مقدار را برمی‌گرداند."""
    async def _wrapped():
        return value

    return _wrapped()


# ------------------------------------------------------------- اعتبارها
@pytest.mark.asyncio
async def test_credits_add_and_consume(tmp_path, monkeypatch):
    db_module, _task_id = await _setup(tmp_path, monkeypatch, settings={})
    try:
        from telkap.plans import CREDIT_WATERMARK
        from telkap.services import credits

        assert await credits.balance(7, CREDIT_WATERMARK) == 0
        assert await credits.add(7, CREDIT_WATERMARK, 100) == 100
        assert await credits.consume(7, CREDIT_WATERMARK, 30) is True
        assert await credits.balance(7, CREDIT_WATERMARK) == 70
        # بیش از مانده مصرف نمی‌شود و چیزی هم کم نمی‌کند
        assert await credits.consume(7, CREDIT_WATERMARK, 100) is False
        assert await credits.balance(7, CREDIT_WATERMARK) == 70
    finally:
        await db_module.close_db()


@pytest.mark.asyncio
async def test_credits_never_go_negative(tmp_path, monkeypatch):
    db_module, _task_id = await _setup(tmp_path, monkeypatch, settings={})
    try:
        from telkap.plans import CREDIT_HISTORY
        from telkap.services import credits

        await credits.add(7, CREDIT_HISTORY, 10)
        assert await credits.add(7, CREDIT_HISTORY, -50) == 0
    finally:
        await db_module.close_db()


@pytest.mark.asyncio
async def test_credit_purchase_is_granted_on_approval(tmp_path, monkeypatch):
    db_module, _task_id = await _setup(tmp_path, monkeypatch, settings={})
    try:
        from telkap.plans import CREDIT_WATERMARK, credit_price
        from telkap.services import credits, payments

        amount = credit_price(CREDIT_WATERMARK, 200)
        request = await payments.create_credit_request(7, CREDIT_WATERMARK, 200, amount)
        assert request is not None
        assert request.amount_toman == 200_000
        await payments.attach_receipt(request.id, "file-id", "photo")

        approved, sub = await payments.approve(request.id, admin_id=1)
        assert approved is not None
        assert sub is None                     # خرید اعتبار اشتراک نمی‌دهد
        assert await credits.balance(7, CREDIT_WATERMARK) == 200
    finally:
        await db_module.close_db()


@pytest.mark.asyncio
async def test_plan_purchase_still_grants_subscription(tmp_path, monkeypatch):
    db_module, _task_id = await _setup(tmp_path, monkeypatch, settings={})
    try:
        from telkap.services import payments, subscription

        before = await subscription.remaining_days(7)
        request = await payments.create_request(7, "week")
        await payments.attach_receipt(request.id, "file-id", "photo")
        approved, sub = await payments.approve(request.id, admin_id=1)
        assert approved is not None and sub is not None
        assert await subscription.remaining_days(7) > before
    finally:
        await db_module.close_db()


class PhotoClient(FakeClient):
    """کلاینت ساختگی که دانلود رسانه را هم پشتیبانی می‌کند."""

    def __init__(self, tmp_path) -> None:
        super().__init__()
        self._dir = tmp_path

    async def download_media(self, message, file=None):
        path = self._dir / f"img-{getattr(message, 'id', 0)}.jpg"
        path.write_bytes(b"fake-image")
        return str(path)


def _stub_watermark(monkeypatch, tmp_path):
    """واترمارک واقعی به Pillow نیاز دارد؛ اینجا فقط مسیر برگردانده می‌شود."""
    from telkap.services import copier as copier_module

    monkeypatch.setattr(copier_module, "classify_media", lambda m: "photo")
    monkeypatch.setattr(
        copier_module, "add_text_watermark", lambda src, *args, **kwargs: str(src)
    )


WM_SETTINGS = {"watermark_enabled": True, "watermark_text": "@me"}


def _use_plan(monkeypatch, plan, **overrides):
    """پلن کاربر را برای این تست عوض می‌کند."""
    from dataclasses import replace

    from telkap.services import cache

    monkeypatch.setattr(
        cache, "get_plan", lambda uid: _async(replace(plan, **overrides))
    )


async def _copy_photo(copier, task_id, msg_id):
    # متن هر پست متفاوت است تا فیلتر «پست تکراری» جلویش را نگیرد
    return await copier.process(
        7, task_id, [FakeMessage(id=msg_id, message=f"عکس {msg_id}", media=object())]
    )


@pytest.mark.asyncio
async def test_watermark_uses_plan_quota_before_credit(tmp_path, monkeypatch):
    """سهمیه‌ی رایگان طرح اول مصرف می‌شود، اعتبار دست‌نخورده می‌ماند."""
    db_module, task_id = await _setup(tmp_path, monkeypatch, settings=WM_SETTINGS)
    try:
        from telkap.plans import CREDIT_WATERMARK, FEAT_WATERMARK, WEEK
        from telkap.services import credits, entitlement
        from telkap.services.copier import Copier, today_key

        _stub_watermark(monkeypatch, tmp_path)
        _use_plan(monkeypatch, WEEK)          # سهمیه‌ی ۱۰ تایی روزانه
        await credits.add(7, CREDIT_WATERMARK, 5)

        copier = Copier(FakeManager(PhotoClient(tmp_path)))
        assert await _copy_photo(copier, task_id, 1) is True

        day = today_key()
        assert await entitlement.used_today(7, FEAT_WATERMARK, day) == 1
        assert await credits.balance(7, CREDIT_WATERMARK) == 5   # دست‌نخورده
    finally:
        await db_module.close_db()


@pytest.mark.asyncio
async def test_watermark_falls_back_to_credit_when_quota_is_gone(tmp_path, monkeypatch):
    """با تمام شدن سهمیه‌ی روزانه، از اعتبار خریداری‌شده برداشته می‌شود."""
    db_module, task_id = await _setup(tmp_path, monkeypatch, settings=WM_SETTINGS)
    try:
        from telkap.plans import CREDIT_WATERMARK, FEAT_WATERMARK, WEEK
        from telkap.services import credits, entitlement
        from telkap.services.copier import Copier, today_key

        _stub_watermark(monkeypatch, tmp_path)
        _use_plan(monkeypatch, WEEK, watermark_daily=1)   # فقط یک سهمیه
        await credits.add(7, CREDIT_WATERMARK, 5)

        copier = Copier(FakeManager(PhotoClient(tmp_path)))
        assert await _copy_photo(copier, task_id, 1) is True
        assert await _copy_photo(copier, task_id, 2) is True

        day = today_key()
        assert await entitlement.used_today(7, FEAT_WATERMARK, day) == 1
        assert await credits.balance(7, CREDIT_WATERMARK) == 4   # دومی از اعتبار
    finally:
        await db_module.close_db()


@pytest.mark.asyncio
async def test_watermark_stops_when_quota_and_credit_run_out(tmp_path, monkeypatch):
    """پست‌ها می‌روند ولی بدون واترمارک، و کاربر یک بار خبردار می‌شود."""
    db_module, task_id = await _setup(tmp_path, monkeypatch, settings=WM_SETTINGS)
    try:
        from telkap.plans import CREDIT_WATERMARK, WEEK
        from telkap.services import credits
        from telkap.services.copier import Copier

        _stub_watermark(monkeypatch, tmp_path)
        _use_plan(monkeypatch, WEEK, watermark_daily=1)

        warnings: list[tuple[int, str]] = []

        async def notifier(user_id, text):
            warnings.append((user_id, text))

        client = PhotoClient(tmp_path)
        copier = Copier(FakeManager(client), notifier=notifier)
        assert await _copy_photo(copier, task_id, 1) is True
        assert await _copy_photo(copier, task_id, 2) is True   # ارسال شد، بدون واترمارک

        assert len(client.sent) == 2
        assert await credits.balance(7, CREDIT_WATERMARK) == 0  # منفی نمی‌شود
        assert warnings and "واترمارک" in warnings[0][1]
    finally:
        await db_module.close_db()


@pytest.mark.asyncio
async def test_unlimited_plan_never_touches_quota_or_credit(tmp_path, monkeypatch):
    db_module, task_id = await _setup(tmp_path, monkeypatch, settings=WM_SETTINGS)
    try:
        from telkap.plans import CREDIT_WATERMARK, CUSTOM, FEAT_WATERMARK
        from telkap.services import credits, entitlement
        from telkap.services.copier import Copier, today_key

        _stub_watermark(monkeypatch, tmp_path)
        _use_plan(monkeypatch, CUSTOM)
        await credits.add(7, CREDIT_WATERMARK, 3)

        copier = Copier(FakeManager(PhotoClient(tmp_path)))
        for index in range(4):
            assert await _copy_photo(copier, task_id, index + 1) is True

        day = today_key()
        assert await entitlement.used_today(7, FEAT_WATERMARK, day) == 0
        assert await credits.balance(7, CREDIT_WATERMARK) == 3
    finally:
        await db_module.close_db()


# ------------------------------------------------- سهمیه + اعتبار با هم
@pytest.mark.asyncio
async def test_reserve_splits_between_quota_and_credit(tmp_path, monkeypatch):
    db_module, _task_id = await _setup(tmp_path, monkeypatch, settings={})
    try:
        from telkap.plans import CREDIT_HISTORY, FEAT_HISTORY, TWO_WEEK
        from telkap.services import credits, entitlement

        await credits.add(7, CREDIT_HISTORY, 30)
        day = "2026-01-01"

        # سهمیه ۵۰ است؛ درخواست ۷۰ یعنی ۵۰ از سهمیه و ۲۰ از اعتبار
        grant = await entitlement.reserve(7, FEAT_HISTORY, 70, TWO_WEEK, day)
        assert grant is not None
        assert (grant.from_quota, grant.from_credits) == (50, 20)
        assert grant.total == 70
        assert await credits.balance(7, CREDIT_HISTORY) == 10
        assert await entitlement.used_today(7, FEAT_HISTORY, day) == 50
    finally:
        await db_module.close_db()


@pytest.mark.asyncio
async def test_reserve_takes_nothing_when_total_is_short(tmp_path, monkeypatch):
    """اگر مجموع سهمیه و اعتبار کم باشد، هیچ‌کدام نباید کم شوند."""
    db_module, _task_id = await _setup(tmp_path, monkeypatch, settings={})
    try:
        from telkap.plans import CREDIT_HISTORY, FEAT_HISTORY, TWO_WEEK
        from telkap.services import credits, entitlement

        await credits.add(7, CREDIT_HISTORY, 5)
        day = "2026-01-01"

        assert await entitlement.reserve(7, FEAT_HISTORY, 100, TWO_WEEK, day) is None
        assert await credits.balance(7, CREDIT_HISTORY) == 5
        assert await entitlement.used_today(7, FEAT_HISTORY, day) == 0
    finally:
        await db_module.close_db()


@pytest.mark.asyncio
async def test_release_gives_quota_and_credit_back(tmp_path, monkeypatch):
    db_module, _task_id = await _setup(tmp_path, monkeypatch, settings={})
    try:
        from telkap.plans import CREDIT_HISTORY, FEAT_HISTORY, TWO_WEEK
        from telkap.services import credits, entitlement

        await credits.add(7, CREDIT_HISTORY, 30)
        day = "2026-01-01"

        grant = await entitlement.reserve(7, FEAT_HISTORY, 70, TWO_WEEK, day)
        await entitlement.release(7, grant, day)

        assert await credits.balance(7, CREDIT_HISTORY) == 30
        assert await entitlement.used_today(7, FEAT_HISTORY, day) == 0
    finally:
        await db_module.close_db()


@pytest.mark.asyncio
async def test_quota_is_per_day_not_per_subscription(tmp_path, monkeypatch):
    db_module, _task_id = await _setup(tmp_path, monkeypatch, settings={})
    try:
        from telkap.plans import FEAT_HISTORY, TWO_WEEK
        from telkap.services import entitlement

        assert await entitlement.reserve(7, FEAT_HISTORY, 50, TWO_WEEK, "2026-01-01")
        assert await entitlement.quota_left(7, FEAT_HISTORY, TWO_WEEK, "2026-01-01") == 0
        # روز بعد سهمیه دوباره کامل است
        assert await entitlement.quota_left(7, FEAT_HISTORY, TWO_WEEK, "2026-01-02") == 50
    finally:
        await db_module.close_db()


@pytest.mark.asyncio
async def test_plan_without_the_feature_uses_credit_only(tmp_path, monkeypatch):
    db_module, _task_id = await _setup(tmp_path, monkeypatch, settings={})
    try:
        from telkap.plans import CREDIT_HISTORY, FEAT_HISTORY, WEEK
        from telkap.services import credits, entitlement

        day = "2026-01-01"
        assert await entitlement.quota_left(7, FEAT_HISTORY, WEEK, day) == 0
        assert await entitlement.reserve(7, FEAT_HISTORY, 10, WEEK, day) is None

        await credits.add(7, CREDIT_HISTORY, 10)
        grant = await entitlement.reserve(7, FEAT_HISTORY, 10, WEEK, day)
        assert grant is not None
        assert (grant.from_quota, grant.from_credits) == (0, 10)
    finally:
        await db_module.close_db()


@pytest.mark.asyncio
async def test_affordable_reports_without_charging(tmp_path, monkeypatch):
    db_module, _task_id = await _setup(tmp_path, monkeypatch, settings={})
    try:
        from telkap.plans import CREDIT_HISTORY, FEAT_HISTORY, TWO_WEEK
        from telkap.services import credits, entitlement

        day = "2026-01-01"
        assert await entitlement.affordable(7, FEAT_HISTORY, 50, TWO_WEEK, day) is True
        assert await entitlement.affordable(7, FEAT_HISTORY, 60, TWO_WEEK, day) is False

        await credits.add(7, CREDIT_HISTORY, 10)
        assert await entitlement.affordable(7, FEAT_HISTORY, 60, TWO_WEEK, day) is True
        # هیچ‌چیز مصرف نشده است
        assert await credits.balance(7, CREDIT_HISTORY) == 10
        assert await entitlement.used_today(7, FEAT_HISTORY, day) == 0
    finally:
        await db_module.close_db()


# -------------------------------------------------------- عضویت اجباری
@pytest.mark.asyncio
async def test_force_join_channels_add_toggle_remove(tmp_path, monkeypatch):
    db_module, _task_id = await _setup(tmp_path, monkeypatch, settings={})
    try:
        from telkap.services import forcejoin

        forcejoin.invalidate()
        assert await forcejoin.active_channels() == []

        first = await forcejoin.add("@news", "اخبار", admin_id=1)
        second = await forcejoin.add("https://t.me/sport", "ورزش", admin_id=1)
        assert first.ref == "news" and second.ref == "sport"

        forcejoin.invalidate()
        assert len(await forcejoin.active_channels()) == 2

        assert await forcejoin.toggle(first.id) is False
        forcejoin.invalidate()
        assert len(await forcejoin.active_channels()) == 1

        assert await forcejoin.remove(second.id) is True
        forcejoin.invalidate()
        assert await forcejoin.active_channels() == []
    finally:
        forcejoin.invalidate()
        await db_module.close_db()


@pytest.mark.asyncio
async def test_force_join_add_is_idempotent(tmp_path, monkeypatch):
    db_module, _task_id = await _setup(tmp_path, monkeypatch, settings={})
    try:
        from telkap.services import forcejoin

        forcejoin.invalidate()
        first = await forcejoin.add("@same", "یک", admin_id=1)
        again = await forcejoin.add("same", "دو", admin_id=1)
        assert first.id == again.id
        assert len(await forcejoin.all_channels()) == 1
    finally:
        forcejoin.invalidate()
        await db_module.close_db()


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("@channel", "channel"),
        ("channel", "channel"),
        ("https://t.me/channel", "channel"),
        ("http://t.me/channel", "channel"),
        ("t.me/channel/", "channel"),
        ("-1001234567890", "-1001234567890"),
        ("   @spaced  ", "spaced"),
        ("", ""),
    ],
)
def test_force_join_normalizes_input(raw, expected):
    from telkap.services import forcejoin

    assert forcejoin.normalize(raw) == expected


def test_force_join_channel_url_prefers_invite_link():
    from telkap.models import ForceJoinChannel

    public = ForceJoinChannel(ref="mychannel", invite_link="")
    assert public.url == "https://t.me/mychannel"

    private = ForceJoinChannel(ref="-100123", invite_link="https://t.me/+abcdef")
    assert private.url == "https://t.me/+abcdef"


# ------------------------------------------------------------- راهنما
def test_guide_sections_fit_in_a_telegram_message():
    """هر بخش راهنما باید در یک پیام تلگرام جا شود (سقف ۴۰۹۶ نویسه)."""
    from telkap.handlers import guide

    for key, (title, builder) in guide.SECTIONS.items():
        body = builder()
        assert body, f"بخش {key} خالی است"
        assert len(body) < 4000, f"بخش {key} خیلی بلند است: {len(body)}"
        assert title.strip()
    assert len(guide._home()) < 4000


def test_guide_layout_covers_every_section_exactly_once():
    from telkap.handlers import guide

    placed = [key for row in guide.LAYOUT for key in row]
    assert sorted(placed) == sorted(guide.SECTIONS)
    assert len(placed) == len(set(placed))


def test_guide_rows_never_exceed_two_buttons():
    """بیش از دو دکمه در یک ردیف روی موبایل بریده می‌شود."""
    from telkap.handlers import guide

    for row in guide.LAYOUT:
        assert 1 <= len(row) <= 2


def test_guide_html_tags_are_balanced():
    """تلگرام با تگ بازِ بسته‌نشده پیام را رد می‌کند."""
    import re

    from telkap.handlers import guide

    for key, (_title, builder) in list(guide.SECTIONS.items()) + [
        ("home", ("", guide._home))
    ]:
        body = builder()
        for tag in ("b", "i", "code"):
            opens = len(re.findall(rf"<{tag}>", body))
            closes = len(re.findall(rf"</{tag}>", body))
            assert opens == closes, f"تگ <{tag}> در بخش {key} متوازن نیست"


def test_guide_quotes_live_prices_from_plans():
    """راهنما باید قیمت‌ها را از plans.py بخواند، نه سفت‌کد شده."""
    from telkap.handlers import guide
    from telkap.plans import CUSTOM, MONTH

    plans_text = guide.SECTIONS["plans"][1]()
    assert MONTH.price_label in plans_text
    assert CUSTOM.price_label in plans_text
    assert MONTH.daily_label in plans_text


def test_guide_next_button_walks_every_section():
    """دکمه‌ی «بعدی» باید از بخش اول تا آخر همه را پوشش دهد."""
    from telkap.handlers import guide

    keys = list(guide.SECTIONS)
    for key in keys[:-1]:
        markup = guide._section_keyboard(key).as_markup()
        targets = [b.callback_data for row in markup.inline_keyboard for b in row]
        assert "guide:home" in targets
        assert any(t.startswith("guide:") and t != "guide:home" for t in targets)
    # بخش آخر فقط دکمه‌ی بازگشت دارد
    last = guide._section_keyboard(keys[-1]).as_markup()
    targets = [b.callback_data for row in last.inline_keyboard for b in row]
    assert targets == ["guide:home"]
