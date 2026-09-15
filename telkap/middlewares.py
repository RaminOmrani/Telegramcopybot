"""میدل‌ورهای سراسری."""
from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware
from aiogram.exceptions import TelegramAPIError
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
    TelegramObject,
)

from telkap import i18n
from telkap.config import get_settings
from telkap.db import get_session
from telkap.models import User
from telkap.services import forcejoin, maintenance, roles

log = logging.getLogger(__name__)


class LanguageMiddleware(BaseMiddleware):
    """زبان کاربر را پیش از هر هندلری سر جایش می‌گذارد.

    زبان در یک ContextVar می‌نشیند تا `t()` را بشود هرجای کد صدا زد،
    بدون اینکه لازم باشد زبان از هندلر تا عمق توابع دست‌به‌دست شود.
    چون در هر پیام لازم است، یک کش کوچک نگه داشته می‌شود؛ زبان به‌ندرت
    عوض می‌شود و موقع تغییر همان‌جا باطل می‌گردد.
    """

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        tg_user = data.get("event_from_user")
        lang = i18n.DEFAULT
        if tg_user is not None:
            lang = await i18n.language_of(tg_user.id, fallback=tg_user.language_code)
        i18n.set_current(lang)
        data["lang"] = lang
        return await handler(event, data)


class MaintenanceMiddleware(BaseMiddleware):
    """در حالت تعمیر، فقط ادمین‌ها اجازه‌ی کار دارند.

    کاربر به‌جای خطای مبهم، دلیل و یک پیام محترمانه می‌بیند.
    """

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        on, note = await maintenance.mode()
        if not on:
            return await handler(event, data)

        tg_user = data.get("event_from_user")
        if tg_user is not None and await roles.is_staff(tg_user.id):
            return await handler(event, data)

        text = f"🛠 <b>در دست تعمیر</b>\n\n{note}"
        if isinstance(event, Message):
            await event.answer(text)
        elif isinstance(event, CallbackQuery):
            await event.answer(note[:190], show_alert=True)
        return None


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


class CapMiddleware(BaseMiddleware):
    """کل یک روتر را پشت یک دسترسی قفل می‌کند.

    بخش‌هایی مثل «طرح‌ها» یا «کاربران» یکدست‌اند و همه‌ی هندلرهایشان یک
    دسترسی می‌خواهند؛ گذاشتن گارد در تک‌تکِ آن‌ها هم تکراری است و هم
    دیر یا زود یکی‌اش جا می‌ماند.
    """

    def __init__(self, cap: str) -> None:
        self.cap = cap

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        tg_user = data.get("event_from_user")
        if tg_user is not None and await roles.can(tg_user.id, self.cap):
            return await handler(event, data)
        if isinstance(event, CallbackQuery):
            await event.answer("به این بخش دسترسی ندارید", show_alert=True)
        return None


class ForceJoinMiddleware(BaseMiddleware):
    """تا کاربر در همه‌ی کانال‌های اجباری عضو نشود، اجازه‌ی کار نمی‌دهد.

    فهرست کانال‌ها را ادمین از داخل پنل مدیریت می‌سازد و می‌تواند هر
    تعداد کانال داشته باشد.
    """

    def __init__(self) -> None:
        # کاربرانی که در این اجرا تأیید شده‌اند؛ با تغییر فهرست پاک می‌شود
        self._verified: set[int] = set()
        self._signature: tuple[int, ...] = ()

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        tg_user = data.get("event_from_user")
        if tg_user is None:
            return await handler(event, data)

        channels = await forcejoin.active_channels()
        signature = tuple(channel.id for channel in channels)
        if signature != self._signature:
            # فهرست عوض شده؛ تأییدهای قبلی دیگر معتبر نیستند
            self._signature = signature
            self._verified.clear()

        if not channels or get_settings().is_admin(tg_user.id):
            return await handler(event, data)
        if tg_user.id in self._verified:
            return await handler(event, data)

        bot = data.get("bot")
        if bot is None:
            return await handler(event, data)

        missing = [
            channel
            for channel in channels
            if not await self._is_member(bot, channel.ref, tg_user.id)
        ]
        if not missing:
            self._verified.add(tg_user.id)
            return await handler(event, data)

        markup = InlineKeyboardMarkup(
            inline_keyboard=[
                *[
                    [
                        InlineKeyboardButton(
                            text=f"📢 عضویت در {(channel.title or channel.ref)[:30]}",
                            url=channel.url,
                        )
                    ]
                    for channel in missing
                ],
                [InlineKeyboardButton(text="✅ عضو شدم، بررسی کن", callback_data="join:check")],
            ]
        )
        plural = "کانال‌های زیر" if len(missing) > 1 else "کانال زیر"
        text = (
            "🔐 <b>یک قدم مانده!</b>\n\n"
            f"برای استفاده از ربات، ابتدا در {plural} عضو شوید و "
            "بعد دکمه‌ی «عضو شدم» را بزنید."
        )
        if isinstance(event, CallbackQuery) and event.data == "join:check":
            await event.answer("هنوز در همه‌ی کانال‌ها عضو نشده‌اید.", show_alert=True)
            return None
        if isinstance(event, Message):
            await event.answer(text, reply_markup=markup)
        elif isinstance(event, CallbackQuery):
            await event.answer("ابتدا در کانال‌های اجباری عضو شوید.", show_alert=True)
        return None

    @staticmethod
    async def _is_member(bot, ref: str, user_id: int) -> bool:
        chat = ref if ref.lstrip("-").isdigit() else f"@{ref}"
        try:
            member = await bot.get_chat_member(chat, user_id)
        except TelegramAPIError:
            # ربات در کانال ادمین نیست یا نشانی اشتباه است؛ کاربر نباید قفل شود
            log.warning("بررسی عضویت در %s ممکن نبود؛ این کانال نادیده گرفته شد", chat)
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
