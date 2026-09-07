"""تست دور دهم: گزارش‌های تجاری، نقش ادمین‌ها، هشدارها و حالت تعمیر."""
from __future__ import annotations

from datetime import timedelta

import pytest

from tests.test_copier import _setup


async def _pay(db_module, user_id: int, kind: str, amount: int, *, days_ago: int = 0):
    from telkap.models import PaymentRequest, utcnow

    async with db_module.get_session() as db:
        row = PaymentRequest(
            user_id=user_id,
            plan_code="month",
            kind=kind,
            amount_toman=amount,
            status=PaymentRequest.STATUS_APPROVED,
            created_at=utcnow() - timedelta(days=days_ago),
        )
        db.add(row)
        await db.commit()


# ------------------------------------------------------------ نقش‌ها
@pytest.mark.asyncio
async def test_env_admins_are_owners_and_cannot_be_demoted(tmp_path, monkeypatch):
    monkeypatch.setenv("ADMIN_IDS", "7")
    db_module, _ = await _setup(tmp_path, monkeypatch, settings={})
    try:
        from telkap.services import roles

        roles.invalidate()
        assert await roles.role_of(7) == roles.ROLE_OWNER
        assert await roles.caps(7) == roles.ALL_CAPS

        # نه می‌شود نقشش را عوض کرد، نه حذفش کرد
        assert await roles.set_role(7, roles.ROLE_SUPPORT) is False
        assert await roles.remove(7) is False
        assert await roles.role_of(7) == roles.ROLE_OWNER
    finally:
        await db_module.close_db()


@pytest.mark.asyncio
async def test_each_role_sees_only_its_own_sections(tmp_path, monkeypatch):
    monkeypatch.setenv("ADMIN_IDS", "7")
    db_module, _ = await _setup(tmp_path, monkeypatch, settings={})
    try:
        from telkap.services import roles

        roles.invalidate()
        await roles.set_role(101, roles.ROLE_FINANCE, added_by=7)
        await roles.set_role(102, roles.ROLE_SUPPORT, added_by=7)

        assert await roles.can(101, roles.CAP_MONEY)
        assert await roles.can(101, roles.CAP_REPORTS)
        assert not await roles.can(101, roles.CAP_SYSTEM)   # پشتیبان‌گیری نه
        assert not await roles.can(101, roles.CAP_TICKETS)

        assert await roles.can(102, roles.CAP_TICKETS)
        assert not await roles.can(102, roles.CAP_MONEY)    # قیمت‌ها را نبیند

        # کسی که اصلاً ادمین نیست
        assert not await roles.is_staff(999)
        assert await roles.caps(999) == frozenset()
    finally:
        await db_module.close_db()


@pytest.mark.asyncio
async def test_removing_a_role_takes_effect_immediately(tmp_path, monkeypatch):
    monkeypatch.setenv("ADMIN_IDS", "7")
    db_module, _ = await _setup(tmp_path, monkeypatch, settings={})
    try:
        from telkap.services import roles

        roles.invalidate()
        await roles.set_role(101, roles.ROLE_SUPPORT, added_by=7)
        assert await roles.is_staff(101)

        assert await roles.remove(101) is True
        assert not await roles.is_staff(101)     # کش کهنه نمانده باشد
        assert await roles.remove(101) is False  # دوباره حذف، بی‌اثر
    finally:
        await db_module.close_db()


@pytest.mark.asyncio
async def test_alerts_go_only_to_admins_with_that_access(tmp_path, monkeypatch):
    monkeypatch.setenv("ADMIN_IDS", "7")
    db_module, _ = await _setup(tmp_path, monkeypatch, settings={})
    try:
        from telkap.services import roles

        roles.invalidate()
        await roles.set_role(101, roles.ROLE_FINANCE, added_by=7)
        await roles.set_role(102, roles.ROLE_SUPPORT, added_by=7)

        money = await roles.staff_ids(roles.CAP_MONEY)
        assert set(money) == {7, 101}          # پشتیبانی خبر رسید نمی‌گیرد

        system = await roles.staff_ids(roles.CAP_SYSTEM)
        assert set(system) == {7}              # فقط مالک

        assert set(await roles.staff_ids()) == {7, 101, 102}
    finally:
        await db_module.close_db()


