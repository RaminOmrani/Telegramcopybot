"""مدل‌های دیتابیس."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    JSON,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class User(Base):
    """کاربر ربات. `id` همان آیدی عددی تلگرام است."""

    __tablename__ = "users"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False)
    first_name: Mapped[str] = mapped_column(String(128), default="")
    username: Mapped[str | None] = mapped_column(String(64), nullable=True)
    phone: Mapped[str | None] = mapped_column(String(32), nullable=True)

    # سشن اکانت کاربری (رمزنگاری‌شده) برای خواندن کانال مبدا
    session_enc: Mapped[str | None] = mapped_column(Text, nullable=True)
    account_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    account_name: Mapped[str | None] = mapped_column(String(128), nullable=True)

    # احراز هویت دو مرحله‌ای در سطح ربات (پین عددی)
    pin_hash: Mapped[str | None] = mapped_column(String(128), nullable=True)

    is_banned: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    tasks: Mapped[list["Task"]] = relationship(back_populates="user", cascade="all, delete-orphan")
    subscriptions: Mapped[list["Subscription"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )

    @property
    def is_logged_in(self) -> bool:
        return bool(self.session_enc)


class Subscription(Base):
    __tablename__ = "subscriptions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    plan_code: Mapped[str] = mapped_column(String(32))
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    granted_by: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    note: Mapped[str | None] = mapped_column(String(255), nullable=True)

    user: Mapped[User] = relationship(back_populates="subscriptions")

    def is_active(self, now: datetime | None = None) -> bool:
        now = now or utcnow()
        expires = self.expires_at
        if expires.tzinfo is None:
            expires = expires.replace(tzinfo=timezone.utc)
        return expires > now


class Task(Base):
    """یک کار کپی: از کانال مبدا به کانال مقصد."""

    __tablename__ = "tasks"
    __table_args__ = (Index("ix_tasks_user_enabled", "user_id", "enabled"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)

    title: Mapped[str] = mapped_column(String(128), default="")

    source_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    source_ref: Mapped[str] = mapped_column(String(128))
    source_title: Mapped[str] = mapped_column(String(160), default="")

    dest_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    dest_ref: Mapped[str] = mapped_column(String(128))
    dest_title: Mapped[str] = mapped_column(String(160), default="")

    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    settings: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)

    copied_count: Mapped[int] = mapped_column(Integer, default=0)
    skipped_count: Mapped[int] = mapped_column(Integer, default=0)
    last_copy_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error: Mapped[str | None] = mapped_column(String(400), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    user: Mapped[User] = relationship(back_populates="tasks")
    rules: Mapped[list["Rule"]] = relationship(
        back_populates="task", cascade="all, delete-orphan", lazy="selectin"
    )


class Rule(Base):
    """قاعده‌ی متنی روی پست‌ها: جایگزینی کلمه، فیلتر، یا الگوی regex."""

    __tablename__ = "rules"

    KIND_REPLACE = "replace"          # جایگزینی کلمه/عبارت
    KIND_REGEX = "regex"              # جایگزینی با الگو
    KIND_BLOCK = "block"              # اگر بود، پست کپی نشود
    KIND_ALLOW = "allow"              # فقط اگر بود، کپی شود (لیست سفید)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    task_id: Mapped[int] = mapped_column(ForeignKey("tasks.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(16))
    pattern: Mapped[str] = mapped_column(String(512))
    replacement: Mapped[str] = mapped_column(String(512), default="")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    task: Mapped[Task] = relationship(back_populates="rules")


class ForwardProfile(Base):
    """پروفایل «فوروارد پیشرفته»: کاربر پیام را به ربات فوروارد می‌کند،
    ربات تنظیمات را اعمال کرده و در کانال مقصد می‌فرستد."""

    __tablename__ = "forward_profiles"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    title: Mapped[str] = mapped_column(String(128), default="")
    dest_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    dest_ref: Mapped[str] = mapped_column(String(128))
    dest_title: Mapped[str] = mapped_column(String(160), default="")
    is_default: Mapped[bool] = mapped_column(Boolean, default=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    settings: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class MessageMap(Base):
    """نگاشت پیام مبدا به پیام مقصد؛ برای همگام‌سازی ویرایش/حذف و جلوگیری از تکرار."""

    __tablename__ = "message_map"
    __table_args__ = (
        UniqueConstraint("task_id", "src_msg_id", name="uq_message_map_task_src"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    task_id: Mapped[int] = mapped_column(ForeignKey("tasks.id", ondelete="CASCADE"), index=True)
    src_msg_id: Mapped[int] = mapped_column(BigInteger)
    dst_msg_id: Mapped[int] = mapped_column(BigInteger)
    content_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class ActivityLog(Base):
    __tablename__ = "activity_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int | None] = mapped_column(BigInteger, index=True, nullable=True)
    task_id: Mapped[int | None] = mapped_column(Integer, index=True, nullable=True)
    level: Mapped[str] = mapped_column(String(16), default="info")
    event: Mapped[str] = mapped_column(String(64))
    detail: Mapped[str] = mapped_column(String(600), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
