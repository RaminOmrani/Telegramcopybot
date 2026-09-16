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

    async def broken(coin=usdtrate.coins.USDT, **kwargs):
        raise usdtrate.RateError("صرافی در دسترس نیست")

    monkeypatch.setattr(usdtrate, "market_toman", broken)

    outcome = await usdtrate.refresh()
    assert outcome.rate == 0
    assert outcome.error          # علت باید گفته شود، نه فقط «نشد»
    assert await crypto.rate() == 90_000


@pytest.mark.asyncio
async def test_nothing_is_fetched_while_auto_is_off(tmp_path, monkeypatch):
    await _setup(tmp_path, monkeypatch, settings={})
    called = []

    async def spy(coin=usdtrate.coins.USDT, **kwargs):
        called.append(1)
        return 100_000

    monkeypatch.setattr(usdtrate, "market_toman", spy)

    assert (await usdtrate.refresh()).rate == 0
    assert called == []


@pytest.mark.asyncio
async def test_auto_refresh_applies_the_rate_with_its_margin(tmp_path, monkeypatch):
    await _setup(tmp_path, monkeypatch, settings={})
    from telkap.services import crypto

    await usdtrate.set_auto(True, admin_id=1)
    await usdtrate.set_margin(2, admin_id=1)

    async def market(coin=usdtrate.coins.USDT, **kwargs):
        return 100_000

    monkeypatch.setattr(usdtrate, "market_toman", market)

    assert (await usdtrate.refresh()).rate == 98_000
    assert await crypto.rate() == 98_000


@pytest.mark.asyncio
async def test_a_wild_jump_is_not_applied_silently(tmp_path, monkeypatch):
    """قیمت با یک پاسخ مشکوک عوض نمی‌شود؛ ادمین خبردار می‌شود."""
    await _setup(tmp_path, monkeypatch, settings={})
    from telkap.services import alerts, crypto

    await crypto.set_rate(100_000, admin_id=1)
    await usdtrate.set_auto(True, admin_id=1)

    async def market(coin=usdtrate.coins.USDT, **kwargs):
        return 400_000

    told = []

    async def fake_send(text, **kwargs):
        told.append(text)
        return 1

    monkeypatch.setattr(usdtrate, "market_toman", market)
    monkeypatch.setattr(alerts, "send", fake_send)

    outcome = await usdtrate.refresh()
    assert outcome.rate == 0
    assert "جهش" in outcome.note      # خوانده شد ولی اعمال نشد — و می‌گوید چرا
    assert await crypto.rate() == 100_000
    assert told and "اعمال نشد" in told[0]


@pytest.mark.asyncio
async def test_an_admin_can_force_a_big_change(tmp_path, monkeypatch):
    """محافظِ جهش برای خطاست، نه برای جلوگیری از تصمیم ادمین."""
    await _setup(tmp_path, monkeypatch, settings={})
    from telkap.services import crypto

    await crypto.set_rate(100_000, admin_id=1)

    async def market(coin=usdtrate.coins.USDT, **kwargs):
        return 400_000

    monkeypatch.setattr(usdtrate, "market_toman", market)

    assert (await usdtrate.refresh(force=True)).rate == 392_000
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


# ── چرا نرخ گرفته نشد ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_failure_says_which_source_failed_and_why(tmp_path, monkeypatch):
    """<b>«نشد» جواب نیست.</b>

    پیام قبلی سه حالتِ کاملاً متفاوت را یکی می‌کرد — صرافی در دسترس
    نیست، بازار تکان نخورده، جهش مشکوک بود — و ادمین برای تشخیصشان
    مجبور بود لاگ سرور را ببیند، یعنی عملاً هیچ‌وقت نمی‌فهمید.
    """
    await _setup(tmp_path, monkeypatch, settings={})

    async def broken(coin=usdtrate.coins.USDT, **kwargs):
        raise usdtrate.RateError("نوبیتکس (سفارش‌ها): پاسخ 403")

    monkeypatch.setattr(usdtrate, "market_toman", broken)

    outcome = await usdtrate.refresh(force=True)

    assert outcome.rate == 0
    assert "403" in outcome.error