@pytest.mark.asyncio
async def test_cached_check_never_grants_access_it_should_not(tmp_path, monkeypatch):
    monkeypatch.setenv("ADMIN_IDS", "7")
    db_module, _ = await _setup(tmp_path, monkeypatch, settings={})
    try:
        from telkap.services import roles

        roles.invalidate()
        await roles.set_role(101, roles.ROLE_SUPPORT, added_by=7)

        # حافظه سرد است: پاسخ «نه» می‌دهد، نه «بله»ی اشتباه
        roles.invalidate()
        assert roles.can_cached(101, roles.CAP_USERS) is False
        assert roles.can_cached(7, roles.CAP_SYSTEM) is True  # مالک .env همیشه

        await roles.caps(101)  # گرم شدن، همان کاری که میدل‌ور می‌کند
        assert roles.can_cached(101, roles.CAP_USERS) is True
        assert roles.can_cached(101, roles.CAP_MONEY) is False
    finally:
        await db_module.close_db()


# ------------------------------------------------------------- درآمد
@pytest.mark.asyncio
async def test_revenue_is_split_by_source_and_window(tmp_path, monkeypatch):
    db_module, _ = await _setup(tmp_path, monkeypatch, settings={})
    try:
        from telkap.models import PaymentRequest, ResellerSale, utcnow
        from telkap.services import analytics

        await _pay(db_module, 7, PaymentRequest.KIND_PLAN, 400_000)
        await _pay(db_module, 7, PaymentRequest.KIND_CREDIT, 60_000)
        await _pay(db_module, 7, PaymentRequest.KIND_PLAN, 900_000, days_ago=45)

        async with db_module.get_session() as db:
            db.add(
                ResellerSale(
                    reseller_id=7, customer_id=8, plan_code="month", paid_toman=300_000
                )
            )
            await db.commit()

        recent = await analytics.revenue(since=utcnow() - timedelta(days=30))
        assert recent.plans == 400_000
        assert recent.credits == 60_000
        assert recent.reseller == 300_000
        assert recent.total == 760_000

        everything = await analytics.revenue()
        assert everything.plans == 1_300_000     # فروش قدیمی هم شمرده شد
        assert everything.payers == 1
        assert everything.per_payer == everything.total
    finally:
        await db_module.close_db()


@pytest.mark.asyncio
async def test_revenue_ignores_unapproved_receipts(tmp_path, monkeypatch):
    db_module, _ = await _setup(tmp_path, monkeypatch, settings={})
    try:
        from telkap.models import PaymentRequest
        from telkap.services import analytics

        async with db_module.get_session() as db:
            db.add(
                PaymentRequest(
                    user_id=7, plan_code="month", amount_toman=500_000,
                    status=PaymentRequest.STATUS_PENDING,
                )
            )
            db.add(
                PaymentRequest(
                    user_id=7, plan_code="month", amount_toman=500_000,
                    status=PaymentRequest.STATUS_REJECTED,
                )
            )
            await db.commit()

        rev = await analytics.revenue()
        assert rev.total == 0 and rev.payers == 0
    finally:
        await db_module.close_db()


@pytest.mark.asyncio
async def test_growth_is_zero_when_there_is_nothing_to_compare(tmp_path, monkeypatch):
    db_module, _ = await _setup(tmp_path, monkeypatch, settings={})
    try:
        from telkap.models import PaymentRequest
        from telkap.services import analytics

        await _pay(db_module, 7, PaymentRequest.KIND_PLAN, 400_000)
        board = await analytics.dashboard()
        assert board.last_month.total == 0
        assert board.growth == 0          # تقسیم بر صفر نمی‌کند
    finally:
        await db_module.close_db()


# -------------------------------------------------------- قیف تبدیل
@pytest.mark.asyncio
async def test_funnel_counts_each_step_and_finds_the_biggest_drop(tmp_path, monkeypatch):
    db_module, _ = await _setup(tmp_path, monkeypatch, settings={})
    try:
        from telkap.models import User
        from telkap.services import analytics

        # کاربر ۷ در _setup ساخته شده، اکانت وصل ندارد ولی کار دارد
        async with db_module.get_session() as db:
            db.add(User(id=8, first_name="بی‌کار"))
            db.add(User(id=9, first_name="متصل", session_enc="x"))
            await db.commit()

        data = await analytics.funnel()
        assert data.started == 3
        assert data.connected == 1
        assert data.built_task == 1
        assert data.paid == 0

        assert data.rate(data.connected) == 33
        titles = [title for title, _count, _pct in data.steps]
        assert len(titles) == 4

        where, lost = data.biggest_drop
        assert "استارت" in where and lost == 2
    finally:
        await db_module.close_db()


@pytest.mark.asyncio
async def test_empty_funnel_does_not_divide_by_zero(tmp_path, monkeypatch):
    from telkap.services.analytics import Funnel

    empty = Funnel()
    assert empty.rate(0) == 0
    assert empty.steps[0][2] == 0


