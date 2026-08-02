"""راه‌اندازی موتور دیتابیس و توابع کمکی دسترسی."""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncIterator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from telkap.config import BASE_DIR, get_settings
from telkap.models import ActivityLog, Base

log = logging.getLogger(__name__)

_engine = None
_session_factory: async_sessionmaker[AsyncSession] | None = None


def _ensure_sqlite_dir(url: str) -> None:
    marker = "sqlite+aiosqlite:///"
    if not url.startswith(marker):
        return
    path = Path(url[len(marker):])
    if not path.is_absolute():
        path = BASE_DIR / path
    path.parent.mkdir(parents=True, exist_ok=True)


async def init_db() -> None:
    global _engine, _session_factory
    url = get_settings().database_url
    _ensure_sqlite_dir(url)
    _engine = create_async_engine(url, echo=False, pool_pre_ping=True)
    _session_factory = async_sessionmaker(_engine, expire_on_commit=False)
    async with _engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    log.info("دیتابیس آماده شد: %s", url)


async def close_db() -> None:
    if _engine is not None:
        await _engine.dispose()


def session_factory() -> async_sessionmaker[AsyncSession]:
    if _session_factory is None:
        raise RuntimeError("init_db() فراخوانی نشده است")
    return _session_factory


@asynccontextmanager
async def get_session() -> AsyncIterator[AsyncSession]:
    async with session_factory()() as session:
        yield session


async def log_activity(
    *,
    user_id: int | None = None,
    task_id: int | None = None,
    event: str,
    detail: str = "",
    level: str = "info",
) -> None:
    """ثبت رویداد در جدول لاگ فعالیت‌ها (قابلیت «لاگ فعالیت‌ها»)."""
    try:
        async with get_session() as session:
            session.add(
                ActivityLog(
                    user_id=user_id,
                    task_id=task_id,
                    event=event,
                    detail=detail[:600],
                    level=level,
                )
            )
            await session.commit()
    except Exception:  # لاگ هرگز نباید مسیر اصلی را بشکند
        log.exception("ثبت لاگ فعالیت ناموفق بود (%s)", event)
