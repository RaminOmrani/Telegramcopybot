"""تست دور نهم: کد تخفیف، ارتقا با محاسبه‌ی باقی‌مانده، و صورتحساب."""
from __future__ import annotations

import pytest

from tests.test_copier import _setup


# ------------------------------------------------------- کد تخفیف
def test_coupon_codes_are_normalized():
    from telkap.services.coupons import normalize

    assert normalize(" nowruz ") == "NOWRUZ"
    assert normalize("Back 20") == "BACK20"
    assert normalize("") == ""


@pytest.mark.asyncio
async def test_percent_and_fixed_coupons(tmp_path, monkeypatch):
    db_module, _ = await _setup(tmp_path, monkeypatch, settings={})
    try:
        from telkap.models import Coupon
        from telkap.services import coupons

        await coupons.create("TEN", Coupon.KIND_PERCENT, 10)
        await coupons.create("FLAT", Coupon.KIND_FIXED, 50_000)

        offer = await coupons.validate("ten", 7, "month", 400_000)
        assert offer.discount == 40_000 and offer.payable == 360_000

        flat = await coupons.validate("FLAT", 7, "month", 400_000)
        assert flat.discount == 50_000 and flat.payable == 350_000
    finally:
        await db_module.close_db()


@pytest.mark.asyncio
async def test_discount_never_exceeds_the_price(tmp_path, monkeypatch):
    db_module, _ = await _setup(tmp_path, monkeypatch, settings={})
    try:
        from telkap.models import Coupon
        from telkap.services import coupons

        await coupons.create("HUGE", Coupon.KIND_FIXED, 999_000)
        offer = await coupons.validate("HUGE", 7, "week", 129_000)
        assert offer.discount == 129_000
        assert offer.payable == 0        # هرگز منفی نمی‌شود
    finally:
        await db_module.close_db()


@pytest.mark.asyncio
async def test_coupon_rules_are_each_enforced(tmp_path, monkeypatch):
    db_module, _ = await _setup(tmp_path, monkeypatch, settings={})
    try:
        from telkap.models import Coupon
        from telkap.services import coupons

        assert isinstance(await coupons.validate("NOPE", 7, "month", 100), str)

        off = await coupons.create("OFF", Coupon.KIND_PERCENT, 10)
        await coupons.toggle(off.id)
        assert "غیرفعال" in await coupons.validate("OFF", 7, "month", 100_000)

        await coupons.create("BIGONLY", Coupon.KIND_PERCENT, 10, min_toman=300_000)
        assert "بالا" in await coupons.validate("BIGONLY", 7, "week", 129_000)

        await coupons.create(
            "MONTHONLY", Coupon.KIND_PERCENT, 10, plan_codes=["month"]
        )
        assert "طرح" in await coupons.validate("MONTHONLY", 7, "week", 129_000)
        assert not isinstance(
            await coupons.validate("MONTHONLY", 7, "month", 429_000), str
        )
    finally:
        await db_module.close_db()


@pytest.mark.asyncio
async def test_per_user_limit_blocks_the_second_use(tmp_path, monkeypatch):
    db_module, _ = await _setup(tmp_path, monkeypatch, settings={})
    try:
        from telkap.models import Coupon
        from telkap.services import coupons

        coupon = await coupons.create("ONCE", Coupon.KIND_PERCENT, 10)
        assert not isinstance(await coupons.validate("ONCE", 7, "month", 400_000), str)

        await coupons.redeem(coupon.id, 7, 40_000)
        assert "قبلاً" in await coupons.validate("ONCE", 7, "month", 400_000)
        # ولی برای کاربر دیگر همچنان کار می‌کند
        assert not isinstance(await coupons.validate("ONCE", 8, "month", 400_000), str)
    finally:
        await db_module.close_db()


@pytest.mark.asyncio
async def test_total_capacity_is_enforced(tmp_path, monkeypatch):
    db_module, _ = await _setup(tmp_path, monkeypatch, settings={})
    try:
        from telkap.models import Coupon
        from telkap.services import coupons

        coupon = await coupons.create(
            "TWO", Coupon.KIND_PERCENT, 10, max_uses=2, per_user_limit=0
        )
        await coupons.redeem(coupon.id, 7, 1)
        await coupons.redeem(coupon.id, 8, 1)
        assert "ظرفیت" in await coupons.validate("TWO", 9, "month", 400_000)

        count, total = await coupons.usage(coupon.id)
        assert count == 2 and total == 2
    finally:
        await db_module.close_db()


