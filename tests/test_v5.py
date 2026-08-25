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
        assert plan.period_messages > 0 or plan.period_messages == UNLIMITED
        assert plan.extra_destinations == plan.max_destinations - 1
        # داشتن قابلیت و داشتن سهمیه باید با هم بخوانند
        for feature in (FEAT_WATERMARK, FEAT_HISTORY):
            assert plan.has(feature) == (plan.quota(feature) != 0), (
                f"{plan.code}: {feature}"
            )
    for plan in PURCHASABLE:
        assert plan.price_toman > 0
        assert plan.code != "trial"


def test_plan_prices_match_the_price_list():
    from telkap.plans import CUSTOM, MONTH, TWO_WEEK, WEEK

    assert (WEEK.price_toman, TWO_WEEK.price_toman) == (129_000, 229_000)
    assert (MONTH.price_toman, CUSTOM.price_toman) == (429_000, 890_000)


def test_message_quotas_are_per_period():
    from telkap.plans import CUSTOM, MONTH, TRIAL, TWO_WEEK, UNLIMITED, WEEK

    assert [p.period_messages for p in (TRIAL, WEEK, TWO_WEEK, MONTH)] == [
        20, 2_000, 10_000, 20_000
    ]
    assert CUSTOM.period_messages == UNLIMITED
    assert CUSTOM.messages_label == "نامحدود"
    # نامحدود نمایش داده می‌شود ولی سقف مصرف منصفانه دارد
    assert CUSTOM.fair_use_daily == 10_000
    assert all(p.fair_use_daily == 0 for p in (TRIAL, WEEK, TWO_WEEK, MONTH))


def test_watermark_and_history_quotas_match_the_price_list():
    from telkap.plans import CUSTOM, MONTH, TRIAL, TWO_WEEK, WEEK

    # واترمارک فقط از ۱۴ روزه به بعد
    assert [p.watermark_quota for p in (TRIAL, WEEK, TWO_WEEK, MONTH, CUSTOM)] == [
        0, 0, 10, 20, 50
    ]
    # کپی پیام گذشته فقط ۳۰ روزه و اختصاصی
    assert [p.history_quota for p in (TRIAL, WEEK, TWO_WEEK, MONTH, CUSTOM)] == [
        0, 0, 0, 50, 100
    ]
    assert CUSTOM.max_tasks == 50
    assert TRIAL.watermark_label == "ندارد"
    assert CUSTOM.history_label == "۱۰۰"


def test_higher_plans_are_never_worse():
    """هر طرح بالاتر باید در همه‌ی سقف‌ها دست‌کم برابر طرح پایین‌تر باشد."""
    from telkap.plans import MONTH, TRIAL, TWO_WEEK, WEEK

    ladder = [TRIAL, WEEK, TWO_WEEK, MONTH]
    for lower, higher in zip(ladder, ladder[1:], strict=False):
        assert higher.max_tasks >= lower.max_tasks
        assert higher.max_destinations >= lower.max_destinations
        assert higher.period_messages >= lower.period_messages
        assert higher.watermark_quota >= lower.watermark_quota
        assert higher.history_quota >= lower.history_quota
        assert lower.features <= higher.features


def test_credit_price_scales_with_quantity():
    from telkap.plans import CREDIT_HISTORY, CREDIT_WATERMARK, credit_price

    assert credit_price(CREDIT_WATERMARK, 100) == 100_000
    assert credit_price(CREDIT_HISTORY, 250) == 250_000
    assert credit_price(CREDIT_WATERMARK, 0) == 0
    assert credit_price("چیز نامعتبر", 10) == 0


def _async(value):
    """یک coroutine ساده که همان مقدار را برمی‌گرداند."""
    async def _wrapped():
        return value

    return _wrapped()


def _use_plan(monkeypatch, plan, **overrides):
    """طرح فعال کاربر ۷ را برای این تست عوض می‌کند (شناسه‌ی اشتراک ۱)."""
    from dataclasses import replace

    from telkap.services import cache

    entry = cache.Entitlement(replace(plan, **overrides), 1)
    monkeypatch.setattr(cache, "get_entitlement", lambda uid: _async(entry))


# --------------------------------------------- سهمیه‌ی پیام کل دوره
@pytest.mark.asyncio
async def test_message_quota_stops_copying_after_the_period_limit(tmp_path, monkeypatch):
    db_module, task_id = await _setup(tmp_path, monkeypatch, settings={})
    try:
        from telkap.plans import MONTH
        from telkap.services.copier import Copier

        _use_plan(monkeypatch, MONTH, period_messages=2)

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
        assert warnings and "سهمیه" in warnings[0][1]
    finally:
        await db_module.close_db()


