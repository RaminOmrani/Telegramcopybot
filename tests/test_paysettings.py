"""تست تنظیمات پرداخت: شماره کارت و نرخ خودکار تتر.

<b>دو چیزی که اینجا سنجیده می‌شود، هر دو مستقیم پول‌اند.</b> اولی
اینکه اگر هیچ راه پرداختی تنظیم نشده باشد، کاربر به در بسته می‌خورد
و فروش انجام نمی‌شود. دومی جهتِ حاشیه‌ی نرخ — که اگر برعکس باشد،
هر فروش ضرر می‌دهد و هیچ خطایی هم در لاگ نمی‌آید.
"""
from __future__ import annotations

import pytest

from telkap.services import cardinfo, usdtrate
from tests.test_copier import _setup


def _env_card(monkeypatch, value: str) -> None:
    """مقدار CARD_NUMBER در .env را عوض می‌کند.

    Settings یک dataclass منجمد است، پس تک‌فیلدش را نمی‌شود عوض کرد —
    کل شیء جایگزین می‌شود.
    """
    import dataclasses

    from telkap import config

    monkeypatch.setattr(
        config, "settings", dataclasses.replace(config.settings, card_number=value)
    )


# ── شماره کارت ───────────────────────────────────────────────────────


def test_a_plain_sixteen_digit_number_is_accepted():
    assert cardinfo.normalize("6037991234567890") == "6037991234567890"


def test_spaces_and_dashes_are_ignored():
    """کاربر شماره را همان‌طور که روی کارت است می‌نویسد."""
    assert cardinfo.normalize("6037 9912 3456 7890") == "6037991234567890"
    assert cardinfo.normalize("6037-9912-3456-7890") == "6037991234567890"


def test_persian_digits_are_accepted():
    """کسی که با کیبورد فارسی تایپ می‌کند نباید «نامعتبر» بگیرد."""
    assert cardinfo.normalize("۶۰۳۷۹۹۱۲۳۴۵۶۷۸۹۰") == "6037991234567890"


def test_arabic_digits_are_accepted_too():
    assert cardinfo.normalize("٦٠٣٧٩٩١٢٣٤٥٦٧٨٩٠") == "6037991234567890"


def test_a_wrong_length_is_refused():
    """۱۵ یا ۱۷ رقم یعنی غلط تایپی — و غلط تایپی یعنی پولِ رفته."""
    assert cardinfo.normalize("603799123456789") == ""
    assert cardinfo.normalize("60379912345678901") == ""
    assert cardinfo.normalize("") == ""
    assert cardinfo.normalize("شماره ندارم") == ""


def test_the_number_is_shown_in_groups_of_four():
    """شانزده رقمِ پیوسته را نمی‌شود با کارت مقایسه کرد."""
    assert cardinfo.pretty("6037991234567890") == "6037 9912 3456 7890"


def test_an_invalid_number_is_shown_as_it_is():
    """صفحه‌ی ادمین نباید به‌خاطر یک مقدار عجیب بشکند."""
    assert cardinfo.pretty("چیز عجیب") == "چیز عجیب"
    assert cardinfo.pretty("") == ""


@pytest.mark.asyncio
async def test_the_number_is_stored_and_read_back(tmp_path, monkeypatch):
    await _setup(tmp_path, monkeypatch, settings={})

    await cardinfo.set_number("6037 9912 3456 7890", admin_id=1)

    assert await cardinfo.number() == "6037991234567890"
    assert await cardinfo.available() is True


@pytest.mark.asyncio
async def test_an_invalid_number_changes_nothing(tmp_path, monkeypatch):
    await _setup(tmp_path, monkeypatch, settings={})
    await cardinfo.set_number("6037991234567890", admin_id=1)

    assert await cardinfo.set_number("۱۲۳", admin_id=1) is None
    assert await cardinfo.number() == "6037991234567890"


@pytest.mark.asyncio
async def test_the_env_value_is_used_when_the_panel_is_empty(tmp_path, monkeypatch):
    """<b>نصب‌های موجود با آپدیت چیزی از دست نمی‌دهند.</b>

    شماره تا امروز فقط در .env بود. اگر پنل خالی باشد، همان خوانده
    می‌شود.
    """
    await _setup(tmp_path, monkeypatch, settings={})
    _env_card(monkeypatch, "5022291234567890")

    assert await cardinfo.number() == "5022291234567890"


