"""تست درگاه زرین‌پال.

قرارداد API از نمونه‌ی رسمی خودِ زرین‌پال گرفته شده، پس اینجا شکل
درخواست مو‌به‌مو سنجیده می‌شود — یک نام فیلدِ اشتباه یعنی هیچ پرداختی
انجام نمی‌شود و علتش هم در لاگ پیدا نیست.

مهم‌ترین تست اینجا آن است که پارامترهای نشانیِ بازگشت پرداخت را ثابت
نمی‌کنند؛ هرکسی می‌تواند آن نشانی را دستی باز کند.
"""
from __future__ import annotations

import dataclasses

import pytest

from telkap import config
from telkap.services import zarinpal

CODE = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"


@pytest.fixture
def merchant(monkeypatch):
    """درگاه تنظیم‌شده، بدون دست زدن به .env واقعی.

    کد پذیرنده از دیتابیس خوانده می‌شود، پس اینجا هم جایگزین می‌شود:
    تست‌های این فایل درباره‌ی <b>شکل درخواست</b> به زرین‌پال‌اند، نه
    درباره‌ی جای نگهداری کد — و بالا آوردن یک دیتابیس برایشان فقط
    کندشان می‌کند.
    """
    base = config.get_settings()
    fake = dataclasses.replace(
        base,
        zarinpal_merchant=CODE,
        web_base_url="https://botpanel.example.com",
    )
    monkeypatch.setattr(config, "get_settings", lambda: fake)
    monkeypatch.setattr(zarinpal, "get_settings", lambda: fake)

    async def stored():
        return CODE

    monkeypatch.setattr(zarinpal, "merchant", stored)
    return fake


class Calls:
    """جای تماس با شبکه می‌نشیند و آنچه فرستاده شده را نگه می‌دارد."""

    def __init__(self, reply=None):
        self.sent: list[tuple[str, dict]] = []
        self.reply = reply

    async def post(self, url, payload):
        self.sent.append((url, payload))
        return self.reply


# ── بدون تنظیمات، هیچ تماسی گرفته نمی‌شود ───────────────────────────


@pytest.mark.asyncio
async def test_an_unconfigured_gateway_makes_no_calls(monkeypatch):
    async def explode(*args, **kwargs):
        raise AssertionError("نباید تماسی گرفته می‌شد")

    async def no_merchant():
        return ""

    monkeypatch.setattr(zarinpal, "_post", explode)
    monkeypatch.setattr(zarinpal, "merchant", no_merchant)

    assert await zarinpal.start(129_000, "اشتراک", request_id=1) is None
    assert await zarinpal.verify("A000", 129_000) is None


@pytest.mark.asyncio
async def test_both_pieces_are_required(monkeypatch):
    """بدون کد پذیرنده درخواستی ساخته نمی‌شود، و بدون نشانی عمومی
    زرین‌پال جایی برای برگرداندن کاربر ندارد."""
    base = config.get_settings()

    for merchant_id, base_url, ready in [
        ("m", "https://x.example.com", True),
        ("m", "", False),
        ("", "https://x.example.com", False),
        ("", "", False),
    ]:
        fake = dataclasses.replace(
            base, zarinpal_merchant=merchant_id, web_base_url=base_url
        )
        monkeypatch.setattr(zarinpal, "get_settings", lambda f=fake: f)

        async def stored(value=merchant_id):
            return value

        monkeypatch.setattr(zarinpal, "merchant", stored)
        assert await zarinpal.configured() is ready


# ── شکل درخواست ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_the_request_matches_the_official_contract(merchant, monkeypatch):
    """نام فیلدها از نمونه‌ی رسمی زرین‌پال آمده‌اند.

    یکی‌شان که غلط باشد، درگاه درخواست را رد می‌کند و کاربر فقط
    «اتصال ممکن نشد» می‌بیند.
    """
    calls = Calls({"data": {"code": 100, "authority": "A00000000000000000000000000000000001"}})
    monkeypatch.setattr(zarinpal, "_post", calls.post)

    authority = await zarinpal.start(129_000, "اشتراک ۷ روزه", request_id=42)

    assert authority == "A00000000000000000000000000000000001"
    url, payload = calls.sent[0]
    assert url == "https://api.zarinpal.com/pg/v4/payment/request.json"
    assert set(payload) == {
        "merchant_id", "amount", "currency", "callback_url", "description"
    }
    assert payload["amount"] == 129_000
    # مبلغ به تومان می‌رود. ارز صریح فرستاده می‌شود تا به پیش‌فرضِ
    # سرویس تکیه نکنیم — اگر روزی پیش‌فرض ریال شود، مبلغ‌ها ده برابر
    # اشتباه می‌شدند.
    assert payload["currency"] == "IRT"
    assert payload["callback_url"] == "https://botpanel.example.com/pay/zarinpal?rid=42"


