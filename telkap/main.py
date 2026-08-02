"""نقطه‌ی شروع ربات."""
from __future__ import annotations

import asyncio
import logging
import sys

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage
from sqlalchemy import select

from telkap.config import get_settings
from telkap.db import close_db, get_session, init_db
from telkap.handlers import build_router
from telkap.handlers import history as history_handlers
from telkap.handlers import tasks as task_handlers
from telkap.middlewares import BanMiddleware, ErrorLogMiddleware
from telkap.models import Task
from telkap.services.copier import Copier
from telkap.services.history import HistoryCopier
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
    async def notify(user_id: int, text: str) -> None:
        try:
            await bot.send_message(user_id, text)
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

    bot = Bot(
        token=cfg.bot_token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    notify = make_notifier(bot)

    copier = Copier(manager, notifier=notify)
    manager.bind_copier(copier)
    history_copier = HistoryCopier(manager, copier, notifier=notify)
    history_handlers.bind(history_copier)
    task_handlers.bind_history(history_copier)

    dispatcher = Dispatcher(storage=MemoryStorage())
    dispatcher.message.middleware(ErrorLogMiddleware())
    dispatcher.callback_query.middleware(ErrorLogMiddleware())
    dispatcher.message.middleware(BanMiddleware())
    dispatcher.callback_query.middleware(BanMiddleware())
    dispatcher.include_router(build_router())

    await manager.restore_all()
    watchdog = asyncio.create_task(subscription_watchdog(notify))

    me = await bot.get_me()
    log.info("ربات @%s آماده است", me.username)

    try:
        await bot.delete_webhook(drop_pending_updates=True)
        await dispatcher.start_polling(bot)
    finally:
        watchdog.cancel()
        await asyncio.gather(watchdog, return_exceptions=True)
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
