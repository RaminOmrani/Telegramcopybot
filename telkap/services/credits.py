"""اعتبارهای مصرفی که جدا از اشتراک خریده می‌شوند.

دو نوع اعتبار وجود دارد و هر دو «واحدی» مصرف می‌شوند:
  * واترمارک — به ازای هر تصویری که واترمارک بخورد
  * کپی پیام‌های گذشته — به ازای هر پیام قدیمی که کپی شود

اگر پلن کاربر خودش آن قابلیت را داشته باشد، اعتبار مصرف نمی‌شود؛ اعتبار
فقط برای کاربرانی است که پلنشان آن قابلیت را ندارد.
"""
from __future__ import annotations

import logging

from telkap.db import get_session, log_activity
from telkap.models import User
from telkap.plans import CREDIT_AI, CREDIT_HISTORY, CREDIT_WATERMARK

log = logging.getLogger(__name__)

# نام ستون هر نوع اعتبار روی مدل کاربر
_FIELDS = {
    CREDIT_WATERMARK: "watermark_credits",
    CREDIT_HISTORY: "history_credits",
    CREDIT_AI: "ai_credits",
}


async def balance(user_id: int, kind: str) -> int:
    field = _FIELDS.get(kind)
    if field is None:
        return 0
    async with get_session() as db:
        user = await db.get(User, user_id)
        return int(getattr(user, field, 0) or 0) if user else 0


async def balances(user_id: int) -> dict[str, int]:
    async with get_session() as db:
        user = await db.get(User, user_id)
        if user is None:
            return dict.fromkeys(_FIELDS, 0)
        return {kind: int(getattr(user, field, 0) or 0) for kind, field in _FIELDS.items()}


async def add(user_id: int, kind: str, amount: int, *, note: str = "") -> int:
    """اعتبار اضافه (یا با عدد منفی کم) می‌کند و مانده‌ی تازه را برمی‌گرداند."""
    field = _FIELDS.get(kind)
    if field is None or amount == 0:
        return await balance(user_id, kind)
    async with get_session() as db:
        user = await db.get(User, user_id)
        if user is None:
            return 0
        current = int(getattr(user, field, 0) or 0)
        new_value = max(0, current + amount)
        setattr(user, field, new_value)
        await db.commit()
    await log_activity(
        user_id=user_id,
        event="credit",
        detail=f"{kind} {amount:+d} → {new_value}{(' | ' + note) if note else ''}",
    )
    return new_value


async def consume(user_id: int, kind: str, amount: int = 1) -> bool:
    """اگر مانده کافی باشد کم می‌کند و True می‌دهد، وگرنه دست نمی‌زند."""
    field = _FIELDS.get(kind)
    if field is None or amount <= 0:
        return False
    async with get_session() as db:
        user = await db.get(User, user_id)
        if user is None:
            return False
        current = int(getattr(user, field, 0) or 0)
        if current < amount:
            return False
        setattr(user, field, current - amount)
        await db.commit()
    return True