@pytest.mark.asyncio
async def test_a_rejected_request_returns_none(merchant, monkeypatch):
    calls = Calls({"data": {"code": -9}, "errors": {"message": "مبلغ نامعتبر"}})
    monkeypatch.setattr(zarinpal, "_post", calls.post)
    assert await zarinpal.start(129_000, "اشتراک", request_id=1) is None


@pytest.mark.asyncio
async def test_code_100_without_an_authority_is_not_trusted(merchant, monkeypatch):
    """پاسخ ناقص نباید کاربر را به نشانی بی‌معنی بفرستد."""
    calls = Calls({"data": {"code": 100}})
    monkeypatch.setattr(zarinpal, "_post", calls.post)
    assert await zarinpal.start(129_000, "اشتراک", request_id=1) is None


@pytest.mark.asyncio
async def test_a_dead_service_returns_none(merchant, monkeypatch):
    calls = Calls(None)
    monkeypatch.setattr(zarinpal, "_post", calls.post)
    assert await zarinpal.start(129_000, "اشتراک", request_id=1) is None


@pytest.mark.asyncio
async def test_a_zero_amount_is_refused(merchant, monkeypatch):
    async def explode(*args, **kwargs):
        raise AssertionError("نباید تماسی گرفته می‌شد")

    monkeypatch.setattr(zarinpal, "_post", explode)
    assert await zarinpal.start(0, "اشتراک", request_id=1) is None


# ── تأیید ───────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_verify_sends_the_amount_we_hold_not_the_one_in_the_url(
    merchant, monkeypatch
):
    """مبلغ از دیتابیس خودمان می‌رود، نه از نشانی بازگشت.

    وگرنه کسی می‌توانست با دست‌کاری نشانی، اشتراک گران را با مبلغ ارزان
    تأیید کند.
    """
    calls = Calls({"data": {"code": 100, "ref_id": 12345, "card_pan": "1234***5678"}})
    monkeypatch.setattr(zarinpal, "_post", calls.post)

    result = await zarinpal.verify("A0001", 429_000)

    url, payload = calls.sent[0]
    assert url == "https://api.zarinpal.com/pg/v4/payment/verify.json"
    assert payload["amount"] == 429_000
    assert payload["authority"] == "A0001"
    assert result["ref_id"] == "12345"


@pytest.mark.asyncio
async def test_already_verified_counts_as_success(merchant, monkeypatch):
    """کد ۱۰۱ یعنی «قبلاً تأیید شده» — پرداخت واقعی است.

    اگر شکست حساب می‌شد، یک تلاش دوباره‌ی بی‌ضرر (قطعی لحظه‌ای شبکه،
    دوبار باز شدن صفحه‌ی بازگشت) کاربری را که پول داده رد می‌کرد.
    """
    calls = Calls({"data": {"code": 101, "ref_id": 999}})
    monkeypatch.setattr(zarinpal, "_post", calls.post)

    result = await zarinpal.verify("A0001", 129_000)
    assert result is not None
    assert result["already"] is True


@pytest.mark.asyncio
@pytest.mark.parametrize("code", [-51, -54, 0, None])
async def test_any_other_code_is_a_failure(merchant, monkeypatch, code):
    calls = Calls({"data": {"code": code}})
    monkeypatch.setattr(zarinpal, "_post", calls.post)
    assert await zarinpal.verify("A0001", 129_000) is None