@pytest.mark.asyncio
async def test_message_quota_does_not_reset_at_midnight(tmp_path, monkeypatch):
    """سهمیه برای کل دوره است، پس با عوض شدن روز پر نمی‌شود."""
    db_module, task_id = await _setup(tmp_path, monkeypatch, settings={})
    try:
        from telkap.plans import FEAT_MESSAGES, MONTH
        from telkap.services import entitlement
        from telkap.services.copier import Copier

        _use_plan(monkeypatch, MONTH, period_messages=2)
        copier = Copier(FakeManager(FakeClient()))
        await copier.process(7, task_id, [FakeMessage(id=1, message="یک")])
        await copier.process(7, task_id, [FakeMessage(id=2, message="دو")])

        # شمارنده روی اشتراک ثبت شده، نه روی روز
        assert await entitlement.used(1, FEAT_MESSAGES) == 2
        assert await entitlement.quota_left(1, FEAT_MESSAGES, MONTH) == MONTH.period_messages - 2
    finally:
        await db_module.close_db()


@pytest.mark.asyncio
async def test_unlimited_plan_respects_fair_use_daily(tmp_path, monkeypatch):
    """طرح اختصاصی «نامحدود» است ولی سقف مصرف منصفانه دارد."""
    db_module, task_id = await _setup(tmp_path, monkeypatch, settings={})
    try:
        from telkap.plans import CUSTOM
        from telkap.services.copier import Copier
        from telkap.services.ratelimit import daily_quota

        daily_quota.forget(7)
        _use_plan(monkeypatch, CUSTOM, fair_use_daily=2)

        warnings: list[tuple[int, str]] = []

        async def notifier(user_id, text):
            warnings.append((user_id, text))

        client = FakeClient()
        copier = Copier(FakeManager(client), notifier=notifier)
        assert await copier.process(7, task_id, [FakeMessage(id=1, message="یک")]) is True
        assert await copier.process(7, task_id, [FakeMessage(id=2, message="دو")]) is True
        assert await copier.process(7, task_id, [FakeMessage(id=3, message="سه")]) is False
        assert len(client.sent) == 2
        assert warnings and "منصفانه" in warnings[0][1]
    finally:
        daily_quota.forget(7)
        await db_module.close_db()


@pytest.mark.asyncio
async def test_unlimited_plan_has_no_period_ceiling(tmp_path, monkeypatch):
    db_module, task_id = await _setup(tmp_path, monkeypatch, settings={})
    try:
        from telkap.plans import CUSTOM, FEAT_MESSAGES
        from telkap.services import entitlement
        from telkap.services.copier import Copier
        from telkap.services.ratelimit import daily_quota

        daily_quota.forget(7)
        _use_plan(monkeypatch, CUSTOM)

        client = FakeClient()
        copier = Copier(FakeManager(client))
        for index in range(6):
            assert await copier.process(
                7, task_id, [FakeMessage(id=index + 1, message=f"پیام {index}")]
            ) is True
        assert len(client.sent) == 6
        # برای طرح نامحدود اصلاً شمارنده‌ای نوشته نمی‌شود
        assert await entitlement.used(1, FEAT_MESSAGES) == 0
    finally:
        daily_quota.forget(7)
        await db_module.close_db()


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


# ------------------------------------------------------- واترمارک
WM_SETTINGS = {"watermark_enabled": True, "watermark_text": "@me"}


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
        from telkap.plans import CREDIT_WATERMARK, FEAT_WATERMARK, MONTH
        from telkap.services import credits, entitlement
        from telkap.services.copier import Copier

        _stub_watermark(monkeypatch, tmp_path)
        _use_plan(monkeypatch, MONTH)          # سهمیه‌ی ۲۰ تایی
        await credits.add(7, CREDIT_WATERMARK, 5)

        copier = Copier(FakeManager(PhotoClient(tmp_path)))
        assert await _copy_photo(copier, task_id, 1) is True

        assert await entitlement.used(1, FEAT_WATERMARK) == 1
        assert await credits.balance(7, CREDIT_WATERMARK) == 5   # دست‌نخورده
    finally:
        await db_module.close_db()


