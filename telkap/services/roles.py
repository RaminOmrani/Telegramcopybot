"""نقش و سطح دسترسی ادمین‌ها.

تا امروز «ادمین» یک بله/خیر بود: هر کسی که در `.env` بود، همه‌کاره بود.
وقتی برای پشتیبانی کسی را می‌آورید این خطرناک است — نباید بتواند قیمت‌ها
را عوض کند یا از دیتابیس پشتیبان بگیرد.

قاعده‌ی ساده: ادمین‌های `.env` مالک‌اند و دست‌نخوردنی؛ بقیه از همین‌جا
نقش می‌گیرند و فقط همان بخش‌ها را می‌بینند.
"""
from __future__ import annotations

import logging

from sqlalchemy import delete, select

from telkap.config import get_settings
from telkap.db import get_session
from telkap.models import AdminRole

log = logging.getLogger(__name__)

# ------------------------------------------------------------ توانایی‌ها
CAP_USERS = "users"        # کاربران، مسدودسازی، پیام همگانی
CAP_TICKETS = "tickets"    # تیکت‌های پشتیبانی
CAP_MONEY = "money"        # رسیدها، طرح‌ها، کد تخفیف، نمایندگی، دعوت
CAP_REPORTS = "reports"    # آمار، درآمد، قیف تبدیل، ریزش
CAP_SYSTEM = "system"      # پشتیبان‌گیری، سقف‌ها، عضویت اجباری، نقش‌ها

ALL_CAPS = frozenset({CAP_USERS, CAP_TICKETS, CAP_MONEY, CAP_REPORTS, CAP_SYSTEM})

ROLE_OWNER = AdminRole.ROLE_OWNER
ROLE_FINANCE = AdminRole.ROLE_FINANCE
ROLE_SUPPORT = AdminRole.ROLE_SUPPORT

ROLE_CAPS: dict[str, frozenset[str]] = {
    ROLE_OWNER: ALL_CAPS,
    ROLE_FINANCE: frozenset({CAP_MONEY, CAP_REPORTS}),
    ROLE_SUPPORT: frozenset({CAP_USERS, CAP_TICKETS}),
}

ROLE_LABELS: dict[str, str] = {
    ROLE_OWNER: "👑 مالک (همه‌ی بخش‌ها)",
    ROLE_FINANCE: "💰 مالی (پرداخت، طرح‌ها، گزارش‌ها)",
    ROLE_SUPPORT: "🛟 پشتیبانی (تیکت‌ها و کاربران)",
}

CAP_LABELS: dict[str, str] = {
    CAP_USERS: "کاربران",
    CAP_TICKETS: "پشتیبانی",
    CAP_MONEY: "مالی",
    CAP_REPORTS: "گزارش‌ها",
    CAP_SYSTEM: "سیستم",
}

# نقش‌ها به‌ندرت عوض می‌شوند ولی در هر کلیک ادمین خوانده می‌شوند؛ پس یک
# بار می‌خوانیم و فقط موقع تغییر دور می‌ریزیم.
_cache: dict[int, str] | None = None


def _env_owner(user_id: int) -> bool:
    return get_settings().is_admin(user_id)


async def _all_roles() -> dict[int, str]:
    global _cache
    if _cache is None:
        async with get_session() as db:
            rows = await db.execute(select(AdminRole.user_id, AdminRole.role))
            _cache = {int(uid): role for uid, role in rows.all()}
    return _cache


def invalidate() -> None:
    global _cache
    _cache = None


async def role_of(user_id: int) -> str | None:
    """نقش کاربر، یا None اگر اصلاً ادمین نیست."""
    if _env_owner(user_id):
        return ROLE_OWNER
    return (await _all_roles()).get(user_id)


async def caps(user_id: int) -> frozenset[str]:
    role = await role_of(user_id)
    return ROLE_CAPS.get(role or "", frozenset())


async def can(user_id: int, cap: str) -> bool:
    return cap in await caps(user_id)


def can_cached(user_id: int, cap: str) -> bool:
    """نسخه‌ی همگامِ `can` برای هندلرهایی که پشت `CapMiddleware` هستند.

    میدل‌ور پیش از رسیدن به هندلر همان نقش را از دیتابیس خوانده، پس
    حافظه گرم است. اگر به هر دلیل گرم نباشد پاسخ «نه» است — یعنی این
    تابع هیچ‌وقت دسترسیِ نداشته را نمی‌دهد، فقط ممکن است سخت‌گیر شود.
    """
    if _env_owner(user_id):
        return True
    role = (_cache or {}).get(user_id)
    return cap in ROLE_CAPS.get(role or "", frozenset())


async def is_staff(user_id: int) -> bool:
    """آیا اصلاً چیزی از پنل مدیریت می‌بیند؟"""
    return bool(await caps(user_id))


async def is_owner(user_id: int) -> bool:
    return await role_of(user_id) == ROLE_OWNER


async def set_role(
    user_id: int, role: str, *, note: str = "", added_by: int | None = None
) -> bool:
    """افزودن یا تغییر نقش. روی ادمین‌های `.env` اثری ندارد."""
    if role not in ROLE_CAPS:
        return False
    if _env_owner(user_id):
        return False  # قبلاً مالک است؛ ردیف اضافه فقط گیج‌کننده است
    async with get_session() as db:
        row = await db.get(AdminRole, user_id)
        if row is None:
            row = AdminRole(user_id=user_id, added_by=added_by)
            db.add(row)
        row.role = role
        row.note = note[:120]
        await db.commit()
    invalidate()
    return True


async def remove(user_id: int) -> bool:
    """گرفتن دسترسی. ادمین `.env` را نمی‌شود از اینجا حذف کرد."""
    if _env_owner(user_id):
        return False
    async with get_session() as db:
        result = await db.execute(delete(AdminRole).where(AdminRole.user_id == user_id))
        await db.commit()
    invalidate()
    return bool(result.rowcount)


async def listing() -> list[AdminRole]:
    async with get_session() as db:
        rows = await db.execute(select(AdminRole).order_by(AdminRole.created_at))
        return list(rows.scalars())


async def staff_ids(cap: str | None = None) -> list[int]:
    """شناسه‌ی همه‌ی ادمین‌ها — برای اطلاع‌رسانی.

    با `cap` فقط کسانی که آن دسترسی را دارند برمی‌گردند؛ مثلاً هشدار
    رسید تازه به پشتیبانی که کاری با پول ندارد فرستاده نمی‌شود.
    """
    if cap is not None and cap not in ALL_CAPS:
        return []
    owners = list(get_settings().admin_ids)
    extra = [
        uid
        for uid, role in (await _all_roles()).items()
        if uid not in owners and (cap is None or cap in ROLE_CAPS.get(role, frozenset()))
    ]
    return owners + extra
