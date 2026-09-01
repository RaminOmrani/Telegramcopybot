"""تأیید خودکار پرداخت‌های تتر.

<b>مسئله‌ای که حل می‌کند.</b> کاربر هش تراکنش را می‌فرستاد و اشتراکش
تا وقتی ادمین بیدار می‌شد و نگاه می‌کرد فعال نمی‌شد. برای محصولی که
شبانه‌روز فروخته می‌شود، این یعنی مشتریِ منتظر — و مشتریِ منتظر
گاهی می‌رود.

<b>چهار قاعده‌ای که هیچ‌کدام قابل حذف نیستند.</b> پول واقعی در میان
است، پس هر کدام از این‌ها که نباشد یک راه سوءاستفاده باز می‌شود:

۱. <b>تراکنش باید به ولتِ ما رسیده باشد.</b> تضمینش در خودِ روش
   پرسیدن است: فهرست واریزهای ولتِ خودمان خوانده می‌شود، پس
   تراکنشی که جای دیگری رفته اصلاً در فهرست نیست.

۲. <b>هر هش فقط یک بار.</b> بدون این، ده نفر می‌توانستند یک هشِ
   واقعی را کپی کنند و ده اشتراک بگیرند. مصرف‌شدن روی خودِ
   دیتابیس بررسی می‌شود، نه در حافظه.

۳. <b>مبلغ باید کافی باشد.</b> با یک رواداری کوچک، چون کارمزد و
   گرد کردنِ کیف پول‌ها چند سنت اختلاف می‌سازد و رد کردن یک
   پرداختِ درست بدتر از پذیرفتن چند سنت کمتر است.

۴. <b>فقط تتر واقعی.</b> ساختن توکنی به نام USDT روی ترون کار چند
   دقیقه است؛ بررسیِ نشانیِ قرارداد در <code>tron.py</code> جلویش
   را می‌گیرد.

<b>وقتی مطمئن نیستیم، رد نمی‌کنیم.</b> تراکنشی که پیدا نشود شاید
هنوز تأیید نشده باشد. درخواست دست‌نخورده در صف می‌ماند تا دور بعد،
و در نهایت ادمین همیشه می‌تواند دستی تأییدش کند. هیچ مسیری در این
ماژول پرداختی را رد نمی‌کند.
"""
from __future__ import annotations

import asyncio
import logging
from decimal import Decimal, InvalidOperation

from sqlalchemy import select

from telkap.db import get_session, log_activity
from telkap.models import PaymentRequest
from telkap.services import crypto, payments, tron

log = logging.getLogger(__name__)

INTERVAL_SECONDS = 120          # هر دو دقیقه یک دور
LOOKBACK_MS = 48 * 3600 * 1000  # تا دو روز عقب؛ قدیمی‌تر کار ادمین است

# چند سنت کمتر پذیرفته می‌شود. کیف پول‌ها مبلغ را گرد می‌کنند و
# کارمزد شبکه گاهی از خودِ مبلغ کم می‌شود؛ رد کردن پرداختی که واقعاً
# انجام شده، از پذیرفتن دو سنت کمتر بدتر است.
TOLERANCE = Decimal("0.02")

# ادمینِ ساختگی برای رویدادهایی که خودِ سیستم انجام می‌دهد. صفر
# نمی‌گذاریم چون در گزارش‌ها با «ناشناس» اشتباه می‌شود.
SYSTEM_ADMIN_ID = -1


async def pending_usdt() -> list[PaymentRequest]:
    """درخواست‌های تتری که هش دارند و هنوز بررسی نشده‌اند."""
    async with get_session() as db:
        rows = await db.execute(
            select(PaymentRequest).where(
                PaymentRequest.status == PaymentRequest.STATUS_PENDING,
                PaymentRequest.pay_method == payments.METHOD_USDT,
                PaymentRequest.tx_hash != "",
            )
        )
        return list(rows.scalars())


async def hash_already_used(tx_hash: str, *, except_id: int = 0) -> bool:
    """آیا این هش قبلاً برای درخواست دیگری تأیید شده است.

    <b>مهم‌ترین محافظ این ماژول.</b> بدون آن، یک هشِ واقعی را می‌شد
    بین چند نفر پخش کرد و هرکدام یک اشتراک بگیرند.
    """
    cleaned = crypto.normalize_tx(tx_hash)
    if not cleaned:
        return False
    async with get_session() as db:
        found = await db.scalar(
            select(PaymentRequest.id)
            .where(
                PaymentRequest.tx_hash == cleaned,
                PaymentRequest.id != except_id,
                PaymentRequest.status == PaymentRequest.STATUS_APPROVED,
            )
            .limit(1)
        )
    return found is not None


def _expected(request: PaymentRequest) -> Decimal:
    """مبلغ تتری‌ای که هنگام ساخت درخواست قفل شده بود."""
    try:
        return Decimal(str(request.usdt_amount or "0"))
    except (InvalidOperation, ValueError):
        return Decimal("0")


def enough(paid: Decimal, expected: Decimal) -> bool:
    """آیا مبلغ واریزی کافی است."""
    if expected <= 0:
        return False
    return paid >= expected - TOLERANCE


