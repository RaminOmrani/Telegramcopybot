"""نقطه‌ی شروع ربات."""
from __future__ import annotations

import asyncio
import logging
import sys

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramNetworkError
from aiogram.fsm.storage.memory import MemoryStorage
from sqlalchemy import select

from telkap import proxy
from telkap.config import get_settings
from telkap.db import close_db, get_session, init_db
from telkap.handlers import approvals as approval_handlers
from telkap.handlers import build_router
from telkap.handlers import history as history_handlers
from telkap.middlewares import (
    BanMiddleware,
    ErrorLogMiddleware,
    ForceJoinMiddleware,
    LanguageMiddleware,
    MaintenanceMiddleware,
)
from telkap.models import Task
from telkap.services import (
    alerts,
    backup,
    digest,
    forcejoin,
    maintenance,
    planstore,
    reminders,
    renewal,
)
from telkap.services.copier import Copier
from telkap.services.history import HistoryCopier
from telkap.services.pending import ReleaseWorker
from telkap.services.retry import RetryWorker
from telkap.services.subscription import active_plan_for
from telkap.services.userbot import manager

log = logging.getLogger(__name__)

SUBSCRIPTION_CHECK_SECONDS = 900  # هر ۱۵ دقیقه


def setup_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level, logging.INFO),
        format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        stream=sys.stdout,
    )
    logging.getLogger("telethon").setLevel(logging.WARNING)
    logging.getLogger("aiogram.event").setLevel(logging.WARNING)


def make_notifier(bot: Bot):
    async def notify(user_id: int, text: str, markup=None) -> None:
        try:
            await bot.send_message(user_id, text, reply_markup=markup)
        except Exception:
            log.debug("ارسال اعلان به کاربر %s ناموفق بود", user_id, exc_info=True)

    return notify


async def subscription_watchdog(notify) -> None:
    """کارهای کاربرانی که اشتراکشان تمام شده را متوقف می‌کند."""
    while True:
        try:
            await asyncio.sleep(SUBSCRIPTION_CHECK_SECONDS)
            async with get_session() as db:
                rows = await db.execute(
                    select(Task.user_id).where(Task.enabled.is_(True)).distinct()
                )
                user_ids = [uid for uid in rows.scalars()]

            for user_id in user_ids:
                if await active_plan_for(user_id) is not None:
                    continue
                async with get_session() as db:
                    rows = await db.execute(
                        select(Task).where(Task.user_id == user_id, Task.enabled.is_(True))
                    )
                    stopped = list(rows.scalars())
                    for task in stopped:
                        task.enabled = False
                        task.last_error = "اشتراک منقضی شده است"
                    await db.commit()
                if stopped:
                    await manager.reload_user(user_id)
                    await notify(
                        user_id,
                        f"⛔️ اشتراک شما به پایان رسید و {len(stopped)} کار کپی متوقف شد.\n"
                        "برای ادامه، از بخش «💳 خرید اشتراک» تمدید کنید.",
                    )
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("بررسی دوره‌ای اشتراک‌ها با خطا مواجه شد")


