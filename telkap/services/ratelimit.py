"""مدیریت ترافیک ارسال برای جلوگیری از محدود شدن توسط تلگرام.

دو لایه دارد:
  * سطل توکن به ازای هر مقصد (پیام بر دقیقه)
  * سقف ساعتی اختیاری که کاربر در تنظیمات کار تعیین می‌کند
"""
from __future__ import annotations

import asyncio
import logging
import time
from collections import defaultdict, deque

log = logging.getLogger(__name__)


class RateLimiter:
    def __init__(self, per_minute: int = 20) -> None:
        self.per_minute = max(1, per_minute)
        self._sent: dict[str, deque[float]] = defaultdict(deque)
        self._locks: dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)

    async def acquire(self, key: str) -> None:
        """تا زمانی که ارسال به این مقصد مجاز شود منتظر می‌ماند."""
        async with self._locks[key]:
            while True:
                now = time.monotonic()
                window = self._sent[key]
                while window and now - window[0] > 60:
                    window.popleft()
                if len(window) < self.per_minute:
                    window.append(now)
                    return
                wait = 60 - (now - window[0]) + 0.05
                log.debug("محدودیت ارسال برای %s؛ %.1f ثانیه صبر", key, wait)
                await asyncio.sleep(wait)


class HourlyQuota:
    """سقف تعداد پیام در ساعت به ازای هر کار (تنظیم `max_per_hour`)."""

    def __init__(self) -> None:
        self._counts: dict[int, deque[float]] = defaultdict(deque)

    def allow(self, task_id: int, limit: int) -> bool:
        if limit <= 0:
            return True
        now = time.monotonic()
        window = self._counts[task_id]
        while window and now - window[0] > 3600:
            window.popleft()
        if len(window) >= limit:
            return False
        window.append(now)
        return True

    def forget(self, task_id: int) -> None:
        self._counts.pop(task_id, None)


class DailyQuota:
    """سقف پیام در شبانه‌روز به ازای هر کاربر (از روی پلن او).

    شمارش در حافظه است تا مسیر هر پست پرس‌وجوی دیتابیس نزند، ولی مقدار
    اولیه‌ی هر روز از جدول آمار خوانده می‌شود؛ وگرنه با هر ری‌استارت
    سقف صفر می‌شد و کاربر می‌توانست دوباره از اول شروع کند.
    """

    def __init__(self) -> None:
        self._counts: dict[int, int] = {}
        self._day: str = ""

    def _roll(self, day: str) -> None:
        if day != self._day:
            self._day = day
            self._counts.clear()

    def is_seeded(self, user_id: int, day: str) -> bool:
        self._roll(day)
        return user_id in self._counts

    def seed(self, user_id: int, day: str, used: int) -> None:
        self._roll(day)
        self._counts.setdefault(user_id, max(0, used))

    def remaining(self, user_id: int, day: str, limit: int) -> int:
        """چند پیام دیگر مجاز است؛ برای نامحدود (عدد منفی) عدد بزرگ."""
        if limit < 0:
            return 1 << 30
        self._roll(day)
        return max(0, limit - self._counts.get(user_id, 0))

    def allow(self, user_id: int, day: str, limit: int) -> bool:
        """اگر جا باشد یکی به شمارنده اضافه می‌کند و True می‌دهد.

        عدد منفی یعنی نامحدود؛ صفر یعنی هیچ پیامی مجاز نیست.
        """
        if limit < 0:
            return True  # نامحدود
        self._roll(day)
        used = self._counts.get(user_id, 0)
        if used >= limit:
            return False
        self._counts[user_id] = used + 1
        return True

    def used(self, user_id: int, day: str) -> int:
        self._roll(day)
        return self._counts.get(user_id, 0)

    def forget(self, user_id: int) -> None:
        self._counts.pop(user_id, None)


hourly_quota = HourlyQuota()
daily_quota = DailyQuota()