async def check_one(
    request: PaymentRequest, transfers: dict[str, tron.Transfer]
) -> tron.Transfer | None:
    """آیا این درخواست با یکی از واریزها می‌خواند.

    None یعنی «هنوز نه» — نه «رد شد». تراکنش ممکن است هنوز تأیید
    نشده باشد یا کاربر اشتباه فرستاده باشد؛ در هر دو حالت درخواست
    برای ادمین باقی می‌ماند.
    """
    cleaned = crypto.normalize_tx(request.tx_hash)
    if not cleaned:
        return None

    transfer = transfers.get(cleaned)
    if transfer is None:
        return None

    if not enough(transfer.amount, _expected(request)):
        log.info(
            "پرداخت #%s کمتر از مبلغ خواسته‌شده است: %s در برابر %s",
            request.id, transfer.amount, request.usdt_amount,
        )
        return None

    if await hash_already_used(cleaned, except_id=request.id):
        log.warning("هش تراکنش درخواست #%s قبلاً استفاده شده بود", request.id)
        return None

    return transfer


async def run_once(*, notifier=None) -> int:
    """یک دور کامل. تعداد پرداخت‌هایی که خودکار تأیید شدند."""
    requests = await pending_usdt()
    if not requests:
        return 0

    wallet = await crypto.address()
    if not wallet:
        return 0

    from telkap.config import get_settings

    # فقط تا زمانِ قدیمی‌ترین درخواستِ در انتظار خوانده می‌شود، نه کل
    # تاریخچه‌ی ولت
    oldest = min(
        (int(r.created_at.timestamp() * 1000) for r in requests if r.created_at),
        default=0,
    )
    since = max(oldest - 3600_000, 0) if oldest else 0

    try:
        transfers = await tron.incoming_usdt(
            wallet,
            since_ms=since,
            api_key=getattr(get_settings(), "tron_api_key", "") or "",
        )
    except tron.TronError as exc:
        # شبکه در دسترس نیست؛ هیچ پرداختی گم نمی‌شود، فقط این دور رد
        # می‌شود و مسیر دستیِ ادمین سر جایش است
        log.warning("خواندن تراکنش‌های ترون ناموفق بود: %s", exc)
        return 0

    approved = 0
    for request in requests:
        transfer = await check_one(request, transfers)
        if transfer is None:
            continue

        result, sub = await payments.approve(request.id, SYSTEM_ADMIN_ID)
        if result is None:
            continue

        approved += 1
        await log_activity(
            user_id=request.user_id,
            event="payment_auto",
            detail=(
                f"درخواست #{request.id} با تأیید خودکار بلاک‌چین فعال شد "
                f"({transfer.amount} USDT)"
            ),
        )
        if notifier is not None:
            await notifier(request.user_id, _message(result, sub, transfer))

    return approved


async def verify_now(request_id: int) -> str:
    """فقط همین یک درخواست را بررسی می‌کند؛ متن پیام یا رشته‌ی خالی.

    <b>چرا جدا از run_once.</b> صدا زدنِ run_once از داخل هندلر یعنی
    درخواست‌های <b>بقیه‌ی کاربران</b> هم همان‌جا تأیید می‌شوند، بی‌آنکه
    کسی به آن‌ها خبر بدهد. این نسخه فقط به یک درخواست دست می‌زند.

    کاربردش این است که تراکنش معمولاً چند ثانیه پیش از فرستادن هش
    تأیید شده، پس اغلب همین‌جا فعال می‌شود و کاربر منتظر چرخه‌ی
    دو دقیقه‌ای نمی‌ماند.
    """
    async with get_session() as db:
        request = await db.get(PaymentRequest, request_id)
        if (
            request is None
            or request.status != PaymentRequest.STATUS_PENDING
            or request.pay_method != payments.METHOD_USDT
            or not request.tx_hash
        ):
            return ""
        db.expunge(request)

    wallet = await crypto.address()
    if not wallet:
        return ""

    from telkap.config import get_settings

    try:
        transfers = await tron.incoming_usdt(
            wallet,
            since_ms=_since(request),
            api_key=getattr(get_settings(), "tron_api_key", "") or "",
        )
    except tron.TronError as exc:
        log.info("بررسی فوری تراکنش #%s ممکن نشد: %s", request_id, exc)
        return ""

    transfer = await check_one(request, transfers)
    if transfer is None:
        return ""

    result, sub = await payments.approve(request_id, SYSTEM_ADMIN_ID)
    if result is None:
        return ""

    await log_activity(
        user_id=result.user_id,
        event="payment_auto",
        detail=(
            f"درخواست #{result.id} با تأیید خودکار بلاک‌چین فعال شد "
            f"({transfer.amount} USDT)"
        ),
    )
    return _message(result, sub, transfer)


def _since(request: PaymentRequest) -> int:
    """از یک ساعت پیش از ساخت درخواست، تا کل تاریخچه خوانده نشود."""
    if not request.created_at:
        return 0
    return max(int(request.created_at.timestamp() * 1000) - 3600_000, 0)


def _message(request: PaymentRequest, sub, transfer: tron.Transfer) -> str:
    """پیام فعال‌سازی — با لینک تراکنش، تا کاربر خودش هم ببیند."""
    lines = [
        "✅ <b>پرداخت شما تأیید شد.</b>",
        "",
        f"مبلغ دریافتی: <b>{crypto.format_usdt(transfer.amount)} USDT</b>",
        f"کد پیگیری: <code>{request.id}</code>",
    ]
    if sub is not None:
        lines.append(f"اشتراک شما تا <b>{sub.expires_at:%Y/%m/%d}</b> فعال است.")
    lines += [
        "",
        f"<a href='https://tronscan.org/#/transaction/{transfer.tx_id}'>مشاهده‌ی تراکنش</a>",
        "",
        "<i>این تأیید خودکار و از روی خودِ بلاک‌چین انجام شد.</i>",
    ]
    return "\n".join(lines)


async def run_forever(notifier=None) -> None:
    while True:
        try:
            await asyncio.sleep(INTERVAL_SECONDS)
            await run_once(notifier=notifier)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("چرخه‌ی تأیید خودکار تتر با خطا مواجه شد")