@pytest.mark.asyncio
async def test_no_change_is_not_reported_as_a_failure(tmp_path, monkeypatch):
    """بازارِ آرام خطا نیست، و نباید مثل خطا به نظر برسد."""
    from telkap.services import crypto

    await _setup(tmp_path, monkeypatch, settings={})
    await usdtrate.set_margin(0, admin_id=1)
    await crypto.set_rate(100_000, admin_id=1)

    async def market(coin=usdtrate.coins.USDT, **kwargs):
        return 100_000

    monkeypatch.setattr(usdtrate, "market_toman", market)

    outcome = await usdtrate.refresh(force=True)

    assert outcome.error == ""          # خطایی در کار نبود
    assert outcome.note                 # ولی چیزی هم عوض نشد
    assert outcome.changed is False


# ── چند منبع، و مقایسه‌شان ───────────────────────────────────────────


class _Reply:
    """پاسخ ساختگی aiohttp، به اندازه‌ای که _fetch لازم دارد."""

    def __init__(self, status=200, body=None):
        self.status = status
        self._body = body if body is not None else {}

    async def json(self, **kwargs):
        return self._body

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False


class _Session:
    """نشست ساختگی: پاسخ بر اساس نشانی انتخاب می‌شود.

    منبعی که تست درباره‌اش نیست «در دسترس نیست» می‌دهد — نه خطا —
    وگرنه هر تست مجبور بود همه‌ی منبع‌ها را نام ببرد و با اضافه شدن
    منبع بعدی بشکند.
    """

    def __init__(self, replies: dict):
        self.replies = replies
        self.seen = []

    def _next(self, url):
        self.seen.append(url)
        for fragment, reply in self.replies.items():
            if fragment in url:
                return reply
        return _Reply(status=503)

    def get(self, url, **kwargs):
        return self._next(url)

    def post(self, url, **kwargs):
        return self._next(url)


@pytest.mark.asyncio
async def test_one_source_answering_is_enough():
    """<b>یک نقطه‌ی اتصال برای قیمت‌گذاری کافی نیست.</b>

    ربات روی سرور خارج از ایران است و صرافی ایرانی؛ بدون منبع دوم،
    هر تکانِ آن سمت قیمت‌گذاری را می‌خواباند.
    """
    session = _Session(
        {"market/stats": _Reply(body={"stats": {"usdt-rls": {"latest": "1200000"}}})}
    )

    assert await usdtrate.market_toman(usdtrate.coins.USDT, session=session) == 120_000


@pytest.mark.asyncio
async def test_every_source_is_read_so_they_can_be_cross_checked():
    """<b>چرا دیگر روی اولین موفق نمی‌ایستیم.</b>

    بعضی از این صرافی‌ها ریال می‌دهند و بعضی تومان. اگر واحدِ یکی را
    اشتباه بفهمیم عددش ده برابر است — و آن عدد هنوز «معقول» به نظر
    می‌رسد، پس هیچ بازه‌ای نمی‌گیردش. تنها چیزی که می‌گیردش، مقایسه
    با بقیه است؛ و مقایسه یعنی همه باید خوانده شوند.
    """
    spec = usdtrate.coins.get(usdtrate.coins.USDT)
    session = _Session({"nobitex.ir/v2": _Reply(body={"lastTradePrice": "1200000"})})

    await usdtrate.market_toman(usdtrate.coins.USDT, session=session)

    assert len(session.seen) == len(usdtrate._sources(spec))


@pytest.mark.asyncio
async def test_sources_that_disagree_change_nothing():
    """<b>وقتی مطمئن نیستیم، قیمت را عوض نمی‌کنیم.</b>

    اختلاف زیاد بین دو بازار یعنی یکی‌شان را اشتباه می‌خوانیم — به
    احتمال زیاد ریال را تومان گرفته‌ایم. نرخِ کمی قدیمی از نرخِ
    قاطعانه غلط بی‌نهایت بهتر است.
    """
    session = _Session(
        {
            "nobitex.ir/v2": _Reply(body={"lastTradePrice": "1200000"}),   # ۱۲۰٬۰۰۰
            "raastin": _Reply(body={"bids": [{"price": "12000"}]}),        # ۱۲٬۰۰۰
        }
    )

    with pytest.raises(usdtrate.RateError) as caught:
        await usdtrate.market_toman(usdtrate.coins.USDT, session=session)

    assert "نمی‌خوانند" in str(caught.value)


