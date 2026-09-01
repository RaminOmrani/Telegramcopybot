"""تست پرداخت با ترون.

<b>چرا ترون تست جدا دارد.</b> تتر و ترون به یک نشانی واریز می‌شوند و
از یک صرافی قیمت می‌گیرند، ولی در دو چیز اساساً فرق دارند: تتر توکن
است و ترون ارز بومیِ شبکه، پس مسیر خواندنشان از بلاک‌چین یکی نیست؛
و ترون در یک روز ده درصد بالا و پایین می‌رود، پس نرخِ دستی برایش
یعنی ضرر. هر دو تفاوت جایی هستند که اشتباهشان مستقیم به پول ترجمه
می‌شود.
"""
from __future__ import annotations

from decimal import Decimal

import pytest

from telkap.models import PaymentRequest
from telkap.services import coins, cryptocheck, payments, tron, usdtrate
from tests.test_copier import _setup


def native_row(
    *,
    tx_id="a" * 64,
    amount=12_500_000,
    success=True,
    kind="TransferContract",
) -> dict:
    """یک تراکنش خام ترون، همان شکلی که TronGrid می‌دهد."""
    return {
        "txID": tx_id,
        "block_timestamp": 1_700_000_000_000,
        "ret": [{"contractRet": "SUCCESS" if success else "REVERT"}],
        "raw_data": {
            "contract": [
                {
                    "type": kind,
                    "parameter": {
                        "value": {
                            "amount": amount,
                            "owner_address": "41SENDER",
                            "to_address": "41OURS",
                        }
                    },
                }
            ]
        },
    }


# ── خواندن مقدار ─────────────────────────────────────────────────────


def test_sun_becomes_real_trx():
    """<b>یک ترون یک میلیون سان است.</b>

    همان تله‌ی تتر، با ضریب دیگری: خواندن عدد خام یعنی مبلغ را یک
    میلیون برابر دیدن، و اشتراکی که با یک میلیونیمِ قیمت فعال می‌شود.
    """
    parsed = tron._parse_native(native_row(amount=12_500_000))

    assert parsed.amount == Decimal("12.5")
    assert parsed.symbol == "TRX"


def test_a_failed_transaction_moved_no_money():
    """<b>تراکنش شکست‌خورده در فهرست هست ولی پولی جابه‌جا نکرده.</b>

    اگر فقط به وجود داشتنِ تراکنش نگاه می‌کردیم، هر کسی می‌توانست یک
    انتقالِ شکست‌خورده بسازد و هشش را بفرستد.
    """
    assert tron._parse_native(native_row(success=False)) is None


def test_a_transaction_that_is_not_a_transfer_is_ignored():
    """قرارداد هوشمند و رأی‌دادن هم تراکنش‌اند، ولی واریز نیستند."""
    assert tron._parse_native(native_row(kind="TriggerSmartContract")) is None
    assert tron._parse_native(native_row(kind="VoteWitnessContract")) is None


def test_a_row_we_do_not_understand_is_dropped():
    assert tron._parse_native({}) is None
    assert tron._parse_native({"txID": "کوتاه"}) is None
    assert tron._parse_native(native_row(amount=0)) is None
    assert tron._parse_native(native_row(amount="نه عدد")) is None
    assert tron._parse_native("رشته") is None


def test_trx_is_never_mistaken_for_usdt():
    """ارز بومی قرارداد ندارد، پس هیچ‌وقت از سد بررسیِ قرارداد رد نمی‌شود."""
    parsed = tron._parse_native(native_row())

    assert parsed.contract == ""
    assert parsed.is_usdt is False


# ── انتخاب مسیر ──────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_each_coin_is_read_from_its_own_endpoint(monkeypatch):
    """<b>یک مسیر مشترک وسوسه‌انگیز بود و کار نمی‌کرد.</b>

    انتقال تتر یک رویداد روی قرارداد است و در فهرست trc20 می‌آید؛
    انتقال ترون یک TransferContract داخل خودِ تراکنش است. نه نشانیِ
    سرویس یکی است نه شکل پاسخ.
    """
    used = []

    async def usdt_reader(address, **kwargs):
        used.append("usdt")
        return {}

    async def trx_reader(address, **kwargs):
        used.append("trx")
        return {}

    monkeypatch.setattr(tron, "incoming_usdt", usdt_reader)
    monkeypatch.setattr(tron, "incoming_trx", trx_reader)

    await tron.incoming(coins.USDT, "TWalletOfOurs")
    await tron.incoming(coins.TRX, "TWalletOfOurs")

    assert used == ["usdt", "trx"]