@pytest.mark.asyncio
async def test_the_panel_wins_over_env(tmp_path, monkeypatch):
    await _setup(tmp_path, monkeypatch, settings={})
    _env_card(monkeypatch, "5022291234567890")
    await cardinfo.set_number("6037991234567890", admin_id=1)

    assert await cardinfo.number() == "6037991234567890"


@pytest.mark.asyncio
async def test_nothing_anywhere_means_not_available(tmp_path, monkeypatch):
    await _setup(tmp_path, monkeypatch, settings={})
    _env_card(monkeypatch, "")

    assert await cardinfo.available() is False


# ── جهت حاشیه ────────────────────────────────────────────────────────


def test_the_margin_lowers_the_rate_never_raises_it():
    """<b>مهم‌ترین تست این فایل.</b>

    مبلغ تتری از تقسیم می‌آید: usdt = تومان ÷ نرخ. پس نرخِ بالاتر
    یعنی مشتری تتر <b>کمتری</b> می‌دهد.

    اگر جهت حاشیه اشتباه باشد، هر فروش ضرر می‌دهد و هیچ خطایی هم در
    لاگ نمی‌آید — فقط ماه بعد می‌بینید کمتر از انتظار دستتان است.
    """
    market = 100_000

    assert usdtrate.with_margin(market, 2) == 98_000
    assert usdtrate.with_margin(market, 2) < market


def test_a_lower_rate_really_means_more_usdt_from_the_customer():
    """جهت را با خودِ محاسبه‌ی مبلغ می‌سنجیم، نه فقط با عدد نرخ."""
    from telkap.services.crypto import to_usdt

    price = 500_000
    market = 100_000
    safe = usdtrate.with_margin(market, 2)

    assert to_usdt(price, safe) > to_usdt(price, market)


def test_zero_margin_is_the_market_rate():
    assert usdtrate.with_margin(100_000, 0) == 100_000


def test_the_margin_is_clamped():
    """درصدِ بی‌معنی نباید نرخ را نابود کند."""
    assert usdtrate.with_margin(100_000, 999) == usdtrate.with_margin(
        100_000, usdtrate.MAX_MARGIN
    )
    assert usdtrate.with_margin(100_000, -5) == 100_000


def test_no_market_rate_means_no_sell_rate():
    assert usdtrate.with_margin(0, 2) == 0


# ── محافظ جهش ────────────────────────────────────────────────────────


def test_a_normal_move_is_allowed():
    assert usdtrate.jumped(100_000, 105_000) is False


def test_a_wild_move_is_refused():
    """بازار در پانزده دقیقه دو برابر نمی‌شود؛ چنین چیزی یعنی خطا."""
    assert usdtrate.jumped(100_000, 200_000) is True
    assert usdtrate.jumped(100_000, 10_000) is True


def test_the_first_rate_is_never_a_jump():
    """وقتی نرخی نیست، چیزی برای مقایسه هم نیست."""
    assert usdtrate.jumped(0, 100_000) is False


# ── خواندن پاسخ صرافی ────────────────────────────────────────────────


def test_rial_is_divided_into_toman():
    """<b>جایی که ده برابر خطا ممکن است.</b>

    نوبیتکس ریال می‌دهد. اگر تقسیم بر ده از قلم بیفتد، نرخ ده برابر
    می‌شود — یعنی مشتری یک‌دهم قیمت می‌پردازد.
    """
    assert usdtrate._first_number({"lastTradePrice": "1000000"}, ("lastTradePrice",))


def test_alternative_field_names_are_tried():
    """ساختار پاسخ صرافی بدون اطلاع عوض می‌شود."""
    keys = ("lastTradePrice", "last", "latest")

    assert usdtrate._first_number({"last": "990000"}, keys) is not None
    assert usdtrate._first_number({"latest": "990000"}, keys) is not None


def test_a_response_we_cannot_read_gives_nothing():
    keys = ("lastTradePrice",)

    assert usdtrate._first_number({}, keys) is None
    assert usdtrate._first_number({"lastTradePrice": "نه عدد"}, keys) is None
    assert usdtrate._first_number({"lastTradePrice": "0"}, keys) is None
    assert usdtrate._first_number("رشته", keys) is None


# ── تنظیمات نرخ خودکار ───────────────────────────────────────────────


@pytest.mark.asyncio
async def test_auto_rate_is_off_until_asked_for(tmp_path, monkeypatch):
    await _setup(tmp_path, monkeypatch, settings={})

    assert await usdtrate.is_auto() is False


@pytest.mark.asyncio
async def test_auto_rate_can_be_switched(tmp_path, monkeypatch):
    await _setup(tmp_path, monkeypatch, settings={})

    await usdtrate.set_auto(True, admin_id=1)
    assert await usdtrate.is_auto() is True

    await usdtrate.set_auto(False, admin_id=1)
    assert await usdtrate.is_auto() is False