@pytest.mark.asyncio
async def test_sources_that_agree_give_the_middle_one():
    """میانه، نه میانگین: یک عددِ پرت میانگین را می‌کشد."""
    session = _Session(
        {
            "nobitex.ir/v2": _Reply(body={"lastTradePrice": "1200000"}),   # ۱۲۰٬۰۰۰
            "raastin": _Reply(body={"bids": [{"price": "121000"}]}),       # ۱۲۱٬۰۰۰
            "exir": _Reply(body={"bids": [["122000", "1"]]}),              # ۱۲۲٬۰۰۰
        }
    )

    assert await usdtrate.market_toman(usdtrate.coins.USDT, session=session) == 121_000


@pytest.mark.asyncio
async def test_when_every_source_fails_the_message_names_them_all():
    """وقتی هیچ‌کدام کار نمی‌کند، دانستنِ اینکه هرکدام چه گفت تنها سرنخ است."""
    session = _Session(
        {
            "nobitex.ir/v2": _Reply(status=403),
            "market/stats": _Reply(status=502),
        }
    )

    with pytest.raises(usdtrate.RateError) as caught:
        await usdtrate.market_toman(usdtrate.coins.USDT, session=session)

    assert "403" in str(caught.value)
    assert "502" in str(caught.value)


@pytest.mark.asyncio
async def test_a_nonsense_price_is_dropped_not_used():
    """<b>عدد نامعقول بدتر از پاسخ ندادن است.</b>

    پاسخِ خراب هنوز «یک عدد» است و بی‌صدا قیمت را عوض می‌کند. رد
    کردنش همان کاری است که با خطای شبکه می‌کنیم.
    """
    session = _Session(
        {
            "nobitex.ir/v2": _Reply(body={"lastTradePrice": "12"}),        # بی‌معنی
            "market/stats": _Reply(
                body={"stats": {"usdt-rls": {"latest": "1200000"}}}
            ),
        }
    )

    assert await usdtrate.market_toman(usdtrate.coins.USDT, session=session) == 120_000


def test_every_source_declares_its_own_unit():
    """<b>تقسیم بر ده نباید یک جای مشترک باشد.</b>

    نوبیتکس ریال می‌دهد، راستین و اکسیر تومان. اگر واحد یک جای ثابت
    فرض شود، اضافه کردن منبعِ تومانی یعنی ده برابر خطا در قیمت — و آن
    خطا بی‌صداست، چون عددش هنوز معقول به نظر می‌رسد.
    """
    for code in usdtrate.coins.all_codes():
        for source in usdtrate._sources(usdtrate.coins.get(code)):
            assert source.divisor in (1, 10)


def test_a_price_can_be_read_from_a_list_as_well_as_a_dict():
    """صرافی‌ها هر دو شکل را می‌دهند: asks[0].price و asks[0][0]."""
    body = {"bids": [{"price": "121000"}], "asks": [["122000", "1"]]}

    assert usdtrate._number_at(body, ("bids", 0), ("price",)) == 121_000
    assert usdtrate._number_at(body, ("asks", 0, 0), ()) == 122_000
    assert usdtrate._number_at(body, ("bids", 9), ("price",)) is None
    assert usdtrate._number_at(body, ("nope", 0), ("price",)) is None


# ── وقتی نامِ صرافی ترجمه نمی‌شود ────────────────────────────────────