@pytest.mark.asyncio
async def test_an_unknown_coin_reads_nothing(monkeypatch):
    """ارز ناشناخته یعنی خطای برنامه‌نویس؛ نباید به شبکه بزند."""

    async def boom(*args, **kwargs):
        raise AssertionError("نباید صدا زده می‌شد")

    monkeypatch.setattr(tron, "incoming_usdt", boom)
    monkeypatch.setattr(tron, "incoming_trx", boom)

    assert await tron.incoming("dogecoin", "TWalletOfOurs") == {}


# ── نرخ ──────────────────────────────────────────────────────────────


def test_the_two_coins_have_different_markets():
    """<b>قیمت تومانی ترون مستقیم از بازار می‌آید.</b>

    نه از ضرب قیمت دلاری در نرخ دلار — یک مرحله کمتر، یک جای کمترِ
    خطا. نوبیتکس هر دو را به ریال می‌دهد.
    """
    assert coins.get(coins.USDT).market == "USDTIRT"
    assert coins.get(coins.TRX).market == "TRXIRT"


def test_the_sane_ranges_do_not_fit_one_another():
    """<b>بازه‌ی معقول برای هر ارز جداست، و باید هم باشد.</b>

    یک بازه‌ی مشترک یا آن‌قدر گشاد می‌شد که هیچ خطایی را نگیرد، یا
    قیمت درستِ ترون را رد می‌کرد: یک تتر ده‌ها هزار تومان است و یک
    ترون چند هزار تومان.
    """
    trx_low, trx_high = usdtrate.SANE_RANGE[coins.TRX]
    usdt_low, usdt_high = usdtrate.SANE_RANGE[coins.USDT]

    assert trx_low <= 6_000 <= trx_high            # نرخ واقعی ترون
    assert not usdt_low <= 6_000 <= usdt_high      # ولی برای تتر بی‌معنی
    assert usdt_low <= 120_000 <= usdt_high        # نرخ واقعی تتر


@pytest.mark.asyncio
async def test_a_rial_price_read_as_toman_is_caught_by_the_jump_guard(
    tmp_path, monkeypatch
):
    """<b>بازه‌ی معقول جلوی خطای ده‌برابری را نمی‌گیرد — و نمی‌تواند.</b>

    نوبیتکس ریال می‌دهد و ما بر ده تقسیم می‌کنیم. اگر روزی آن تقسیم
    از قلم بیفتد، نرخ ده برابر می‌شود؛ ولی بازه باید آن‌قدر گشاد
    بماند که تورمِ سال‌های بعد را هم جا بدهد، پس عددِ ده‌برابری هنوز
    داخلش می‌افتد.

    چیزی که واقعاً می‌گیردش محافظِ <b>جهش</b> است: ده برابر شدن در یک
    ربع ساعت بازار نیست، خطاست. این تست هست تا آن محافظ به بهانه‌ی
    «بازه که هست» برداشته نشود.
    """
    from telkap.services import alerts, crypto

    await _setup(tmp_path, monkeypatch, settings={})
    await crypto.set_rate(6_000, coin=coins.TRX, admin_id=1)
    await usdtrate.set_auto(True, admin_id=1)

    async def market(coin=coins.USDT, **kwargs):
        return 60_000                     # ریال، به‌اشتباه به‌جای تومان

    told = []

    async def fake_send(text, **kwargs):
        told.append(text)
        return 1

    monkeypatch.setattr(usdtrate, "market_toman", market)
    monkeypatch.setattr(alerts, "send", fake_send)

    low, high = usdtrate.SANE_RANGE[coins.TRX]
    assert low <= 60_000 <= high          # از سد بازه رد می‌شود

    assert (await usdtrate.refresh(coins.TRX)).rate == 0
    assert await crypto.rate(coins.TRX) == 6_000
    assert told and "اعمال نشد" in told[0]


@pytest.mark.asyncio
async def test_trx_carries_a_wider_margin_by_default(tmp_path, monkeypatch):
    """<b>ترون در نیم ساعت تکان می‌خورد، تتر نه.</b>

    مبلغ در لحظه‌ی ساخت درخواست قفل می‌شود. اگر مشتری نیم ساعت بعد
    بپردازد، حاشیه همان چیزی است که جلوی ضرر را می‌گیرد.
    """
    await _setup(tmp_path, monkeypatch, settings={})

    assert await usdtrate.margin(coins.TRX) > await usdtrate.margin(coins.USDT)


