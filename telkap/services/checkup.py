"""چرا یک کار کپی نمی‌کند.

<b>مسئله این است که «کار نمی‌کند» هیچ نشانه‌ای ندارد.</b> کاری که
مبدأش پیدا نشده، کاری که اکانتش از کانال بیرون افتاده، و کاری که
مبدأش واقعاً چیزی منتشر نکرده — هر سه از بیرون یک شکل‌اند: کانال مقصد
خالی می‌ماند. کاربر ده کار دارد و نمی‌داند کدام یکی خراب است، چه
برسد به اینکه چرا.

پس این ماژول همان چیزی را می‌پرسد که آدم دستی می‌پرسید، فقط برای همه‌ی
کارها با هم:

    اکانت وصل است؟ اشتراک فعال است؟ روی این مبدأ گوش می‌دهیم؟
    اکانت هنوز مبدأ را می‌بیند؟ در مقصد اجازه‌ی ارسال دارد؟

و برای هر مشکل، <b>کارِ بعدی</b> را هم می‌گوید. تشخیصی که نگوید چه
باید کرد، فقط نگرانی اضافه می‌کند.

<b>چرا واقعاً از تلگرام می‌پرسد و نه فقط از دیتابیس.</b> شایع‌ترین
خرابی‌ها — بیرون افتادن از کانال، سلب دسترسی ادمین، حذف شدن کانال —
هیچ‌کدام در دیتابیسِ ما ردی ندارند. تشخیصی که فقط جدول‌ها را بخواند
دقیقاً همان‌هایی را از دست می‌دهد که آدم دنبالشان است.
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select

from telkap.db import get_session
from telkap.models import Destination, RetryItem, Task, User
from telkap.services import health, subscription
from telkap.services.userbot import manager
from telkap.texts import fa_num

log = logging.getLogger(__name__)

# هر کار چند تماس با تلگرام می‌گیرد. مهلت هست تا یک کارِ گیرکرده کل
# گزارش را معطل نکند، و محدودیت هم‌زمانی هست تا ده کار با هم به
# تلگرام حمله نکنند و به محدودیت نرخ نخوریم.
TASK_TIMEOUT = 25
CONCURRENCY = 3

OK = "ok"
WARN = "warn"
BAD = "bad"


@dataclass
class TaskHealth:
    """وضعیت یک کار، با زبان آدمیزاد."""

    task_id: int
    title: str
    enabled: bool
    state: str = OK
    problems: list[str] = field(default_factory=list)
    fixes: list[str] = field(default_factory=list)
    copied: int = 0
    last_copy: datetime | None = None
    # حقایقِ خنثی — نه مشکل‌اند نه راه‌حل، ولی بدون آن‌ها نمی‌شود
    # فهمید «چیزی نیامده» یعنی خرابی یا یعنی مبدأ ساکت بوده
    notes: list[str] = field(default_factory=list)

    def fail(self, problem: str, fix: str = "") -> None:
        self.state = BAD
        self.problems.append(problem)
        if fix:
            self.fixes.append(fix)

    def warn(self, problem: str, fix: str = "") -> None:
        if self.state != BAD:
            self.state = WARN
        self.problems.append(problem)
        if fix:
            self.fixes.append(fix)


@dataclass
class Report:
    """کل تصویر: اول اکانت، بعد تک‌تک کارها."""

    account: list[str] = field(default_factory=list)
    fixes: list[str] = field(default_factory=list)
    tasks: list[TaskHealth] = field(default_factory=list)

    @property
    def healthy(self) -> int:
        return sum(1 for task in self.tasks if task.enabled and task.state == OK)

    @property
    def broken(self) -> int:
        return sum(1 for task in self.tasks if task.enabled and task.state == BAD)

    @property
    def live(self) -> int:
        return sum(1 for task in self.tasks if task.enabled)


async def check_user(user_id: int) -> Report:
    """گزارش سلامت همه‌ی کارهای یک کاربر."""
    report = Report()

    async with get_session() as db:
        person = await db.get(User, user_id)
        rows = await db.execute(
            select(Task).where(Task.user_id == user_id).order_by(Task.id)
        )
        tasks = list(rows.scalars())
        extras: dict[int, list[Destination]] = {}
        if tasks:
            found = await db.execute(
                select(Destination).where(
                    Destination.task_id.in_([task.id for task in tasks])
                )
            )
            for row in found.scalars():
                extras.setdefault(row.task_id, []).append(row)

    if person is None or not person.session_enc:
        report.account.append("اکانت کاربری وصل نیست؛ بدون آن هیچ کاری اجرا نمی‌شود.")
        report.fixes.append("در ربات «👤 حساب کاربری» ← «اتصال اکانت» را بزنید.")

    plan = await subscription.active_plan_for(user_id)
    if plan is None:
        report.account.append("اشتراک فعالی ندارید؛ کارها موقع اولین پست متوقف می‌شوند.")
        report.fixes.append("از «💳 خرید اشتراک» تمدید کنید.")

    # محدودیتِ خودِ تلگرام روی اکانت. این را از قبل ثبت کرده‌ایم و
    # مهم‌ترین علتِ «هیچ‌کدام کار نمی‌کنند» است — ولی تا امروز فقط
    # وقتی گفته می‌شد که تازه اتفاق افتاده بود.
    state = await health.state_of(user_id)
    if state != health.STATE_OK:
        report.account.append(
            "وضعیت اکانت: " + health.STATE_LABELS.get(state, state)
        )
        if state in health.FATAL_STATES:
            report.fixes.append(
                "تا این وضعیت برطرف نشود هیچ پستی ارسال نمی‌شود؛"
                " در ربات «👤 حساب کاربری» را ببینید."
            )

    # بدون کلاینت، بررسیِ تک‌تک کارها بی‌معنی است — ولی همان‌ها را هم
    # برمی‌گردانیم تا کاربر فهرستش را ببیند و بداند چرا خالی است.
    client = None
    if person is not None and person.session_enc:
        try:
            client = await asyncio.wait_for(manager.ensure_client(user_id), TASK_TIMEOUT)
        except Exception:
            # هر خطایی اینجا یعنی همان یک چیز: وصل نشد. تفکیکشان به
            # کاربر کمکی نمی‌کند و گزارش را از دست می‌دهیم.
            log.exception("اتصال اکانت %s برای بررسی سلامت ناموفق بود", user_id)
            client = None
        if client is None:
            report.account.append("اتصال به اکانت برقرار نشد.")
            report.fixes.append(
                "اگر از تلگرام «قطع سایر نشست‌ها» زده‌اید، باید دوباره وارد شوید."
            )

    gate = asyncio.Semaphore(CONCURRENCY)

    async def one(task: Task) -> TaskHealth:
        async with gate:
            try:
                return await asyncio.wait_for(
                    _check_task(user_id, task, extras.get(task.id, []), client),
                    TASK_TIMEOUT,
                )
            except TimeoutError:
                health = _blank(task)
                health.warn(
                    "بررسی این کار بیش از حد طول کشید؛ تلگرام کند پاسخ می‌دهد.",
                    "چند دقیقه بعد دوباره بررسی کنید.",
                )
                return health
            except Exception:
                log.exception("بررسی سلامت کار %s ناموفق بود", task.id)
                health = _blank(task)
                health.warn("بررسی این کار ناتمام ماند.")
                return health

    report.tasks = list(await asyncio.gather(*(one(task) for task in tasks)))
    return report


def _blank(task: Task) -> TaskHealth:
    return TaskHealth(
        task_id=task.id,
        title=task.title or task.source_title or f"کار #{task.id}",
        enabled=bool(task.enabled),
        copied=int(task.copied_count or 0),
        last_copy=task.last_copy_at,
    )


def _ago(value) -> str:
    """«چقدر پیش»، به فارسیِ خوانا."""
    from telkap.models import utcnow

    if value is None:
        return "هیچ‌وقت"
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    seconds = max(0, int((utcnow() - value).total_seconds()))
    if seconds < 90:
        return "همین حالا"
    if seconds < 3600:
        return f"{fa_num(seconds // 60)} دقیقه پیش"
    if seconds < 86_400:
        return f"{fa_num(seconds // 3600)} ساعت پیش"
    return f"{fa_num(seconds // 86_400)} روز پیش"


async def _compare_with_source(health: TaskHealth, task: Task, client, source) -> None:
    """آخرین پستِ مبدأ را با آخرین کپیِ ما می‌سنجد."""
    try:
        recent = await client.get_messages(source, limit=1)
    except Exception:
        log.debug("خواندن آخرین پست مبدأ %s نشد", task.source_ref, exc_info=True)
        return

    latest = (list(recent) or [None])[0]
    if latest is None or getattr(latest, "date", None) is None:
        health.notes.append("کانال مبدأ هیچ پستی ندارد.")
        return

    posted = latest.date
    if posted.tzinfo is None:
        posted = posted.replace(tzinfo=UTC)
    health.notes.append(f"آخرین پست مبدأ: {_ago(posted)}")
    health.notes.append(f"آخرین کپی ما: {_ago(task.last_copy_at)}")

    copied_at = task.last_copy_at
    if copied_at is not None and copied_at.tzinfo is None:
        copied_at = copied_at.replace(tzinfo=UTC)

    # چند دقیقه ارفاق، چون خودِ کپی چند ثانیه طول می‌کشد و ساعتِ
    # تلگرام و ما دقیقاً یکی نیست
    if copied_at is not None and posted <= copied_at + timedelta(minutes=2):
        return

    if copied_at is None:
        health.fail(
            "کانال مبدأ پست دارد ولی این کار تا حالا حتی یک پست کپی نکرده.",
            "با «تست» ببینید فیلترها چه می‌کنند؛ اگر تست هم چیزی نداد،"
            " کار را خاموش و روشن کنید.",
        )
        return

    # <b>این لزوماً خرابی نیست</b> و نباید طوری گفته شود که انگار
    # هست: فیلترها، تکراری بودن، یا نوع رسانه هم می‌توانند دلیلش
    # باشند. ولی کاربر باید بداند که فاصله‌ای هست.
    health.warn(
        "مبدأ بعد از آخرین کپیِ ما پست گذاشته است.",
        "اگر آن پست‌ها باید می‌رفتند، «تست» را بزنید تا ببینید کدام"
        " فیلتر جلویشان را گرفته.",
    )


async def _check_task(
    user_id: int, task: Task, extras: list[Destination], client
) -> TaskHealth:
    health = _blank(task)

    # خطای ثبت‌شده‌ی خود کار، پیش از هر چیز: این چیزی است که واقعاً
    # اتفاق افتاده، نه چیزی که ما حدس می‌زنیم.
    if task.last_error:
        health.warn("آخرین خطا: " + task.last_error[:200])

    # <b>صفِ تلاش مجدد، دیده‌نشدنی‌ترین نشانه‌ی خرابی است.</b> کار
    # روشن است، مبدا و مقصد سر جایشان‌اند، ولی هر پست پشت سر هم شکست
    # می‌خورد و در صف می‌ماند. از بیرون فقط «مقصد خالی» دیده می‌شود.
    async with get_session() as db:
        waiting = int(
            await db.scalar(
                select(func.count(RetryItem.id)).where(RetryItem.task_id == task.id)
            )
            or 0
        )
    if waiting:
        health.warn(
            f"{waiting} پست در صف تلاش مجدد مانده — یعنی ارسال‌ها شکست خورده‌اند.",
            "اگر عدد بالا می‌رود، دسترسی ارسال در کانال مقصد را بررسی کنید.",
        )

    if not task.enabled:
        health.problems.append("این کار خاموش است.")
        health.fixes.append("برای شروع، روشنش کنید.")
        return health

    if client is None:
        health.fail("تا اکانت وصل نشود، این کار اجرا نمی‌شود.")
        return health

    # ── مبدأ ────────────────────────────────────────────────────────
    #
    # «گوش دادن» و «دیده شدن» دو چیزند. ممکن است اکانت کانال را ببیند
    # ولی ما هندلری رویش نداشته باشیم (مثلاً موقع راه‌اندازی resolve
    # نشده)؛ آن‌وقت پست‌ها می‌آیند و هیچ‌جا ثبت نمی‌شوند.
    source = await manager.resolve_entity(client, task.source_ref)
    if source is None:
        health.fail(
            f"کانال مبدأ «{task.source_ref}» پیدا نشد.",
            "با اکانت متصل عضو کانال شوید، یا اگر آدرسش عوض شده کار را دوباره بسازید.",
        )
    elif getattr(source, "left", False):
        health.fail(
            "اکانت شما دیگر عضو کانال مبدأ نیست.",
            "دوباره عضو کانال مبدأ شوید.",
        )

    if not manager.is_listening(user_id, task.source_id):
        health.fail(
            "روی کانال مبدأ گوش داده نمی‌شود؛ پست‌های تازه اصلاً به ربات نمی‌رسند.",
            "کار را یک بار خاموش و روشن کنید تا دوباره وصل شود.",
        )

    # <b>نیمه‌ی گمشده‌ی هر تشخیصی.</b>
    #
    # تا امروز فقط می‌دانستیم آخرین بار کِی <b>کپی</b> کرده‌ایم. ولی
    # «سه روز است چیزی نیامده» دو معنیِ کاملاً متفاوت دارد: یا مبدأ
    # ساکت بوده، یا مبدأ پست گذاشته و ما نگرفته‌ایم. این دو از بیرون
    # یک شکل‌اند و بدون پرسیدن از تلگرام قابل تفکیک نیستند — پس هر
    # بار به حدس زدن می‌گذشت.
    if source is not None:
        await _compare_with_source(health, task, client, source)

    # ── مقصدها ──────────────────────────────────────────────────────
    targets = [(task.dest_ref, task.dest_title)]
    targets += [(row.ref, row.title) for row in extras if row.enabled]

    from telkap.services.chats import _usable_as_destination

    for ref, title in targets:
        name = title or ref
        entity = await manager.resolve_entity(client, ref)
        if entity is None:
            health.fail(
                f"کانال مقصد «{name}» پیدا نشد.",
                "با اکانت متصل عضو کانال مقصد شوید.",
            )
            continue
        if not _usable_as_destination(entity):
            health.fail(
                f"در «{name}» اجازه‌ی ارسال ندارید.",
                "اکانت متصل را در کانال مقصد ادمین کنید.",
            )

    return health
