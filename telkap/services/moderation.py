"""مسدود کردن کاربر — یک جا، برای ربات و پنل وب.

<b>چرا سرویس شد.</b> این منطق داخل هندلر ربات بود. پنل وب که همان کار
را می‌خواست، یا باید کپی‌اش می‌کرد یا هندلر را صدا می‌زد؛ کپی یعنی روزی
یکی از دو نسخه اصلاح می‌شود و دیگری نه — و آن روز، «مسدود» در یک جا
یعنی متوقف شدن کارها و در جای دیگر یعنی فقط یک برچسب.

<b>مسدود کردن سه کار است، نه یکی.</b> برچسب زدن به کاربر، خاموش کردن
کارهایش، و متوقف کردن اکانتِ در حال اجرا. اگر فقط اولی انجام شود،
کاربرِ «مسدود» همچنان محتوا کپی می‌کند.
"""
from __future__ import annotations

import logging

from sqlalchemy import select

from telkap.db import get_session, log_activity
from telkap.models import Task, User

log = logging.getLogger(__name__)


async def set_ban(user_id: int, banning: bool, *, admin_id: int | None = None) -> bool:
    """کاربر را مسدود یا آزاد می‌کند. False یعنی کاربر پیدا نشد."""
    async with get_session() as db:
        user = await db.get(User, user_id)
        if user is None:
            return False
        user.is_banned = banning
        if banning:
            # کارِ روشن یعنی کپیِ ادامه‌دار. بدون این، «مسدود» فقط یک
            # برچسب است و کاربر همچنان از سرویس استفاده می‌کند.
            rows = await db.execute(select(Task).where(Task.user_id == user_id))
            for task in rows.scalars():
                task.enabled = False
        await db.commit()

    from telkap.services.userbot import manager

    if banning:
        await manager.stop_user(user_id)
    else:
        # کارها عمداً خاموش می‌مانند: آزاد کردن یعنی «اجازه دارد»، نه
        # «همین حالا دوباره شروع کن». روشن کردنشان تصمیم خودِ کاربر است.
        await manager.reload_user(user_id)

    await log_activity(
        user_id=user_id,
        event="admin",
        detail=("مسدود شد" if banning else "از مسدودی درآمد")
        + (f" توسط ادمین {admin_id}" if admin_id else ""),
    )
    return True


async def is_banned(user_id: int) -> bool:
    async with get_session() as db:
        user = await db.get(User, user_id)
    return bool(user and user.is_banned)