@pytest.mark.asyncio
async def test_a_margin_set_for_one_coin_does_not_move_the_other(
    tmp_path, monkeypatch
):
    await _setup(tmp_path, monkeypatch, settings={})

    await usdtrate.set_margin(9, coin=coins.TRX, admin_id=1)

    assert await usdtrate.margin(coins.TRX) == 9
    assert await usdtrate.margin(coins.USDT) == coins.get(coins.USDT).default_margin


@pytest.mark.asyncio
async def test_each_coin_keeps_its_own_rate(tmp_path, monkeypatch):
    """نرخ مشترک یعنی خریدِ ترون به قیمت تتر."""
    from telkap.services import crypto

    await _setup(tmp_path, monkeypatch, settings={})

    await crypto.set_rate(120_000, coin=coins.USDT, admin_id=1)
    await crypto.set_rate(6_000, coin=coins.TRX, admin_id=1)

    assert await crypto.rate(coins.USDT) == 120_000
    assert await crypto.rate(coins.TRX) == 6_000


@pytest.mark.asyncio
async def test_one_broken_market_does_not_stop_the_other(tmp_path, monkeypatch):
    """<b>خرابیِ یک بازار نباید فروشِ ارز دیگر را بخواباند.</b>"""
    from telkap.services import crypto

    await _setup(tmp_path, monkeypatch, settings={})
    await usdtrate.set_auto(True, admin_id=1)

    async def market(coin=coins.USDT, **kwargs):
        if coin == coins.TRX:
            raise usdtrate.RateError("بازار ترون در دسترس نیست")
        return 120_000

    monkeypatch.setattr(usdtrate, "market_toman", market)

    result = await usdtrate.refresh_all()

    assert result[coins.USDT].rate > 0
    assert result[coins.TRX].rate == 0
    assert result[coins.TRX].error        # و می‌گوید کدام بازار خراب بود
    assert await crypto.rate(coins.USDT) > 0


# ── مبلغ گفته‌شده به کاربر ───────────────────────────────────────────


def test_a_cheap_coin_needs_more_decimals():
    """<b>دو رقم اعشار برای ترون کافی نیست.</b>

    با نرخ شش هزار تومان، هر صدم ترون شصت تومان است — ولی مسئله
    گرد شدن نیست، مسئله این است که مبلغِ گفته‌شده با مبلغِ خواسته‌شده
    یکی بماند.
    """
    assert coins.get(coins.TRX).quantize == "0.0001"
    assert coins.get(coins.USDT).quantize == "0.01"


def test_trailing_zeros_are_not_shown():
    """۱۲٫۰۰ تتر که «12» نوشته شود، همان مبلغ است و خوانا‌تر."""
    from telkap.services import crypto

    assert crypto.format_amount(Decimal("12.00")) == "12"
    assert crypto.format_amount(Decimal("12.50")) == "12.5"
    assert crypto.format_amount(Decimal("0.0001")) == "0.0001"


@pytest.mark.asyncio
async def test_a_trx_quote_uses_the_trx_rate_and_precision(tmp_path, monkeypatch):
    from telkap.services import crypto

    await _setup(tmp_path, monkeypatch, settings={})
    await crypto.set_address("T" + "9" * 33, admin_id=1)
    await crypto.set_rate(6_000, coin=coins.TRX, admin_id=1)

    quote = await crypto.quote(500_000, coins.TRX)

    assert quote["symbol"] == "TRX"
    assert quote["rate"] == 6_000
    assert quote["amount"] == Decimal("83.3334")      # رو به بالا
    assert quote["amount_text"] == "83.3334"


@pytest.mark.asyncio
async def test_a_coin_without_a_rate_is_not_offered(tmp_path, monkeypatch):
    """<b>ارزی که نرخ ندارد نباید در منو باشد.</b>

    نشان دادنش یعنی مشتری روی دکمه‌ای می‌زند که به بن‌بست می‌رسد.
    """
    from telkap.services import crypto

    await _setup(tmp_path, monkeypatch, settings={})
    await crypto.set_address("T" + "9" * 33, admin_id=1)
    await crypto.set_rate(120_000, coin=coins.USDT, admin_id=1)

    assert await crypto.ready_coins() == (coins.USDT,)


