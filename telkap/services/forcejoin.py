"""کانال‌های عضویت اجباری.

ادمین از داخل پنل هر تعداد کانال اضافه می‌کند و کاربر تا در همه‌ی آن‌ها
عضو نشود نمی‌تواند از ربات استفاده کند. فهرست در حافظه کش می‌شود چون
میدل‌ور روی هر پیام اجرا می‌شود.
"""
from __future__ import annotations

import logging
import time

from sqlalchemy import select

from telkap.config import get_settings
from telkap.db import get_session
from telkap.models import ForceJoinChannel

log = logging.getLogger(__name__)

CACHE_TTL = 60.0
_cache: tuple[float, list[ForceJoinChannel]] | None = None


def invalidate() -> None:
    global _cache
    _cache = None


async def active_channels() -> list[ForceJoinChannel]:
    """کانال‌های فعال، با کش کوتاه‌مدت."""
    global _cache
    now = time.monotonic()
    if _cache is not None and now - _cache[0] < CACHE_TTL:
        return _cache[1]
    async with get_session() as db:
        rows = await db.execute(
            select(ForceJoinChannel)
            .where(ForceJoinChannel.enabled.is_(True))
            .order_by(ForceJoinChannel.id)
        )
        channels = list(rows.scalars())
    _cache = (now, channels)
    return channels


async def all_channels() -> list[ForceJoinChannel]:
    async with get_session() as db:
        rows = await db.execute(select(ForceJoinChannel).order_by(ForceJoinChannel.id))
        return list(rows.scalars())


def normalize(raw: str) -> str:
    """ورودی ادمین را به شکل یکسان درمی‌آورد: یوزرنیم بدون @ یا آیدی عددی."""
    ref = (raw or "").strip()
    for prefix in ("https://t.me/", "http://t.me/", "t.me/", "@"):
        if ref.startswith(prefix):
            ref = ref[len(prefix):]
            break
    return ref.strip().strip("/")


async def add(ref: str, title: str = "", *, invite_link: str = "", admin_id: int | None = None):
    """کانال را اضافه می‌کند؛ اگر از قبل باشد همان را برمی‌گرداند."""
    ref = normalize(ref)
    if not ref:
        return None
    async with get_session() as db:
        existing = await db.execute(
            select(ForceJoinChannel).where(ForceJoinChannel.ref == ref)
        )
        row = existing.scalar_one_or_none()
        if row is not None:
            row.enabled = True
            if title:
                row.title = title[:160]
            await db.commit()
            await db.refresh(row)
            invalidate()
            return row
        row = ForceJoinChannel(
            ref=ref,
            title=(title or ref)[:160],
            invite_link=invite_link[:256],
            added_by=admin_id,
        )
        db.add(row)
        await db.commit()
        await db.refresh(row)
    invalidate()
    return row


async def remove(channel_id: int) -> bool:
    async with get_session() as db:
        row = await db.get(ForceJoinChannel, channel_id)
        if row is None:
            return False
        await db.delete(row)
        await db.commit()
    invalidate()
    return True


async def toggle(channel_id: int) -> bool | None:
    async with get_session() as db:
        row = await db.get(ForceJoinChannel, channel_id)
        if row is None:
            return None
        row.enabled = not row.enabled
        state = row.enabled
        await db.commit()
    invalidate()
    return state


async def seed_from_env() -> None:
    """کانال قدیمی FORCE_JOIN_CHANNEL را یک بار به جدول منتقل می‌کند."""
    legacy = get_settings().force_join_channel
    if not legacy:
        return
    async with get_session() as db:
        rows = await db.execute(select(ForceJoinChannel.id).limit(1))
        if rows.scalar_one_or_none() is not None:
            return  # ادمین از قبل کانال‌هایی دارد؛ دست نمی‌زنیم
    await add(legacy, legacy, admin_id=None)
    log.info("کانال عضویت اجباری از .env به جدول منتقل شد: %s", legacy)
