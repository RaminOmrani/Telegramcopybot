"""تست تأیید خودکار پرداخت تتر.

<b>چرا این فایل سخت‌گیرترین تست‌های پروژه را دارد.</b> اینجا تنها
جایی است که یک اشتباه، مستقیم به «اشتراک رایگان» ترجمه می‌شود. هر
یک از قاعده‌هایی که پایین سنجیده می‌شوند، اگر روزی بی‌صدا حذف شود
یک راه سوءاستفاده باز می‌کند — و چون تأیید خودکار است، کسی هم
نمی‌بیند.
"""
from __future__ import annotations

from decimal import Decimal

import pytest

from telkap.models import PaymentRequest
from telkap.services import cryptocheck, payments, tron
from tests.test_copier import _setup


def transfer(
    *,
    tx_id="a" * 64,
    amount="12.5",
    contract=tron.USDT_CONTRACT,
    to="TWalletOfOurs",
) -> tron.Transfer:
    return tron.Transfer(
        tx_id=tx_id,
        sender="TSender",
        to=to,
        contract=contract,
        amount=Decimal(amount),
        symbol="USDT",
        timestamp_ms=1_700_000_000_000,
    )


def row(*, tx_id="a" * 64, value="12500000", decimals=6, symbol="USDT",
        contract=tron.USDT_CONTRACT) -> dict:
    """یک ردیف خام، همان شکلی که TronGrid می‌دهد."""
    return {
        "transaction_id": tx_id,
        "from": "TSender",
        "to": "TWalletOfOurs",
        "value": value,
        "block_timestamp": 1_700_000_000_000,
        "token_info": {"address": contract, "decimals": decimals, "symbol": symbol},
    }


# ── خواندن مقدار ─────────────────────────────────────────────────────


def test_raw_value_becomes_real_usdt():
    """تتر شش رقم اعشار دارد؛ خواندن عدد خام یعنی یک میلیون برابر دیدن."""
    parsed = tron._parse(row(value="12500000", decimals=6))

    assert parsed.amount == Decimal("12.5")


def test_a_token_with_different_decimals_is_read_correctly():
    parsed = tron._parse(row(value="125", decimals=1))

    assert parsed.amount == Decimal("12.5")


def test_a_row_we_do_not_understand_is_dropped():
    """حدس زدن روی داده‌ی پولی بدترین کار ممکن است."""
    assert tron._parse({}) is None
    assert tron._parse({"transaction_id": "کوتاه"}) is None
    assert tron._parse(row(value="0")) is None
    assert tron._parse(row(value="نه عدد")) is None
    assert tron._parse("رشته") is None


def test_a_fake_usdt_token_is_not_real_usdt():
    """<b>ساختن توکنی به نام USDT روی ترون کار چند دقیقه است.</b>

    اگر فقط به نام توکن نگاه می‌کردیم، یک توکن بی‌ارزش می‌توانست
    جای پول واقعی قبول شود. نشانی قرارداد تنها چیزی است که جعل
    نمی‌شود.
    """
    fake = tron._parse(row(contract="TFakeContract1111111111111111111", symbol="USDT"))

    assert fake.symbol == "USDT"        # نامش درست است
    assert fake.is_usdt is False        # ولی خودش نه


def test_the_real_contract_passes():
    assert tron._parse(row()).is_usdt is True


# ── کافی بودن مبلغ ───────────────────────────────────────────────────


def test_the_exact_amount_is_enough():
    assert cryptocheck.enough(Decimal("12.5"), Decimal("12.5")) is True


def test_more_than_asked_is_enough():
    assert cryptocheck.enough(Decimal("13"), Decimal("12.5")) is True


def test_a_couple_of_cents_short_is_still_accepted():
    """کیف پول‌ها گرد می‌کنند و کارمزد چند سنت می‌خورد.

    رد کردن پرداختی که واقعاً انجام شده، از پذیرفتن دو سنت کمتر
    بدتر است — چون یک رفت‌وبرگشت با پشتیبانی می‌سازد.
    """
    assert cryptocheck.enough(Decimal("12.49"), Decimal("12.5")) is True


def test_meaningfully_short_is_refused():
    assert cryptocheck.enough(Decimal("12"), Decimal("12.5")) is False
    assert cryptocheck.enough(Decimal("0.01"), Decimal("12.5")) is False


def test_a_request_without_an_expected_amount_never_passes():
    """مبلغِ نامشخص یعنی نمی‌دانیم چه چیزی را بسنجیم."""
    assert cryptocheck.enough(Decimal("999"), Decimal("0")) is False


# ── هر هش فقط یک بار ─────────────────────────────────────────────────