@pytest.mark.asyncio
async def test_no_wallet_means_no_coin_at_all(tmp_path, monkeypatch):
    from telkap.services import crypto

    await _setup(tmp_path, monkeypatch, settings={})
    await crypto.set_rate(120_000, coin=coins.USDT, admin_id=1)

    assert await crypto.ready_coins() == ()


# ── مسیر کامل ────────────────────────────────────────────────────────


async def _request(db_module, **kwargs) -> PaymentRequest:
    fields = {
        "user_id": 7,
        "plan_code": "month",
        "pay_method": payments.METHOD_TRX,
        "amount_toman": 500_000,
        "usdt_amount": "83.3334",
        "usdt_rate": 6_000,
    }
    fields.update(kwargs)
    async with db_module.get_session() as db:
        request = PaymentRequest(**fields)
        db.add(request)
        await db.commit()
        await db.refresh(request)
        return request


@pytest.mark.asyncio
async def test_the_pay_method_decides_which_chain_is_read(tmp_path, monkeypatch):
    """روش پرداخت و کد ارز عمداً یکی نگه داشته شده‌اند."""
    db_module, _ = await _setup(tmp_path, monkeypatch, settings={})
    trx = await _request(db_module, tx_hash="a" * 64)
    usdt = await _request(
        db_module, tx_hash="b" * 64, pay_method=payments.METHOD_USDT
    )

    assert cryptocheck.coin_of(trx) == coins.TRX
    assert cryptocheck.coin_of(usdt) == coins.USDT


@pytest.mark.asyncio
async def test_an_old_request_without_a_coin_is_read_as_usdt(tmp_path, monkeypatch):
    """درخواست‌های پیش از افزودن ترون همه تتری بودند."""
    db_module, _ = await _setup(tmp_path, monkeypatch, settings={})
    old = await _request(db_module, tx_hash="a" * 64, pay_method="")

    assert cryptocheck.coin_of(old) == coins.USDT


@pytest.mark.asyncio
async def test_a_trx_payment_activates_the_subscription(tmp_path, monkeypatch):
    """همان چیزی که کل این مسیر برایش نوشته شد."""
    db_module, _ = await _setup(tmp_path, monkeypatch, settings={})
    request = await _request(db_module, tx_hash="a" * 64)

    async def wallet():
        return "TWalletOfOurs"

    async def transfers(address, **kwargs):
        return {
            "a" * 64: tron.Transfer(
                tx_id="a" * 64,
                sender="41SENDER",
                to="41OURS",
                contract="",
                amount=Decimal("83.3334"),
                symbol="TRX",
                timestamp_ms=1_700_000_000_000,
            )
        }

    monkeypatch.setattr(cryptocheck.crypto, "address", wallet)
    monkeypatch.setattr(tron, "incoming_trx", transfers)

    told = []

    async def notify(user_id, text):
        told.append((user_id, text))

    assert await cryptocheck.run_once(notifier=notify) == 1

    async with db_module.get_session() as db:
        after = await db.get(PaymentRequest, request.id)
        assert after.status == PaymentRequest.STATUS_APPROVED

    assert told and "TRX" in told[0][1]


@pytest.mark.asyncio
async def test_a_usdt_transfer_does_not_pay_for_a_trx_request(tmp_path, monkeypatch):
    """<b>هشِ یک واریزِ تتر نباید درخواستِ ترونی را باز کند.</b>

    مبلغ‌ها هم‌جنس نیستند: هشتاد و سه تتر بیست برابرِ هشتاد و سه
    ترون می‌ارزد. چون هر ارز از فهرست خودش خوانده می‌شود، هشِ تتری
    اصلاً در فهرست ترون نیست.
    """
    db_module, _ = await _setup(tmp_path, monkeypatch, settings={})
    request = await _request(db_module, tx_hash="a" * 64)

    async def wallet():
        return "TWalletOfOurs"

    async def usdt_only(address, **kwargs):
        return {
            "a" * 64: tron.Transfer(
                tx_id="a" * 64,
                sender="TSender",
                to="TWalletOfOurs",
                contract=tron.USDT_CONTRACT,
                amount=Decimal("83.3334"),
                symbol="USDT",
                timestamp_ms=1_700_000_000_000,
            )
        }

    async def no_trx(address, **kwargs):
        return {}

    monkeypatch.setattr(cryptocheck.crypto, "address", wallet)
    monkeypatch.setattr(tron, "incoming_usdt", usdt_only)
    monkeypatch.setattr(tron, "incoming_trx", no_trx)

    assert await cryptocheck.run_once() == 0

    async with db_module.get_session() as db:
        after = await db.get(PaymentRequest, request.id)
        assert after.status == PaymentRequest.STATUS_PENDING


