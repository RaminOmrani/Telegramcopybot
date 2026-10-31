"""تیکت پشتیبانی — گفتگوی کاربر و مدیریت، بدون افشای هویت هیچ‌کدام.

کاربر پیامش را در ربات می‌نویسد؛ پیام برای همه‌ی ادمین‌ها می‌رود و هرکدام
می‌توانند پاسخ بدهند. پاسخ با عنوان «پشتیبانی» به کاربر می‌رسد و آیدی
ادمین هرگز دیده نمی‌شود.
"""
from __future__ import annotations

import logging

from sqlalchemy import func, select

from telkap.db import get_session, log_activity
from telkap.models import SupportMessage, SupportTicket, utcnow

log = logging.getLogger(__name__)

MAX_TEXT = 3000


async def open_ticket(user_id: int) -> SupportTicket | None:
    """تیکت باز کاربر، اگر داشته باشد."""
    async with get_session() as db:
        rows = await db.execute(
            select(SupportTicket)
            .where(
                SupportTicket.user_id == user_id,
                SupportTicket.status == SupportTicket.STATUS_OPEN,
            )
            .order_by(SupportTicket.id.desc())
            .limit(1)
        )
        return rows.scalar_one_or_none()


async def add_user_message(user_id: int, text: str) -> tuple[SupportTicket, bool]:
    """پیام کاربر را ثبت می‌کند.

    خروجی: (تیکت، آیا تیکت تازه ساخته شد). اگر تیکت بازی باشد پیام به آن
    اضافه می‌شود تا گفتگو یک‌تکه بماند.
    """
    text = (text or "").strip()[:MAX_TEXT]
    created = False
    async with get_session() as db:
        rows = await db.execute(
            select(SupportTicket)
            .where(
                SupportTicket.user_id == user_id,
                SupportTicket.status == SupportTicket.STATUS_OPEN,
            )
            .order_by(SupportTicket.id.desc())
            .limit(1)
        )
        ticket = rows.scalar_one_or_none()
        if ticket is None:
            ticket = SupportTicket(user_id=user_id, subject=text[:120])
            db.add(ticket)
            await db.flush()
            created = True
        ticket.awaiting_reply = True
        ticket.last_at = utcnow()
        db.add(SupportMessage(ticket_id=ticket.id, from_admin=False, text=text))
        await db.commit()
        await db.refresh(ticket)

    await log_activity(user_id=user_id, event="support", detail=f"تیکت #{ticket.id}")
    return ticket, created


async def add_admin_reply(ticket_id: int, admin_id: int, text: str) -> SupportTicket | None:
    """پاسخ ادمین را ثبت می‌کند و تیکت را از حالت «منتظر پاسخ» درمی‌آورد."""
    text = (text or "").strip()[:MAX_TEXT]
    async with get_session() as db:
        ticket = await db.get(SupportTicket, ticket_id)
        if ticket is None:
            return None
        ticket.awaiting_reply = False
        ticket.status = SupportTicket.STATUS_OPEN
        ticket.last_at = utcnow()
        db.add(
            SupportMessage(
                ticket_id=ticket.id, from_admin=True, admin_id=admin_id, text=text
            )
        )
        await db.commit()
        await db.refresh(ticket)
    return ticket


async def close(ticket_id: int) -> SupportTicket | None:
    async with get_session() as db:
        ticket = await db.get(SupportTicket, ticket_id)
        if ticket is None:
            return None
        ticket.status = SupportTicket.STATUS_CLOSED
        ticket.awaiting_reply = False
        ticket.last_at = utcnow()
        await db.commit()
        await db.refresh(ticket)
    return ticket


async def reopen(ticket_id: int) -> SupportTicket | None:
    async with get_session() as db:
        ticket = await db.get(SupportTicket, ticket_id)
        if ticket is None:
            return None
        ticket.status = SupportTicket.STATUS_OPEN
        ticket.last_at = utcnow()
        await db.commit()
        await db.refresh(ticket)
    return ticket


async def get(ticket_id: int) -> SupportTicket | None:
    async with get_session() as db:
        return await db.get(SupportTicket, ticket_id)


async def history(ticket_id: int, limit: int = 20) -> list[SupportMessage]:
    """آخرین پیام‌های یک تیکت، از قدیم به جدید."""
    async with get_session() as db:
        rows = await db.execute(
            select(SupportMessage)
            .where(SupportMessage.ticket_id == ticket_id)
            .order_by(SupportMessage.id.desc())
            .limit(limit)
        )
        return list(reversed(list(rows.scalars())))


async def user_tickets(user_id: int, limit: int = 10) -> list[SupportTicket]:
    async with get_session() as db:
        rows = await db.execute(
            select(SupportTicket)
            .where(SupportTicket.user_id == user_id)
            .order_by(SupportTicket.id.desc())
            .limit(limit)
        )
        return list(rows.scalars())


async def open_tickets(limit: int = 20) -> list[SupportTicket]:
    """تیکت‌های باز، آن‌هایی که منتظر پاسخ‌اند اول."""
    async with get_session() as db:
        rows = await db.execute(
            select(SupportTicket)
            .where(SupportTicket.status == SupportTicket.STATUS_OPEN)
            .order_by(SupportTicket.awaiting_reply.desc(), SupportTicket.last_at.desc())
            .limit(limit)
        )
        return list(rows.scalars())


async def waiting_count() -> int:
    """چند تیکت منتظر پاسخ ادمین است (برای نشان روی دکمه)."""
    async with get_session() as db:
        return int(
            await db.scalar(
                select(func.count(SupportTicket.id)).where(
                    SupportTicket.status == SupportTicket.STATUS_OPEN,
                    SupportTicket.awaiting_reply.is_(True),
                )
            )
            or 0
        )