async def _request(db_module, **kwargs) -> PaymentRequest:
    fields = {
        "user_id": 7,
        "plan_code": "month",
        "pay_method": payments.METHOD_USDT,
        "amount_toman": 500_000,
        "usdt_amount": "12.5",
        "usdt_rate": 40_000,
    }
    fields.update(kwargs)
    async with db_module.get_session() as db:
        request = PaymentRequest(**fields)
        db.add(request)
        await db.commit()
        await db.refresh(request)
        return request


@pytest.mark.asyncio
async def test_a_fresh_hash_is_not_used(tmp_path, monkeypatch):
    db_module, _ = await _setup(tmp_path, monkeypatch, settings={})
    await _request(db_module, tx_hash="b" * 64)

    assert await cryptocheck.hash_already_used("c" * 64) is False


@pytest.mark.asyncio
async def test_an_approved_hash_cannot_be_reused(tmp_path, monkeypatch):
    """<b>مهم‌ترین تست این فایل.</b>

    بدون این قاعده، یک هشِ واقعی را می‌شد بین ده نفر پخش کرد و
    هرکدام یک اشتراک بگیرند.
    """
    db_module, _ = await _setup(tmp_path, monkeypatch, settings={})
    await _request(
        db_module, tx_hash="b" * 64, status=PaymentRequest.STATUS_APPROVED
    )

    assert await cryptocheck.hash_already_used("b" * 64) is True


@pytest.mark.asyncio
async def test_a_request_does_not_block_itself(tmp_path, monkeypatch):
    """درخواست خودش نباید مانع تأیید خودش شود."""
    db_module, _ = await _setup(tmp_path, monkeypatch, settings={})
    mine = await _request(
        db_module, tx_hash="b" * 64, status=PaymentRequest.STATUS_APPROVED
    )

    assert await cryptocheck.hash_already_used("b" * 64, except_id=mine.id) is False


@pytest.mark.asyncio
async def test_a_pending_or_rejected_hash_does_not_block(tmp_path, monkeypatch):
    """فقط پرداختِ <b>تأییدشده</b> هش را مصرف می‌کند.

    اگر درخواستِ در انتظار هم مسدود می‌کرد، کاربری که هش را اشتباه
    فرستاده و دوباره درست می‌فرستد گیر می‌افتاد.
    """
    db_module, _ = await _setup(tmp_path, monkeypatch, settings={})
    await _request(db_module, tx_hash="b" * 64)
    await _request(
        db_module, tx_hash="d" * 64, status=PaymentRequest.STATUS_REJECTED
    )

    assert await cryptocheck.hash_already_used("b" * 64) is False
    assert await cryptocheck.hash_already_used("d" * 64) is False


@pytest.mark.asyncio
async def test_a_malformed_hash_is_not_treated_as_used(tmp_path, monkeypatch):
    await _setup(tmp_path, monkeypatch, settings={})

    assert await cryptocheck.hash_already_used("نه هش") is False
    assert await cryptocheck.hash_already_used("") is False


# ── تطبیق یک درخواست ─────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_matching_transfer_is_accepted(tmp_path, monkeypatch):
    db_module, _ = await _setup(tmp_path, monkeypatch, settings={})
    request = await _request(db_module, tx_hash="a" * 64)

    found = await cryptocheck.check_one(request, {"a" * 64: transfer()})

    assert found is not None
    assert found.amount == Decimal("12.5")


@pytest.mark.asyncio
async def test_a_hash_that_never_reached_our_wallet_is_not_accepted(
    tmp_path, monkeypatch
):
    """<b>محافظ اصلی در برابر «هشِ کسِ دیگری».</b>

    فهرست فقط واریزهای ولت خودمان است. هشی که در آن نباشد یعنی به
    ما نرسیده — چه جعلی باشد چه تراکنش واقعیِ شخص دیگری.
    """
    db_module, _ = await _setup(tmp_path, monkeypatch, settings={})
    request = await _request(db_module, tx_hash="f" * 64)

    assert await cryptocheck.check_one(request, {"a" * 64: transfer()}) is None


@pytest.mark.asyncio
async def test_an_underpayment_is_not_accepted(tmp_path, monkeypatch):
    db_module, _ = await _setup(tmp_path, monkeypatch, settings={})
    request = await _request(db_module, tx_hash="a" * 64)

    found = await cryptocheck.check_one(
        request, {"a" * 64: transfer(amount="5")}
    )

    assert found is None


@pytest.mark.asyncio
async def test_a_hash_already_spent_elsewhere_is_not_accepted(tmp_path, monkeypatch):
    """دو درخواست، یک هش. دومی نباید بگیرد."""
    db_module, _ = await _setup(tmp_path, monkeypatch, settings={})
    await _request(
        db_module, tx_hash="a" * 64, status=PaymentRequest.STATUS_APPROVED
    )
    second = await _request(db_module, tx_hash="a" * 64)

    assert await cryptocheck.check_one(second, {"a" * 64: transfer()}) is None