@pytest.mark.asyncio
async def test_expired_coupon_is_refused(tmp_path, monkeypatch):
    db_module, _ = await _setup(tmp_path, monkeypatch, settings={})
    try:
        from datetime import timedelta

        from telkap.models import Coupon, utcnow
        from telkap.services import coupons

        coupon = await coupons.create("OLD", Coupon.KIND_PERCENT, 10)
        async with db_module.get_session() as db:
            row = await db.get(Coupon, coupon.id)
            row.expires_at = utcnow() - timedelta(days=1)
            await db.commit()

        assert "مهلت" in await coupons.validate("OLD", 7, "month", 400_000)
    finally:
        await db_module.close_db()


@pytest.mark.asyncio
async def test_duplicate_and_invalid_codes_are_rejected(tmp_path, monkeypatch):
    db_module, _ = await _setup(tmp_path, monkeypatch, settings={})
    try:
        from telkap.models import Coupon
        from telkap.services import coupons

        await coupons.create("SAME", Coupon.KIND_PERCENT, 10)
        assert isinstance(await coupons.create("same", Coupon.KIND_PERCENT, 5), str)
        assert isinstance(await coupons.create("BAD CODE!", Coupon.KIND_PERCENT, 5), str)
        assert isinstance(await coupons.create("ZERO", Coupon.KIND_PERCENT, 0), str)
        assert isinstance(await coupons.create("OVER", Coupon.KIND_PERCENT, 150), str)
    finally:
        await db_module.close_db()


# --------------------------------------------------- ارتقای طرح
@pytest.mark.asyncio
async def test_remaining_value_shrinks_as_the_period_passes(tmp_path, monkeypatch):
    db_module, _ = await _setup(tmp_path, monkeypatch, settings={})
    try:
        from datetime import timedelta

        from telkap.models import Subscription, utcnow
        from telkap.plans import MONTH
        from telkap.services import subscription

        # _setup طرح ۳۰ روزه داده است؛ تازه شروع شده پس تقریباً کامل
        value = await subscription.remaining_value(7)
        assert value > MONTH.price_toman * 0.9

        # ۱۵ روز جلو می‌بریم
        async with db_module.get_session() as db:
            sub = (await db.execute(
                __import__("sqlalchemy").select(Subscription).where(
                    Subscription.user_id == 7
                )
            )).scalars().first()
            sub.expires_at = utcnow() + timedelta(days=15)
            await db.commit()

        half = await subscription.remaining_value(7)
        assert MONTH.price_toman * 0.4 < half < MONTH.price_toman * 0.6
    finally:
        await db_module.close_db()


@pytest.mark.asyncio
async def test_upgrade_credits_only_apply_to_a_pricier_plan(tmp_path, monkeypatch):
    db_module, _ = await _setup(tmp_path, monkeypatch, settings={})
    try:
        from telkap.services import subscription

        # از ۳۰ روزه به اختصاصی: ارتقا است
        credit, is_upgrade = await subscription.upgrade_quote(7, "custom")
        assert is_upgrade and credit > 0

        # به طرح ارزان‌تر: تمدید عادی، بدون اعتبار
        assert await subscription.upgrade_quote(7, "week") == (0, False)
        # به همان طرح: هم تمدید است
        assert await subscription.upgrade_quote(7, "month") == (0, False)
    finally:
        await db_module.close_db()


@pytest.mark.asyncio
async def test_upgrade_starts_now_instead_of_stacking(tmp_path, monkeypatch):
    db_module, _ = await _setup(tmp_path, monkeypatch, settings={})
    try:
        from telkap.plans import CUSTOM
        from telkap.services import subscription

        before = await subscription.remaining_days(7)
        assert before >= 29

        await subscription.grant(7, "custom", replace=True)
        after = await subscription.remaining_days(7)

        # طرح تازه از حالا شروع شده، نه از انتهای طرح قبلی
        assert after <= CUSTOM.days + 1
        plan = await subscription.active_plan_for(7)
        assert plan.code == "custom"
    finally:
        await db_module.close_db()


