"""راه‌اندازی موتور دیتابیس و توابع کمکی دسترسی."""
from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from sqlalchemy import inspect
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


def _table_columns(conn, table: str) -> set[str]:
    inspector = inspect(conn)
    if table not in inspector.get_table_names():
        return set()
    return {col["name"] for col in inspector.get_columns(table)}


def _prepare_schema(conn) -> bool:
    """مهاجرت‌های لازم پیش از create_all.

    خروجی True یعنی داده‌ی جدول قدیمی message_map باید بعداً منتقل شود.
    """
    columns = _table_columns(conn, "message_map")
    if columns and "dest_chat" not in columns:
        # ستون و قید یکتای جدید لازم است؛ در SQLite با ساخت دوباره‌ی جدول
        log.info("مهاجرت جدول message_map برای پشتیبانی از چند مقصد…")
        conn.exec_driver_sql("ALTER TABLE message_map RENAME TO message_map_legacy")
        return True
    return False


def _finish_schema(conn, migrated: bool) -> None:
    # ایندکس‌هایی که روی جدول‌های از پیش موجود ساخته نمی‌شوند
    conn.exec_driver_sql(
        "CREATE INDEX IF NOT EXISTS ix_message_map_dedupe "
        "ON message_map (task_id, content_hash)"
    )
    if not migrated:
        return
    conn.exec_driver_sql(
        "INSERT INTO message_map (task_id, src_msg_id, dst_msg_id, dest_chat, content_hash, created_at) "
        "SELECT task_id, src_msg_id, dst_msg_id, '', content_hash, created_at FROM message_map_legacy"
    )
    conn.exec_driver_sql("DROP TABLE message_map_legacy")
    log.info("مهاجرت message_map انجام شد")


async def init_db() -> None:
    global _engine, _session_factory
    url = get_settings().database_url
    _ensure_sqlite_dir(url)
    _engine = create_async_engine(url, echo=False, pool_pre_ping=True)
    _session_factory = async_sessionmaker(_engine, expire_on_commit=False)
    async with _engine.begin() as conn:
        migrated = await conn.run_sync(_prepare_schema)
        await conn.run_sync(Base.metadata.create_all)
        await conn.run_sync(_finish_schema, migrated)
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
