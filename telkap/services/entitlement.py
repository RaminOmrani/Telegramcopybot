"""سهمیه‌ی طرح + اعتبار خریداری‌شده، در یک جا.

قابلیت‌های مصرفی (واترمارک و کپی پیام‌های گذشته) دو منبع دارند:

  ۱. <b>سهمیه‌ی روزانه‌ی طرح</b> — مثلاً ۲۰ واترمارک در روز. رایگان است و
     هر شبانه‌روز از نو پر می‌شود.
  ۲. <b>اعتبار خریداری‌شده</b> — واحدی، بدون انقضا، وقتی سهمیه تمام شود.

ترتیب مصرف همیشه «اول سهمیه، بعد اعتبار» است تا کاربر بی‌دلیل پول ندهد.
اگر هر دو با هم هم کافی نباشند، عملیات انجام نمی‌شود و چیزی کم نمی‌گردد.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from telkap.db import get_session
from telkap.models import DailyUsage
from telkap.plans import (
    CREDIT_HISTORY,
    CREDIT_WATERMARK,
    FEAT_HISTORY,
    FEAT_WATERMARK,
    UNLIMITED,
)
from telkap.services import credits

log = logging.getLogger(__name__)

# قابلیت → (کلید مصرف روزانه، نوع اعتبار)
_FEATURES = {
    FEAT_WATERMARK: ("watermark", CREDIT_WATERMARK),
    FEAT_HISTORY: ("history", CREDIT_HISTORY),
}


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
        return " + ".join(parts)


async def used_today(user_id: int, feature: str, day: str) -> int:
    kind = _FEATURES[feature][0]
    async with get_session() as db:
        rows = await db.execute(
            select(DailyUsage.used).where(
                DailyUsage.user_id == user_id,
                DailyUsage.day == day,
                DailyUsage.kind == kind,
            )
        )
        return int(rows.scalar_one_or_none() or 0)


async def quota_left(user_id: int, feature: str, plan, day: str) -> int:
    """چقدر از سهمیه‌ی رایگان امروز باقی مانده (برای نامحدود عدد بزرگ)."""
    limit = plan.quota(feature) if plan else 0
    if limit == UNLIMITED:
        return 1 << 30
    if limit <= 0:
        return 0
    return max(0, limit - await used_today(user_id, feature, day))


async def _take_quota(user_id: int, feature: str, day: str, amount: int) -> None:
    kind = _FEATURES[feature][0]
    async with get_session() as db:
        rows = await db.execute(
            select(DailyUsage).where(
                DailyUsage.user_id == user_id,
                DailyUsage.day == day,
                DailyUsage.kind == kind,
            )
        )
        row = rows.scalar_one_or_none()
        if row is None:
            db.add(DailyUsage(user_id=user_id, day=day, kind=kind, used=amount))
            try:
                await db.commit()
                return
            except IntegrityError:
                # دو ارسال همزمان؛ ردیف را کس دیگری ساخته است
                await db.rollback()
                rows = await db.execute(
                    select(DailyUsage).where(
                        DailyUsage.user_id == user_id,
                        DailyUsage.day == day,
                        DailyUsage.kind == kind,
                    )
                )
                row = rows.scalar_one_or_none()
                if row is None:
                    return
        row.used = max(0, (row.used or 0) + amount)
        await db.commit()


async def reserve(user_id: int, feature: str, amount: int, plan, day: str) -> Grant | None:
    """اول از سهمیه، بعد از اعتبار برمی‌دارد.

    اگر مجموع کافی نباشد None برمی‌گرداند و <b>هیچ‌چیز کم نمی‌شود</b>.
    """
    if feature not in _FEATURES or amount <= 0:
        return None

    limit = plan.quota(feature) if plan else 0
    if limit == UNLIMITED:
        return Grant(feature, unlimited=True)

    available = await quota_left(user_id, feature, plan, day)
    from_quota = min(amount, available)
    from_credits = amount - from_quota

    if from_credits and not await credits.consume(user_id, _FEATURES[feature][1], from_credits):
        return None  # اعتبار کم است؛ سهمیه هم دست‌نخورده می‌ماند

    if from_quota:
        await _take_quota(user_id, feature, day, from_quota)
    return Grant(feature, from_quota=from_quota, from_credits=from_credits)


async def release(user_id: int, grant: Grant | None, day: str) -> None:
    """اگر عملیات انجام نشد، سهمیه و اعتبار برمی‌گردند."""
    if grant is None or grant.unlimited:
        return
    if grant.from_quota:
        await _take_quota(user_id, grant.feature, day, -grant.from_quota)
    if grant.from_credits:
        await credits.add(
            user_id,
            _FEATURES[grant.feature][1],
            grant.from_credits,
            note="بازگشت؛ عملیات انجام نشد",
        )


async def affordable(user_id: int, feature: str, amount: int, plan, day: str) -> bool:
    """آیا این تعداد از مجموع سهمیه و اعتبار قابل تأمین است؟ (بدون کم کردن)"""
    if feature not in _FEATURES or amount <= 0:
        return False
    if (plan.quota(feature) if plan else 0) == UNLIMITED:
        return True
    left = await quota_left(user_id, feature, plan, day)
    if left >= amount:
        return True
    balance = await credits.balance(user_id, _FEATURES[feature][1])
    return left + balance >= amount
