"""تست دور هفتم: کیف پول، دفتر تراکنش و برنامه‌ی دعوت با ضدتقلبش."""
from __future__ import annotations

import pytest

from tests.test_copier import _setup


@pytest.fixture(autouse=True)
def _fresh_referral_cache():
    """تنظیمات دعوت کش می‌شود؛ بین تست‌ها باید پاک شود."""
    from telkap.services import referral

    referral.invalidate()
    yield
    referral.invalidate()


async def _add_user(db_module, user_id: int, name: str = "کاربر"):
    from telkap.models import User

    async with db_module.get_session() as db:
        db.add(User(id=user_id, first_name=name))
        await db.commit()


async def _payment(db_module, user_id: int, amount: int, *, credit: bool = False):
    """یک رسید تأییدشده‌ی ساختگی می‌سازد."""
    from telkap.models import PaymentRequest

    async with db_module.get_session() as db:
        request = PaymentRequest(
            user_id=user_id,
            plan_code="wm" if credit else "month",
            kind=PaymentRequest.KIND_CREDIT if credit else PaymentRequest.KIND_PLAN,
            quantity=10 if credit else 0,
            amount_toman=amount,
            status=PaymentRequest.STATUS_APPROVED,
        )
        db.add(request)
        await db.commit()
        await db.refresh(request)
    return request


# ------------------------------------------------------------ کیف پول
@pytest.mark.asyncio
async def test_wallet_credit_and_debit_keep_a_ledger(tmp_path, monkeypatch):
    db_module, _ = await _setup(tmp_path, monkeypatch, settings={})
    try:
        from telkap.services import wallet

        assert await wallet.balance(7) == 0

        assert await wallet.credit(7, 50_000, note="اول") == 50_000
        assert await wallet.credit(7, 30_000, note="دوم") == 80_000
        assert await wallet.debit(7, 20_000, note="خرید") == 60_000

        entries = await wallet.history(7)
        assert [e.amount_toman for e in entries] == [-20_000, 30_000, 50_000]
        # مانده‌ی هر ردیف باید با موجودی همان لحظه بخواند
        assert [e.balance_after for e in entries] == [60_000, 80_000, 50_000]
        assert await wallet.balance(7) == 60_000
    finally:
        await db_module.close_db()


@pytest.mark.asyncio
async def test_wallet_never_goes_negative(tmp_path, monkeypatch):
    db_module, _ = await _setup(tmp_path, monkeypatch, settings={})
    try:
        from telkap.services import wallet

        await wallet.credit(7, 10_000)
        # برداشت بیش از موجودی باید کامل رد شود، نه جزئی
        assert await wallet.debit(7, 15_000) is None
        assert await wallet.balance(7) == 10_000
        assert len(await wallet.history(7)) == 1      # ردیفی ثبت نشده

        assert await wallet.debit(7, 10_000) == 0     # دقیقاً به اندازه، مجاز
        assert await wallet.balance(7) == 0
    finally:
        await db_module.close_db()


@pytest.mark.asyncio
async def test_wallet_ignores_zero_and_negative_amounts(tmp_path, monkeypatch):
    db_module, _ = await _setup(tmp_path, monkeypatch, settings={})
    try:
        from telkap.services import wallet

        assert await wallet.credit(7, 0) is None
        assert await wallet.credit(7, -500) is None
        assert await wallet.debit(7, 0) is None
        assert await wallet.history(7) == []
    finally:
        await db_module.close_db()


@pytest.mark.asyncio
async def test_admin_adjust_works_both_directions(tmp_path, monkeypatch):
    db_module, _ = await _setup(tmp_path, monkeypatch, settings={})
    try:
        from telkap.services import wallet

        assert await wallet.adjust(7, 40_000, admin_id=1) == 40_000
        assert await wallet.adjust(7, -15_000, admin_id=1) == 25_000
        assert await wallet.adjust(7, -99_000, admin_id=1) is None   # بیش از موجودی
        assert await wallet.balance(7) == 25_000
    finally:
        await db_module.close_db()