@pytest.mark.asyncio
async def test_an_out_of_range_margin_is_refused(tmp_path, monkeypatch):
    await _setup(tmp_path, monkeypatch, settings={})

    assert await usdtrate.set_margin(99, admin_id=1) is None
    assert await usdtrate.set_margin("هرچی", admin_id=1) is None
    assert await usdtrate.margin() == usdtrate.DEFAULT_MARGIN


@pytest.mark.asyncio
async def test_a_failed_fetch_leaves_the_old_rate_alone(tmp_path, monkeypatch):
    """<b>نرخِ کمی قدیمی بهتر از پرداختِ خاموش است.</b>"""
    await _setup(tmp_path, monkeypatch, settings={})
    from telkap.services import crypto

    await crypto.set_rate(90_000, admin_id=1)
    await usdtrate.set_auto(True, admin_id=1)

    async def broken(**kwargs):
        raise usdtrate.RateError("صرافی در دسترس نیست")

    monkeypatch.setattr(usdtrate, "market_toman", broken)

    assert await usdtrate.refresh() == 0
    assert await crypto.rate() == 90_000


@pytest.mark.asyncio
async def test_nothing_is_fetched_while_auto_is_off(tmp_path, monkeypatch):
    await _setup(tmp_path, monkeypatch, settings={})
    called = []

    async def spy(**kwargs):
        called.append(1)
        return 100_000

    monkeypatch.setattr(usdtrate, "market_toman", spy)

    assert await usdtrate.refresh() == 0
    assert called == []


@pytest.mark.asyncio
async def test_auto_refresh_applies_the_rate_with_its_margin(tmp_path, monkeypatch):
    await _setup(tmp_path, monkeypatch, settings={})
    from telkap.services import crypto

    await usdtrate.set_auto(True, admin_id=1)
    await usdtrate.set_margin(2, admin_id=1)

    async def market(**kwargs):
        return 100_000

    monkeypatch.setattr(usdtrate, "market_toman", market)

    assert await usdtrate.refresh() == 98_000
    assert await crypto.rate() == 98_000


@pytest.mark.asyncio
async def test_a_wild_jump_is_not_applied_silently(tmp_path, monkeypatch):
    """قیمت با یک پاسخ مشکوک عوض نمی‌شود؛ ادمین خبردار می‌شود."""
    await _setup(tmp_path, monkeypatch, settings={})
    from telkap.services import alerts, crypto

    await crypto.set_rate(100_000, admin_id=1)
    await usdtrate.set_auto(True, admin_id=1)

    async def market(**kwargs):
        return 400_000

    told = []

    async def fake_send(text, **kwargs):
        told.append(text)
        return 1

    monkeypatch.setattr(usdtrate, "market_toman", market)
    monkeypatch.setattr(alerts, "send", fake_send)

    assert await usdtrate.refresh() == 0
    assert await crypto.rate() == 100_000
    assert told and "اعمال نشد" in told[0]


@pytest.mark.asyncio
async def test_an_admin_can_force_a_big_change(tmp_path, monkeypatch):
    """محافظِ جهش برای خطاست، نه برای جلوگیری از تصمیم ادمین."""
    await _setup(tmp_path, monkeypatch, settings={})
    from telkap.services import crypto

    await crypto.set_rate(100_000, admin_id=1)

    async def market(**kwargs):
        return 400_000

    monkeypatch.setattr(usdtrate, "market_toman", market)

    assert await usdtrate.refresh(force=True) == 392_000
    assert await crypto.rate() == 392_000


@pytest.mark.asyncio
async def test_the_automatic_refresh_does_not_spam_the_admins(tmp_path, monkeypatch):
    """<b>هشداری که هر ربع ساعت بیاید، روزی که مهم است هم دیده نمی‌شود.</b>

    تغییر نشانی همیشه خبر می‌دهد، ولی نرخِ خودکار نه — وگرنه روزی
    ده‌ها پیام می‌شد.
    """
    await _setup(tmp_path, monkeypatch, settings={})
    from telkap.services import alerts, crypto

    told = []

    async def fake_send(text, **kwargs):
        told.append(text)
        return 1

    monkeypatch.setattr(alerts, "send", fake_send)

    await crypto.set_rate(100_000, admin_id=None)      # سیستم
    assert told == []

    await crypto.set_rate(101_000, admin_id=5)         # آدم
    assert len(told) == 1
