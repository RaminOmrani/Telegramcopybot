"""کیف پول کاربر و دفتر تراکنش‌هایش.

پول فقط وارد می‌شود: هیچ مبلغی از این کیف پول به حساب بانکی کسی
برنمی‌گردد. تنها «برگشت» ممکن، اصلاح یک برداشتِ درون‌رباتی است که چیزی
نخریده — مثل فعال‌سازی ناموفق نمایندگی.

موجودی روی `users.wallet_toman` است و هر تغییرش یک ردیف در
`wallet_entries` می‌گذارد. این دو باید همیشه با هم نوشته شوند، وگرنه
دفتر با موجودی نمی‌خواند و اختلاف حساب با مشتری غیرقابل حل می‌شود.

برداشت با یک `UPDATE … WHERE wallet_toman >= amount` انجام می‌شود، نه با
خواندن و بعد نوشتن؛ اینطور دو برداشت همزمان نمی‌توانند موجودی را منفی
کنند حتی اگر دقیقاً هم‌زمان برسند.
"""
from __future__ import annotations

import logging

from sqlalchemy import select, update

from telkap.db import get_session, log_activity
from telkap.models import User, WalletEntry
from telkap.plans import toman

log = logging.getLogger(__name__)

# برچسب فارسی هر علت، برای نمایش در تاریخچه
REASON_LABELS: dict[str, str] = {
    WalletEntry.REASON_REFERRAL: "🎁 پاداش دعوت",
    WalletEntry.REASON_TOPUP: "➕ شارژ کیف پول",
    WalletEntry.REASON_PURCHASE: "🛒 خرید",
    WalletEntry.REASON_REFUND: "↩️ اصلاح تراکنش ناموفق",
    WalletEntry.REASON_ADMIN: "🛠 تنظیم ادمین",
}


def reason_label(reason: str) -> str:
    return REASON_LABELS.get(reason, reason)


async def balance(user_id: int) -> int:
    async with get_session() as db:
        value = await db.scalar(select(User.wallet_toman).where(User.id == user_id))
    return int(value or 0)


async def credit(
    user_id: int,
    amount: int,
    *,
    reason: str = WalletEntry.REASON_ADMIN,
    note: str = "",
    ref_id: int | None = None,
    admin_id: int | None = None,
) -> int | None:
    """مبلغی به کیف پول اضافه می‌کند و موجودی تازه را برمی‌گرداند."""
    amount = int(amount)
    if amount <= 0:
        return None

    async with get_session() as db:
        user = await db.get(User, user_id)
        if user is None:
            return None
        new_balance = int(user.wallet_toman or 0) + amount
        user.wallet_toman = new_balance
        db.add(
            WalletEntry(
                user_id=user_id,
                amount_toman=amount,
                balance_after=new_balance,
                reason=reason,
                note=note[:255],
                ref_id=ref_id,
                admin_id=admin_id,
            )
        )
        await db.commit()

    await log_activity(
        user_id=user_id,
        event="wallet_credit",
        detail=f"{toman(amount)} — {reason_label(reason)}",
    )
    return new_balance


async def debit(
    user_id: int,
    amount: int,
    *,
    reason: str = WalletEntry.REASON_PURCHASE,
    note: str = "",
    ref_id: int | None = None,
) -> int | None:
    """اگر موجودی کافی باشد برداشت می‌کند و موجودی تازه را می‌دهد.

    در صورت کافی نبودن، None برمی‌گرداند و هیچ‌چیز تغییر نمی‌کند.
    """
    amount = int(amount)
    if amount <= 0:
        return None

    async with get_session() as db:
        # شرط روی خود UPDATE است تا برداشت همزمان موجودی را منفی نکند
        result = await db.execute(
            update(User)
            .where(User.id == user_id, User.wallet_toman >= amount)
            .values(wallet_toman=User.wallet_toman - amount)
        )
        if result.rowcount == 0:
            await db.rollback()
            return None
        new_balance = await db.scalar(select(User.wallet_toman).where(User.id == user_id))
        db.add(
            WalletEntry(
                user_id=user_id,
                amount_toman=-amount,
                balance_after=int(new_balance or 0),
                reason=reason,
                note=note[:255],
                ref_id=ref_id,
            )
        )
        await db.commit()

    await log_activity(
        user_id=user_id,
        event="wallet_debit",
        detail=f"{toman(amount)} — {reason_label(reason)}",
    )
    return int(new_balance or 0)


async def adjust(
    user_id: int, amount: int, *, admin_id: int | None = None, note: str = ""
) -> int | None:
    """کم و زیاد کردن دستی توسط ادمین. عدد منفی یعنی کسر."""
    if amount == 0:
        return None
    if amount > 0:
        return await credit(
            user_id,
            amount,
            reason=WalletEntry.REASON_ADMIN,
            note=note or f"توسط ادمین {admin_id or '—'}",
            admin_id=admin_id,
        )
    return await debit(
        user_id,
        -amount,
        reason=WalletEntry.REASON_ADMIN,
        note=note or f"توسط ادمین {admin_id or '—'}",
    )


async def history(user_id: int, limit: int = 15) -> list[WalletEntry]:
    async with get_session() as db:
        rows = await db.execute(
            select(WalletEntry)
            .where(WalletEntry.user_id == user_id)
            .order_by(WalletEntry.id.desc())
            .limit(limit)
        )
        return list(rows.scalars())
