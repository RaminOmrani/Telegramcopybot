"""تست آمار سرعت انتشار.

<b>چرا این آمار مهم است.</b> «گاهی یک دقیقه، گاهی بیست دقیقه» چند
علتِ ممکن دارد و از بیرون همه یک‌شکل‌اند. تا وقتی عدد نباشد، هر
تشخیصی حدس است — و حدس‌های قبلی‌مان درست از آب درنیامدند.
"""
from __future__ import annotations

from datetime import timedelta

import pytest

from tests.test_copier import _setup


async def _rows(db_module, *samples):
    """(ثانیه، مسیر، حجم، چند روز پیش)"""
    from telkap.models import DeliveryTiming, utcnow

    async with db_module.get_session() as db:
        for seconds, path, size, ago in samples:
            db.add(
                DeliveryTiming(
                    task_id=1,
                    user_id=7,
                    source_msg_id=seconds,
                    seconds=seconds,
                    path=path,
                    size_bytes=size,
                    created_at=utcnow() - timedelta(days=ago),
                )
            )
        await db.commit()


@pytest.mark.asyncio
async def test_the_middle_matters_more_than_the_average(tmp_path, monkeypatch):
    """<b>یک ویدئوی ده‌دقیقه‌ای میانگین را می‌برد بالا.</b>

    و تصویری می‌سازد که هیچ کاربری تجربه‌اش نکرده. میانه می‌گوید
    نصفِ پست‌ها زیر چند ثانیه رسیده‌اند.
    """
    db_module, _ = await _setup(tmp_path, monkeypatch, settings={})
    try:
        from telkap.models import DeliveryTiming
        from telkap.services import timings

        direct = DeliveryTiming.PATH_DIRECT
        await _rows(
            db_module,
            (1, direct, 0, 0), (2, direct, 0, 0), (3, direct, 0, 0),
            (4, direct, 0, 0), (600, DeliveryTiming.PATH_REUPLOAD, 1 << 30, 0),
        )

        data = await timings.report(days=7)

        assert data.overall.count == 5
        assert data.overall.median == 3          # میانگین ۱۲۲ بود
        assert data.overall.worst == 600
        assert data.overall.over_minute == 1
        assert data.overall.over_minute_percent == 20
    finally:
        await db_module.close_db()


@pytest.mark.asyncio
async def test_the_paths_are_told_apart(tmp_path, monkeypatch):
    """<b>بدون تفکیک مسیر، یک عددِ کلی هیچ تصمیمی نمی‌سازد.</b>

    «مستقیم» یعنی فایل اصلاً دانلود نشده و حجمش هیچ ربطی به سرعت
    ندارد؛ بقیه یعنی دانلود و آپلود دوباره.
    """
    db_module, _ = await _setup(tmp_path, monkeypatch, settings={})
    try:
        from telkap.models import DeliveryTiming
        from telkap.services import timings

        await _rows(
            db_module,
            (2, DeliveryTiming.PATH_DIRECT, 5 << 20, 0),
            (3, DeliveryTiming.PATH_DIRECT, 900 << 20, 0),   # حجیم ولی سریع
            (400, DeliveryTiming.PATH_REUPLOAD, 900 << 20, 0),
            (500, DeliveryTiming.PATH_REUPLOAD, 900 << 20, 0),
        )

        data = await timings.report(days=7)
        by_path = {bucket.label: bucket for bucket in data.by_path}

        assert by_path[DeliveryTiming.PATH_DIRECT].median <= 3
        assert by_path[DeliveryTiming.PATH_REUPLOAD].median >= 400
        # کندترین اول می‌آید، چون همان چیزی است که باید دیده شود
        assert data.by_path[0].label == DeliveryTiming.PATH_REUPLOAD
    finally:
        await db_module.close_db()


@pytest.mark.asyncio
async def test_only_the_asked_for_window_counts(tmp_path, monkeypatch):
    db_module, _ = await _setup(tmp_path, monkeypatch, settings={})
    try:
        from telkap.models import DeliveryTiming
        from telkap.services import timings

        direct = DeliveryTiming.PATH_DIRECT
        await _rows(db_module, (5, direct, 0, 0), (900, direct, 0, 30))

        assert (await timings.report(days=7)).overall.count == 1
        assert (await timings.report(days=60)).overall.count == 2
    finally:
        await db_module.close_db()


@pytest.mark.asyncio
async def test_old_rows_are_thrown_away(tmp_path, monkeypatch):
    """جدولِ بی‌انتها فقط دیتابیس را سنگین می‌کند."""
    db_module, _ = await _setup(tmp_path, monkeypatch, settings={})
    try:
        from telkap.models import DeliveryTiming
        from telkap.services import timings

        direct = DeliveryTiming.PATH_DIRECT
        await _rows(
            db_module,
            (5, direct, 0, 1),
            (6, direct, 0, timings.KEEP_DAYS + 5),
        )

        assert await timings.prune() == 1
        assert await timings.count() == 1
    finally:
        await db_module.close_db()


@pytest.mark.asyncio
async def test_an_empty_history_does_not_break_anything(tmp_path, monkeypatch):
    """صفحه‌ی آمار در روز اول هم باید باز شود."""
    db_module, _ = await _setup(tmp_path, monkeypatch, settings={})
    try:
        from telkap.services import timings

        data = await timings.report(days=7)

        assert data.overall.count == 0
        assert data.overall.median == 0
        assert data.overall.over_minute_percent == 0
        assert data.by_path == [] and data.slowest == []
        assert await timings.daily() == []
    finally:
        await db_module.close_db()


@pytest.mark.asyncio
async def test_a_copied_post_really_writes_a_row(tmp_path, monkeypatch):
    """<b>آماری که پر نشود، بدتر از نبودنش است.</b>

    صفحه باز می‌شود، خالی است، و آدم فکر می‌کند هیچ پستی کند نبوده.
    """
    db_module, _ = await _setup(tmp_path, monkeypatch, settings={})
    try:
        from datetime import UTC, datetime

        from telkap.models import DeliveryTiming
        from telkap.services import timings
        from telkap.services.copier import Copier

        class _Msg:
            id = 42
            date = datetime.now(UTC) - timedelta(seconds=12)
            media = None
            message = "سلام"

        copier = Copier(manager=None)
        copier._last_path = DeliveryTiming.PATH_REUPLOAD
        await copier._record_latency(7, 1, _Msg())

        data = await timings.report(days=1)

        assert data.overall.count == 1
        assert 10 <= data.overall.median <= 20
        assert data.by_path[0].label == DeliveryTiming.PATH_REUPLOAD
    finally:
        await db_module.close_db()
