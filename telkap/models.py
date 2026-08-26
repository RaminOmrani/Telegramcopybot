"""مدل‌های دیتابیس."""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def utcnow() -> datetime:
    return datetime.now(UTC)


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

    # اعتبارهای خریداری‌شده‌ی جدا از اشتراک (هر واحد = یک تصویر یا یک پیام)
    watermark_credits: Mapped[int] = mapped_column(Integer, default=0)
    history_credits: Mapped[int] = mapped_column(Integer, default=0)

    # سقف‌ها و قابلیت‌های اختصاصی این کاربر که ادمین دستی تعیین کرده و
    # روی طرحش سوار می‌شود. کلید نبود یعنی «همان مقدار طرح».
    limits: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)

    # موجودی کیف پول به تومان. مرجعِ حقیقت همین ستون است و جدول
    # wallet_entries دفترِ توضیح آن؛ هر تغییر باید هر دو را بنویسد.
    wallet_toman: Mapped[int] = mapped_column(Integer, default=0)

    # چه کسی این کاربر را دعوت کرده. فقط یک بار در اولین /start بسته
    # می‌شود و هرگز بازنویسی نمی‌گردد، وگرنه پاداش قابل دزدیدن است.
    referred_by: Mapped[int | None] = mapped_column(BigInteger, nullable=True, index=True)

    # تمدید خودکار از موجودی کیف پول. عمداً پیش‌فرض خاموش است: برداشت
    # خودکار پول باید انتخاب صریح کاربر باشد، نه چیزی که سرش بیاید.
    auto_renew: Mapped[bool] = mapped_column(Boolean, default=False)

    # نمایندگی: اشتراک را با تخفیف از کیف پولش می‌خرد و برای مشتری خودش
    # فعال می‌کند. درصد تخفیف برای هر نماینده جدا تعیین می‌شود.
    is_reseller: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    reseller_discount: Mapped[int] = mapped_column(Integer, default=0)

    # سلامت اکانت کاربری متصل: ok | flood | peer_flood | banned | revoked
    account_state: Mapped[str] = mapped_column(String(16), default="ok", index=True)
    account_note: Mapped[str] = mapped_column(String(120), default="")
    account_checked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # سطح نمایش منوها: simple | pro
    # با ده‌ها گزینه، منوی کامل کاربر تازه را می‌ترساند. حالت ساده فقط
    # چیزهایی را نشان می‌دهد که واقعاً لازم‌اند و بقیه یک کلیک دورترند.
    display_level: Mapped[str] = mapped_column(String(8), default="simple")
    # خلاصه‌ی روزانه‌ی کارها
    daily_digest: Mapped[bool] = mapped_column(Boolean, default=False)

    tasks: Mapped[list[Task]] = relationship(back_populates="user", cascade="all, delete-orphan")
    subscriptions: Mapped[list[Subscription]] = relationship(
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
            expires = expires.replace(tzinfo=UTC)
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
    rules: Mapped[list[Rule]] = relationship(
        back_populates="task", cascade="all, delete-orphan", lazy="selectin"
    )
    destinations: Mapped[list[Destination]] = relationship(
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


class Destination(Base):
    """مقصد اضافی یک کار. مقصد اصلی روی خود Task است؛ این جدول
    مقصدهای دوم به بعد را نگه می‌دارد تا یک مبدا در چند کانال منتشر شود."""

    __tablename__ = "destinations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    task_id: Mapped[int] = mapped_column(ForeignKey("tasks.id", ondelete="CASCADE"), index=True)
    chat_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    ref: Mapped[str] = mapped_column(String(128))
    title: Mapped[str] = mapped_column(String(160), default="")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    # تنظیمات اختصاصی این مقصد که روی تنظیمات کار سوار می‌شود؛
    # مثلاً امضا یا فوتر متفاوت برای هر کانال
    overrides: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    task: Mapped[Task] = relationship(back_populates="destinations")


class MessageMap(Base):
    """نگاشت پیام مبدا به پیام مقصد؛ برای همگام‌سازی ویرایش/حذف و جلوگیری از تکرار.

    با پشتیبانی از چند مقصد، به ازای هر مقصد یک ردیف ثبت می‌شود.
    """

    __tablename__ = "message_map"
    __table_args__ = (
        UniqueConstraint("task_id", "src_msg_id", "dest_chat", name="uq_message_map_task_src_dest"),
        # تشخیص تکراری بودن به ازای هر پست اجرا می‌شود؛ بدون ایندکس، جدول پیمایش می‌شد
        Index("ix_message_map_dedupe", "task_id", "content_hash"),
        # همان پرسش ولی بین چند مبدا: آیا این محتوا قبلاً به این کانال رفته؟
        Index("ix_message_map_dest_dedupe", "dest_chat", "content_hash"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    task_id: Mapped[int] = mapped_column(ForeignKey("tasks.id", ondelete="CASCADE"), index=True)
    src_msg_id: Mapped[int] = mapped_column(BigInteger)
    dst_msg_id: Mapped[int] = mapped_column(BigInteger)
    dest_chat: Mapped[str] = mapped_column(String(64), default="")
    content_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # اثر انگشت هم‌ارز: بدون امضا، ایموجی و لینک — همان‌هایی که بین دو
    # نسخه‌ی یک خبر فرق می‌کنند
    norm_hash: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    # اثر انگشت شباهت؛ با تغییر کوچکِ متن کمی تغییر می‌کند
    simhash: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class RetryItem(Base):
    """پستی که ارسالش شکست خورده و باید دوباره تلاش شود.

    خود پیام ذخیره نمی‌شود؛ فقط نشانی‌اش. هنگام تلاش مجدد، پیام از
    کانال مبدا دوباره خوانده می‌شود تا محتوای تازه ارسال شود.
    """

    __tablename__ = "retry_queue"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    task_id: Mapped[int] = mapped_column(ForeignKey("tasks.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[int] = mapped_column(BigInteger, index=True)
    src_chat_id: Mapped[int] = mapped_column(BigInteger)
    src_msg_ids: Mapped[str] = mapped_column(String(400))  # آیدی‌ها با کاما
    dest_chat: Mapped[str] = mapped_column(String(64), default="")
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    next_try_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    last_error: Mapped[str] = mapped_column(String(400), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    @property
    def message_ids(self) -> list[int]:
        return [int(part) for part in self.src_msg_ids.split(",") if part.strip()]


class PendingPost(Base):
    """پستی که هنوز منتشر نشده — یا منتظر تأیید کاربر است یا منتظر ساعتش.

    مثل صف تلاش مجدد، خود پیام ذخیره نمی‌شود؛ فقط نشانی‌اش. هنگام
    انتشار دوباره از مبدا خوانده می‌شود، پس اگر پست در این فاصله ویرایش
    یا حذف شده باشد، نسخه‌ی درست منتشر می‌گردد یا اصلاً نمی‌رود.

    یک جدول برای هر دو حالت است چون سازوکارشان یکی است و فقط شرط آزاد
    شدنشان فرق می‌کند: یکی کلیک کاربر، دیگری رسیدن زمان.
    """

    __tablename__ = "pending_posts"
    __table_args__ = (
        UniqueConstraint("task_id", "src_msg_ids", name="uq_pending_once"),
    )

    REASON_APPROVAL = "approval"   # منتظر تأیید کاربر
    REASON_SCHEDULE = "schedule"   # منتظر رسیدن ساعت انتشار

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    task_id: Mapped[int] = mapped_column(ForeignKey("tasks.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[int] = mapped_column(BigInteger, index=True)
    src_chat_id: Mapped[int] = mapped_column(BigInteger)
    src_msg_ids: Mapped[str] = mapped_column(String(400))  # آیدی‌ها با کاما
    reason: Mapped[str] = mapped_column(String(16), default=REASON_APPROVAL, index=True)
    # چند کلمه از خود پست، تا کاربر در فهرست بفهمد کدام است
    preview: Mapped[str] = mapped_column(String(200), default="")
    media_kind: Mapped[str] = mapped_column(String(16), default="text")
    # برای حالت زمان‌بندی؛ در حالت تأیید خالی است
    release_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    @property
    def message_ids(self) -> list[int]:
        return [int(part) for part in self.src_msg_ids.split(",") if part.strip()]


class ForceJoinChannel(Base):
    """کانالی که کاربر پیش از استفاده از ربات باید عضو آن باشد.

    ادمین از داخل پنل هر تعداد کانال اضافه می‌کند؛ ربات باید در همه‌ی
    آن‌ها ادمین باشد تا بتواند عضویت را بررسی کند.
    """

    __tablename__ = "force_join_channels"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    ref: Mapped[str] = mapped_column(String(128), unique=True)   # username یا -100…
    title: Mapped[str] = mapped_column(String(160), default="")
    invite_link: Mapped[str] = mapped_column(String(256), default="")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    added_by: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    @property
    def url(self) -> str:
        if self.invite_link:
            return self.invite_link
        return f"https://t.me/{self.ref.lstrip('@')}"


class PaymentRequest(Base):
    """درخواست خرید اشتراک یا بسته‌ی اعتبار، با رسید کارت‌به‌کارت."""

    __tablename__ = "payment_requests"

    STATUS_PENDING = "pending"
    STATUS_APPROVED = "approved"
    STATUS_REJECTED = "rejected"

    KIND_PLAN = "plan"
    KIND_CREDIT = "credit"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    # برای خرید اشتراک کد پلن، و برای اعتبار کد بسته (wm / hist)
    plan_code: Mapped[str] = mapped_column(String(32))
    kind: Mapped[str] = mapped_column(String(16), default=KIND_PLAN)
    quantity: Mapped[int] = mapped_column(Integer, default=0)
    amount_toman: Mapped[int] = mapped_column(Integer, default=0)
    # کد تخفیف اعمال‌شده و اثرش؛ برای صورتحساب و حسابرسی نگه داشته می‌شوند
    coupon_code: Mapped[str] = mapped_column(String(32), default="")
    discount_toman: Mapped[int] = mapped_column(Integer, default=0)
    # ارزش باقی‌مانده‌ی اشتراک قبلی که هنگام ارتقا کسر شده است
    credit_toman: Mapped[int] = mapped_column(Integer, default=0)
    # قیمت فهرست پیش از هر کسری
    list_toman: Mapped[int] = mapped_column(Integer, default=0)

    receipt_file_id: Mapped[str | None] = mapped_column(String(256), nullable=True)
    receipt_kind: Mapped[str] = mapped_column(String(16), default="photo")  # photo | document | text
    note: Mapped[str] = mapped_column(String(400), default="")
    status: Mapped[str] = mapped_column(String(16), default=STATUS_PENDING, index=True)
    reviewed_by: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class DailyStat(Base):
    """آمار روزانه‌ی هر کار، برای نمایش «امروز چند پست»."""

    __tablename__ = "daily_stats"
    __table_args__ = (UniqueConstraint("task_id", "day", name="uq_daily_stat_task_day"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    task_id: Mapped[int] = mapped_column(ForeignKey("tasks.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[int] = mapped_column(BigInteger, index=True)
    day: Mapped[str] = mapped_column(String(10))  # YYYY-MM-DD به وقت UTC
    copied: Mapped[int] = mapped_column(Integer, default=0)
    skipped: Mapped[int] = mapped_column(Integer, default=0)
    failed: Mapped[int] = mapped_column(Integer, default=0)


class PeriodUsage(Base):
    """مصرف سهمیه‌های یک اشتراک، در کل دوره‌ی آن.

    سهمیه‌ها روزانه نیستند: «۲۰۰۰ پیام» یعنی ۲۰۰۰ پیام در کل ۳۰ روز. پس
    شمارنده به اشتراک گره خورده، نه به روز. با تمدید یا خرید طرح تازه یک
    اشتراک تازه ساخته می‌شود و شمارنده‌ها از صفر شروع می‌کنند.

    در دیتابیس نگه داشته می‌شود نه در حافظه، تا با ری‌استارت ربات سهمیه‌ی
    کسی صفر نشود.
    """

    __tablename__ = "period_usage"
    __table_args__ = (
        UniqueConstraint("subscription_id", "kind", name="uq_period_usage"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    subscription_id: Mapped[int] = mapped_column(
        ForeignKey("subscriptions.id", ondelete="CASCADE"), index=True
    )
    kind: Mapped[str] = mapped_column(String(16))  # messages | watermark | history
    used: Mapped[int] = mapped_column(Integer, default=0)


class ReminderState(Base):
    """جلوگیری از ارسال تکراری یادآوری انقضای اشتراک."""

    __tablename__ = "reminder_state"
    __table_args__ = (UniqueConstraint("user_id", "kind", "sub_id", name="uq_reminder"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(BigInteger, index=True)
    kind: Mapped[str] = mapped_column(String(32))
    sub_id: Mapped[int] = mapped_column(Integer)
    sent_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class SupportTicket(Base):
    """گفتگوی پشتیبانی یک کاربر.

    کاربر هرگز آیدی ادمین را نمی‌بیند؛ پیام‌ها از طریق خود ربات رد و بدل
    می‌شوند و پاسخ‌ها با عنوان «پشتیبانی» به او می‌رسد.
    """

    __tablename__ = "support_tickets"

    STATUS_OPEN = "open"
    STATUS_CLOSED = "closed"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    subject: Mapped[str] = mapped_column(String(120), default="")
    status: Mapped[str] = mapped_column(String(16), default=STATUS_OPEN, index=True)
    # آیا آخرین پیام از سمت کاربر است و هنوز جواب نگرفته؟
    awaiting_reply: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    last_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    messages: Mapped[list[SupportMessage]] = relationship(
        back_populates="ticket", cascade="all, delete-orphan", lazy="selectin"
    )


class SupportMessage(Base):
    """یک پیام درون گفتگوی پشتیبانی."""

    __tablename__ = "support_messages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    ticket_id: Mapped[int] = mapped_column(
        ForeignKey("support_tickets.id", ondelete="CASCADE"), index=True
    )
    from_admin: Mapped[bool] = mapped_column(Boolean, default=False)
    # فقط برای گزارش داخلی؛ هرگز به کاربر نشان داده نمی‌شود
    admin_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    text: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    ticket: Mapped[SupportTicket] = relationship(back_populates="messages")


class WalletEntry(Base):
    """دفتر تراکنش کیف پول: هر واریز و برداشت با علت و مرجع.

    بدون این دفتر، وقتی کاربر می‌گوید «موجودی‌ام کم شده» هیچ راهی برای
    اثبات نیست. `amount_toman` علامت‌دار است: مثبت واریز، منفی برداشت.
    `balance_after` هم ذخیره می‌شود تا بازسازی تاریخچه به جمع‌زدن کل
    جدول نیاز نداشته باشد.
    """

    __tablename__ = "wallet_entries"
    __table_args__ = (Index("ix_wallet_user_time", "user_id", "id"),)

    REASON_REFERRAL = "referral"     # پاداش دعوت
    REASON_TOPUP = "topup"           # شارژ توسط کاربر
    REASON_PURCHASE = "purchase"     # خرید از موجودی
    REASON_REFUND = "refund"         # اصلاح برداشتی که چیزی نخرید
    REASON_ADMIN = "admin"           # تنظیم دستی ادمین

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    amount_toman: Mapped[int] = mapped_column(Integer)      # علامت‌دار
    balance_after: Mapped[int] = mapped_column(Integer, default=0)
    reason: Mapped[str] = mapped_column(String(16), default=REASON_ADMIN)
    note: Mapped[str] = mapped_column(String(255), default="")
    # شناسه‌ی چیزی که این تراکنش به آن مربوط است (رسید، پاداش و…)
    ref_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    admin_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class ReferralReward(Base):
    """پاداش دعوت، از لحظه‌ی تعلق تا لحظه‌ی پرداخت.

    پاداش هنگام ثبت‌نام ساخته نمی‌شود؛ فقط وقتی خریدِ کاربرِ دعوت‌شده
    تأیید شود. قید یکتا جلوی پرداخت دوباره برای یک رسید را می‌گیرد.
    """

    __tablename__ = "referral_rewards"
    __table_args__ = (
        UniqueConstraint("payment_id", name="uq_referral_payment"),
        Index("ix_referral_referrer", "referrer_id", "id"),
    )

    STATUS_PAID = "paid"
    STATUS_VOID = "void"          # رد شده: زیر حداقل خرید، سقف پر، یا خاموش

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    referrer_id: Mapped[int] = mapped_column(BigInteger, index=True)
    referred_id: Mapped[int] = mapped_column(BigInteger, index=True)
    payment_id: Mapped[int] = mapped_column(Integer)
    # مبلغ خریدی که پاداش از آن حساب شده، برای حسابرسی بعدی
    basis_toman: Mapped[int] = mapped_column(Integer, default=0)
    amount_toman: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(16), default=STATUS_PAID, index=True)
    note: Mapped[str] = mapped_column(String(255), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Coupon(Base):
    """کد تخفیف. یک کد، چند قانون: چه کسی، چند بار، روی کدام طرح، تا کی."""

    __tablename__ = "coupons"

    KIND_PERCENT = "percent"
    KIND_FIXED = "fixed"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    code: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    kind: Mapped[str] = mapped_column(String(16), default=KIND_PERCENT)
    value: Mapped[int] = mapped_column(Integer, default=0)       # درصد یا تومان

    max_uses: Mapped[int] = mapped_column(Integer, default=0)    # ۰ = بی‌نهایت
    used_count: Mapped[int] = mapped_column(Integer, default=0)
    per_user_limit: Mapped[int] = mapped_column(Integer, default=1)

    # فقط روی این طرح‌ها؛ خالی یعنی همه
    plan_codes: Mapped[list] = mapped_column(JSON, default=list)
    min_toman: Mapped[int] = mapped_column(Integer, default=0)

    expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    note: Mapped[str] = mapped_column(String(160), default="")
    created_by: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class CouponUse(Base):
    """هر بار استفاده از یک کد؛ برای سقف «هر کاربر چند بار» و گزارش کمپین."""

    __tablename__ = "coupon_uses"
    __table_args__ = (Index("ix_coupon_use_pair", "coupon_id", "user_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    coupon_id: Mapped[int] = mapped_column(
        ForeignKey("coupons.id", ondelete="CASCADE"), index=True
    )
    user_id: Mapped[int] = mapped_column(BigInteger, index=True)
    payment_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    discount_toman: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class GiftCode(Base):
    """کد هدیه یا پیش‌فروش: یک بار مصرف، یک طرح مشخص.

    برخلاف کد تخفیف که روی قیمت اثر می‌گذارد، این کد خودش اشتراک است —
    کاربر واردش می‌کند و طرح بدون پرداخت فعال می‌شود.
    """

    __tablename__ = "gift_codes"
    __table_args__ = (Index("ix_gift_batch", "batch"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    code: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    plan_code: Mapped[str] = mapped_column(String(32))
    batch: Mapped[str] = mapped_column(String(32), default="")
    note: Mapped[str] = mapped_column(String(160), default="")

    used_by: Mapped[int | None] = mapped_column(BigInteger, nullable=True, index=True)
    used_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_by: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class ResellerSale(Base):
    """یک فروش نمایندگی: نماینده از کیف پولش خرید و برای مشتری فعال کرد.

    قیمت فهرست هم ذخیره می‌شود تا بعداً معلوم باشد تخفیف در لحظه‌ی فروش
    چقدر بوده، حتی اگر قیمت‌ها یا درصد تخفیف بعداً عوض شوند.
    """

    __tablename__ = "reseller_sales"
    __table_args__ = (Index("ix_reseller_sales_seller", "reseller_id", "id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    reseller_id: Mapped[int] = mapped_column(BigInteger, index=True)
    customer_id: Mapped[int] = mapped_column(BigInteger, index=True)
    plan_code: Mapped[str] = mapped_column(String(32))
    paid_toman: Mapped[int] = mapped_column(Integer, default=0)       # آنچه نماینده داد
    list_toman: Mapped[int] = mapped_column(Integer, default=0)       # قیمت فهرست
    discount_percent: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class PlanOverride(Base):
    """تغییرهای ادمین روی یک طرح.

    مقادیر پیش‌فرض در `plans.py` می‌مانند و اینجا فقط تفاوت‌ها ذخیره
    می‌شوند؛ پس پاک کردن یک ردیف، طرح را به حالت کارخانه برمی‌گرداند.
    """

    __tablename__ = "plan_overrides"

    code: Mapped[str] = mapped_column(String(32), primary_key=True)
    data: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    updated_by: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class AppSetting(Base):
    """تنظیم عمومیِ قابل ویرایش از پنل ادمین (قیمت اعتبار، سقف‌ها و…)."""

    __tablename__ = "app_settings"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[Any] = mapped_column(JSON, default=None)
    updated_by: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class ActivityLog(Base):
    __tablename__ = "activity_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int | None] = mapped_column(BigInteger, index=True, nullable=True)
    task_id: Mapped[int | None] = mapped_column(Integer, index=True, nullable=True)
    level: Mapped[str] = mapped_column(String(16), default="info")
    event: Mapped[str] = mapped_column(String(64))
    detail: Mapped[str] = mapped_column(String(600), default="")
    # چه کسی این کار را کرد. برای کارهای ادمین با user_id (که هدفِ کار است)
    # فرق دارد؛ بدون این ستون، لاگ حسابرسی قابل اتکا نیست.
    actor_id: Mapped[int | None] = mapped_column(BigInteger, index=True, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)


class AdminRole(Base):
    """نقش هر ادمین. ادمین‌های داخل .env همیشه دسترسی کامل دارند.

    وقتی کسی را برای پشتیبانی می‌آورید، نباید بتواند قیمت‌ها را عوض کند.
    """

    __tablename__ = "admin_roles"

    ROLE_OWNER = "owner"        # همه‌چیز
    ROLE_FINANCE = "finance"    # پرداخت، طرح‌ها، کدهای تخفیف، گزارش درآمد
    ROLE_SUPPORT = "support"    # تیکت‌ها، کاربران، پیام همگانی

    user_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False)
    role: Mapped[str] = mapped_column(String(16), default=ROLE_SUPPORT)
    note: Mapped[str] = mapped_column(String(120), default="")
    added_by: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class ChurnFeedback(Base):
    """چرا کاربر تمدید نکرد.

    ارزشمندترین داده‌ای که می‌شود جمع کرد و ارزان‌ترین راه گرفتنش: یک
    سؤال تک‌دکمه‌ای در همان پیام انقضا.
    """

    __tablename__ = "churn_feedback"
    __table_args__ = (UniqueConstraint("user_id", "sub_id", name="uq_churn_once"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(BigInteger, index=True)
    sub_id: Mapped[int] = mapped_column(Integer)
    reason: Mapped[str] = mapped_column(String(32), index=True)
    note: Mapped[str] = mapped_column(String(400), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