@pytest.mark.asyncio
async def test_a_name_is_looked_up_over_https_when_the_system_dns_fails(monkeypatch):
    """<b>خطایی که روی سرور واقعی دیدیم.</b>

    سرور به اینترنت وصل بود — تلگرام و گیت‌هاب کار می‌کردند — ولی
    <code>api.nobitex.ir</code> اصلاً به IP ترجمه نمی‌شد. صرافی ما را
    رد نکرده بود؛ ما اصلاً پیدایش نمی‌کردیم.
    """
    from telkap.services import dnsfix

    dnsfix.forget()

    async def no_system_dns(host, port, family):
        raise OSError("نام ترجمه نشد")

    async def doh(host, session=None):
        return ["1.2.3.4"]

    monkeypatch.setattr(dnsfix, "_system", no_system_dns)
    monkeypatch.setattr(dnsfix, "over_https", doh)

    found = await dnsfix.Resolver().resolve("api.nobitex.ir", 443)

    assert found[0]["host"] == "1.2.3.4"
    # نام باید بماند، وگرنه گواهی TLS با IP سنجیده می‌شود و رد می‌شود
    assert found[0]["hostname"] == "api.nobitex.ir"


@pytest.mark.asyncio
async def test_the_system_dns_is_tried_first(monkeypatch):
    """<b>راه معمولی سریع‌تر است و به سرویس بیرونی وابسته نیست.</b>

    اگر همیشه از HTTPS می‌پرسیدیم، برای مسئله‌ای که اغلب وجود ندارد
    یک وابستگی تازه می‌ساختیم.
    """
    from telkap.services import dnsfix

    dnsfix.forget()

    async def system(host, port, family):
        return [{"hostname": host, "host": "5.6.7.8", "port": port,
                 "family": 2, "proto": 0, "flags": 0}]

    async def explode(host, session=None):
        raise AssertionError("نباید از HTTPS پرسیده می‌شد")

    monkeypatch.setattr(dnsfix, "_system", system)
    monkeypatch.setattr(dnsfix, "over_https", explode)

    found = await dnsfix.Resolver().resolve("api.nobitex.ir", 443)

    assert found[0]["host"] == "5.6.7.8"


@pytest.mark.asyncio
async def test_a_name_that_resolves_nowhere_raises_clearly(monkeypatch):
    """اگر هیچ راهی جواب ندهد، خطا باید بگوید هر دو راه امتحان شد."""
    from telkap.services import dnsfix

    dnsfix.forget()

    async def no_system_dns(host, port, family):
        raise OSError("نام ترجمه نشد")

    async def nothing(host, session=None):
        return []

    monkeypatch.setattr(dnsfix, "_system", no_system_dns)
    monkeypatch.setattr(dnsfix, "over_https", nothing)

    with pytest.raises(OSError) as caught:
        await dnsfix.Resolver().resolve("api.nobitex.ir", 443)

    assert "DoH" in str(caught.value)


@pytest.mark.asyncio
async def test_only_real_address_records_are_used(monkeypatch):
    """<b>CNAME یک IP نیست.</b>

    پاسخ DoH رکوردهای دیگری هم دارد. دادنشان به aiohttp یعنی خطای
    مبهم در لایه‌ی بعد، جایی که دیگر معلوم نیست از کجا آمده.
    """
    from telkap.services import dnsfix

    dnsfix.forget()
    session = _Session(
        {
            "cloudflare": _Reply(
                body={
                    "Answer": [
                        {"type": 5, "data": "nobitex.ir.cdn.example."},
                        {"type": 1, "data": "9.9.9.9"},
                    ]
                }
            )
        }
    )

    found = await dnsfix.over_https("api.nobitex.ir", session=session)

    assert found == ["9.9.9.9"]


@pytest.mark.asyncio
async def test_a_resolved_name_is_remembered(monkeypatch):
    """پرسیدنِ دوباره در هر دور نرخ، یک رفت‌وبرگشت بی‌فایده است."""
    from telkap.services import dnsfix

    dnsfix.forget()
    session = _Session(
        {"cloudflare": _Reply(body={"Answer": [{"type": 1, "data": "9.9.9.9"}]})}
    )

    await dnsfix.over_https("api.nobitex.ir", session=session)
    await dnsfix.over_https("api.nobitex.ir", session=session)

    assert len(session.seen) == 1
    dnsfix.forget()
