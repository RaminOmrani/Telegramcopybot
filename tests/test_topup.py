"""تست شارژ کیف پول و طرح‌های بلندمدت.

<b>هر دو مستقیم پول‌اند.</b> شارژ موجودی واقعی می‌سازد و طرح بلندمدت
مبلغ بزرگ‌تری می‌گیرد در ازای تعهد بلندتر؛ اشتباه در هرکدام یا به
ضرر مشتری تمام می‌شود یا به ضرر ما.
"""
from __future__ import annotations

import pytest

from telkap.models import PaymentRequest, User
from telkap.plans import MONTH, all_purchasable, get_plan, long_term, purchasable
from telkap.services import payments, wallet
from tests.test_copier import _setup


async def _user(db_module, user_id: int = 7) -> None:
    async with db_module.get_session() as db:
        if await db.get(User, user_id) is None:
            db.add(User(id=user_id, first_name="ر"))
            await db.commit()


# ── شارژ کیف پول ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_topup_lands_in_the_wallet(tmp_path, monkeypatch):
    db_module, _ = await _setup(tmp_path, monkeypatch, settings={})
    await _user(db_module)

    request = await payments.create_topup(7, 1_500_000)
    result, sub = await payments.approve(request.id, admin_id=1)

    assert result is not None
    assert sub is None                      # شارژ اشتراک فعال نمی‌کند
    assert await wallet.balance(7) == 1_500_000


@pytest.mark.asyncio
async def test_an_amount_outside_the_range_is_refused(tmp_path, monkeypatch):
    """<b>کف و سقف هر دو لازم‌اند.</b>

    مبلغ خیلی کم یعنی رسیدی که ارزش بررسی ندارد؛ مبلغ نجومی معمولاً
    یعنی کاربر ریال را تومان تایپ کرده.
    """
    db_module, _ = await _setup(tmp_path, monkeypatch, settings={})
    await _user(db_module)

    assert await payments.create_topup(7, payments.MIN_TOPUP_TOMAN - 1) is None
    assert await payments.create_topup(7, payments.MAX_TOPUP_TOMAN + 1) is None
    assert await payments.create_topup(7, 0) is None
    assert await payments.create_topup(7, -5000) is None


@pytest.mark.asyncio
async def test_a_topup_is_described_as_a_topup(tmp_path, monkeypatch):
    """ادمین در صف رسیدها باید بفهمد این خرید طرح نیست."""
    db_module, _ = await _setup(tmp_path, monkeypatch, settings={})
    await _user(db_module)

    request = await payments.create_topup(7, 500_000)

    assert request.kind == PaymentRequest.KIND_TOPUP
    assert "شارژ کیف پول" in payments.describe(request)


@pytest.mark.asyncio
async def test_the_notice_talks_about_balance_not_credits(tmp_path, monkeypatch):
    """<b>پیامِ اشتباه یعنی تیکت پشتیبانی.</b>

    پیش از این، شارژ به شاخه‌ی «اعتبار» می‌افتاد و به کاربر
    «مانده‌ی اعتبار: ۰ واحد» می‌گفت — در حالی که پولش سالم رسیده بود.
    """
    db_module, _ = await _setup(tmp_path, monkeypatch, settings={})
    await _user(db_module)

    request = await payments.create_topup(7, 800_000)
    result, sub = await payments.approve(request.id, admin_id=1)
    notice = await payments.approval_notice(result, sub)

    assert "کیف پول" in notice
    assert "واحد" not in notice


@pytest.mark.asyncio
async def test_a_topup_is_not_approved_twice(tmp_path, monkeypatch):
    db_module, _ = await _setup(tmp_path, monkeypatch, settings={})
    await _user(db_module)

    request = await payments.create_topup(7, 300_000)
    await payments.approve(request.id, admin_id=1)
    again, _sub = await payments.approve(request.id, admin_id=1)

    assert again is None
    assert await wallet.balance(7) == 300_000


# ── طرح‌های بلندمدت ─────────────────────────────────────────────────


def test_every_long_plan_is_cheaper_per_month_than_the_monthly_one():
    """<b>اگر ماهانه ارزان‌تر نباشد، دلیلی برای خرید بلندمدت نیست.</b>"""
    monthly = MONTH.price_toman

    for plan in long_term():
        if not plan.code.startswith("month"):
            continue
        months = round(plan.days / 30)
        assert plan.price_toman / months < monthly


def test_the_discount_grows_with_the_term():
    """تخفیف پلکانی: هرچه دوره بلندتر، تخفیف بیشتر."""
    ladder = [p for p in long_term() if p.code.startswith(("month", "year"))]
    savings = []
    for plan in ladder:
        months = round(plan.days / 30)
        savings.append(1 - plan.price_toman / (MONTH.price_toman * months))

    assert savings == sorted(savings)


def test_quotas_grow_with_the_term():
    """<b>وگرنه طرح بلندمدت یک تله است.</b>

    «۲۰٬۰۰۰ پیام» برای یک ماه سخاوتمندانه است و برای یک سال تقریباً
    هیچ. مشتری‌ای که یک‌سال خریده نباید ماه دوم به دیوار بخورد.
    """
    year = get_plan("year")

    assert year.period_messages == MONTH.period_messages * 12
    assert year.watermark_quota == MONTH.watermark_quota * 12
    assert year.history_quota == MONTH.history_quota * 12


def test_unlimited_stays_unlimited():
    """نامحدودِ ضرب‌شده هنوز نامحدود است؛ عددش فقط بی‌معنی می‌شود."""
    from telkap.plans import UNLIMITED

    for code in ("custom2", "custom3", "custom6", "custom_year"):
        assert get_plan(code).period_messages == UNLIMITED


def test_the_custom_family_has_long_terms_too():
    """طرح اختصاصی هم باید همین دوره‌ها را داشته باشد."""
    codes = {p.code for p in long_term()}

    assert {"custom2", "custom3", "custom6", "custom_year"} <= codes


def test_the_first_screen_stays_short():
    """<b>سیزده طرح در یک صفحه یعنی هیچ‌کدام دیده نمی‌شوند.</b>"""
    assert len(purchasable()) == 4
    assert len(all_purchasable()) == len(purchasable()) + len(long_term())


def test_every_purchasable_plan_can_be_looked_up():
    """کدی که در فهرست باشد ولی طرحش پیدا نشود، یعنی خریدِ شکسته."""
    for plan in all_purchasable():
        assert get_plan(plan.code) is not None