@pytest.mark.asyncio
async def test_both_coins_are_checked_in_the_same_round(tmp_path, monkeypatch):
    """<b>هر ارز یک بار خوانده می‌شود، نه یک بار برای هر درخواست.</b>"""
    db_module, _ = await _setup(tmp_path, monkeypatch, settings={})
    await _request(db_module, tx_hash="a" * 64)
    await _request(db_module, tx_hash="c" * 64)                    # ترونِ دوم
    await _request(
        db_module,
        tx_hash="b" * 64,
        pay_method=payments.METHOD_USDT,
        usdt_amount="12.5",
    )

    calls = {"usdt": 0, "trx": 0}

    async def wallet():
        return "TWalletOfOurs"

    def reader(name, result):
        async def read(address, **kwargs):
            calls[name] += 1
            return result

        return read

    monkeypatch.setattr(cryptocheck.crypto, "address", wallet)
    monkeypatch.setattr(tron, "incoming_usdt", reader("usdt", {}))
    monkeypatch.setattr(tron, "incoming_trx", reader("trx", {}))

    await cryptocheck.run_once()

    assert calls == {"usdt": 1, "trx": 1}


# ── وقتی صرافی ایرانی در دسترس نیست ─────────────────────────────────


class _Reply:
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
    """نشست ساختگی: هر نشانی پاسخ خودش را دارد."""

    def __init__(self, replies: dict):
        self.replies = replies
        self.seen = []

    def _next(self, url):
        self.seen.append(url)
        for fragment, reply in self.replies.items():
            if fragment in url:
                if isinstance(reply, Exception):
                    raise reply
                return reply
        raise AssertionError(f"نشانی پیش‌بینی‌نشده: {url}")

    def get(self, url, **kwargs):
        return self._next(url)

    def post(self, url, **kwargs):
        return self._next(url)


@pytest.mark.asyncio
async def test_trx_is_priced_from_the_world_when_iran_is_unreachable(
    tmp_path, monkeypatch
):
    """<b>مسئله‌ای که روی سرور واقعی دیدیم.</b>

    نامِ صرافی ایرانی از این سرور اصلاً به IP ترجمه نمی‌شود — نه با
    DNS سرور، نه از راه DoH. ولی قیمت جهانی ترون از هر جای دنیا
    خوانده می‌شود، و تتر عملاً همان دلار است:

        تومانِ ترون = (ترون به تتر) × (تومانِ تتر)
    """
    from telkap.services import crypto

    await _setup(tmp_path, monkeypatch, settings={})
    await crypto.set_rate(120_000, coin=coins.USDT, admin_id=1)

    session = _Session(
        {
            "nobitex": _Reply(status=503),
            "binance": _Reply(body={"symbol": "TRXUSDT", "price": "0.05"}),
        }
    )

    toman = await usdtrate.market_toman(coins.TRX, session=session)

    assert toman == 6_000                       # ۰٫۰۵ × ۱۲۰٬۰۰۰


@pytest.mark.asyncio
async def test_the_iranian_market_still_wins_when_it_answers(tmp_path, monkeypatch):
    """<b>مسیر مستقیم بهتر است و اول امتحان می‌شود.</b>

    قیمت ریالی مستقیم از بازار می‌آید، بدون ضرب و بدون تکیه بر عددی
    که ادمین گذاشته. مسیر جهانی فقط جایگزین است.
    """
    from telkap.services import crypto

    await _setup(tmp_path, monkeypatch, settings={})
    await crypto.set_rate(120_000, coin=coins.USDT, admin_id=1)

    session = _Session({"nobitex": _Reply(body={"lastTradePrice": "70000"})})

    assert await usdtrate.market_toman(coins.TRX, session=session) == 7_000
    assert not any("binance" in u for u in session.seen)