@pytest.mark.asyncio
async def test_a_request_with_a_broken_hash_is_skipped(tmp_path, monkeypatch):
    db_module, _ = await _setup(tmp_path, monkeypatch, settings={})
    request = await _request(db_module, tx_hash="نامعتبر")

    assert await cryptocheck.check_one(request, {"a" * 64: transfer()}) is None


# ── انتخاب درخواست‌ها ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_only_pending_usdt_requests_with_a_hash_are_looked_at(
    tmp_path, monkeypatch
):
    db_module, _ = await _setup(tmp_path, monkeypatch, settings={})
    wanted = await _request(db_module, tx_hash="a" * 64)
    await _request(db_module, tx_hash="")                       # هنوز هش نفرستاده
    await _request(db_module, tx_hash="b" * 64,
                   status=PaymentRequest.STATUS_APPROVED)        # تمام‌شده
    await _request(db_module, tx_hash="c" * 64,
                   pay_method=payments.METHOD_CARD)              # کارت، نه تتر

    found = await cryptocheck.pending_usdt()

    assert [r.id for r in found] == [wanted.id]


# ── وقتی شبکه در دسترس نیست ──────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_network_failure_approves_nothing_and_raises_nothing(
    tmp_path, monkeypatch
):
    """<b>وقتی مطمئن نیستیم، رد نمی‌کنیم و خطا هم نمی‌دهیم.</b>

    درخواست دست‌نخورده در صف ادمین می‌ماند و دور بعد دوباره امتحان
    می‌شود.
    """
    db_module, _ = await _setup(tmp_path, monkeypatch, settings={})
    await _request(db_module, tx_hash="a" * 64)

    async def broken(*args, **kwargs):
        raise tron.TronError("شبکه در دسترس نیست")

    async def wallet():
        return "TWalletOfOurs"

    monkeypatch.setattr(cryptocheck.tron, "incoming_usdt", broken)
    monkeypatch.setattr(cryptocheck.crypto, "address", wallet)

    assert await cryptocheck.run_once() == 0

    async with db_module.get_session() as db:
        rows = await db.execute(
            __import__("sqlalchemy").select(PaymentRequest.status)
        )
        assert all(s == PaymentRequest.STATUS_PENDING for (s,) in rows.all())


@pytest.mark.asyncio
async def test_nothing_happens_without_a_wallet_address(tmp_path, monkeypatch):
    """ولتی تنظیم نشده یعنی اصلاً پرداخت تتری در کار نیست."""
    db_module, _ = await _setup(tmp_path, monkeypatch, settings={})
    await _request(db_module, tx_hash="a" * 64)

    async def no_wallet():
        return ""

    monkeypatch.setattr(cryptocheck.crypto, "address", no_wallet)

    assert await cryptocheck.run_once() == 0


# ── مسیر کامل ────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_valid_payment_activates_the_subscription(tmp_path, monkeypatch):
    """همان چیزی که کل این ماژول برایش نوشته شده."""
    db_module, _ = await _setup(tmp_path, monkeypatch, settings={})
    request = await _request(db_module, tx_hash="a" * 64)

    async def wallet():
        return "TWalletOfOurs"

    async def transfers(*args, **kwargs):
        return {"a" * 64: transfer()}

    monkeypatch.setattr(cryptocheck.crypto, "address", wallet)
    monkeypatch.setattr(cryptocheck.tron, "incoming_usdt", transfers)

    told = []

    async def notify(user_id, text):
        told.append((user_id, text))

    assert await cryptocheck.run_once(notifier=notify) == 1

    async with db_module.get_session() as db:
        after = await db.get(PaymentRequest, request.id)
        assert after.status == PaymentRequest.STATUS_APPROVED

    assert told and told[0][0] == 7
    assert "تأیید شد" in told[0][1]


@pytest.mark.asyncio
async def test_the_same_payment_is_not_approved_twice(tmp_path, monkeypatch):
    """دور دوم نباید دوباره اشتراک بدهد."""
    db_module, _ = await _setup(tmp_path, monkeypatch, settings={})
    await _request(db_module, tx_hash="a" * 64)

    async def wallet():
        return "TWalletOfOurs"

    async def transfers(*args, **kwargs):
        return {"a" * 64: transfer()}

    monkeypatch.setattr(cryptocheck.crypto, "address", wallet)
    monkeypatch.setattr(cryptocheck.tron, "incoming_usdt", transfers)

    assert await cryptocheck.run_once() == 1
    assert await cryptocheck.run_once() == 0


