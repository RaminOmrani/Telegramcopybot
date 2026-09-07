"""آمار تأخیر انتشار: از مبدا تا مقصد، با عدد.

<b>چرا این ماژول هست.</b> «گاهی یک دقیقه، گاهی بیست دقیقه» چند علتِ
ممکن دارد و از بیرون همه یک‌شکل‌اند. تا وقتی عدد نباشد، هر تشخیصی
حدس است — و حدس‌های قبلی‌مان درست از آب درنیامدند.

هر ردیف می‌گوید یک پست چند ثانیه بعد از انتشار در مبدا به مقصد رسید،
از کدام مسیر رفت و چقدر حجم داشت. با همین سه چیز می‌شود پرسید «کدام
مسیر کندترین است» و «آیا حجم واقعاً ربطی دارد» — به‌جای اینکه فرض
کنیم.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import timedelta

from sqlalchemy import delete, func, select

from telkap.db import get_session
from telkap.models import DeliveryTiming, utcnow

# بیش از این نگه نمی‌داریم. آمارِ سه ماه پیش تصمیمی عوض نمی‌کند و
# جدولِ بی‌انتها فقط دیتابیس را سنگین می‌کند.
KEEP_DAYS = 60


@dataclass(slots=True)
class Bucket:
    """آمار یک گروه — کل، یک مسیر، یا یک کار."""

    label: str = ""
    count: int = 0
    median: int = 0
    p90: int = 0
    worst: int = 0
    over_minute: int = 0        # چندتا بیشتر از یک دقیقه طول کشیدند
    bytes_median: int = 0

    @property
    def over_minute_percent(self) -> int:
        return round(self.over_minute * 100 / self.count) if self.count else 0


@dataclass(slots=True)
class Report:
    days: int = 7
    overall: Bucket = field(default_factory=Bucket)
    by_path: list[Bucket] = field(default_factory=list)
    slowest: list[DeliveryTiming] = field(default_factory=list)


def _percentile(values: list[int], share: float) -> int:
    """مقدارِ صدکِ خواسته‌شده از یک فهرستِ مرتب‌شده.

    میانگین عمداً به کار نمی‌رود: یک ویدئوی ده‌دقیقه‌ای میانگین را
    می‌برد بالا و تصویری می‌سازد که هیچ کاربری تجربه‌اش نکرده.
    """
    if not values:
        return 0
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, round((len(ordered) - 1) * share)))
    return ordered[index]


def _bucket(label: str, rows: list[DeliveryTiming]) -> Bucket:
    seconds = [int(row.seconds) for row in rows]
    sizes = [int(row.size_bytes) for row in rows if row.size_bytes]
    return Bucket(
        label=label,
        count=len(rows),
        median=_percentile(seconds, 0.5),
        p90=_percentile(seconds, 0.9),
        worst=max(seconds) if seconds else 0,
        over_minute=sum(1 for value in seconds if value >= 60),
        bytes_median=_percentile(sizes, 0.5),
    )


async def report(*, days: int = 7, user_id: int | None = None, task_id: int | None = None):
    """گزارش تأخیر — کل، به تفکیک مسیر، و کندترین‌ها."""
    since = utcnow() - timedelta(days=days)
    async with get_session() as db:
        statement = select(DeliveryTiming).where(DeliveryTiming.created_at >= since)
        if user_id is not None:
            statement = statement.where(DeliveryTiming.user_id == user_id)
        if task_id is not None:
            statement = statement.where(DeliveryTiming.task_id == task_id)
        rows = list((await db.execute(statement)).scalars())

    by_path: dict[str, list[DeliveryTiming]] = {}
    for row in rows:
        by_path.setdefault(row.path, []).append(row)

    buckets = [_bucket(path, group) for path, group in by_path.items()]
    buckets.sort(key=lambda bucket: -bucket.median)

    return Report(
        days=days,
        overall=_bucket("همه", rows),
        by_path=buckets,
        slowest=sorted(rows, key=lambda row: -row.seconds)[:15],
    )


async def daily(days: int = 14, *, user_id: int | None = None) -> list[tuple[str, int]]:
    """میانه‌ی تأخیر هر روز — برای دیدنِ روند، نه یک عدد تنها."""
    since = utcnow() - timedelta(days=days)
    async with get_session() as db:
        statement = select(DeliveryTiming).where(DeliveryTiming.created_at >= since)
        if user_id is not None:
            statement = statement.where(DeliveryTiming.user_id == user_id)
        rows = list((await db.execute(statement)).scalars())

    per_day: dict[str, list[int]] = {}
    for row in rows:
        key = row.created_at.strftime("%m/%d")
        per_day.setdefault(key, []).append(int(row.seconds))
    return [(day, _percentile(values, 0.5)) for day, values in sorted(per_day.items())]


async def prune() -> int:
    """ردیف‌های کهنه‌تر از KEEP_DAYS را پاک می‌کند."""
    cutoff = utcnow() - timedelta(days=KEEP_DAYS)
    async with get_session() as db:
        result = await db.execute(
            delete(DeliveryTiming).where(DeliveryTiming.created_at < cutoff)
        )
        await db.commit()
    return int(result.rowcount or 0)


async def count() -> int:
    async with get_session() as db:
        return int(await db.scalar(select(func.count(DeliveryTiming.id))) or 0)