@pytest.mark.asyncio
async def test_without_a_tether_rate_the_derived_price_is_refused(
    tmp_path, monkeypatch
):
    """<b>ضرب در صفر یعنی ترون رایگان.</b>

    نرخ تتر لنگرِ این محاسبه است. اگر تنظیم نشده باشد، هیچ عددی
    نباید ساخته شود — نه صفر، نه حدس.
    """
    await _setup(tmp_path, monkeypatch, settings={})

    session = _Session(
        {
            "nobitex": _Reply(status=503),
            "binance": _Reply(body={"price": "0.05"}),
        }
    )

    with pytest.raises(usdtrate.RateError) as caught:
        await usdtrate.market_toman(coins.TRX, session=session)

    assert "تتر" in str(caught.value)


@pytest.mark.asyncio
async def test_tether_itself_is_never_derived(tmp_path, monkeypatch):
    """<b>تتر لنگر است و نمی‌تواند از خودش حساب شود.</b>

    اگر مسیر جهانی برای تتر هم باز بود، نرخِ ذخیره‌شده در خودش ضرب
    می‌شد و هر دور همان عدد را «تأیید» می‌کرد — یک حلقه که هیچ‌وقت
    خطا نمی‌داد و هیچ‌وقت هم درست نبود.
    """
    from telkap.services import crypto

    await _setup(tmp_path, monkeypatch, settings={})
    await crypto.set_rate(120_000, coin=coins.USDT, admin_id=1)

    session = _Session({"nobitex": _Reply(status=503)})

    with pytest.raises(usdtrate.RateError):
        await usdtrate.market_toman(coins.USDT, session=session)

    assert not any("binance" in u for u in session.seen)


@pytest.mark.asyncio
async def test_a_second_world_exchange_is_tried(tmp_path, monkeypatch):
    from telkap.services import crypto

    await _setup(tmp_path, monkeypatch, settings={})
    await crypto.set_rate(120_000, coin=coins.USDT, admin_id=1)

    session = _Session(
        {
            "nobitex": _Reply(status=503),
            "binance": _Reply(status=451),          # بایننس بعضی کشورها را رد می‌کند
            "kucoin": _Reply(body={"data": {"price": "0.05"}}),
        }
    )

    assert await usdtrate.market_toman(coins.TRX, session=session) == 6_000


@pytest.mark.asyncio
async def test_a_nonsense_world_price_is_refused(tmp_path, monkeypatch):
    """<b>قیمت دلاری تورم ندارد، پس بازه‌اش می‌تواند تنگ باشد.</b>

    ترون هیچ‌وقت یک دلار نبوده. چنین عددی یعنی پاسخ را اشتباه
    خوانده‌ایم — و بدون این بررسی، نرخ بیست برابر می‌شد.
    """
    from telkap.services import crypto

    await _setup(tmp_path, monkeypatch, settings={})
    await crypto.set_rate(120_000, coin=coins.USDT, admin_id=1)

    session = _Session(
        {
            "nobitex": _Reply(status=503),
            "binance": _Reply(body={"price": "1000"}),
            "kucoin": _Reply(body={"data": {"price": "1000"}}),
        }
    )

    with pytest.raises(usdtrate.RateError):
        await usdtrate.market_toman(coins.TRX, session=session)


@pytest.mark.asyncio
async def test_the_manual_rate_buttons_never_disappear(tmp_path, monkeypatch):
    """<b>لنگرِ محاسبه باید همیشه قابل تنظیم باشد.</b>

    وقتی بازار ایرانی در دسترس نیست، نرخ تتر دستی گذاشته می‌شود و
    ترون از رویش حساب می‌گردد. قبلاً این دکمه‌ها با روشن شدن «نرخ
    خودکار» پنهان می‌شدند — یعنی ادمینی که نرخ خودکار را روشن کرده
    بود راهی نداشت آن لنگر را بگذارد، و هر دو ارز خاموش می‌ماندند.
    """
    from telkap.handlers import admin_system

    await _setup(tmp_path, monkeypatch, settings={})
    await usdtrate.set_auto(True, admin_id=1)

    seen = {}

    class FakeMessage:
        async def answer(self, text, reply_markup=None):
            seen["text"] = text
            seen["kb"] = reply_markup

    await admin_system._usdt_screen(FakeMessage())

    buttons = {
        button.callback_data
        for row in seen["kb"].inline_keyboard
        for button in row
    }
    assert "sys:usdtrate:usdt" in buttons
    assert "sys:usdtrate:trx" in buttons