@pytest.mark.asyncio
async def test_a_garbage_response_does_not_raise(merchant, monkeypatch):
    for reply in [{}, {"data": None}, {"data": "چیز نامعلوم"}, {"errors": []}]:
        calls = Calls(reply)
        monkeypatch.setattr(zarinpal, "_post", calls.post)
        assert await zarinpal.verify("A0001", 129_000) is None
        assert await zarinpal.start(1000, "x", request_id=1) is None


# ── نشانی‌ها ────────────────────────────────────────────────────────


def test_the_pay_url_matches_the_official_sample():
    assert zarinpal.pay_url("A0001") == "https://www.zarinpal.com/pg/StartPay/A0001"


def test_the_callback_path_is_stable(merchant):
    """این مسیر در پنل زرین‌پال ثبت می‌شود؛ عوض شدنش پرداخت‌ها را
    می‌شکند بدون اینکه چیزی در کد خطا بدهد."""
    assert zarinpal.CALLBACK_PATH == "/pay/zarinpal"
    assert zarinpal.callback_url() == "https://botpanel.example.com/pay/zarinpal"


def test_a_trailing_slash_does_not_double_up(monkeypatch):
    base = config.get_settings()
    fake = dataclasses.replace(
        base, zarinpal_merchant="m", web_base_url="https://x.example.com/"
    )
    monkeypatch.setattr(zarinpal, "get_settings", lambda: fake)
    assert zarinpal.callback_url() == "https://x.example.com/pay/zarinpal"


# ── کد پذیرنده از پنل ───────────────────────────────────────────────


@pytest.mark.asyncio
async def test_the_panel_value_wins_over_the_env_file(tmp_path, monkeypatch):
    """<b>چرا کد پذیرنده از .env به پنل آمد.</b>

    تا امروز فقط در .env بود، یعنی روشن کردن درگاه به SSH نیاز داشت —
    همان چیزی که شماره کارت را ماه‌ها خالی نگه داشت و هر خرید را به
    «به پشتیبانی پیام بدهید» می‌رساند.
    """
    from tests.test_copier import _setup

    await _setup(tmp_path, monkeypatch, settings={})
    base = config.get_settings()
    fake = dataclasses.replace(base, zarinpal_merchant="11111111-1111-1111-1111-111111111111")
    monkeypatch.setattr(zarinpal, "get_settings", lambda: fake)

    assert await zarinpal.merchant() == "11111111-1111-1111-1111-111111111111"

    await zarinpal.set_merchant("22222222-2222-2222-2222-222222222222", admin_id=1)

    assert await zarinpal.merchant() == "22222222-2222-2222-2222-222222222222"


@pytest.mark.asyncio
async def test_a_malformed_merchant_code_is_refused(tmp_path, monkeypatch):
    """<b>کد ناقص، تنها نشانه‌اش شکست خوردنِ هر خرید است.</b>

    زرین‌پال کد بد را با یک خطای عمومی رد می‌کند و کاربر فقط می‌بیند
    «درگاه در دسترس نیست». گرفتنِ غلط تایپی همین‌جا، ارزان‌ترین جای
    ممکن است.
    """
    from tests.test_copier import _setup

    await _setup(tmp_path, monkeypatch, settings={})

    for bad in ["", "کوتاه", "11111111-1111-1111-1111", "zzzzzzzz-1111-1111-1111-111111111111"]:
        assert await zarinpal.set_merchant(bad, admin_id=1) is None


@pytest.mark.asyncio
async def test_the_stored_code_is_the_one_actually_sent(tmp_path, monkeypatch):
    """ثبت شدن کافی نیست؛ باید همان به زرین‌پال برود."""
    from tests.test_copier import _setup

    await _setup(tmp_path, monkeypatch, settings={})
    base = config.get_settings()
    fake = dataclasses.replace(base, web_base_url="https://botpanel.example.com")
    monkeypatch.setattr(zarinpal, "get_settings", lambda: fake)

    await zarinpal.set_merchant("33333333-3333-3333-3333-333333333333", admin_id=1)

    calls = Calls({"data": {"code": 100, "authority": "A123"}})
    monkeypatch.setattr(zarinpal, "_post", calls.post)

    await zarinpal.start(129_000, "اشتراک", request_id=7)

    _, payload = calls.sent[0]
    assert payload["merchant_id"] == "33333333-3333-3333-3333-333333333333"
