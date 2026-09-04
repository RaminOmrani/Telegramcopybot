"""تنظیمات مشترک تست‌ها."""
from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _clear_caches():
    """کش تنظیمات بین تست‌ها پاک می‌شود.

    کش سراسری است و آیدی کارها در هر دیتابیس تازه از ۱ شروع می‌شود؛
    بدون این پاک‌سازی، یک تست می‌توانست عکس فوریِ تست قبلی را ببیند.
    """
    from telkap.services import cache

    cache.clear()
    yield
    cache.clear()