@pytest.mark.asyncio
async def test_watermark_falls_back_to_credit_when_quota_is_gone(tmp_path, monkeypatch):
    """با تمام شدن سهمیه، از اعتبار خریداری‌شده برداشته می‌شود."""
    db_module, task_id = await _setup(tmp_path, monkeypatch, settings=WM_SETTINGS)
    try:
        from telkap.plans import CREDIT_WATERMARK, FEAT_WATERMARK, MONTH
        from telkap.services import credits, entitlement
        from telkap.services.copier import Copier

        _stub_watermark(monkeypatch, tmp_path)
        _use_plan(monkeypatch, MONTH, watermark_quota=1)   # فقط یک سهمیه
        await credits.add(7, CREDIT_WATERMARK, 5)

        copier = Copier(FakeManager(PhotoClient(tmp_path)))
        assert await _copy_photo(copier, task_id, 1) is True
        assert await _copy_photo(copier, task_id, 2) is True

        assert await entitlement.used(1, FEAT_WATERMARK) == 1
        assert await credits.balance(7, CREDIT_WATERMARK) == 4   # دومی از اعتبار
    finally:
        await db_module.close_db()


@pytest.mark.asyncio
async def test_watermark_stops_when_quota_and_credit_run_out(tmp_path, monkeypatch):
    """پست‌ها می‌روند ولی بدون واترمارک، و کاربر یک بار خبردار می‌شود."""
    db_module, task_id = await _setup(tmp_path, monkeypatch, settings=WM_SETTINGS)
    try:
        from telkap.plans import CREDIT_WATERMARK, MONTH
        from telkap.services import credits
        from telkap.services.copier import Copier

        _stub_watermark(monkeypatch, tmp_path)
        _use_plan(monkeypatch, MONTH, watermark_quota=1)

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
async def test_unlimited_watermark_never_touches_quota_or_credit(tmp_path, monkeypatch):
    db_module, task_id = await _setup(tmp_path, monkeypatch, settings=WM_SETTINGS)
    try:
        from telkap.plans import CREDIT_WATERMARK, CUSTOM, FEAT_WATERMARK, UNLIMITED
        from telkap.services import credits, entitlement
        from telkap.services.copier import Copier

        _stub_watermark(monkeypatch, tmp_path)
        _use_plan(monkeypatch, CUSTOM, watermark_quota=UNLIMITED)
        await credits.add(7, CREDIT_WATERMARK, 3)

        copier = Copier(FakeManager(PhotoClient(tmp_path)))
        for index in range(4):
            assert await _copy_photo(copier, task_id, index + 1) is True

        assert await entitlement.used(1, FEAT_WATERMARK) == 0
        assert await credits.balance(7, CREDIT_WATERMARK) == 3
    finally:
        await db_module.close_db()


# ------------------------------------------------- سهمیه + اعتبار با هم
@pytest.mark.asyncio
async def test_reserve_splits_between_quota_and_credit(tmp_path, monkeypatch):
    db_module, _task_id = await _setup(tmp_path, monkeypatch, settings={})
    try:
        from telkap.plans import CREDIT_HISTORY, FEAT_HISTORY, MONTH
        from telkap.services import credits, entitlement

        await credits.add(7, CREDIT_HISTORY, 30)

        # سهمیه ۵۰ است؛ درخواست ۷۰ یعنی ۵۰ از سهمیه و ۲۰ از اعتبار
        grant = await entitlement.reserve(7, FEAT_HISTORY, 70, MONTH, 1)
        assert grant is not None
        assert (grant.from_quota, grant.from_credits) == (50, 20)
        assert grant.total == 70
        assert await credits.balance(7, CREDIT_HISTORY) == 10
        assert await entitlement.used(1, FEAT_HISTORY) == 50
    finally:
        await db_module.close_db()


@pytest.mark.asyncio
async def test_reserve_takes_nothing_when_total_is_short(tmp_path, monkeypatch):
    """اگر مجموع سهمیه و اعتبار کم باشد، هیچ‌کدام نباید کم شوند."""
    db_module, _task_id = await _setup(tmp_path, monkeypatch, settings={})
    try:
        from telkap.plans import CREDIT_HISTORY, FEAT_HISTORY, MONTH
        from telkap.services import credits, entitlement

        await credits.add(7, CREDIT_HISTORY, 5)

        assert await entitlement.reserve(7, FEAT_HISTORY, 100, MONTH, 1) is None
        assert await credits.balance(7, CREDIT_HISTORY) == 5
        assert await entitlement.used(1, FEAT_HISTORY) == 0
    finally:
        await db_module.close_db()


