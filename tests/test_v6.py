"""تست دور ششم: طرح‌های قابل ویرایش از پنل و سقف‌های اختصاصی هر کاربر."""
from __future__ import annotations

import pytest

from tests.test_copier import FakeClient, FakeManager, FakeMessage, _setup


@pytest.fixture(autouse=True)
def _restore_plans():
    """طرح‌ها و قیمت‌ها حالت سراسری‌اند؛ بعد از هر تست به کارخانه برمی‌گردند."""
    from telkap import plans

    before_plans = dict(plans.PLANS)
    before_units = dict(plans.CREDIT_UNITS)
    yield
    plans.PLANS.clear()
    plans.PLANS.update(before_plans)
    plans.CREDIT_UNITS.clear()
    plans.CREDIT_UNITS.update(before_units)


# ------------------------------------------------- ویرایش طرح از پنل
@pytest.mark.asyncio
async def test_admin_edit_changes_the_live_plan(tmp_path, monkeypatch):
    db_module, _ = await _setup(tmp_path, monkeypatch, settings={})
    try:
        from telkap.plans import get_plan
        from telkap.services import planstore

        plan = await planstore.set_field("month", "price_toman", 555_000)
        assert plan is not None and plan.price_toman == 555_000
        # هرچه از get_plan بیاید باید همان مقدار تازه باشد
        assert get_plan("month").price_toman == 555_000

        await planstore.set_field("month", "watermark_quota", 99)
        assert get_plan("month").watermark_quota == 99
    finally:
        await db_module.close_db()


@pytest.mark.asyncio
async def test_edits_survive_a_restart(tmp_path, monkeypatch):
    """load() هنگام بالا آمدن ربات مقدارهای ذخیره‌شده را برمی‌گرداند."""
    db_module, _ = await _setup(tmp_path, monkeypatch, settings={})
    try:
        from telkap import plans
        from telkap.services import planstore

        await planstore.set_field("week", "period_messages", 4_242)

        # شبیه‌سازی ری‌استارت: حافظه پاک، بعد load
        plans.PLANS.clear()
        plans.PLANS.update(plans.DEFAULT_PLANS)
        assert plans.get_plan("week").period_messages == 2_000

        await planstore.load()
        assert plans.get_plan("week").period_messages == 4_242
    finally:
        await db_module.close_db()


@pytest.mark.asyncio
async def test_setting_the_default_value_drops_the_override(tmp_path, monkeypatch):
    db_module, _ = await _setup(tmp_path, monkeypatch, settings={})
    try:
        from telkap.plans import DEFAULT_PLANS
        from telkap.services import planstore

        await planstore.set_field("month", "max_tasks", 33)
        assert "month" in await planstore.customized_codes()

        await planstore.set_field("month", "max_tasks", DEFAULT_PLANS["month"].max_tasks)
        assert "month" not in await planstore.customized_codes()
    finally:
        await db_module.close_db()


@pytest.mark.asyncio
async def test_reset_restores_factory_values(tmp_path, monkeypatch):
    db_module, _ = await _setup(tmp_path, monkeypatch, settings={})
    try:
        from telkap.plans import DEFAULT_PLANS, get_plan
        from telkap.services import planstore

        await planstore.set_field("two_week", "price_toman", 1)
        await planstore.set_field("two_week", "history_quota", 7)
        assert get_plan("two_week").price_toman == 1

        await planstore.reset("two_week")
        assert get_plan("two_week") == DEFAULT_PLANS["two_week"]
        assert "two_week" not in await planstore.customized_codes()
    finally:
        await db_module.close_db()


@pytest.mark.asyncio
async def test_feature_toggle_turns_a_capability_on_and_off(tmp_path, monkeypatch):
    db_module, _ = await _setup(tmp_path, monkeypatch, settings={})
    try:
        from telkap.plans import FEAT_HISTORY, get_plan
        from telkap.services import planstore

        assert not get_plan("week").has(FEAT_HISTORY)

        await planstore.toggle_feature("week", FEAT_HISTORY)
        assert get_plan("week").has(FEAT_HISTORY)

        await planstore.toggle_feature("week", FEAT_HISTORY)
        assert not get_plan("week").has(FEAT_HISTORY)
        # به پیش‌فرض برگشت، پس override هم نباید بماند
        assert "week" not in await planstore.customized_codes()
    finally:
        await db_module.close_db()


@pytest.mark.asyncio
async def test_unlimited_is_accepted_only_for_quotas(tmp_path, monkeypatch):
    db_module, _ = await _setup(tmp_path, monkeypatch, settings={})
    try:
        from telkap.plans import UNLIMITED, get_plan
        from telkap.services import planstore

        assert await planstore.set_field("week", "period_messages", -1) is not None
        assert get_plan("week").period_messages == UNLIMITED

        # مدت و قیمت نمی‌توانند منفی باشند
        assert await planstore.set_field("week", "days", -1) is None
        assert await planstore.set_field("week", "max_tasks", -1) is None
    finally:
        await db_module.close_db()


@pytest.mark.asyncio
async def test_credit_unit_price_is_editable(tmp_path, monkeypatch):
    db_module, _ = await _setup(tmp_path, monkeypatch, settings={})
    try:
        from telkap.plans import CREDIT_WATERMARK, credit_price, credit_unit
        from telkap.services import planstore

        assert credit_unit(CREDIT_WATERMARK) == 1_000
        await planstore.set_credit_unit(CREDIT_WATERMARK, 2_500)

        assert credit_unit(CREDIT_WATERMARK) == 2_500
        assert credit_price(CREDIT_WATERMARK, 10) == 25_000
    finally:
        await db_module.close_db()


