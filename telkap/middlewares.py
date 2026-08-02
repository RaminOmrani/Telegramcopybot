"""میدل‌ورهای سراسری."""
from __future__ import annotations

import logging
from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware
from aiogram.exceptions import TelegramAPIError
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
    TelegramObject,
)

from telkap.config import get_settings
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


class ForceJoinMiddleware(BaseMiddleware):
    """اگر کانال اجباری تنظیم شده باشد، تا پیش از عضویت اجازه‌ی کار نمی‌دهد."""

    ALLOWED_COMMANDS = ("/start", "/help")

    def __init__(self) -> None:
        self._verified: set[int] = set()

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        channel = get_settings().force_join_channel
        tg_user = data.get("event_from_user")
        if not channel or tg_user is None:
            return await handler(event, data)

        # ادمین‌ها و کاربران تأییدشده در همین اجرا دوباره بررسی نمی‌شوند
        if get_settings().is_admin(tg_user.id) or tg_user.id in self._verified:
            return await handler(event, data)

        bot = data.get("bot")
        if bot is None:
            return await handler(event, data)

        if await self._is_member(bot, channel, tg_user.id):
            self._verified.add(tg_user.id)
            return await handler(event, data)

        # کاربر تازه عضو شده و روی «بررسی کردم» زده است
        if isinstance(event, CallbackQuery) and event.data == "join:check":
            await event.answer("هنوز عضو نشده‌اید.", show_alert=True)
            return None

        markup = InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="📢 عضویت در کانال", url=f"https://t.me/{channel}")],
                [InlineKeyboardButton(text="✅ عضو شدم", callback_data="join:check")],
            ]
        )
        text = (
            "📢 برای استفاده از ربات، ابتدا در کانال ما عضو شوید.\n\n"
            "بعد از عضویت، دکمه‌ی «عضو شدم» را بزنید."
        )
        if isinstance(event, Message):
            await event.answer(text, reply_markup=markup)
        elif isinstance(event, CallbackQuery):
            await event.answer("ابتدا در کانال عضو شوید.", show_alert=True)
        return None

    @staticmethod
    async def _is_member(bot, channel: str, user_id: int) -> bool:
        try:
            member = await bot.get_chat_member(f"@{channel}", user_id)
        except TelegramAPIError:
            # ربات در کانال ادمین نیست یا کانال اشتباه است؛ کاربر نباید قفل شود
            log.warning("بررسی عضویت در @%s ممکن نبود؛ عضویت اجباری رد شد", channel)
            return True
        return member.status in {"creator", "administrator", "member", "restricted"}


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