# ------------------------------------------------------ بستن معرف
def test_referral_payload_parsing():
    from telkap.services.referral import link_for, parse_payload

    assert parse_payload("ref_12345") == 12345
    assert parse_payload("  ref_7 ") == 7
    assert parse_payload("ref_abc") is None
    assert parse_payload("12345") is None
    assert parse_payload("") is None
    assert link_for("@MyBot", 99) == "https://t.me/MyBot?start=ref_99"


@pytest.mark.asyncio
async def test_referrer_binds_once_and_never_changes(tmp_path, monkeypatch):
    db_module, _ = await _setup(tmp_path, monkeypatch, settings={})
    try:
        from telkap.models import User
        from telkap.services import referral

        await _add_user(db_module, 8, "معرف")
        await _add_user(db_module, 9, "معرف دوم")

        assert await referral.bind(7, 8) is True
        # تلاش دوم باید رد شود، وگرنه پاداش قابل دزدیدن است
        assert await referral.bind(7, 9) is False

        async with db_module.get_session() as db:
            assert (await db.get(User, 7)).referred_by == 8
    finally:
        await db_module.close_db()


@pytest.mark.asyncio
async def test_referral_rejects_self_and_loops(tmp_path, monkeypatch):
    db_module, _ = await _setup(tmp_path, monkeypatch, settings={})
    try:
        from telkap.services import referral

        await _add_user(db_module, 8)

        assert await referral.bind(7, 7) is False        # معرف خودش
        assert await referral.bind(7, 4242) is False     # معرف وجود ندارد

        assert await referral.bind(8, 7) is True
        assert await referral.bind(7, 8) is False        # حلقه‌ی دوطرفه
    finally:
        await db_module.close_db()


# ------------------------------------------------------ پاداش خرید
@pytest.mark.asyncio
async def test_reward_is_paid_only_after_an_approved_purchase(tmp_path, monkeypatch):
    db_module, _ = await _setup(tmp_path, monkeypatch, settings={})
    try:
        from telkap.models import ReferralReward
        from telkap.services import referral, wallet

        await _add_user(db_module, 8, "معرف")
        await referral.bind(7, 8)

        # صرف ثبت‌نام هیچ پولی نمی‌دهد
        assert await wallet.balance(8) == 0

        await referral.set_value("mode", "percent")
        await referral.set_value("value", 15)
        await referral.set_value("min_purchase_toman", 0)

        reward = await referral.on_payment_approved(
            await _payment(db_module, 7, 400_000)
        )
        assert reward is not None
        assert reward.status == ReferralReward.STATUS_PAID
        assert reward.amount_toman == 60_000        # ۱۵٪ از ۴۰۰٬۰۰۰
        assert await wallet.balance(8) == 60_000
    finally:
        await db_module.close_db()


@pytest.mark.asyncio
async def test_fixed_mode_pays_a_flat_amount(tmp_path, monkeypatch):
    db_module, _ = await _setup(tmp_path, monkeypatch, settings={})
    try:
        from telkap.services import referral, wallet

        await _add_user(db_module, 8)
        await referral.bind(7, 8)
        await referral.set_value("mode", "fixed")
        await referral.set_value("value", 50_000)
        await referral.set_value("min_purchase_toman", 0)

        await referral.on_payment_approved(await _payment(db_module, 7, 129_000))
        assert await wallet.balance(8) == 50_000
    finally:
        await db_module.close_db()


@pytest.mark.asyncio
async def test_no_referrer_means_no_reward(tmp_path, monkeypatch):
    db_module, _ = await _setup(tmp_path, monkeypatch, settings={})
    try:
        from telkap.services import referral

        assert await referral.on_payment_approved(
            await _payment(db_module, 7, 400_000)
        ) is None
    finally:
        await db_module.close_db()


@pytest.mark.asyncio
async def test_purchase_below_the_minimum_pays_nothing(tmp_path, monkeypatch):
    """سد اصلی در برابر زنجیره‌ی خریدهای خرد برای فارم."""
    db_module, _ = await _setup(tmp_path, monkeypatch, settings={})
    try:
        from telkap.models import ReferralReward
        from telkap.services import referral, wallet

        await _add_user(db_module, 8)
        await referral.bind(7, 8)
        await referral.set_value("min_purchase_toman", 100_000)

        reward = await referral.on_payment_approved(
            await _payment(db_module, 7, 20_000)
        )
        assert reward.status == ReferralReward.STATUS_VOID
        assert await wallet.balance(8) == 0
    finally:
        await db_module.close_db()