@pytest.mark.asyncio
async def test_release_gives_quota_and_credit_back(tmp_path, monkeypatch):
    db_module, _task_id = await _setup(tmp_path, monkeypatch, settings={})
    try:
        from telkap.plans import CREDIT_HISTORY, FEAT_HISTORY, MONTH
        from telkap.services import credits, entitlement

        await credits.add(7, CREDIT_HISTORY, 30)
        grant = await entitlement.reserve(7, FEAT_HISTORY, 70, MONTH, 1)
        await entitlement.release(7, grant, 1)

        assert await credits.balance(7, CREDIT_HISTORY) == 30
        assert await entitlement.used(1, FEAT_HISTORY) == 0
    finally:
        await db_module.close_db()


@pytest.mark.asyncio
async def test_quota_is_per_subscription_not_shared(tmp_path, monkeypatch):
    """اشتراک تازه یعنی سهمیه‌ی تازه."""
    db_module, _task_id = await _setup(tmp_path, monkeypatch, settings={})
    try:
        from telkap.plans import FEAT_HISTORY, MONTH
        from telkap.services import entitlement

        assert await entitlement.reserve(7, FEAT_HISTORY, 50, MONTH, 1)
        assert await entitlement.quota_left(1, FEAT_HISTORY, MONTH) == 0
        # اشتراک بعدی شمارنده‌ی خودش را دارد
        assert await entitlement.quota_left(2, FEAT_HISTORY, MONTH) == 50
    finally:
        await db_module.close_db()


@pytest.mark.asyncio
async def test_plan_without_the_feature_uses_credit_only(tmp_path, monkeypatch):
    db_module, _task_id = await _setup(tmp_path, monkeypatch, settings={})
    try:
        from telkap.plans import CREDIT_HISTORY, FEAT_HISTORY, WEEK
        from telkap.services import credits, entitlement

        assert await entitlement.quota_left(1, FEAT_HISTORY, WEEK) == 0
        assert await entitlement.reserve(7, FEAT_HISTORY, 10, WEEK, 1) is None

        await credits.add(7, CREDIT_HISTORY, 10)
        grant = await entitlement.reserve(7, FEAT_HISTORY, 10, WEEK, 1)
        assert grant is not None
        assert (grant.from_quota, grant.from_credits) == (0, 10)
    finally:
        await db_module.close_db()


@pytest.mark.asyncio
async def test_messages_have_no_purchasable_credit(tmp_path, monkeypatch):
    """پیام اعتبار خریدنی ندارد؛ فقط سهمیه‌ی طرح."""
    db_module, _task_id = await _setup(tmp_path, monkeypatch, settings={})
    try:
        from telkap.plans import FEAT_MESSAGES, MONTH
        from telkap.services import entitlement

        too_many = MONTH.period_messages + 1
        assert await entitlement.reserve(7, FEAT_MESSAGES, too_many, MONTH, 1) is None
        assert await entitlement.used(1, FEAT_MESSAGES) == 0
    finally:
        await db_module.close_db()


@pytest.mark.asyncio
async def test_affordable_reports_without_charging(tmp_path, monkeypatch):
    db_module, _task_id = await _setup(tmp_path, monkeypatch, settings={})
    try:
        from telkap.plans import CREDIT_HISTORY, FEAT_HISTORY, MONTH
        from telkap.services import credits, entitlement

        assert await entitlement.affordable(7, FEAT_HISTORY, 50, MONTH, 1) is True
        assert await entitlement.affordable(7, FEAT_HISTORY, 60, MONTH, 1) is False

        await credits.add(7, CREDIT_HISTORY, 10)
        assert await entitlement.affordable(7, FEAT_HISTORY, 60, MONTH, 1) is True
        # هیچ‌چیز مصرف نشده است
        assert await credits.balance(7, CREDIT_HISTORY) == 10
        assert await entitlement.used(1, FEAT_HISTORY) == 0
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
    assert MONTH.messages_label in plans_text


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


