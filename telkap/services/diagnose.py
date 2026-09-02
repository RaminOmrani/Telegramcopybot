"""چرا این کار پست‌ها را نمی‌زند.

<b>مسئله‌ای که حل می‌کند.</b> «بعضی پست‌ها را می‌زند و بعضی را نه»
از بیرون یک چیز به نظر می‌رسد، ولی ده علتِ کاملاً متفاوت دارد: فیلتر
رد کرده، سقف ساعتی پر شده، سهمیه‌ی پیام تمام شده، پست تکراری بوده،
خارج از ساعت فعال رسیده، در صف تأیید مانده، یا کار اصلاً خاموش شده.

<b>و همه‌ی این‌ها از قبل ثبت می‌شدند.</b> هر مسیرِ ردکردن در
<code>copier.py</code> یک رکورد با دلیلش در لاگ فعالیت می‌گذارد.
چیزی که نبود، راهی برای <b>دیدنشان</b> بود — پس کاربر فقط می‌دید
«نزد» و ما فقط می‌توانستیم حدس بزنیم.

<b>چرا این برای این محصول حیاتی است.</b> کل وعده‌ی ربات این است که
چیزی را از دست نمی‌دهید. کاربری که یک بار پستِ نرسیده ببیند و
توضیحی نگیرد، دیگر اعتماد نمی‌کند — حتی اگر رد شدنِ آن پست خواسته‌ی
خودش بوده باشد. پس پاسخ باید <b>همیشه</b> در دسترس باشد، نه فقط
وقتی چیزی خراب است.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import timedelta

from sqlalchemy import func, select

from telkap.db import get_session
from telkap.models import ActivityLog, DailyStat, MessageMap, PendingPost, Task, utcnow
from telkap.plans import FEAT_MESSAGES
from telkap.services import cache, entitlement, subscription

log = logging.getLogger(__name__)

# پنجره‌ای که گزارش رویش ساخته می‌شود. یک روز آن‌قدر هست که الگو
# دیده شود و آن‌قدر کوتاه که به وضعیتِ الان مربوط باشد.
WINDOW_HOURS = 24

# برچسب‌های خواناتر برای دلیل‌هایی که در لاگ ثبت می‌شوند. متن خودِ
# لاگ هم قابل فهم است، ولی این‌ها می‌گویند «چه کاری از دستتان
# برمی‌آید»، که همان چیزی است که کاربر می‌خواهد.
ADVICE = {
    "سقف ساعتی پر شده است": "سقف ساعتی این کار را بالا ببرید یا صفر کنید.",
    "پست تکراری": "اگر می‌خواهید تکراری‌ها هم بروند، «رد کردن تکراری» را خاموش کنید.",
    "خارج از ساعت فعال کار": "بازه‌ی ساعت فعال کار را گسترده‌تر کنید.",
}


@dataclass
class Reason:
    detail: str
    count: int

    @property
    def advice(self) -> str:
        return ADVICE.get(self.detail, "")


@dataclass
class Report:
    """تصویر کاملِ وضعیت یک کار."""

    task_id: int
    title: str
    enabled: bool
    last_error: str = ""
    sent: int = 0                 # در پنجره
    skipped: int = 0              # در پنجره
    total_copied: int = 0         # از ابتدا
    reasons: list[Reason] = field(default_factory=list)
    waiting: int = 0              # در صف تأیید یا زمان‌بندی
    quota_left: int | None = None  # None یعنی نامحدود
    subscription_days: int = 0
    problems: list[str] = field(default_factory=list)

    # ── آنچه از خودِ تلگرام پرسیده شد ──────────────────────────────
    connected: bool = False        # اکانت کاربری وصل است
    listening: bool = False        # روی این کانال گوش می‌دهیم
    source_last_id: int = 0        # تازه‌ترین پستِ مبدا
    our_last_id: int = 0           # تازه‌ترین پستی که ما دیدیم
    probe_error: str = ""

    @property
    def missed(self) -> int:
        """چند پستِ مبدا هرگز به ما نرسید.

        <b>این عدد جای یک حدس را می‌گیرد.</b> پیش از این، «هیچ رکوردی
        نداریم» را «مبدا چیزی منتشر نکرده» ترجمه می‌کردیم — که
        دانستنی نبود و در عمل غلط از آب درآمد.
        """
        if not self.source_last_id or not self.our_last_id:
            return 0
        return max(0, self.source_last_id - self.our_last_id)

    @property
    def healthy(self) -> bool:
        return self.enabled and not self.problems


async def _reasons(task_id: int, since) -> list[Reason]:
    """دلیل‌های رد شدن در پنجره، پرتکرارترین اول.

    <b>این‌ها از قبل ثبت می‌شدند.</b> هر مسیرِ ردکردن در copier یک
    رکورد با دلیلش می‌گذارد؛ چیزی که نبود راهی برای دیدنشان بود.
    """
    async with get_session() as db:
        rows = await db.execute(
            select(ActivityLog.detail, func.count())
            .where(
                ActivityLog.task_id == task_id,
                ActivityLog.event == "skip",
                ActivityLog.created_at >= since,
            )
            .group_by(ActivityLog.detail)
            .order_by(func.count().desc())
            .limit(8)
        )
        return [Reason(detail=detail or "—", count=count) for detail, count in rows]


async def task_report(task_id: int) -> Report | None:
    """گزارش کامل یک کار. None یعنی کار پیدا نشد."""
    async with get_session() as db:
        task = await db.get(Task, task_id)
        if task is None:
            return None
        copied = await db.scalar(
            select(func.count())
            .select_from(MessageMap)
            .where(MessageMap.task_id == task_id)
        )
        # پستِ آزادشده از صف حذف می‌شود، پس هر ردیفی که هست یعنی
        # هنوز منتظر است.
        queued = await db.scalar(
            select(func.count())
            .select_from(PendingPost)
            .where(PendingPost.task_id == task_id)
        )

    since = utcnow() - timedelta(hours=WINDOW_HOURS)
    reasons = await _reasons(task_id, since)
    sent, skipped = await _today(task_id)

    report = Report(
        task_id=task.id,
        title=task.title or task.source_title or task.source_ref,
        enabled=bool(task.enabled),
        last_error=(task.last_error or "").strip(),
        sent=sent,
        skipped=skipped,
        total_copied=int(copied or 0),
        reasons=reasons,
        waiting=int(queued or 0),
    )

    await _probe(report, task)
    await _add_problems(report, task)
    return report


async def _probe(report: Report, task: Task) -> None:
    """از خودِ تلگرام می‌پرسد، به‌جای اینکه حدس بزند.

    <b>چرا این بخش اضافه شد.</b> گزارش قبلی وقتی هیچ رکوردی نداشت
    می‌گفت «مبدا چیزی منتشر نکرده» — ولی این را نمی‌دانست، فقط
    نمی‌دیدش. و دقیقاً همان‌جا اشتباه می‌کرد: کانال نُه پست زده بود و
    هیچ‌کدام به ربات نرسیده بود.

    حالا سه چیز <b>پرسیده</b> می‌شود: اکانت وصل است؟ روی این کانال
    گوش می‌دهیم؟ و تازه‌ترین پست مبدا چند است در برابر تازه‌ترین پستی
    که دیده‌ایم؟ اختلافِ آن دو، تعداد پست‌های گم‌شده است.
    """
    from telkap.services.userbot import manager

    report.connected = manager.is_connected(task.user_id)
    report.listening = manager.is_listening(task.user_id, task.source_id)

    async with get_session() as db:
        report.our_last_id = int(
            await db.scalar(
                select(func.max(MessageMap.src_msg_id)).where(
                    MessageMap.task_id == task.id
                )
            )
            or 0
        )

    if task.source_kind == Task.SOURCE_RSS:
        return          # فید مبدا تلگرامی ندارد که آخرین پستش را بپرسیم

    client = manager.get_client(task.user_id)
    if client is None or not report.connected:
        return
    try:
        target = task.source_id or task.source_ref
        latest = await client.get_messages(target, limit=1)
        if latest:
            report.source_last_id = int(latest[0].id)
    except Exception as exc:                     # noqa: BLE001
        # نرسیدن به مبدا خودش یک یافته است — مثلاً اکانت عضو کانال
        # نیست و برای همین هیچ به‌روزرسانی‌ای نمی‌گیرد.
        report.probe_error = str(exc)[:200]
        log.info("خواندن مبدا کار %s ممکن نشد: %s", task.id, exc)


async def _add_problems(report: Report, task: Task) -> None:
    """چیزهایی که خودِ کاربر باید بداند، به ترتیب اهمیت.

    <b>ترتیب عمدی است.</b> اولی چیزی است که اگر درست نشود بقیه فرقی
    نمی‌کنند — کارِ خاموش هیچ پستی نمی‌زند، هرچقدر هم سهمیه داشته
    باشید.
    """
    if not report.enabled:
        reason = report.last_error or "کسی خاموشش کرده"
        report.problems.append(f"این کار خاموش است — {reason}")

    ent = await cache.get_entitlement(task.user_id)
    if ent.plan is None:
        report.problems.append(
            "اشتراک فعالی ندارید، پس هیچ کاری اجرا نمی‌شود."
        )
    else:
        report.subscription_days = await subscription.remaining_days(task.user_id)

    left = await _quota_left(ent)
    report.quota_left = left
    if left is not None and left <= 0:
        report.problems.append(
            "سهمیه‌ی پیام این دوره تمام شده — تا تمدید، پستی فرستاده نمی‌شود."
        )

    # ── یافته‌های پرسش زنده ────────────────────────────────────────
    if report.enabled:
        if not report.connected:
            report.problems.append(
                "اکانت کاربری وصل نیست، پس هیچ پیامی از مبدا نمی‌رسد."
            )
        elif not report.listening:
            report.problems.append(
                "روی کانال مبدا گوش نمی‌دهیم — پست‌ها می‌آیند ولی به این "
                "کار نمی‌رسند. «🔄 راه‌اندازی دوباره» را بزنید."
            )
        if report.probe_error:
            report.problems.append(
                f"خواندن کانال مبدا ممکن نشد: {report.probe_error}\n"
                "معمولاً یعنی اکانت شما عضو آن کانال نیست."
            )
        elif report.missed:
            report.problems.append(
                f"حدود {report.missed} پست از مبدا هرگز به ربات نرسیده "
                "(تازه‌ترین پست مبدا از تازه‌ترین پستی که دیده‌ایم جلوتر است)."
            )

    snapshot = await cache.get_task(task.id)
    if snapshot is not None and not snapshot.targets:
        report.problems.append("این کار هیچ مقصد فعالی ندارد.")

    if report.waiting:
        report.problems.append(
            f"{report.waiting} پست در صف مانده — از «⏳ در انتظار تأیید» رهایشان کنید."
        )

    if report.last_error and report.enabled:
        report.problems.append(f"آخرین خطا: {report.last_error}")


async def _today(task_id: int) -> tuple[int, int]:
    """(کپی‌شده، ردشده) امروز. آمار روزانه از قبل نگه داشته می‌شود."""
    from telkap.services.copier import today_key

    async with get_session() as db:
        row = await db.scalar(
            select(DailyStat).where(
                DailyStat.task_id == task_id, DailyStat.day == today_key()
            )
        )
    if row is None:
        return 0, 0
    return int(row.copied or 0), int(row.skipped or 0)


async def _quota_left(ent) -> int | None:
    """سهمیه‌ی باقی‌مانده‌ی پیام. None یعنی نامحدود یا نامعلوم."""
    if ent.plan is None:
        return None
    try:
        left = await entitlement.quota_left(
            ent.subscription_id, FEAT_MESSAGES, ent.plan
        )
    except Exception:
        log.debug("خواندن سهمیه ناموفق بود", exc_info=True)
        return None
    # عدد بزرگ یعنی نامحدود؛ نشان دادنش به کاربر گیج‌کننده است
    return None if left >= entitlement.BIG else int(left)


async def user_reports(user_id: int) -> list[Report]:
    """گزارش همه‌ی کارهای یک کاربر."""
    async with get_session() as db:
        rows = await db.execute(
            select(Task.id).where(Task.user_id == user_id).order_by(Task.id)
        )
        ids = [row for (row,) in rows]

    reports = []
    for task_id in ids:
        report = await task_report(task_id)
        if report is not None:
            reports.append(report)
    return reports