@pytest.mark.asyncio
async def test_first_purchase_only_stops_the_second_reward(tmp_path, monkeypatch):
    db_module, _ = await _setup(tmp_path, monkeypatch, settings={})
    try:
        from telkap.models import ReferralReward
        from telkap.services import referral, wallet

        await _add_user(db_module, 8)
        await referral.bind(7, 8)
        await referral.set_value("mode", "fixed")
        await referral.set_value("value", 30_000)
        await referral.set_value("min_purchase_toman", 0)
        await referral.set_value("first_purchase_only", True)

        await referral.on_payment_approved(await _payment(db_module, 7, 400_000))
        second = await referral.on_payment_approved(
            await _payment(db_module, 7, 400_000)
        )
        assert second.status == ReferralReward.STATUS_VOID
        assert await wallet.balance(8) == 30_000
    finally:
        await db_module.close_db()


@pytest.mark.asyncio
async def test_per_referral_cap_limits_total_from_one_person(tmp_path, monkeypatch):
    db_module, _ = await _setup(tmp_path, monkeypatch, settings={})
    try:
        from telkap.services import referral, wallet

        await _add_user(db_module, 8)
        await referral.bind(7, 8)
        await referral.set_value("mode", "fixed")
        await referral.set_value("value", 40_000)
        await referral.set_value("min_purchase_toman", 0)
        await referral.set_value("max_per_referral_toman", 50_000)

        await referral.on_payment_approved(await _payment(db_module, 7, 400_000))
        await referral.on_payment_approved(await _payment(db_module, 7, 400_000))
        # دومی فقط تا سقف پرداخت می‌شود، نه کامل
        assert await wallet.balance(8) == 50_000

        third = await referral.on_payment_approved(
            await _payment(db_module, 7, 400_000)
        )
        assert third.amount_toman == 0
        assert await wallet.balance(8) == 50_000
    finally:
        await db_module.close_db()


@pytest.mark.asyncio
async def test_monthly_cap_limits_the_referrer_overall(tmp_path, monkeypatch):
    db_module, _ = await _setup(tmp_path, monkeypatch, settings={})
    try:
        from telkap.services import referral, wallet

        await _add_user(db_module, 8)          # معرف
        await _add_user(db_module, 10)         # دعوت‌شده‌ی دوم
        await referral.bind(7, 8)
        await referral.bind(10, 8)
        await referral.set_value("mode", "fixed")
        await referral.set_value("value", 60_000)
        await referral.set_value("min_purchase_toman", 0)
        await referral.set_value("monthly_cap_toman", 100_000)

        await referral.on_payment_approved(await _payment(db_module, 7, 400_000))
        await referral.on_payment_approved(await _payment(db_module, 10, 400_000))
        assert await wallet.balance(8) == 100_000     # سقف ماهانه
    finally:
        await db_module.close_db()


@pytest.mark.asyncio
async def test_credit_purchases_pay_nothing_by_default(tmp_path, monkeypatch):
    db_module, _ = await _setup(tmp_path, monkeypatch, settings={})
    try:
        from telkap.models import ReferralReward
        from telkap.services import referral, wallet

        await _add_user(db_module, 8)
        await referral.bind(7, 8)
        await referral.set_value("min_purchase_toman", 0)

        reward = await referral.on_payment_approved(
            await _payment(db_module, 7, 200_000, credit=True)
        )
        assert reward.status == ReferralReward.STATUS_VOID
        assert await wallet.balance(8) == 0

        # ولی اگر ادمین روشنش کند، پاداش می‌گیرد
        await referral.set_value("reward_on_credits", True)
        await referral.set_value("mode", "fixed")
        await referral.set_value("value", 20_000)
        await referral.on_payment_approved(
            await _payment(db_module, 7, 200_000, credit=True)
        )
        assert await wallet.balance(8) == 20_000
    finally:
        await db_module.close_db()