# ---------------------------------------------------- تیکت پشتیبانی
@pytest.mark.asyncio
async def test_support_ticket_round_trip(tmp_path, monkeypatch):
    """کاربر پیام می‌دهد، ادمین جواب می‌دهد، تیکت بسته می‌شود."""
    db_module, _task_id = await _setup(tmp_path, monkeypatch, settings={})
    try:
        from telkap.models import SupportTicket
        from telkap.services import support

        assert await support.open_ticket(7) is None
        assert await support.waiting_count() == 0

        ticket, created = await support.add_user_message(7, "سلام، مشکل دارم")
        assert created is True
        assert ticket.awaiting_reply is True
        assert await support.waiting_count() == 1

        # پیام دوم به همان تیکت می‌چسبد، تیکت تازه نمی‌سازد
        again, created = await support.add_user_message(7, "توضیح بیشتر")
        assert created is False
        assert again.id == ticket.id
        assert len(await support.history(ticket.id)) == 2

        replied = await support.add_admin_reply(ticket.id, admin_id=1, text="سلام، بفرمایید")
        assert replied is not None
        assert replied.awaiting_reply is False
        assert await support.waiting_count() == 0

        closed = await support.close(ticket.id)
        assert closed.status == SupportTicket.STATUS_CLOSED
        assert await support.open_ticket(7) is None
    finally:
        await db_module.close_db()


@pytest.mark.asyncio
async def test_support_reply_never_leaks_admin_identity(tmp_path, monkeypatch):
    """آیدی ادمین فقط داخلی ذخیره می‌شود و در متن پیام کاربر نمی‌آید."""
    db_module, _task_id = await _setup(tmp_path, monkeypatch, settings={})
    try:
        from telkap.services import support

        ticket, _ = await support.add_user_message(7, "سؤال")
        await support.add_admin_reply(ticket.id, admin_id=987654321, text="پاسخ ما")

        messages = await support.history(ticket.id)
        reply = messages[-1]
        assert reply.from_admin is True
        assert reply.admin_id == 987654321      # فقط برای گزارش داخلی
        assert "987654321" not in reply.text    # در متن نیست
    finally:
        await db_module.close_db()


@pytest.mark.asyncio
async def test_support_new_ticket_after_close(tmp_path, monkeypatch):
    db_module, _task_id = await _setup(tmp_path, monkeypatch, settings={})
    try:
        from telkap.services import support

        first, _ = await support.add_user_message(7, "مشکل اول")
        await support.close(first.id)

        second, created = await support.add_user_message(7, "مشکل دوم")
        assert created is True
        assert second.id != first.id
        assert len(await support.user_tickets(7)) == 2
    finally:
        await db_module.close_db()


@pytest.mark.asyncio
async def test_support_open_tickets_puts_waiting_first(tmp_path, monkeypatch):
    db_module, _task_id = await _setup(tmp_path, monkeypatch, settings={})
    try:
        from telkap.models import User
        from telkap.services import support

        async with db_module.get_session() as session:
            session.add(User(id=8, first_name="دومی"))
            await session.commit()

        answered, _ = await support.add_user_message(7, "جواب گرفته")
        await support.add_admin_reply(answered.id, admin_id=1, text="بله")
        waiting, _ = await support.add_user_message(8, "منتظر جواب")

        order = [t.id for t in await support.open_tickets()]
        assert order[0] == waiting.id           # منتظر پاسخ، اول فهرست
        assert answered.id in order
    finally:
        await db_module.close_db()


def test_custom_plan_is_thirty_days_like_the_month_plan():
    """طرح اختصاصی هم ۳۰ روزه است؛ فقط امکاناتش بیشتر است."""
    from telkap.plans import CUSTOM, MONTH

    assert CUSTOM.days == MONTH.days == 30
    assert CUSTOM.max_tasks > MONTH.max_tasks
    assert CUSTOM.watermark_quota > MONTH.watermark_quota
    assert CUSTOM.history_quota > MONTH.history_quota
    # مدت در توضیح طرح هم گفته شده تا کاربر گمان نکند طولانی‌تر است
    assert "۳۰" in CUSTOM.tagline or any("۳۰ روز" in perk for perk in CUSTOM.perks)


def test_guide_plans_section_shows_duration_of_every_plan():
    from telkap.handlers import guide
    from telkap.plans import PURCHASABLE, TRIAL
    from telkap.texts import fa_num

    text = guide.SECTIONS["plans"][1]()
    for plan in (TRIAL, *PURCHASABLE):
        assert f"{fa_num(plan.days)} روز" in text, plan.code


def test_guide_points_users_at_the_quota_screen():
    """کاربر باید بداند مانده‌اش را کجا ببیند."""
    from telkap.handlers import guide

    for key in ("plans", "credits", "faq"):
        assert "سهمیه و اعتبار من" in guide.SECTIONS[key][1](), key
