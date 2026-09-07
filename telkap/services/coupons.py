"""کد تخفیف و کمپین.

اعتبارسنجی و ثبت مصرف عمداً از هم جدا شده‌اند: کد هنگام نمایش قیمت
بررسی می‌شود ولی فقط وقتی «مصرف‌شده» ثبت می‌گردد که خرید واقعاً تأیید
شود. وگرنه کسی می‌توانست با باز و بسته کردن صفحه‌ی خرید، سقف استفاده‌ی
یک کد را تمام کند بی‌آنکه ریالی بدهد.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, timedelta

from sqlalchemy import func, select

from telkap.db import get_session, log_activity
from telkap.models import Coupon, CouponUse, utcnow
from telkap.plans import get_plan, toman

log = logging.getLogger(__name__)

MAX_PERCENT = 100


@dataclass(frozen=True, slots=True)
class Offer:
    """نتیجه‌ی اعمال یک کد روی یک خرید مشخص."""

    coupon: Coupon
    discount: int
    payable: int

    @property
    def label(self) -> str:
        if self.coupon.kind == Coupon.KIND_PERCENT:
            return f"{self.coupon.value}٪"
        return toman(self.coupon.value)


def normalize(code: str) -> str:
    """کد بدون فاصله و بزرگ، تا کاربر بابت حروف کوچک گیر نکند."""
    return (code or "").strip().upper().replace(" ", "")[:32]


def _aware(value):
    if value is None:
        return None
    return value if value.tzinfo else value.replace(tzinfo=UTC)


async def find(code: str) -> Coupon | None:
    cleaned = normalize(code)
    if not cleaned:
        return None
    async with get_session() as db:
        rows = await db.execute(select(Coupon).where(Coupon.code == cleaned))
        return rows.scalar_one_or_none()


async def validate(
    code: str, user_id: int, plan_code: str, amount: int
) -> Offer | str:
    """کد را می‌سنجد. خروجی Offer یا پیام خطای فارسی برای نمایش به کاربر."""
    coupon = await find(code)
    if coupon is None:
        return "این کد تخفیف وجود ندارد."
    if not coupon.enabled:
        return "این کد غیرفعال شده است."

    expires = _aware(coupon.expires_at)
    if expires is not None and expires <= utcnow():
        return "مهلت این کد تمام شده است."

    if coupon.max_uses and coupon.used_count >= coupon.max_uses:
        return "ظرفیت این کد تکمیل شده است."

    if coupon.plan_codes and plan_code not in coupon.plan_codes:
        titles = [
            (get_plan(c).title if get_plan(c) else c) for c in coupon.plan_codes
        ]
        return "این کد فقط برای این طرح‌هاست: " + "، ".join(titles)

    if amount < coupon.min_toman:
        return f"این کد از {toman(coupon.min_toman)} به بالا کار می‌کند."

    if coupon.per_user_limit:
        async with get_session() as db:
            mine = await db.scalar(
                select(func.count(CouponUse.id)).where(
                    CouponUse.coupon_id == coupon.id, CouponUse.user_id == user_id
                )
            )
        if int(mine or 0) >= coupon.per_user_limit:
            return "شما قبلاً از این کد استفاده کرده‌اید."

    if coupon.kind == Coupon.KIND_PERCENT:
        discount = amount * min(coupon.value, MAX_PERCENT) // 100
    else:
        discount = coupon.value
    discount = max(0, min(discount, amount))     # هرگز بیش از خود مبلغ
    return Offer(coupon=coupon, discount=discount, payable=amount - discount)


async def redeem(
    coupon_id: int, user_id: int, discount: int, *, payment_id: int | None = None
) -> None:
    """مصرف کد را ثبت می‌کند — فقط پس از تأیید نهایی خرید."""
    async with get_session() as db:
        coupon = await db.get(Coupon, coupon_id)
        if coupon is None:
            return
        coupon.used_count = int(coupon.used_count or 0) + 1
        db.add(
            CouponUse(
                coupon_id=coupon_id,
                user_id=user_id,
                payment_id=payment_id,
                discount_toman=discount,
            )
        )
        await db.commit()
        code = coupon.code
    await log_activity(
        user_id=user_id,
        event="coupon_used",
        detail=f"{code} — {toman(discount)} تخفیف",
    )


# --------------------------------------------------------- مدیریت ادمین
async def create(
    code: str,
    kind: str,
    value: int,
    *,
    max_uses: int = 0,
    per_user_limit: int = 1,
    plan_codes: list[str] | None = None,
    min_toman: int = 0,
    days_valid: int = 0,
    note: str = "",
    admin_id: int | None = None,
) -> Coupon | str:
    cleaned = normalize(code)
    if not cleaned or not cleaned.replace("-", "").replace("_", "").isalnum():
        return "کد فقط می‌تواند حرف، عدد، خط تیره و زیرخط داشته باشد."
    if kind not in {Coupon.KIND_PERCENT, Coupon.KIND_FIXED}:
        return "نوع تخفیف نامعتبر است."
    value = int(value)
    if value <= 0:
        return "مقدار تخفیف باید بزرگ‌تر از صفر باشد."
    if kind == Coupon.KIND_PERCENT and value > MAX_PERCENT:
        return "درصد تخفیف نمی‌تواند بیشتر از ۱۰۰ باشد."

    if await find(cleaned) is not None:
        return "کدی با همین نام از قبل هست."

    async with get_session() as db:
        coupon = Coupon(
            code=cleaned,
            kind=kind,
            value=value,
            max_uses=max(0, int(max_uses)),
            per_user_limit=max(0, int(per_user_limit)),
            plan_codes=list(plan_codes or []),
            min_toman=max(0, int(min_toman)),
            expires_at=utcnow() + timedelta(days=days_valid) if days_valid else None,
            note=note[:160],
            created_by=admin_id,
        )
        db.add(coupon)
        await db.commit()
        await db.refresh(coupon)

    await log_activity(
        user_id=admin_id, event="coupon_create", detail=f"{cleaned} ({kind} {value})"
    )
    return coupon


async def all_coupons(limit: int = 40) -> list[Coupon]:
    async with get_session() as db:
        rows = await db.execute(
            select(Coupon).order_by(Coupon.id.desc()).limit(limit)
        )
        return list(rows.scalars())


async def toggle(coupon_id: int) -> bool | None:
    async with get_session() as db:
        coupon = await db.get(Coupon, coupon_id)
        if coupon is None:
            return None
        coupon.enabled = not coupon.enabled
        await db.commit()
        return coupon.enabled


async def remove(coupon_id: int) -> bool:
    async with get_session() as db:
        coupon = await db.get(Coupon, coupon_id)
        if coupon is None:
            return False
        await db.delete(coupon)
        await db.commit()
    return True


async def usage(coupon_id: int) -> tuple[int, int]:
    """(تعداد استفاده، مجموع تخفیف داده‌شده)."""
    async with get_session() as db:
        count = await db.scalar(
            select(func.count(CouponUse.id)).where(CouponUse.coupon_id == coupon_id)
        )
        total = await db.scalar(
            select(func.coalesce(func.sum(CouponUse.discount_toman), 0)).where(
                CouponUse.coupon_id == coupon_id
            )
        )
    return int(count or 0), int(total or 0)


def describe(coupon: Coupon) -> str:
    """خلاصه‌ی یک خطی برای فهرست ادمین."""
    amount = (
        f"{coupon.value}٪" if coupon.kind == Coupon.KIND_PERCENT else toman(coupon.value)
    )
    parts = [amount]
    if coupon.max_uses:
        parts.append(f"{coupon.used_count}/{coupon.max_uses}")
    else:
        parts.append(f"{coupon.used_count} بار")
    expires = _aware(coupon.expires_at)
    if expires is not None:
        parts.append("منقضی" if expires <= utcnow() else f"تا {expires:%m/%d}")
    return " · ".join(parts)