# ------------------------------------------------- قیمت‌گذاری خرید
@pytest.mark.asyncio
async def test_quote_combines_upgrade_credit_and_coupon(tmp_path, monkeypatch):
    db_module, _ = await _setup(tmp_path, monkeypatch, settings={})
    try:
        from telkap.models import Coupon
        from telkap.plans import CUSTOM
        from telkap.services import coupons, payments

        await coupons.create("SAVE10", Coupon.KIND_PERCENT, 10)
        priced = await payments.quote(7, "custom", "SAVE10")

        assert priced["list_toman"] == CUSTOM.price_toman
        assert priced["credit_toman"] > 0
        after_credit = CUSTOM.price_toman - priced["credit_toman"]
        # تخفیف روی مبلغ پس از کسر اعتبار حساب می‌شود، نه روی قیمت فهرست
        assert priced["discount_toman"] == after_credit // 10
        assert priced["payable"] == after_credit - priced["discount_toman"]
    finally:
        await db_module.close_db()


@pytest.mark.asyncio
async def test_bad_coupon_is_reported_without_blocking_the_purchase(tmp_path, monkeypatch):
    db_module, _ = await _setup(tmp_path, monkeypatch, settings={})
    try:
        from telkap.services import payments

        priced = await payments.quote(7, "custom", "GARBAGE")
        assert priced["coupon_error"]
        assert priced["coupon_code"] == ""
        assert priced["discount_toman"] == 0
        assert priced["payable"] > 0        # خرید همچنان ممکن است
    finally:
        await db_module.close_db()


@pytest.mark.asyncio
async def test_coupon_is_only_spent_after_approval(tmp_path, monkeypatch):
    """وگرنه می‌شد با باز و بسته کردن صفحه‌ی خرید، ظرفیت یک کد را سوزاند."""
    db_module, _ = await _setup(tmp_path, monkeypatch, settings={})
    try:
        from telkap.models import Coupon
        from telkap.services import coupons, payments

        coupon = await coupons.create("ONETIME", Coupon.KIND_PERCENT, 20)

        request = await payments.create_request(7, "custom", "ONETIME")
        assert request.coupon_code == "ONETIME"
        assert request.discount_toman > 0
        assert (await coupons.usage(coupon.id))[0] == 0     # هنوز مصرف نشده

        await payments.approve(request.id, admin_id=1)
        assert (await coupons.usage(coupon.id))[0] == 1
    finally:
        await db_module.close_db()


@pytest.mark.asyncio
async def test_approved_upgrade_replaces_the_old_subscription(tmp_path, monkeypatch):
    db_module, _ = await _setup(tmp_path, monkeypatch, settings={})
    try:
        from telkap.plans import CUSTOM
        from telkap.services import payments, subscription

        request = await payments.create_request(7, "custom")
        assert request.credit_toman > 0        # ارتقا تشخیص داده شد

        _approved, sub = await payments.approve(request.id, admin_id=1)
        assert sub is not None

        plan = await subscription.active_plan_for(7)
        assert plan.code == "custom"
        # روزها انباشته نشده‌اند
        assert await subscription.remaining_days(7) <= CUSTOM.days + 1
    finally:
        await db_module.close_db()


@pytest.mark.asyncio
async def test_the_bill_keeps_every_line_item(tmp_path, monkeypatch):
    db_module, _ = await _setup(tmp_path, monkeypatch, settings={})
    try:
        from telkap.models import Coupon
        from telkap.plans import CUSTOM
        from telkap.services import coupons, payments

        await coupons.create("BILL", Coupon.KIND_FIXED, 30_000)
        request = await payments.create_request(7, "custom", "BILL")

        # صورتحساب باید بتواند بگوید هر کسری از کجا آمده
        assert request.list_toman == CUSTOM.price_toman
        assert request.credit_toman > 0
        assert request.discount_toman == 30_000
        assert request.coupon_code == "BILL"
        assert request.amount_toman == (
            CUSTOM.price_toman - request.credit_toman - 30_000
        )
    finally:
        await db_module.close_db()
