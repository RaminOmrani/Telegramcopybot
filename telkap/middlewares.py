"""میدل‌ورهای سراسری."""
from __future__ import annotations

import logging
from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, Message, TelegramObject

from telkap.db import get_session
from telkap.models import User

log = logging.getLogger(__name__)


class BanMiddleware(BaseMiddleware):
    """جلوی پردازش هر رویداد از کاربران مسدود را می‌گیرد."""

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        tg_user = data.get("event_from_user")
        if tg_user is not None:
            async with get_session() as db:
                user = await db.get(User, tg_user.id)
            if user is not None and user.is_banned:
                if isinstance(event, Message):
                    await event.answer("⛔️ دسترسی شما به این ربات مسدود شده است.")
                elif isinstance(event, CallbackQuery):
                    await event.answer("⛔️ دسترسی شما مسدود شده است.", show_alert=True)
                return None
        return await handler(event, data)


class ErrorLogMiddleware(BaseMiddleware):
    """خطاهای هندلرها را لاگ می‌کند و به کاربر پیام قابل‌فهم می‌دهد."""

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        try:
            return await handler(event, data)
        except Exception:
            log.exception("خطای پردازش‌نشده در هندلر")
            try:
                if isinstance(event, Message):
                    await event.answer("⚠️ خطایی رخ داد. دوباره تلاش کنید یا /start را بزنید.")
                elif isinstance(event, CallbackQuery):
                    await event.answer("⚠️ خطایی رخ داد.", show_alert=True)
            except Exception:
                log.debug("اطلاع‌رسانی خطا به کاربر ناموفق بود", exc_info=True)
            return None
