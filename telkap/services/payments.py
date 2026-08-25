"""خرید اشتراک با رسید کارت‌به‌کارت.

جریان کار کاملاً داخل تلگرام است:
  کاربر پلن را می‌زند → شماره کارت را می‌بیند → رسید را می‌فرستد
  → ادمین با یک دکمه تأیید یا رد می‌کند → اشتراک خودکار فعال می‌شود.
"""
from __future__ import annotations

import logging

from sqlalchemy import select

from telkap.db import get_session, log_activity
from telkap.models import PaymentRequest, utcnow
from telkap.plans import CREDIT_KINDS, get_plan
from telkap.services import credits
from telkap.services.subscription import grant

log = logging.getLogger(__name__)


async def _fresh_request(db, user_id: int) -> None:
    """درخواست‌های نیمه‌کاره‌ی قبلی همین کاربر کنار گذاشته می‌شوند."""
    rows = await db.execute(
        select(PaymentRequest).where(
            PaymentRequest.user_id == user_id,
            PaymentRequest.status == PaymentRequest.STATUS_PENDING,
            PaymentRequest.receipt_file_id.is_(None),
        )
    )
    for stale in rows.scalars():
        await db.delete(stale)


async def create_request(user_id: int, plan_code: str) -> PaymentRequest | None:
    """یک درخواست خرید اشتراک در انتظار رسید می‌سازد."""
    plan = get_plan(plan_code)
    if plan is None:
        return None
    async with get_session() as db:
        await _fresh_request(db, user_id)
        request = PaymentRequest(
            user_id=user_id,
            plan_code=plan_code,
            kind=PaymentRequest.KIND_PLAN,
            amount_toman=plan.price_toman,
        )
        db.add(request)
        await db.commit()
        await db.refresh(request)
        return request


async def create_credit_request(
    user_id: int, kind: str, quantity: int, amount_toman: int
) -> PaymentRequest | None:
    """درخواست خرید بسته‌ی اعتبار در انتظار رسید."""
    if kind not in CREDIT_KINDS or quantity <= 0:
        return None
    async with get_session() as db:
        await _fresh_request(db, user_id)
        request = PaymentRequest(
            user_id=user_id,
            plan_code=kind,
            kind=PaymentRequest.KIND_CREDIT,
            quantity=quantity,
            amount_toman=amount_toman,
        )
        db.add(request)
        await db.commit()
        await db.refresh(request)
        return request


def describe(request: PaymentRequest) -> str:
    """عنوان خوانا از آنچه خریداری می‌شود، برای پیام‌های ادمین و کاربر."""
    if request.kind == PaymentRequest.KIND_CREDIT:
        info = CREDIT_KINDS.get(request.plan_code)
        title = info[0] if info else request.plan_code
        return f"{title} × {request.quantity}"
    plan = get_plan(request.plan_code)
    return plan.title if plan else request.plan_code


async def awaiting_receipt(user_id: int) -> PaymentRequest | None:
    """درخواستی که منتظر رسید کاربر است."""
    async with get_session() as db:
        rows = await db.execute(
            select(PaymentRequest)
            .where(
                PaymentRequest.user_id == user_id,
                PaymentRequest.status == PaymentRequest.STATUS_PENDING,
                PaymentRequest.receipt_file_id.is_(None),
            )
            .order_by(PaymentRequest.id.desc())
            .limit(1)
        )
        return rows.scalar_one_or_none()


async def attach_receipt(
    request_id: int, file_id: str | None, kind: str, note: str = ""
) -> PaymentRequest | None:
    async with get_session() as db:
        request = await db.get(PaymentRequest, request_id)
        if request is None:
            return None
        request.receipt_file_id = file_id
        request.receipt_kind = kind
        request.note = note[:400]
        await db.commit()
        await db.refresh(request)
        return request


async def pending_requests(limit: int = 20) -> list[PaymentRequest]:
    """درخواست‌هایی که رسید دارند و منتظر بررسی ادمین‌اند."""
    async with get_session() as db:
        rows = await db.execute(
            select(PaymentRequest)
            .where(
                PaymentRequest.status == PaymentRequest.STATUS_PENDING,
                PaymentRequest.receipt_file_id.is_not(None),
            )
            .order_by(PaymentRequest.id)
            .limit(limit)
        )
        return list(rows.scalars())


async def pending_count() -> int:
    return len(await pending_requests(limit=100))


async def approve(request_id: int, admin_id: int):
    """درخواست را تأیید و اشتراک یا اعتبار را فعال می‌کند.

    خروجی: (درخواست، اشتراک) — برای خرید اعتبار، اشتراک None است.
    """
    async with get_session() as db:
        request = await db.get(PaymentRequest, request_id)
        if request is None or request.status != PaymentRequest.STATUS_PENDING:
            return None, None
        request.status = PaymentRequest.STATUS_APPROVED
        request.reviewed_by = admin_id
        request.reviewed_at = utcnow()
        user_id, plan_code = request.user_id, request.plan_code
        kind, quantity = request.kind, request.quantity
        await db.commit()
        await db.refresh(request)

    # پاداش دعوت فقط پس از تأیید خرید تعلق می‌گیرد — همین‌جا، نه هنگام ثبت‌نام
    from telkap.services import referral

    if kind == PaymentRequest.KIND_CREDIT:
        await credits.add(user_id, plan_code, quantity, note=f"رسید #{request_id}")
        await referral.on_payment_approved(request)
        await log_activity(
            user_id=user_id,
            event="payment_approved",
            detail=f"درخواست #{request_id}: {quantity} واحد اعتبار {plan_code}",
        )
        return request, None

    sub = await grant(user_id, plan_code, granted_by=admin_id, note=f"رسید #{request_id}")
    await referral.on_payment_approved(request)
    await log_activity(
        user_id=user_id,
        event="payment_approved",
        detail=f"درخواست #{request_id} پلن {plan_code} تأیید شد",
    )
    return request, sub


async def reject(request_id: int, admin_id: int, reason: str = "") -> PaymentRequest | None:
    async with get_session() as db:
        request = await db.get(PaymentRequest, request_id)
        if request is None or request.status != PaymentRequest.STATUS_PENDING:
            return None
        request.status = PaymentRequest.STATUS_REJECTED
        request.reviewed_by = admin_id
        request.reviewed_at = utcnow()
        if reason:
            request.note = f"{request.note} | رد: {reason}"[:400]
        await db.commit()
        await db.refresh(request)
    await log_activity(
        user_id=request.user_id,
        event="payment_rejected",
        detail=f"درخواست #{request_id} رد شد",
        level="warning",
    )
    return request
