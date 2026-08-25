"""سهمیه‌ی طرح + اعتبار خریداری‌شده، در یک جا.

<b>سهمیه‌ها برای کل دوره‌ی اشتراک‌اند، نه روزانه.</b> «۲٬۰۰۰ پیام» یعنی
۲٬۰۰۰ پیام در کل ۳۰ روز. شمارنده به خود اشتراک گره خورده، پس با تمدید یا
خرید طرح تازه از صفر شروع می‌شود.

قابلیت‌های مصرفی دو منبع دارند:

  ۱. <b>سهمیه‌ی طرح</b> — رایگان، تا سقف دوره
  ۲. <b>اعتبار خریداری‌شده</b> — واحدی، بدون انقضا، وقتی سهمیه تمام شود

ترتیب مصرف همیشه «اول سهمیه، بعد اعتبار» است تا کاربر بی‌دلیل پول ندهد.
اگر هر دو با هم هم کافی نباشند، عملیات انجام نمی‌شود و چیزی کم نمی‌گردد.

پیام‌ها اعتبار خریدنی ندارند؛ فقط سهمیه‌ی طرح.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from telkap.db import get_session
from telkap.models import PeriodUsage
from telkap.plans import (
    CREDIT_HISTORY,
    CREDIT_WATERMARK,
    FEAT_HISTORY,
    FEAT_MESSAGES,
    FEAT_WATERMARK,
    UNLIMITED,
)
from telkap.services import credits

log = logging.getLogger(__name__)

# قابلیت → (کلید مصرف، نوع اعتبار یا None اگر خریدنی نیست)
_FEATURES: dict[str, tuple[str, str | None]] = {
    FEAT_WATERMARK: ("watermark", CREDIT_WATERMARK),
    FEAT_HISTORY: ("history", CREDIT_HISTORY),
    FEAT_MESSAGES: ("messages", None),
}

BIG = 1 << 30  # جای «نامحدود» در محاسبه‌ها


@dataclass(slots=True)
class Grant:
    """نتیجه‌ی یک درخواست: چقدر از سهمیه رفت و چقدر از اعتبار."""

    feature: str
    from_quota: int = 0
    from_credits: int = 0
    unlimited: bool = False

    @property
    def total(self) -> int:
        return self.from_quota + self.from_credits

    @property
    def note(self) -> str:
        """توضیح کوتاه برای نمایش به کاربر."""
        if self.unlimited:
            return "نامحدود"
        parts = []
        if self.from_quota:
            parts.append(f"{self.from_quota} از سهمیه‌ی طرح")
        if self.from_credits:
            parts.append(f"{self.from_credits} از اعتبار")
        return " + ".join(parts) or "۰"


async def used(subscription_id: int, feature: str) -> int:
    """چقدر از سهمیه‌ی این اشتراک تا حالا مصرف شده است."""
    kind = _FEATURES[feature][0]
    async with get_session() as db:
        rows = await db.execute(
            select(PeriodUsage.used).where(
                PeriodUsage.subscription_id == subscription_id,
                PeriodUsage.kind == kind,
            )
        )
        return int(rows.scalar_one_or_none() or 0)


async def quota_left(subscription_id: int | None, feature: str, plan) -> int:
    """سهمیه‌ی باقی‌مانده‌ی این دوره (برای نامحدود عدد بزرگ)."""
    limit = plan.quota(feature) if plan else 0
    if limit == UNLIMITED:
        return BIG
    if limit <= 0 or subscription_id is None:
        return 0
    return max(0, limit - await used(subscription_id, feature))


async def _take(subscription_id: int, feature: str, amount: int) -> None:
    """شمارنده‌ی مصرف را جابه‌جا می‌کند (عدد منفی یعنی برگرداندن)."""
    kind = _FEATURES[feature][0]
    async with get_session() as db:
        rows = await db.execute(
            select(PeriodUsage).where(
                PeriodUsage.subscription_id == subscription_id,
                PeriodUsage.kind == kind,
            )
        )
        row = rows.scalar_one_or_none()
        if row is None:
            db.add(
                PeriodUsage(subscription_id=subscription_id, kind=kind, used=max(0, amount))
            )
            try:
                await db.commit()
                return
            except IntegrityError:
                # دو ارسال همزمان؛ ردیف را کس دیگری ساخته است
                await db.rollback()
                rows = await db.execute(
                    select(PeriodUsage).where(
                        PeriodUsage.subscription_id == subscription_id,
                        PeriodUsage.kind == kind,
                    )
                )
                row = rows.scalar_one_or_none()
                if row is None:
                    return
        row.used = max(0, (row.used or 0) + amount)
        await db.commit()


async def reserve(
    user_id: int, feature: str, amount: int, plan, subscription_id: int | None
) -> Grant | None:
    """اول از سهمیه، بعد از اعتبار برمی‌دارد.

    اگر مجموع کافی نباشد None برمی‌گرداند و <b>هیچ‌چیز کم نمی‌شود</b>.
    """
    if feature not in _FEATURES or amount <= 0:
        return None

    limit = plan.quota(feature) if plan else 0
    if limit == UNLIMITED:
        return Grant(feature, unlimited=True)
    if subscription_id is None:
        return None

    available = await quota_left(subscription_id, feature, plan)
    from_quota = min(amount, available)
    from_credits = amount - from_quota

    credit_kind = _FEATURES[feature][1]
    if from_credits:
        if credit_kind is None:
            return None  # این قابلیت اعتبار خریدنی ندارد
        if not await credits.consume(user_id, credit_kind, from_credits):
            return None  # اعتبار کم است؛ سهمیه هم دست‌نخورده می‌ماند

    if from_quota:
        await _take(subscription_id, feature, from_quota)
    return Grant(feature, from_quota=from_quota, from_credits=from_credits)


async def release(user_id: int, grant: Grant | None, subscription_id: int | None) -> None:
    """اگر عملیات انجام نشد، سهمیه و اعتبار برمی‌گردند."""
    if grant is None or grant.unlimited:
        return
    if grant.from_quota and subscription_id is not None:
        await _take(subscription_id, grant.feature, -grant.from_quota)
    credit_kind = _FEATURES[grant.feature][1]
    if grant.from_credits and credit_kind:
        await credits.add(
            user_id, credit_kind, grant.from_credits, note="بازگشت؛ عملیات انجام نشد"
        )


async def affordable(
    user_id: int, feature: str, amount: int, plan, subscription_id: int | None
) -> bool:
    """آیا این تعداد از مجموع سهمیه و اعتبار قابل تأمین است؟ (بدون کم کردن)"""
    if feature not in _FEATURES or amount <= 0:
        return False
    if (plan.quota(feature) if plan else 0) == UNLIMITED:
        return True
    left = await quota_left(subscription_id, feature, plan)
    if left >= amount:
        return True
    credit_kind = _FEATURES[feature][1]
    if credit_kind is None:
        return False
    return left + await credits.balance(user_id, credit_kind) >= amount