@pytest.mark.asyncio
async def test_verify_now_touches_only_the_named_request(tmp_path, monkeypatch):
    """<b>چرا verify_now جدا از run_once نوشته شد.</b>

    هندلر بعد از گرفتن هش یک بار بلاک‌چین را می‌خواند. اگر run_once
    را صدا می‌زد، درخواست‌های بقیه‌ی کاربران هم همان‌جا تأیید می‌شدند
    بی‌آنکه کسی به آن‌ها خبر بدهد.
    """
    db_module, _ = await _setup(tmp_path, monkeypatch, settings={})
    mine = await _request(db_module, tx_hash="a" * 64)
    other = await _request(db_module, tx_hash="e" * 64)

    async def wallet():
        return "TWalletOfOurs"

    async def transfers(*args, **kwargs):
        return {
            "a" * 64: transfer(),
            "e" * 64: transfer(tx_id="e" * 64),
        }

    monkeypatch.setattr(cryptocheck.crypto, "address", wallet)
    monkeypatch.setattr(cryptocheck.tron, "incoming_usdt", transfers)

    text = await cryptocheck.verify_now(mine.id)

    assert "تأیید شد" in text
    async with db_module.get_session() as db:
        assert (await db.get(PaymentRequest, mine.id)).status == (
            PaymentRequest.STATUS_APPROVED
        )
        assert (await db.get(PaymentRequest, other.id)).status == (
            PaymentRequest.STATUS_PENDING
        )


@pytest.mark.asyncio
async def test_verify_now_says_nothing_when_the_transfer_is_not_there_yet(
    tmp_path, monkeypatch
):
    db_module, _ = await _setup(tmp_path, monkeypatch, settings={})
    mine = await _request(db_module, tx_hash="a" * 64)

    async def wallet():
        return "TWalletOfOurs"

    async def transfers(*args, **kwargs):
        return {}

    monkeypatch.setattr(cryptocheck.crypto, "address", wallet)
    monkeypatch.setattr(cryptocheck.tron, "incoming_usdt", transfers)

    assert await cryptocheck.verify_now(mine.id) == ""


# ── هشدار تغییر ولت ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_changing_the_wallet_address_alerts_the_admins(tmp_path, monkeypatch):
    """<b>خطر واقعی، نه خطر خیالی.</b>

    کلید خصوصی ولت هیچ‌وقت روی سرور نیست، پس حتی هکِ کاملِ سرور هم
    پول موجود را نمی‌برد. ولی کسی که به یک حساب ادمین برسد می‌تواند
    نشانی را عوض کند و درآمد آینده را به ولت خودش ببرد — و آن تغییر
    تا وقتی کسی لاگ را نخواند بی‌صدا می‌ماند.
    """
    from telkap.services import alerts, crypto

    await _setup(tmp_path, monkeypatch, settings={})
    good = "T" + "9" * 33
    evil = "T" + "8" * 33

    sent = []

    async def fake_send(text, **kwargs):
        sent.append((text, kwargs))
        return 1

    monkeypatch.setattr(alerts, "send", fake_send)

    await crypto.set_address(good, admin_id=1)
    sent.clear()                      # اولین ثبت، تغییر حساب نمی‌شود
    await crypto.set_address(evil, admin_id=99)

    assert len(sent) == 1
    text, kwargs = sent[0]
    assert good in text and evil in text     # هم قبلی هم جدید
    assert "99" in text                      # چه کسی عوضش کرد
    assert kwargs["cooldown"] == 0           # هشدار امنیتی خفه نمی‌شود


@pytest.mark.asyncio
async def test_setting_the_same_address_again_is_not_an_alert(tmp_path, monkeypatch):
    """هشدارِ بی‌مورد باعث می‌شود هشدار واقعی هم جدی گرفته نشود."""
    from telkap.services import alerts, crypto

    await _setup(tmp_path, monkeypatch, settings={})
    same = "T" + "7" * 33

    sent = []

    async def fake_send(text, **kwargs):
        sent.append(text)
        return 1

    monkeypatch.setattr(alerts, "send", fake_send)

    await crypto.set_address(same, admin_id=1)
    sent.clear()
    await crypto.set_address(same, admin_id=1)

    assert sent == []


@pytest.mark.asyncio
async def test_changing_the_rate_alerts_too(tmp_path, monkeypatch):
    """نرخ هم پول است: نرخِ دستکاری‌شده یعنی اشتراکِ تقریباً رایگان."""
    from telkap.services import alerts, crypto

    await _setup(tmp_path, monkeypatch, settings={})

    sent = []

    async def fake_send(text, **kwargs):
        sent.append(text)
        return 1

    monkeypatch.setattr(alerts, "send", fake_send)

    await crypto.set_rate(60_000, admin_id=1)
    sent.clear()
    await crypto.set_rate(90_000, admin_id=1)

    assert len(sent) == 1
    assert "نرخ" in sent[0]
