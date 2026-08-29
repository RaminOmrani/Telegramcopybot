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


async def quote(user_id: int, plan_code: str, coupon_code: str = "") -> dict | None:
    """قیمت نهایی یک خرید، با کسر ارتقا و کد تخفیف.

    خروجی شامل هر جزء جداگانه است تا صورتحساب بتواند دقیقاً بگوید هر
    کسری از کجا آمده — نه فقط یک عدد نهایی.
    """
    from telkap.services import coupons, subscription

    plan = get_plan(plan_code)
    if plan is None:
        return None

    credit, is_upgrade = await subscription.upgrade_quote(user_id, plan_code)
    after_credit = max(0, plan.price_toman - credit)

    discount, coupon_id, coupon_error = 0, None, ""
    cleaned = coupons.normalize(coupon_code)
    if cleaned:
        result = await coupons.validate(cleaned, user_id, plan_code, after_credit)
        if isinstance(result, str):
            coupon_error, cleaned = result, ""
        else:
            discount, coupon_id = result.discount, result.coupon.id

    return {
        "plan": plan,
        "list_toman": plan.price_toman,
        "credit_toman": credit,
        "is_upgrade": is_upgrade,
        "coupon_code": cleaned,
        "coupon_id": coupon_id,
        "coupon_error": coupon_error,
        "discount_toman": discount,
        "payable": max(0, after_credit - discount),
    }


async def create_request(
    user_id: int, plan_code: str, coupon_code: str = ""
) -> PaymentRequest | None:
    """یک درخواست خرید اشتراک در انتظار رسید می‌سازد."""
    priced = await quote(user_id, plan_code, coupon_code)
    if priced is None:
        return None
    async with get_session() as db:
        await _fresh_request(db, user_id)
        request = PaymentRequest(
            user_id=user_id,
            plan_code=plan_code,
            kind=PaymentRequest.KIND_PLAN,
            amount_toman=priced["payable"],
            list_toman=priced["list_toman"],
            credit_toman=priced["credit_toman"],
            discount_toman=priced["discount_toman"],
            coupon_code=priced["coupon_code"],
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


async def approval_notice(request: PaymentRequest, sub) -> str:
    """پیامی که پس از تأیید به کاربر می‌رسد.

    اینجاست و نه در هندلر، چون رسید را هم از داخل ربات می‌شود تأیید کرد و هم
    از پنل وب. دو نسخه از این متن یعنی دیر یا زود دو کاربر دو خبر متفاوت
    می‌گیرند برای یک اتفاق.
    """
    from telkap.texts import fa_num

    if sub is not None:
        plan = get_plan(request.plan_code)
        return (
            "🎉 پرداخت شما تأیید شد!\n\n"
            f"اشتراک <b>{plan.title if plan else request.plan_code}</b> فعال است "
            f"تا {sub.expires_at:%Y-%m-%d}.\n\n"
            "حالا می‌توانید کار کپی بسازید."
        )
    left = await credits.balance(request.user_id, request.plan_code)
    return (
        "🎉 پرداخت شما تأیید شد!\n\n"
        f"<b>{describe(request)}</b> به حساب شما اضافه شد.\n"
        f"مانده‌ی اعتبار: <b>{fa_num(left)}</b> واحد"
    )


def rejection_notice(request: PaymentRequest) -> str:
    """پیامی که پس از رد شدن رسید به کاربر می‌رسد."""
    from telkap.config import get_settings

    support = get_settings().support_username
    contact = f"\nدر صورت اشتباه با @{support} تماس بگیرید." if support else ""
    return f"❌ رسید شما (کد {request.id}) تأیید نشد.{contact}"


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
        coupon_code, discount = request.coupon_code, int(request.discount_toman or 0)
        used_credit = int(request.credit_toman or 0)
        await db.commit()
        await db.refresh(request)

    # مصرف کد فقط حالا ثبت می‌شود، نه هنگام نمایش قیمت — وگرنه می‌شد سقف
    # یک کد را با باز و بسته کردن صفحه‌ی خرید تمام کرد
    if coupon_code and discount > 0:
        from telkap.services import coupons

        found = await coupons.find(coupon_code)
        if found is not None:
            await coupons.redeem(found.id, user_id, discount, payment_id=request.id)

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

    # اگر ارزش اشتراک قبلی کسر شده، طرح تازه باید همین حالا شروع شود؛
    # وگرنه کاربر هم پول ارتقا داده و هم باید تا آخر طرح قبلی صبر کند
    sub = await grant(
        user_id,
        plan_code,
        granted_by=admin_id,
        note=f"رسید #{request_id}",
        replace=used_credit > 0,
    )
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