@pytest.mark.asyncio
async def test_the_same_receipt_never_pays_twice(tmp_path, monkeypatch):
    """قید یکتای payment_id باید جلوی پرداخت دوباره را بگیرد."""
    db_module, _ = await _setup(tmp_path, monkeypatch, settings={})
    try:
        from telkap.services import referral, wallet

        await _add_user(db_module, 8)
        await referral.bind(7, 8)
        await referral.set_value("mode", "fixed")
        await referral.set_value("value", 25_000)
        await referral.set_value("min_purchase_toman", 0)

        request = await _payment(db_module, 7, 400_000)
        assert await referral.on_payment_approved(request) is not None
        assert await referral.on_payment_approved(request) is None
        assert await wallet.balance(8) == 25_000
    finally:
        await db_module.close_db()


@pytest.mark.asyncio
async def test_disabled_program_pays_nothing(tmp_path, monkeypatch):
    db_module, _ = await _setup(tmp_path, monkeypatch, settings={})
    try:
        from telkap.services import referral, wallet

        await _add_user(db_module, 8)
        await referral.bind(7, 8)
        await referral.set_value("enabled", False)

        assert await referral.on_payment_approved(
            await _payment(db_module, 7, 400_000)
        ) is None
        assert await wallet.balance(8) == 0
    finally:
        await db_module.close_db()


@pytest.mark.asyncio
async def test_referral_stats_count_invitees_and_buyers(tmp_path, monkeypatch):
    db_module, _ = await _setup(tmp_path, monkeypatch, settings={})
    try:
        from telkap.services import referral

        await _add_user(db_module, 8)
        await _add_user(db_module, 10)
        await referral.bind(7, 8)
        await referral.bind(10, 8)
        await referral.set_value("mode", "fixed")
        await referral.set_value("value", 30_000)
        await referral.set_value("min_purchase_toman", 0)

        await referral.on_payment_approved(await _payment(db_module, 7, 400_000))

        stats = await referral.stats(8)
        assert stats.invited == 2
        assert stats.buyers == 1        # فقط یکی خرید کرده
        assert stats.earned == 30_000
        assert stats.conversion == 50
    finally:
        await db_module.close_db()


@pytest.mark.asyncio
async def test_admin_settings_are_validated_and_persist(tmp_path, monkeypatch):
    db_module, _ = await _setup(tmp_path, monkeypatch, settings={})
    try:
        from telkap.services import referral

        assert await referral.set_value("mode", "چیز نامعتبر") is None
        assert await referral.set_value("کلید ناشناخته", 5) is None

        # درصد بالای ۱۰۰ بریده می‌شود
        await referral.set_value("mode", "percent")
        cfg = await referral.set_value("value", 500)
        assert cfg["value"] == 100

        # پس از پاک شدن کش هم باید از دیتابیس بازخوانی شود
        referral.invalidate()
        assert (await referral.settings())["value"] == 100
    finally:
        await db_module.close_db()


@pytest.mark.asyncio
async def test_approving_a_real_payment_pays_the_referrer(tmp_path, monkeypatch):
    """مسیر کامل: تأیید رسید توسط ادمین ⇐ واریز به کیف پول معرف."""
    db_module, _ = await _setup(tmp_path, monkeypatch, settings={})
    try:
        from telkap.models import PaymentRequest
        from telkap.services import payments, referral, wallet

        await _add_user(db_module, 8)
        await referral.bind(7, 8)
        await referral.set_value("mode", "fixed")
        await referral.set_value("value", 45_000)
        await referral.set_value("min_purchase_toman", 0)

        async with db_module.get_session() as db:
            request = PaymentRequest(
                user_id=7,
                plan_code="month",
                kind=PaymentRequest.KIND_PLAN,
                amount_toman=429_000,
                status=PaymentRequest.STATUS_PENDING,
            )
            db.add(request)
            await db.commit()
            await db.refresh(request)

        approved, sub = await payments.approve(request.id, admin_id=1)
        assert approved is not None and sub is not None
        assert await wallet.balance(8) == 45_000
    finally:
        await db_module.close_db()


