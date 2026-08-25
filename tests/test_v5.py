"""تست دور پنجم: سقف پیام روزانه، اعتبارهای مصرفی، طرح‌ها و عضویت اجباری."""
from __future__ import annotations

import pytest

from tests.test_copier import FakeClient, FakeManager, FakeMessage, _setup


# ------------------------------------------------------------- طرح‌ها
def test_every_plan_is_self_consistent():
    from telkap.plans import PLANS, PURCHASABLE

    for plan in PLANS.values():
        assert plan.days > 0
        assert plan.max_tasks >= 1
        assert plan.max_destinations >= 1
        assert plan.daily_messages >= 0        # ۰ یعنی نامحدود
        assert plan.extra_destinations == plan.max_destinations - 1
    # طرح‌های قابل خرید باید قیمت داشته باشند و آزمایشی نباشند
    for plan in PURCHASABLE:
        assert plan.price_toman > 0
        assert plan.code != "trial"


def test_plan_prices_and_quota_match_the_price_list():
    from telkap.plans import CUSTOM, MONTH, TWO_WEEK, WEEK

    assert (WEEK.price_toman, TWO_WEEK.price_toman) == (129_000, 229_000)
    assert (MONTH.price_toman, CUSTOM.price_toman) == (429_000, 890_000)
    assert MONTH.daily_messages == 4_000
    assert CUSTOM.daily_messages == 0          # نامحدود
    assert CUSTOM.daily_label == "نامحدود"


def test_higher_plans_are_never_worse():
    """هر طرح بالاتر باید در همه‌ی سقف‌ها دست‌کم برابر طرح پایین‌تر باشد."""
    from telkap.plans import MONTH, TRIAL, TWO_WEEK, WEEK

    ladder = [TRIAL, WEEK, TWO_WEEK, MONTH]
    for lower, higher in zip(ladder, ladder[1:], strict=False):
        assert higher.max_tasks >= lower.max_tasks
        assert higher.max_destinations >= lower.max_destinations
        assert higher.daily_messages >= lower.daily_messages
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

        from telkap.services import cache
        from telkap.services.copier import Copier
        from telkap.services.ratelimit import daily_quota

        daily_quota.forget(7)
        plan = await cache.get_plan(7)
        monkeypatch.setattr(
            cache, "get_plan", lambda uid: _async(replace(plan, daily_messages=0))
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


@pytest.mark.asyncio
async def test_watermark_credit_not_charged_when_plan_includes_it(tmp_path, monkeypatch):
    """پلن ۳۰ روزه واترمارک دارد، پس اعتبار نباید مصرف شود."""
    db_module, task_id = await _setup(
        tmp_path,
        monkeypatch,
        settings={"watermark_enabled": True, "watermark_text": "@me"},
    )
    try:
        from telkap.plans import CREDIT_WATERMARK
        from telkap.services import credits
        from telkap.services.copier import Copier

        _stub_watermark(monkeypatch, tmp_path)
        await credits.add(7, CREDIT_WATERMARK, 10)

        client = PhotoClient(tmp_path)
        copier = Copier(FakeManager(client))
        msg = FakeMessage(id=1, message="عکس", media=object())
        assert await copier.process(7, task_id, [msg]) is True
        assert await credits.balance(7, CREDIT_WATERMARK) == 10
    finally:
        await db_module.close_db()


@pytest.mark.asyncio
async def test_watermark_credit_is_charged_when_plan_lacks_it(tmp_path, monkeypatch):
    """پلن ۷ روزه واترمارک ندارد؛ باید از اعتبار خریداری‌شده کم شود."""
    db_module, task_id = await _setup(
        tmp_path,
        monkeypatch,
        settings={"watermark_enabled": True, "watermark_text": "@me"},
    )
    try:
        from dataclasses import replace

        from telkap.plans import CREDIT_WATERMARK, WEEK
        from telkap.services import cache, credits
        from telkap.services.copier import Copier

        _stub_watermark(monkeypatch, tmp_path)
        # پلن کاربر را به هفتگی (بدون واترمارک) عوض می‌کنیم
        monkeypatch.setattr(
            cache, "get_plan", lambda uid: _async(replace(WEEK, daily_messages=0))
        )
        await credits.add(7, CREDIT_WATERMARK, 3)

        client = PhotoClient(tmp_path)
        copier = Copier(FakeManager(client))
        assert await copier.process(
            7, task_id, [FakeMessage(id=1, message="عکس", media=object())]
        ) is True
        assert await credits.balance(7, CREDIT_WATERMARK) == 2
    finally:
        await db_module.close_db()


@pytest.mark.asyncio
async def test_no_watermark_without_plan_or_credit(tmp_path, monkeypatch):
    """نه پلن دارد نه اعتبار: پست می‌رود ولی بدون واترمارک، و اعتبار منفی نمی‌شود."""
    db_module, task_id = await _setup(
        tmp_path,
        monkeypatch,
        settings={"watermark_enabled": True, "watermark_text": "@me"},
    )
    try:
        from dataclasses import replace

        from telkap.plans import CREDIT_WATERMARK, WEEK
        from telkap.services import cache, credits
        from telkap.services.copier import Copier

        _stub_watermark(monkeypatch, tmp_path)
        monkeypatch.setattr(
            cache, "get_plan", lambda uid: _async(replace(WEEK, daily_messages=0))
        )

        client = PhotoClient(tmp_path)
        copier = Copier(FakeManager(client))
        assert await copier.process(
            7, task_id, [FakeMessage(id=1, message="عکس", media=object())]
        ) is True
        assert await credits.balance(7, CREDIT_WATERMARK) == 0
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
