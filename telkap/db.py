"""راه‌اندازی موتور دیتابیس و توابع کمکی دسترسی."""
from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from sqlalchemy import event, inspect
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


def _tune_sqlite(engine) -> None:
    """تنظیم‌های SQLite برای کار با چند ده کاربر همزمان.

    - WAL: خواندن و نوشتن همدیگر را قفل نمی‌کنند. بدون آن، با بالا رفتن
      تعداد کاربران خطای «database is locked» می‌گیریم.
    - busy_timeout: به‌جای خطای فوری، تا ۵ ثانیه منتظر آزاد شدن قفل می‌ماند.
    - synchronous=NORMAL: با WAL امن است و نوشتن را چند برابر سریع‌تر می‌کند.
    - foreign_keys: SQLite این را پیش‌فرض خاموش می‌گذارد، یعنی ON DELETE
      CASCADE مدل‌ها اجرا نمی‌شد و ردیف‌های یتیم باقی می‌ماندند.
    """

    @event.listens_for(engine.sync_engine, "connect")
    def _on_connect(dbapi_connection, _record):  # pragma: no cover - وابسته به درایور
        cursor = dbapi_connection.cursor()
        try:
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute("PRAGMA synchronous=NORMAL")
            cursor.execute("PRAGMA busy_timeout=5000")
            cursor.execute("PRAGMA foreign_keys=ON")
        finally:
            cursor.close()


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


def _add_missing_columns(conn) -> None:
    """ستون‌هایی که بعداً اضافه شده‌اند را به جدول‌های موجود می‌افزاید.

    create_all فقط جدول نبوده را می‌سازد و ستون جدید را به جدول موجود
    اضافه نمی‌کند.
    """
    additions = {
        "destinations": [("overrides", "JSON DEFAULT '{}'")],
        "users": [
            ("watermark_credits", "INTEGER DEFAULT 0"),
            ("history_credits", "INTEGER DEFAULT 0"),
            ("limits", "JSON DEFAULT '{}'"),
            ("wallet_toman", "INTEGER DEFAULT 0"),
            ("referred_by", "BIGINT"),
            ("auto_renew", "BOOLEAN DEFAULT 0"),
            ("is_reseller", "BOOLEAN DEFAULT 0"),
            ("reseller_discount", "INTEGER DEFAULT 0"),
            ("account_state", "VARCHAR(16) DEFAULT 'ok'"),
            ("account_note", "VARCHAR(120) DEFAULT ''"),
            ("account_checked_at", "TIMESTAMP"),
        ],
        "activity_log": [
            ("actor_id", "BIGINT"),
        ],
        "payment_requests": [
            ("kind", "VARCHAR(16) DEFAULT 'plan'"),
            ("quantity", "INTEGER DEFAULT 0"),
            ("amount_toman", "INTEGER DEFAULT 0"),
            ("coupon_code", "VARCHAR(32) DEFAULT ''"),
            ("discount_toman", "INTEGER DEFAULT 0"),
            ("credit_toman", "INTEGER DEFAULT 0"),
            ("list_toman", "INTEGER DEFAULT 0"),
        ],
    }
    for table, columns in additions.items():
        existing = _table_columns(conn, table)
        if not existing:
            continue  # جدول تازه ساخته می‌شود و ستون را دارد
        for name, spec in columns:
            if name not in existing:
                log.info("افزودن ستون %s.%s", table, name)
                conn.exec_driver_sql(f"ALTER TABLE {table} ADD COLUMN {name} {spec}")


def _finish_schema(conn, migrated: bool) -> None:
    _add_missing_columns(conn)
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
    if url.startswith("sqlite"):
        _tune_sqlite(_engine)
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
    actor_id: int | None = None,
) -> None:
    """ثبت رویداد در جدول لاگ فعالیت‌ها (قابلیت «لاگ فعالیت‌ها»).

    `user_id` هدفِ رویداد است و `actor_id` کسی که انجامش داده. برای کار
    خودِ کاربر هر دو یکی‌اند؛ برای کار ادمین فرق می‌کنند و همین تفاوت،
    لاگ حسابرسی را قابل اتکا می‌کند.
    """
    try:
        async with get_session() as session:
            session.add(
                ActivityLog(
                    user_id=user_id,
                    task_id=task_id,
                    event=event,
                    detail=detail[:600],
                    level=level,
                    actor_id=actor_id,
                )
            )
            await session.commit()
    except Exception:  # لاگ هرگز نباید مسیر اصلی را بشکند
        log.exception("ثبت لاگ فعالیت ناموفق بود (%s)", event)