# ---------------------------------------------------------- نمایندگی
@pytest.mark.asyncio
async def test_reseller_buys_at_a_discount_and_activates_instantly(tmp_path, monkeypatch):
    db_module, _ = await _setup(tmp_path, monkeypatch, settings={})
    try:
        from telkap.plans import MONTH
        from telkap.services import reseller, subscription, wallet

        await _add_user(db_module, 8, "مشتری")
        await reseller.set_reseller(7, True, 25)
        await wallet.credit(7, 500_000)

        sale = await reseller.activate(7, 8, "month")

        expected = reseller.discounted(MONTH.price_toman, 25)
        assert sale.paid_toman == expected
        assert sale.list_toman == MONTH.price_toman
        assert await wallet.balance(7) == 500_000 - expected

        # مشتری واقعاً اشتراک گرفته است
        plan = await subscription.active_plan_for(8)
        assert plan is not None and plan.code == "month"
    finally:
        await db_module.close_db()


@pytest.mark.asyncio
async def test_reseller_cannot_sell_without_enough_balance(tmp_path, monkeypatch):
    db_module, _ = await _setup(tmp_path, monkeypatch, settings={})
    try:
        from telkap.services import reseller, subscription, wallet

        await _add_user(db_module, 8)
        await reseller.set_reseller(7, True, 10)
        await wallet.credit(7, 1_000)

        with pytest.raises(reseller.ResellerError, match="کافی نیست"):
            await reseller.activate(7, 8, "month")

        assert await wallet.balance(7) == 1_000        # چیزی کم نشده
        assert await subscription.active_plan_for(8) is None
    finally:
        await db_module.close_db()


@pytest.mark.asyncio
async def test_only_resellers_can_use_the_panel(tmp_path, monkeypatch):
    db_module, _ = await _setup(tmp_path, monkeypatch, settings={})
    try:
        from telkap.services import reseller, wallet

        await _add_user(db_module, 8)
        await wallet.credit(7, 900_000)

        with pytest.raises(reseller.ResellerError, match="نماینده نیستید"):
            await reseller.activate(7, 8, "month")
    finally:
        await db_module.close_db()


@pytest.mark.asyncio
async def test_reseller_gets_a_clear_error_for_unknown_customer(tmp_path, monkeypatch):
    """مشتری که ربات را استارت نکرده، کاربر ندارد و باید پیام روشن بگیرد."""
    db_module, _ = await _setup(tmp_path, monkeypatch, settings={})
    try:
        from telkap.services import reseller, wallet

        await reseller.set_reseller(7, True, 20)
        await wallet.credit(7, 900_000)

        with pytest.raises(reseller.ResellerError, match="استارت"):
            await reseller.activate(7, 999_999, "month")
        assert await wallet.balance(7) == 900_000
    finally:
        await db_module.close_db()


@pytest.mark.asyncio
async def test_reseller_cannot_sell_to_self(tmp_path, monkeypatch):
    db_module, _ = await _setup(tmp_path, monkeypatch, settings={})
    try:
        from telkap.services import reseller, wallet

        await reseller.set_reseller(7, True, 20)
        await wallet.credit(7, 900_000)

        with pytest.raises(reseller.ResellerError, match="خودتان"):
            await reseller.activate(7, 7, "month")
    finally:
        await db_module.close_db()


@pytest.mark.asyncio
async def test_reseller_stats_track_sales_and_saving(tmp_path, monkeypatch):
    db_module, _ = await _setup(tmp_path, monkeypatch, settings={})
    try:
        from telkap.services import reseller, wallet

        await _add_user(db_module, 8)
        await _add_user(db_module, 10)
        await reseller.set_reseller(7, True, 30)
        await wallet.credit(7, 2_000_000)

        await reseller.activate(7, 8, "week")
        await reseller.activate(7, 10, "week")
        await reseller.activate(7, 8, "month")     # همان مشتری، خرید دوم

        stats = await reseller.stats(7)
        assert stats.sales == 3
        assert stats.customers == 2
        assert stats.saved > 0                     # نسبت به قیمت فهرست
        assert len(await reseller.sales(7)) == 3
    finally:
        await db_module.close_db()