async def main() -> None:
    cfg = get_settings()
    setup_logging(cfg.log_level)
    log.info("راه‌اندازی ربات…")

    await init_db()
    cfg.download_dir.mkdir(parents=True, exist_ok=True)

    session = None
    if cfg.proxy_url:
        try:
            session = AiohttpSession(proxy=proxy.for_aiogram(cfg.proxy_url))
            log.info("پروکسی فعال است: %s", proxy.describe(cfg.proxy_url))
        except proxy.ProxyError as exc:
            log.error("PROXY_URL نامعتبر است: %s", exc)
            return
        except Exception:
            log.exception(
                "ساخت اتصال پروکسی ناموفق بود. برای پروکسی socks این را نصب کنید:\n"
                "    pip install aiohttp-socks"
            )
            return

    bot = Bot(
        token=cfg.bot_token,
        session=session,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    notify = make_notifier(bot)
    # تا بخش‌هایی که Bot ندارند (مثل موتور کپی) هم بتوانند به ادمین هشدار بدهند
    alerts.bind(bot)

    copier = Copier(manager, notifier=notify)
    manager.bind_copier(copier)
    # تا کاربر بفهمد چرا اکانتش قطع یا محدود شده، نه اینکه فقط ببیند کار نمی‌کند
    manager.bind_notifier(notify)
    history_copier = HistoryCopier(manager, copier, notifier=notify)
    history_handlers.bind(history_copier)

    retry_worker = RetryWorker(manager, copier, notifier=notify)
    release_worker = ReleaseWorker(manager, copier, notifier=notify)
    # تا دکمه‌ی «تأیید» بتواند همان لحظه منتشر کند، نه در چرخه‌ی بعدی
    approval_handlers.bind(release_worker)

    dispatcher = Dispatcher(storage=MemoryStorage())
    for observer in (dispatcher.message, dispatcher.callback_query):
        observer.middleware(ErrorLogMiddleware())
        observer.middleware(LanguageMiddleware())
        observer.middleware(BanMiddleware())
        observer.middleware(MaintenanceMiddleware())
        observer.middleware(ForceJoinMiddleware())
    dispatcher.include_router(build_router())

    # طرح‌ها و قیمت‌های ویرایش‌شده‌ی ادمین باید پیش از هر درخواستی سر جایشان باشند
    await planstore.load()
    await forcejoin.seed_from_env()
    await manager.restore_all()

    background = [
        asyncio.create_task(subscription_watchdog(notify), name="subscriptions"),
        asyncio.create_task(retry_worker.run_forever(), name="retry"),
        asyncio.create_task(release_worker.run_forever(), name="release"),
        asyncio.create_task(reminders.run_forever(notify), name="reminders"),
        asyncio.create_task(digest.run_forever(notify), name="digest"),
        asyncio.create_task(backup.run_forever(bot), name="backup"),
        asyncio.create_task(renewal.run_forever(notify), name="renewal"),
        asyncio.create_task(maintenance.run_forever(), name="maintenance"),
        asyncio.create_task(alerts.run_forever(bot), name="alerts"),
    ]

    try:
        me = await bot.get_me()
    except TelegramNetworkError as exc:
        log.error(
            "\n"
            "──────────────────────────────────────────────\n"
            "❌ اتصال به تلگرام برقرار نشد.\n"
            "\n"
            "این خطای کد نیست؛ یعنی شبکه‌ی شما به api.telegram.org راه نمی‌دهد.\n"
            "\n"
            "راه‌حل‌ها:\n"
            "  ۱) VPN را در حالت TUN / Global روشن کنید و دوباره اجرا کنید\n"
            "  ۲) یا در فایل .env پروکسی خود را بگذارید، مثلاً:\n"
            "       PROXY_URL=socks5://127.0.0.1:10808\n"
            "     (برای پروکسی socks این را هم نصب کنید: pip install aiohttp-socks)\n"
            "\n"
            "پروکسی فعلی: %s\n"
            "جزئیات فنی: %s\n"
            "──────────────────────────────────────────────",
            proxy.describe(cfg.proxy_url),
            exc,
        )
        for task in background:
            task.cancel()
        await asyncio.gather(*background, return_exceptions=True)
        await bot.session.close()
        await close_db()
        return

    log.info("ربات @%s آماده است", me.username)

    try:
        await bot.delete_webhook(drop_pending_updates=True)
        await dispatcher.start_polling(bot)
    finally:
        for task in background:
            task.cancel()
        await asyncio.gather(*background, return_exceptions=True)
        await manager.shutdown()
        await bot.session.close()
        await close_db()
        log.info("ربات متوقف شد")


def run() -> None:
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        log.info("خروج با درخواست کاربر")


if __name__ == "__main__":
    run()
