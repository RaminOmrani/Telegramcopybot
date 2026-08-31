"""تست پرداخت تتر.

اینجا پول واقعی جابه‌جا می‌شود، پس چیزهایی سنجیده می‌شوند که اشتباهشان
گران است: جهت گرد کردن، اعتبارسنجی نشانی، و اینکه راه پرداخت تا کامل
نشدنش به کاربر نشان داده نشود.
"""
from __future__ import annotations

from decimal import Decimal

import pytest

from telkap.services import crypto

# ── نشانی ولت ───────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "value",
    [
        "TJRabPrwbZy45sbavfcjinPJC18kjpRTv8",
        "  TJRabPrwbZy45sbavfcjinPJC18kjpRTv8  ",
    ],
)
def test_a_real_looking_address_passes(value):
    assert crypto.valid_address(value) is True


@pytest.mark.parametrize(
    "value",
    [
        "",
        "not-an-address",
        "0x1234567890abcdef1234567890abcdef12345678",   # نشانی اتریوم
        "TJRabPrwbZy45sbavfcjinPJC18kjpRTv",             # یک نویسه کم
        "TJRabPrwbZy45sbavfcjinPJC18kjpRTv88",           # یک نویسه زیاد
        "BJRabPrwbZy45sbavfcjinPJC18kjpRTv8",            # با T شروع نمی‌شود
        "TJRabPrwbZy45sbavfcjinPJC18kjpRT0O",            # 0 و O در Base58 نیستند
    ],
)
def test_a_wrong_address_is_rejected(value):
    """نشانی اشتباه یعنی پولِ مشتری به جای دیگری می‌رود."""
    assert crypto.valid_address(value) is False


# ── هش تراکنش ───────────────────────────────────────────────────────


def test_a_hash_is_normalized():
    raw = "A" * 64
    assert crypto.normalize_tx(raw) == "a" * 64
    assert crypto.normalize_tx("0x" + raw) == "a" * 64
    assert crypto.normalize_tx(f"  {raw}  ") == "a" * 64


@pytest.mark.parametrize(
    "value",
    ["", "abc", "z" * 64, "a" * 63, "a" * 65, None],
)
def test_a_wrong_hash_is_rejected(value):
    assert crypto.normalize_tx(value) == ""


# ── تبدیل مبلغ ──────────────────────────────────────────────────────


def test_conversion_rounds_up():
    """کم بودن مبلغ یعنی پرداخت ناقص و یک رفت‌وبرگشت با پشتیبانی.

    چند سنتِ بیشتر این دردسر را ندارد، پس عمداً رو به بالا گرد می‌شود.
    """
    # ۱۲۹٬۰۰۰ ÷ ۹۵٬۰۰۰ = 1.3578…  → باید ۱٫۳۶ شود نه ۱٫۳۵
    assert crypto.to_usdt(129_000, 95_000) == Decimal("1.36")


def test_an_exact_amount_is_not_inflated():
    assert crypto.to_usdt(190_000, 95_000) == Decimal("2")


@pytest.mark.parametrize(
    "amount,rate",
    [(0, 95_000), (-1, 95_000), (129_000, 0), (129_000, -5)],
)
def test_nonsense_input_gives_zero(amount, rate):
    assert crypto.to_usdt(amount, rate) == Decimal("0")


@pytest.mark.parametrize(
    "value,expected",
    [
        (Decimal("12.00"), "12"),
        (Decimal("12.50"), "12.5"),
        (Decimal("12.34"), "12.34"),
        (Decimal("0.10"), "0.1"),
        (Decimal("0"), "0"),
    ],
)
def test_amounts_are_shown_without_useless_zeros(value, expected):
    assert crypto.format_usdt(value) == expected


# ── نرخ و در دسترس بودن ─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_the_rate_accepts_separators(monkeypatch):
    saved = {}

    async def write(key, value, admin_id):
        saved[key] = value

    monkeypatch.setattr(crypto, "_write", write)

    assert await crypto.set_rate("95,000") == 95_000
    assert await crypto.set_rate("95،000") == 95_000     # جداکننده‌ی فارسی
    # ادمین فارسی‌زبان احتمالاً با کیبورد فارسی عدد می‌زند. int خودِ
    # پایتون ارقام فارسی را می‌فهمد، ولی این را صریح می‌سنجیم چون اگر
    # روزی تبدیل دستی اضافه شود، همین‌جا لو می‌رود.
    assert await crypto.set_rate("۹۵۰۰۰") == 95_000
    assert saved[crypto.RATE_KEY] == 95_000


@pytest.mark.asyncio
@pytest.mark.parametrize("value", ["", "abc", "0", "500", None])
async def test_a_nonsense_rate_is_refused(monkeypatch, value):
    """نرخ صفر یعنی مبلغ‌های بی‌معنی و پرداخت‌های خراب."""
    async def write(key, v, admin_id):
        raise AssertionError("نباید ذخیره می‌شد")

    monkeypatch.setattr(crypto, "_write", write)
    assert await crypto.set_rate(value) is None


@pytest.mark.asyncio
async def test_a_stored_rate_below_the_floor_reads_as_unset(monkeypatch):
    """اگر مقداری از نسخه‌ای قدیمی‌تر مانده باشد، نباید قبولش کنیم."""
    async def read(key):
        return 5

    monkeypatch.setattr(crypto, "_read", read)
    assert await crypto.rate() == 0


@pytest.mark.asyncio
async def test_a_corrupt_stored_rate_does_not_raise(monkeypatch):
    async def read(key):
        return "چیز نامعلوم"

    monkeypatch.setattr(crypto, "_read", read)
    assert await crypto.rate() == 0


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "wallet,rate_value,ready",
    [
        ("TJRabPrwbZy45sbavfcjinPJC18kjpRTv8", 95_000, True),
        ("TJRabPrwbZy45sbavfcjinPJC18kjpRTv8", 0, False),   # نشانی بدون نرخ
        ("", 95_000, False),                                 # نرخ بدون نشانی
        ("", 0, False),
    ],
)
async def test_both_pieces_are_required(monkeypatch, wallet, rate_value, ready):
    """نشانیِ بدون نرخ یعنی نمی‌دانیم چقدر بخواهیم، و نرخِ بدون نشانی
    یعنی جایی برای واریز نیست. هیچ‌کدام به‌تنهایی کافی نیست."""
    async def read(key):
        return wallet if key == crypto.ADDRESS_KEY else rate_value

    monkeypatch.setattr(crypto, "_read", read)
    assert await crypto.available() is ready


@pytest.mark.asyncio
async def test_the_quote_carries_everything_the_screen_needs(monkeypatch):
    async def read(key):
        if key == crypto.ADDRESS_KEY:
            return "TJRabPrwbZy45sbavfcjinPJC18kjpRTv8"
        return 95_000

    monkeypatch.setattr(crypto, "_read", read)

    priced = await crypto.quote(129_000)
    assert priced["address"] == "TJRabPrwbZy45sbavfcjinPJC18kjpRTv8"
    assert priced["rate"] == 95_000
    assert priced["usdt_text"] == "1.36"


@pytest.mark.asyncio
async def test_no_quote_when_the_method_is_not_ready(monkeypatch):
    async def read(key):
        return None

    monkeypatch.setattr(crypto, "_read", read)
    assert await crypto.quote(129_000) is None


def test_both_methods_have_labels():
    assert all(crypto.METHOD_LABELS[m] for m in crypto.METHODS)
