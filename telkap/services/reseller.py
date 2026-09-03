"""نمایندگی فروش: اشتراک با تخفیف، فعال‌سازی فوری برای مشتری نماینده.

نماینده کیف پولش را شارژ می‌کند و بعد هر وقت خواست، اشتراک را با درصد
تخفیف خودش می‌خرد و مستقیم برای مشتری‌اش فعال می‌کند — بدون رسید، بدون
انتظار تأیید. همین «فوری بودن» تمام ارزش نمایندگی است.

پول اول از کیف پول کم و بعد اشتراک داده می‌شود؛ اگر دادن اشتراک به هر
دلیلی شکست بخورد، مبلغ همان لحظه برمی‌گردد.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC

from sqlalchemy import func, select

from telkap.db import get_session, log_activity
from telkap.models import (
    AppSetting,
    ResellerSale,
    Subscription,
    User,
    WalletEntry,
    utcnow,
)
from telkap.plans import get_plan, toman
from telkap.services import subscription, wallet

log = logging.getLogger(__name__)

SETTING_KEY = "reseller"
DEFAULT_DISCOUNT = 20        # درصد تخفیف پیش‌فرض نماینده‌ی تازه
MAX_DISCOUNT = 90

# از چند روز مانده، مشتری «در حال تمام شدن» شمرده می‌شود. نماینده باید
# پیش از ما سراغش برود، نه بعدِ ما.
EXPIRY_WARNING_DAYS = 7


@dataclass(slots=True)
class Stats:
    sales: int = 0
    customers: int = 0
    spent: int = 0
    saved: int = 0
    commission: int = 0      # از خریدهای مستقیمِ مشتری‌هایش


@dataclass(slots=True)
class Customer:
    """یک مشتریِ نماینده، با آنچه برای پیگیری لازم است."""

    user_id: int
    name: str
    expires_at: object | None = None
    days_left: int = 0
    plan_code: str = ""

    @property
    def expiring(self) -> bool:
        """نزدیک به تمام شدن — یعنی همین حالا باید سراغش رفت."""
        return 0 < self.days_left <= EXPIRY_WARNING_DAYS

    @property
    def expired(self) -> bool:
        return self.days_left <= 0


class ResellerError(Exception):
    """خطایی که مستقیم به نماینده نشان داده می‌شود."""


# ------------------------------------------------------------ وضعیت
async def profile(user_id: int) -> tuple[bool, int]:
    """(آیا نماینده است، درصد تخفیفش)."""
    async with get_session() as db:
        user = await db.get(User, user_id)
        if user is None or not user.is_reseller:
            return False, 0
        return True, max(0, min(int(user.reseller_discount or 0), MAX_DISCOUNT))


async def set_reseller(
    user_id: int, enabled: bool, discount: int | None = None, *, admin_id: int | None = None
) -> tuple[bool, int] | None:
    """کاربر را نماینده می‌کند یا نمایندگی‌اش را برمی‌دارد."""
    async with get_session() as db:
        user = await db.get(User, user_id)
        if user is None:
            return None
        user.is_reseller = bool(enabled)
        if discount is not None:
            user.reseller_discount = max(0, min(int(discount), MAX_DISCOUNT))
        elif enabled and not user.reseller_discount:
            user.reseller_discount = await default_discount()
        await db.commit()
        result = (user.is_reseller, int(user.reseller_discount or 0))

    await log_activity(
        user_id=user_id,
        event="reseller",
        detail=(
            f"نمایندگی فعال شد با {result[1]}٪ تخفیف"
            if enabled
            else "نمایندگی برداشته شد"
        )
        + f" (ادمین {admin_id or '—'})",
    )
    return result


async def default_discount() -> int:
    async with get_session() as db:
        row = await db.get(AppSetting, SETTING_KEY)
    if row is not None and isinstance(row.value, dict):
        try:
            return max(0, min(int(row.value.get("discount", DEFAULT_DISCOUNT)), MAX_DISCOUNT))
        except (TypeError, ValueError):
            pass
    return DEFAULT_DISCOUNT


async def set_default_discount(value: int, *, admin_id: int | None = None) -> int:
    clean = max(0, min(int(value), MAX_DISCOUNT))
    async with get_session() as db:
        row = await db.get(AppSetting, SETTING_KEY)
        if row is None:
            row = AppSetting(key=SETTING_KEY)
            db.add(row)
        row.value = {"discount": clean}
        row.updated_by = admin_id
        row.updated_at = utcnow()
        await db.commit()
    return clean


# ---------------------------------------------------------- قیمت‌گذاری
def discounted(list_price: int, discount: int) -> int:
    """قیمت نماینده، رند شده به نزدیک‌ترین هزار تومان."""
    price = list_price * (100 - max(0, min(discount, MAX_DISCOUNT))) // 100
    return max(0, (price // 1_000) * 1_000)


async def price_for(user_id: int, plan_code: str) -> int | None:
    plan = get_plan(plan_code)
    if plan is None:
        return None
    _is_reseller, discount = await profile(user_id)
    return discounted(plan.price_toman, discount)


# ------------------------------------------------------------ فروش
async def activate(reseller_id: int, customer_id: int, plan_code: str) -> ResellerSale:
    """اشتراک را از کیف پول نماینده می‌خرد و برای مشتری فعال می‌کند."""
    is_reseller, discount = await profile(reseller_id)
    if not is_reseller:
        raise ResellerError("شما نماینده نیستید.")

    plan = get_plan(plan_code)
    if plan is None or plan.price_toman <= 0:
        raise ResellerError("این طرح قابل فروش نیست.")

    if customer_id == reseller_id:
        raise ResellerError(
            "برای خودتان نمی‌توانید از پنل نمایندگی فعال کنید؛ از «💳 خرید اشتراک» بروید."
        )

    async with get_session() as db:
        customer = await db.get(User, customer_id)
    if customer is None:
        raise ResellerError(
            "این کاربر هنوز ربات را استارت نکرده است.\n\n"
            "از او بخواهید اول ربات را باز کند و <code>/start</code> بزند، "
            "بعد دوباره امتحان کنید."
        )
    if customer.is_banned:
        raise ResellerError("این کاربر مسدود است و نمی‌توان برایش اشتراک فعال کرد.")

    price = discounted(plan.price_toman, discount)
    balance = await wallet.balance(reseller_id)
    if balance < price:
        raise ResellerError(
            f"موجودی کیف پولتان کافی نیست.\n\n"
            f"قیمت این طرح برای شما: <b>{toman(price)}</b>\n"
            f"موجودی شما: <b>{toman(balance)}</b>\n\n"
            f"کسری: <b>{toman(price - balance)}</b>"
        )

    # اول پول، بعد اشتراک — و اگر اشتراک نگرفت، پول همان لحظه برمی‌گردد
    if await wallet.debit(
        reseller_id,
        price,
        reason=WalletEntry.REASON_PURCHASE,
        note=f"فروش نمایندگی: {plan.title} برای {customer_id}",
    ) is None:
        raise ResellerError("برداشت از کیف پول ناموفق بود. دوباره تلاش کنید.")

    sub = await subscription.grant(
        customer_id, plan_code, granted_by=reseller_id, note=f"نمایندگی {reseller_id}"
    )
    if sub is None:
        await wallet.credit(
            reseller_id,
            price,
            reason=WalletEntry.REASON_REFUND,
            note="فعال‌سازی ناموفق بود",
        )
        raise ResellerError(
            "فعال‌سازی اشتراک ناموفق بود؛ مبلغ به موجودی کیف پولتان برگشت."
        )

    async with get_session() as db:
        sale = ResellerSale(
            reseller_id=reseller_id,
            customer_id=customer_id,
            plan_code=plan_code,
            paid_toman=price,
            list_toman=plan.price_toman,
            discount_percent=discount,
        )
        db.add(sale)
        await db.commit()
        await db.refresh(sale)

    await claim(reseller_id, customer_id)

    await log_activity(
        user_id=reseller_id,
        event="reseller_sale",
        detail=f"{plan.title} برای {customer_id} — {toman(price)}",
    )
    return sale


# ------------------------------------------------- مشتریِ نماینده
async def claim(reseller_id: int, customer_id: int) -> bool:
    """مشتری را به نام این نماینده می‌بندد. فقط بار اول.

    <b>چرا فقط بار اول.</b> اگر بازنویسی می‌شد، هر نماینده‌ای می‌توانست
    با یک فعال‌سازیِ ارزان، مشتریِ نماینده‌ی دیگری را به نام خودش کند.
    مثل <code>referred_by</code>، این هم یک‌بار بسته می‌شود.
    """
    if reseller_id == customer_id:
        return False
    async with get_session() as db:
        customer = await db.get(User, customer_id)
        if customer is None or customer.owned_by:
            return False
        customer.owned_by = reseller_id
        await db.commit()
    await log_activity(
        user_id=customer_id,
        event="reseller_claim",
        detail=f"مشتریِ نماینده {reseller_id} شد",
    )
    return True


async def owner_of(customer_id: int) -> int | None:
    async with get_session() as db:
        customer = await db.get(User, customer_id)
    return customer.owned_by if customer is not None else None


async def pay_commission(customer_id: int, plan_code: str, *, ref_id: int = 0) -> int:
    """سهم نماینده از خریدِ مستقیمِ مشتریِ خودش.

    <b>این پاسخِ بزرگ‌ترین نگرانی نماینده است.</b> نماینده می‌ترسد
    مشتری‌اش مستقیم بیاید و دفعه‌ی بعد از او نخرد. با این، خریدِ مستقیم
    هم همان سهم همیشگی‌اش را به کیف پولش می‌ریزد — پس رفتنِ مشتری به
    سراغ ما چیزی از او کم نمی‌کند و دلیلی برای پنهان کردن مشتری ندارد.

    سهم به <b>کیف پول</b> می‌رود، نه به حساب بانکی. اینجا پولی به کسی
    واریز نمی‌شود؛ اعتباری است که با آن اشتراک‌های بعدی را می‌خرد.

    مبلغ سهم را برمی‌گرداند (۰ یعنی چیزی پرداخت نشد).
    """
    owner = await owner_of(customer_id)
    if not owner or owner == customer_id:
        return 0

    is_reseller, discount = await profile(owner)
    if not is_reseller or discount <= 0:
        # نمایندگی‌اش برداشته شده — سهمی هم در کار نیست
        return 0

    plan = get_plan(plan_code)
    if plan is None or plan.price_toman <= 0:
        return 0

    share = plan.price_toman - discounted(plan.price_toman, discount)
    if share <= 0:
        return 0

    await wallet.credit(
        owner,
        share,
        reason=WalletEntry.REASON_COMMISSION,
        note=f"سهم نمایندگی از خرید مستقیم {customer_id} ({plan.title})",
        ref_id=ref_id or None,
    )
    await log_activity(
        user_id=owner,
        event="reseller_commission",
        detail=f"{toman(share)} از خرید مستقیم {customer_id}",
    )
    return share


async def customers(reseller_id: int) -> list[Customer]:
    """مشتری‌های این نماینده، آنکه زودتر تمام می‌شود اول.

    ترتیب عمدی است: کاری که باید انجام شود بالاست، نه تازه‌ترین اسم.
    """
    async with get_session() as db:
        people = list(
            (
                await db.execute(
                    select(User).where(User.owned_by == reseller_id)
                )
            ).scalars()
        )
        if not people:
            return []

        ids = [person.id for person in people]
        rows = await db.execute(
            select(Subscription)
            .where(Subscription.user_id.in_(ids), Subscription.expires_at > utcnow())
            .order_by(Subscription.expires_at.desc())
        )
        active: dict[int, Subscription] = {}
        for sub in rows.scalars():
            active.setdefault(sub.user_id, sub)

    now = utcnow()
    out = []
    for person in people:
        sub = active.get(person.id)
        left = 0
        expires = None
        if sub is not None:
            expires = sub.expires_at
            # SQLite تاریخ را بدون منطقه‌ی زمانی برمی‌گرداند و تفریقش از
            # یک تاریخِ آگاه خطا می‌دهد. همان کاری که remaining_days
            # می‌کند.
            if expires.tzinfo is None:
                expires = expires.replace(tzinfo=UTC)
            delta = expires - now
            left = max(0, delta.days + (1 if delta.seconds else 0))
        out.append(
            Customer(
                user_id=person.id,
                name=(person.first_name or "").strip() or str(person.id),
                expires_at=expires,
                days_left=left,
                plan_code=sub.plan_code if sub else "",
            )
        )
    out.sort(key=lambda c: (c.days_left, c.user_id))
    return out


# ------------------------------------------------------------- آمار
async def sales(reseller_id: int, limit: int = 15) -> list[ResellerSale]:
    async with get_session() as db:
        rows = await db.execute(
            select(ResellerSale)
            .where(ResellerSale.reseller_id == reseller_id)
            .order_by(ResellerSale.id.desc())
            .limit(limit)
        )
        return list(rows.scalars())


async def everyone() -> list[tuple[User, Stats]]:
    """همه‌ی نماینده‌ها همراه آمارشان، پرفروش‌ترین اول.

    آمار همه با سه پرس‌وجوی گروهی گرفته می‌شود، نه یکی به‌ازای هر
    نماینده؛ وگرنه صفحه با ده نماینده سی‌ رفت‌وبرگشت به دیتابیس دارد.
    """
    async with get_session() as db:
        people = list(
            (
                await db.execute(
                    select(User).where(User.is_reseller.is_(True)).order_by(User.id)
                )
            ).scalars()
        )
        if not people:
            return []

        ids = [user.id for user in people]
        rows = await db.execute(
            select(
                ResellerSale.reseller_id,
                func.count(ResellerSale.id),
                func.count(func.distinct(ResellerSale.customer_id)),
                func.coalesce(func.sum(ResellerSale.paid_toman), 0),
                func.coalesce(func.sum(ResellerSale.list_toman), 0),
            )
            .where(ResellerSale.reseller_id.in_(ids))
            .group_by(ResellerSale.reseller_id)
        )
        by_id = {
            seller: Stats(
                sales=int(count or 0),
                customers=int(buyers or 0),
                spent=int(paid or 0),
                saved=int((listed or 0) - (paid or 0)),
            )
            for seller, count, buyers, paid, listed in rows.all()
        }

    pairs = [(user, by_id.get(user.id, Stats())) for user in people]
    pairs.sort(key=lambda pair: (-pair[1].spent, -pair[1].sales, pair[0].id))
    return pairs


async def recent_sales(limit: int = 20) -> list[ResellerSale]:
    """آخرین فروش‌های همه‌ی نماینده‌ها — برای صفحه‌ی مدیریت."""
    async with get_session() as db:
        rows = await db.execute(
            select(ResellerSale).order_by(ResellerSale.id.desc()).limit(limit)
        )
        return list(rows.scalars())


async def stats(reseller_id: int) -> Stats:
    async with get_session() as db:
        total = await db.scalar(
            select(func.count(ResellerSale.id)).where(
                ResellerSale.reseller_id == reseller_id
            )
        )
        customers = await db.scalar(
            select(func.count(func.distinct(ResellerSale.customer_id))).where(
                ResellerSale.reseller_id == reseller_id
            )
        )
        spent = await db.scalar(
            select(func.coalesce(func.sum(ResellerSale.paid_toman), 0)).where(
                ResellerSale.reseller_id == reseller_id
            )
        )
        listed = await db.scalar(
            select(func.coalesce(func.sum(ResellerSale.list_toman), 0)).where(
                ResellerSale.reseller_id == reseller_id
            )
        )
        earned = await db.scalar(
            select(func.coalesce(func.sum(WalletEntry.amount_toman), 0)).where(
                WalletEntry.user_id == reseller_id,
                WalletEntry.reason == WalletEntry.REASON_COMMISSION,
            )
        )
    return Stats(
        sales=int(total or 0),
        customers=int(customers or 0),
        spent=int(spent or 0),
        saved=int((listed or 0) - (spent or 0)),
        commission=int(earned or 0),
    )