# --------------------------------------------------------- ماندگاری
@pytest.mark.asyncio
async def test_repeat_buyers_are_separated_from_one_time_buyers(tmp_path, monkeypatch):
    db_module, _ = await _setup(tmp_path, monkeypatch, settings={})
    try:
        from telkap.models import PaymentRequest, User
        from telkap.services import analytics

        async with db_module.get_session() as db:
            db.add(User(id=8, first_name="دوباره"))
            await db.commit()

        await _pay(db_module, 7, PaymentRequest.KIND_PLAN, 400_000)
        await _pay(db_module, 8, PaymentRequest.KIND_PLAN, 400_000)
        await _pay(db_module, 8, PaymentRequest.KIND_PLAN, 400_000)

        ret = await analytics.retention()
        assert ret.once == 1 and ret.repeat == 1
        assert ret.repeat_rate == 50
    finally:
        await db_module.close_db()


# --------------------------------------------------------- دلیل ریزش
@pytest.mark.asyncio
async def test_each_subscription_can_be_answered_only_once(tmp_path, monkeypatch):
    db_module, _ = await _setup(tmp_path, monkeypatch, settings={})
    try:
        from telkap.services import analytics

        assert await analytics.record_churn(7, 1, "price") is True
        assert await analytics.record_churn(7, 1, "unused") is False   # قید یکتا
        assert await analytics.record_churn(7, 2, "unused") is True    # اشتراک دیگر
        assert await analytics.record_churn(7, 3, "چیز دیگری") is False  # دلیل نامعتبر

        summary = await analytics.churn_summary()
        assert dict(summary) == {"price": 1, "unused": 1}
    finally:
        await db_module.close_db()


@pytest.mark.asyncio
async def test_free_text_reason_is_attached_to_the_existing_answer(tmp_path, monkeypatch):
    db_module, _ = await _setup(tmp_path, monkeypatch, settings={})
    try:
        from telkap.services import analytics

        await analytics.record_churn(7, 1, "other")
        assert await analytics.update_churn_note(7, 1, "کانال‌هایم را بستم") is True
        assert await analytics.update_churn_note(7, 99, "بی‌ربط") is False

        notes = await analytics.churn_notes()
        assert [note.note for note in notes] == ["کانال‌هایم را بستم"]
    finally:
        await db_module.close_db()


@pytest.mark.asyncio
async def test_churn_question_goes_only_to_users_who_really_left(tmp_path, monkeypatch):
    db_module, _ = await _setup(tmp_path, monkeypatch, settings={})
    try:
        from telkap.models import Subscription, User, utcnow
        from telkap.services import reminders

        now = utcnow()
        async with db_module.get_session() as db:
            db.add(User(id=8, first_name="رفته"))
            db.add(User(id=9, first_name="تازه‌رفته"))
            db.add(User(id=10, first_name="برگشته"))
            await db.commit()
            # ۸: دو روز پیش تمام شده → باید پرسیده شود
            db.add(Subscription(user_id=8, plan_code="month", expires_at=now - timedelta(days=2)))
            # ۹: یک ساعت پیش تمام شده → هنوز زود است
            db.add(Subscription(user_id=9, plan_code="month", expires_at=now - timedelta(hours=1)))
            # ۱۰: تمام شده ولی دوباره خریده → نباید پرسیده شود
            db.add(Subscription(user_id=10, plan_code="month", expires_at=now - timedelta(days=2)))
            db.add(Subscription(user_id=10, plan_code="month", expires_at=now + timedelta(days=20)))
            await db.commit()

        asked: list[int] = []

        async def notify(user_id, _text, _markup=None):
            asked.append(user_id)

        assert await reminders.ask_churn(notify) == 1
        assert asked == [8]

        # دوبار پرسیدن در کار نیست
        asked.clear()
        assert await reminders.ask_churn(notify) == 0
    finally:
        await db_module.close_db()


# ------------------------------------------------------------ هشدارها
class _FakeBot:
    def __init__(self) -> None:
        self.sent: list[tuple[int, str]] = []

    async def send_message(self, chat_id, text, reply_markup=None):
        self.sent.append((chat_id, text))


@pytest.mark.asyncio
async def test_the_same_warning_is_not_repeated_every_cycle(tmp_path, monkeypatch):
    monkeypatch.setenv("ADMIN_IDS", "7")
    db_module, _ = await _setup(tmp_path, monkeypatch, settings={})
    try:
        from telkap.services import alerts, roles

        roles.invalidate()
        alerts.reset()
        bot = _FakeBot()

        assert await alerts.send("اول", bot=bot, key="k") == 1
        assert await alerts.send("دوم", bot=bot, key="k") == 0   # هنوز در فاصله
        assert await alerts.send("سوم", bot=bot, key="other") == 1
        assert len(bot.sent) == 2
    finally:
        alerts.reset()
        await db_module.close_db()