def test_reseller_discount_is_bounded_and_rounded():
    from telkap.services.reseller import MAX_DISCOUNT, discounted

    assert discounted(429_000, 0) == 429_000
    assert discounted(429_000, 100) == discounted(429_000, MAX_DISCOUNT)
    # به هزار تومان رند می‌شود تا قیمت‌ها تمیز بمانند
    assert discounted(129_000, 15) % 1_000 == 0
    assert discounted(1_000, 99) >= 0


# ------------------------------------------- فهرست نماینده‌ها برای پنل
@pytest.mark.asyncio
async def test_the_panel_lists_every_reseller_with_their_own_numbers(
    tmp_path, monkeypatch
):
    """<b>آمار هر نماینده باید مالِ خودش باشد.</b>

    این فهرست با یک پرس‌وجوی گروهی ساخته می‌شود نه یکی به‌ازای هر
    نماینده. جایی که چنین پرس‌وجویی اشتباه گروه‌بندی شود، عددها با هم
    قاطی می‌شوند و کسی متوجه نمی‌شود — پس اینجا دو نماینده‌ی متفاوت
    داریم تا قاطی شدن دیده شود.
    """
    db_module, _ = await _setup(tmp_path, monkeypatch, settings={})
    try:
        from telkap.services import reseller, wallet

        await _add_user(db_module, 8, "مشتری الف")
        await _add_user(db_module, 9, "مشتری ب")
        await _add_user(db_module, 20, "نماینده‌ی دوم")

        await reseller.set_reseller(7, True, 25)
        await reseller.set_reseller(20, True, 10)
        await wallet.credit(7, 5_000_000)
        await wallet.credit(20, 5_000_000)

        await reseller.activate(7, 8, "month")
        await reseller.activate(7, 9, "month")
        await reseller.activate(20, 8, "week")

        rows = {user.id: stats for user, stats in await reseller.everyone()}

        assert set(rows) == {7, 20}
        assert rows[7].sales == 2 and rows[7].customers == 2
        assert rows[20].sales == 1 and rows[20].customers == 1
        assert rows[7].spent == (await reseller.stats(7)).spent
        assert rows[20].spent == (await reseller.stats(20)).spent
    finally:
        await db_module.close_db()


@pytest.mark.asyncio
async def test_a_reseller_with_no_sales_still_shows_up(tmp_path, monkeypatch):
    """<b>نماینده‌ی تازه باید دیده شود.</b>

    اگر فهرست از جدولِ فروش ساخته می‌شد، کسی که هنوز نفروخته اصلاً
    وجود نداشت — و ادمین فکر می‌کرد نمایندگی‌اش ثبت نشده.
    """
    db_module, _ = await _setup(tmp_path, monkeypatch, settings={})
    try:
        from telkap.services import reseller

        await reseller.set_reseller(7, True, 30)

        pairs = await reseller.everyone()

        assert [user.id for user, _stats in pairs] == [7]
        assert pairs[0][1].sales == 0
        assert pairs[0][1].spent == 0
    finally:
        await db_module.close_db()


@pytest.mark.asyncio
async def test_taking_the_reselling_away_removes_them_from_the_list(
    tmp_path, monkeypatch
):
    db_module, _ = await _setup(tmp_path, monkeypatch, settings={})
    try:
        from telkap.services import reseller

        await reseller.set_reseller(7, True, 30)
        await reseller.set_reseller(7, False)

        assert await reseller.everyone() == []
    finally:
        await db_module.close_db()


@pytest.mark.asyncio
async def test_recent_sales_are_newest_first_across_all_resellers(
    tmp_path, monkeypatch
):
    db_module, _ = await _setup(tmp_path, monkeypatch, settings={})
    try:
        from telkap.services import reseller, wallet

        await _add_user(db_module, 8)
        await _add_user(db_module, 20, "نماینده‌ی دوم")
        await reseller.set_reseller(7, True, 25)
        await reseller.set_reseller(20, True, 25)
        await wallet.credit(7, 5_000_000)
        await wallet.credit(20, 5_000_000)

        first = await reseller.activate(7, 8, "week")
        second = await reseller.activate(20, 8, "month")

        recent = await reseller.recent_sales()

        assert [sale.id for sale in recent] == [second.id, first.id]
        assert [sale.id for sale in await reseller.recent_sales(limit=1)] == [
            second.id
        ]
    finally:
        await db_module.close_db()