def test_perks_follow_the_edited_numbers():
    """توضیح طرح از خود عددها ساخته می‌شود، پس هرگز کهنه نمی‌ماند."""
    from dataclasses import replace

    from telkap.plans import MONTH

    edited = replace(MONTH, watermark_quota=7, max_tasks=3)
    assert any("۷ واترمارک" in perk for perk in edited.perks)
    assert any("۳ کار کپی" in perk for perk in edited.perks)
    assert not any("۲۰ واترمارک" in perk for perk in edited.perks)


# ------------------------------------------- سقف اختصاصی هر کاربر
def test_user_limits_override_plan_numbers():
    from telkap.plans import MONTH
    from telkap.services import limits

    plan = limits.apply(MONTH, {"period_messages": 5, "watermark_quota": 1})
    assert plan.period_messages == 5
    assert plan.watermark_quota == 1
    # بقیه دست‌نخورده می‌مانند
    assert plan.max_tasks == MONTH.max_tasks
    assert MONTH.period_messages == 20_000       # خود طرح عوض نشده


def test_user_limits_turn_features_on_and_off():
    from telkap.plans import FEAT_HISTORY, FEAT_WATERMARK, WEEK
    from telkap.services import limits

    assert not WEEK.has(FEAT_WATERMARK)
    granted = limits.apply(WEEK, {limits.feature_key(FEAT_WATERMARK): True})
    assert granted.has(FEAT_WATERMARK)

    from telkap.plans import MONTH

    revoked = limits.apply(MONTH, {limits.feature_key(FEAT_HISTORY): False})
    assert not revoked.has(FEAT_HISTORY)
    assert MONTH.has(FEAT_HISTORY)               # خود طرح دست‌نخورده


def test_user_limits_ignore_unknown_and_plan_only_keys():
    from telkap.plans import MONTH
    from telkap.services import limits

    plan = limits.apply(MONTH, {"چیز نامعتبر": 5, "price_toman": 1, "days": 900})
    assert plan == MONTH


@pytest.mark.asyncio
async def test_admin_limit_applies_to_that_user_only(tmp_path, monkeypatch):
    db_module, _ = await _setup(tmp_path, monkeypatch, settings={})
    try:
        from telkap.models import User
        from telkap.services import limits, subscription

        async with db_module.get_session() as db:
            db.add(User(id=8, first_name="دومی"))
            await db.commit()
        await subscription.grant(8, "month")

        assert await limits.set_value(7, "period_messages", 5)

        mine = await subscription.active_plan_for(7)
        other = await subscription.active_plan_for(8)
        assert mine.period_messages == 5
        assert other.period_messages == 20_000
    finally:
        await db_module.close_db()


@pytest.mark.asyncio
async def test_clearing_a_limit_gives_the_plan_value_back(tmp_path, monkeypatch):
    db_module, _ = await _setup(tmp_path, monkeypatch, settings={})
    try:
        from telkap.services import limits, subscription

        await limits.set_value(7, "watermark_quota", 3)
        assert (await subscription.active_plan_for(7)).watermark_quota == 3

        assert await limits.clear(7, "watermark_quota")
        assert (await subscription.active_plan_for(7)).watermark_quota == 20
    finally:
        await db_module.close_db()


@pytest.mark.asyncio
async def test_admin_can_grant_a_feature_the_plan_lacks(tmp_path, monkeypatch):
    db_module, _ = await _setup(tmp_path, monkeypatch, settings={})
    try:
        from telkap.plans import FEAT_HISTORY
        from telkap.services import limits, subscription

        await subscription.grant(7, "week")          # طرحی که پیام گذشته ندارد
        await limits.set_feature(7, FEAT_HISTORY, True)
        await limits.set_value(7, "history_quota", 25)

        plan = await subscription.active_plan_for(7)
        assert plan.has(FEAT_HISTORY)
        assert plan.history_quota == 25
    finally:
        await db_module.close_db()


@pytest.mark.asyncio
async def test_copier_respects_a_per_user_message_limit(tmp_path, monkeypatch):
    """سقفی که ادمین دستی گذاشته باید در موتور کپی هم اعمال شود."""
    db_module, task_id = await _setup(tmp_path, monkeypatch, settings={})
    try:
        from telkap.services import cache, limits
        from telkap.services.copier import Copier

        await limits.set_value(7, "period_messages", 2)
        cache.clear()

        warnings: list[tuple[int, str]] = []

        async def notifier(user_id, text):
            warnings.append((user_id, text))

        client = FakeClient()
        copier = Copier(FakeManager(client), notifier=notifier)
        assert await copier.process(7, task_id, [FakeMessage(id=1, message="یک")]) is True
        assert await copier.process(7, task_id, [FakeMessage(id=2, message="دو")]) is True
        assert await copier.process(7, task_id, [FakeMessage(id=3, message="سه")]) is False

        assert len(client.sent) == 2
        assert warnings and "سهمیه" in warnings[0][1]
    finally:
        await db_module.close_db()


@pytest.mark.asyncio
async def test_limit_description_shows_the_difference(tmp_path, monkeypatch):
    db_module, _ = await _setup(tmp_path, monkeypatch, settings={})
    try:
        from telkap.plans import FEAT_HISTORY, MONTH, quota_label
        from telkap.services import limits

        await limits.set_value(7, "period_messages", 50)
        await limits.set_feature(7, FEAT_HISTORY, False)

        lines = limits.describe(MONTH, await limits.get(7))
        expected = f"{quota_label(MONTH.period_messages)} ← <b>{quota_label(50)}</b>"
        assert any(expected in line for line in lines)
        assert any("خاموش" in line for line in lines)
    finally:
        await db_module.close_db()