@pytest.mark.asyncio
async def test_watchdog_warns_about_stale_receipts_and_stops_when_fixed(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("ADMIN_IDS", "7")
    db_module, _ = await _setup(tmp_path, monkeypatch, settings={})
    try:
        from telkap.models import PaymentRequest, utcnow
        from telkap.services import alerts, roles

        roles.invalidate()
        alerts.reset()
        bot = _FakeBot()

        assert await alerts.run_checks(bot) == []   # همه‌چیز آرام است

        old = utcnow() - timedelta(hours=alerts.PENDING_PAY_HOURS + 1)
        async with db_module.get_session() as db:
            for _ in range(alerts.PENDING_PAY_LIMIT):
                db.add(
                    PaymentRequest(
                        user_id=7, plan_code="month", amount_toman=1,
                        receipt_file_id="f", created_at=old,
                    )
                )
            await db.commit()

        assert "stale_receipts" in await alerts.run_checks(bot)
        assert len(bot.sent) == 1
        # چرخه‌ی بعدی نباید دوباره پیام بدهد
        await alerts.run_checks(bot)
        assert len(bot.sent) == 1
    finally:
        alerts.reset()
        await db_module.close_db()


# --------------------------------------------------------- حالت تعمیر
@pytest.mark.asyncio
async def test_maintenance_mode_is_off_by_default_and_survives_a_restart(
    tmp_path, monkeypatch
):
    db_module, _ = await _setup(tmp_path, monkeypatch, settings={})
    try:
        from telkap.services import maintenance

        maintenance.invalidate_mode()
        on, note = await maintenance.mode()
        assert on is False and note == maintenance.DEFAULT_NOTE

        await maintenance.set_mode(True, note="تا ساعت ۳ برمی‌گردیم", by=7)
        on, note = await maintenance.mode()
        assert on is True and note == "تا ساعت ۳ برمی‌گردیم"

        # خواندن دوباره از دیتابیس، همان را می‌دهد
        maintenance.invalidate_mode()
        assert await maintenance.mode() == (True, "تا ساعت ۳ برمی‌گردیم")

        await maintenance.set_mode(False, note="")
        assert await maintenance.mode() == (False, maintenance.DEFAULT_NOTE)
    finally:
        await db_module.close_db()


# --------------------------------------------------- کانال پشتیبان
@pytest.mark.asyncio
async def test_backup_channel_can_be_set_from_the_panel(tmp_path, monkeypatch):
    monkeypatch.setenv("BACKUP_CHAT_ID", "")
    db_module, _ = await _setup(tmp_path, monkeypatch, settings={})
    try:
        from telkap.services import backup

        backup.invalidate_chat()
        assert await backup.chat_id() == ""      # نه در .env، نه در پنل

        assert await backup.set_chat_id("-1001234567890", by=7) == "-1001234567890"
        assert await backup.chat_id() == "-1001234567890"

        # از دیتابیس هم همان درمی‌آید، پس ری‌استارت لازم نیست
        backup.invalidate_chat()
        assert await backup.chat_id() == "-1001234567890"

        # خالی کردن یعنی برگشت به مقدار .env
        await backup.set_chat_id("")
        assert await backup.chat_id() == ""
    finally:
        backup.invalidate_chat()
        await db_module.close_db()


@pytest.mark.asyncio
async def test_env_backup_channel_still_works_when_panel_is_empty(tmp_path, monkeypatch):
    monkeypatch.setenv("BACKUP_CHAT_ID", "-100999")
    db_module, _ = await _setup(tmp_path, monkeypatch, settings={})
    try:
        from telkap.services import backup

        backup.invalidate_chat()
        assert await backup.chat_id() == "-100999"

        # پنل بر .env اولویت دارد
        await backup.set_chat_id("-100111")
        assert await backup.chat_id() == "-100111"
    finally:
        backup.invalidate_chat()
        await db_module.close_db()


# ------------------------------------------------------- لاگ حسابرسی
@pytest.mark.asyncio
async def test_admin_actions_record_who_did_them(tmp_path, monkeypatch):
    db_module, _ = await _setup(tmp_path, monkeypatch, settings={})
    try:
        from sqlalchemy import select

        from telkap.models import ActivityLog

        await db_module.log_activity(
            user_id=8, actor_id=7, event="admin_role_set", detail="نقش support"
        )
        await db_module.log_activity(user_id=8, event="copied")  # کار خود کاربر

        async with db_module.get_session() as db:
            rows = await db.execute(
                select(ActivityLog).where(ActivityLog.actor_id.is_not(None))
            )
            audit = list(rows.scalars())

        assert len(audit) == 1
        assert audit[0].actor_id == 7 and audit[0].user_id == 8
    finally:
        await db_module.close_db()
