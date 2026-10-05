"""تست مرورِ عملکرد.

<b>چرا یک ابزارِ گزارش هم تست می‌خواهد.</b>

گزارشی که عدد اشتباه بدهد از نبودنش بدتر است: بر اساسش تصمیم گرفته
می‌شود. و خطایش بی‌صداست — هیچ‌کس «۹۸٪ موفقیت» را نمی‌رود دستی
بشمارد.

<b>و سخت‌ترینِ اینها، روزِ خالی است.</b> روزی که هیچ پستی نرفته یعنی
قطعی؛ همان قطعی‌ای که کسی گزارشش نکرده چون مشتری فقط فکر کرده مبدأ
ساکت بوده. اگر گزارش هم نگویدش، برای همیشه نادیده می‌ماند.
"""
from __future__ import annotations

import importlib.util
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from tests.test_copier import _setup

REVIEW = Path(__file__).parent.parent / "tools" / "review.py"


def _load():
    spec = importlib.util.spec_from_file_location("review_tool", REVIEW)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_median_is_used_not_the_mean():
    """<b>میانگین در اینجا تقریباً همیشه دروغ می‌گوید.</b>

    یک ویدیوی سنگین که ۲۰ دقیقه طول کشیده، میانگینِ هزار پستِ
    یک‌ثانیه‌ای را بالا می‌برد و گزارش می‌شود «سرویس کند است». میانه
    آن را نشان نمی‌دهد و p95 خودِ دنباله را جدا می‌گوید.
    """
    review = _load()
    one_slow = [1.0] * 99 + [1200.0]
    got = review._percentiles(one_slow)

    assert got["p50"] == 1.0, "یک پستِ کند، عددِ اصلی را خراب کرد"
    assert got["max"] == 1200.0, "دنباله گم شد"


def test_percentiles_of_nothing_do_not_explode():
    assert _load()._percentiles([]) == {}


def test_a_share_of_zero_is_not_a_division_error():
    """روز اول، همه‌ی مخرج‌ها صفرند."""
    review = _load()
    assert review._pct(0, 0) == "--"
    assert review._bar(0, 0).strip() == ""


def test_the_report_is_pure_ascii_where_it_counts():
    """<b>کنسول VNC فارسی را نشان نمی‌دهد.</b>

    عنوان‌ها و برچسب‌ها باید لاتین بمانند. متنِ خطاهایی که از دیتابیس
    می‌آید ناگزیر فارسی است و با <code>--send</code> در تلگرام خوانده
    می‌شود؛ ولی اسکلتِ گزارش باید روی همان کنسول خوانا باشد.
    """
    source = REVIEW.read_text(encoding="utf-8")
    labels = [
        line for line in source.splitlines()
        if "out(" in line and '"' in line and not line.strip().startswith("#")
    ]
    persian = [
        line for line in labels
        if any("؀" <= ch <= "ۿ" for ch in line)
    ]
    assert not persian, f"برچسبِ فارسی در خروجی: {persian[:3]}"


@pytest.mark.asyncio
async def test_a_day_with_no_posts_at_all_is_reported(tmp_path, monkeypatch, capsys):
    """<b>قطعی‌ای که کسی ندیده.</b>

    اگر ربات یک روز پایین باشد، مشتری فقط فکر می‌کند مبدأ ساکت بوده و
    چیزی نمی‌گوید. تنها ردش همین سوراخ در آمار روزانه است.
    """
    db_module, task_id = await _setup(tmp_path, monkeypatch, settings={})
    try:
        from telkap.models import DailyStat

        today = datetime.now(UTC).date()
        async with db_module.get_session() as db:
            for back in (4, 3, 1, 0):          # روزِ «۲ روز پیش» عمداً نیست
                day = today - timedelta(days=back)
                db.add(DailyStat(
                    task_id=task_id, user_id=7, day=f"{day:%Y-%m-%d}",
                    copied=10, skipped=0, failed=0,
                ))
            await db.commit()

        review = _load()
        monkeypatch.setattr(review.sys, "argv", ["review.py"])
        await review.main()

        printed = capsys.readouterr().out
        missing = today - timedelta(days=2)
        assert "day(s) with NO activity" in printed, "سوراخِ آمار گزارش نشد"
        assert f"{missing:%Y-%m-%d}" in printed, "روزِ درست نشان داده نشد"
    finally:
        await db_module.close_db()


@pytest.mark.asyncio
async def test_the_success_rate_counts_attempts_not_days(tmp_path, monkeypatch, capsys):
    """نرخ موفقیت باید «کپی‌شده از تلاش‌ها» باشد.

    اگر ردشده‌ها (فیلتر، تکراری، خارج از ساعت) هم در مخرج بیایند، نرخ
    با سخت‌تر شدنِ فیلترهای مشتری پایین می‌آید — یعنی عددی که قرار بود
    سلامتِ سرویس را بگوید، به تنظیماتِ او وصل می‌شود.
    """
    db_module, task_id = await _setup(tmp_path, monkeypatch, settings={})
    try:
        from telkap.models import DailyStat

        async with db_module.get_session() as db:
            db.add(DailyStat(
                task_id=task_id, user_id=7,
                day=f"{datetime.now(UTC).date():%Y-%m-%d}",
                copied=90, skipped=500, failed=10,
            ))
            await db.commit()

        review = _load()
        monkeypatch.setattr(review.sys, "argv", ["review.py"])
        await review.main()

        printed = capsys.readouterr().out
        assert "SUCCESS RATE: 90.0%" in printed, (
            "ردشده‌ها در مخرج آمدند و نرخ را به تنظیماتِ مشتری وصل کردند"
        )
    finally:
        await db_module.close_db()


@pytest.mark.asyncio
async def test_an_empty_database_still_produces_a_report(tmp_path, monkeypatch, capsys):
    """<b>روزِ اول هم باید کار کند.</b>

    ابزاری که روی دیتابیس خالی بترکد، دقیقاً وقتی لازم است که
    نمی‌دانیم چیزی ثبت شده یا نه.
    """
    db_module, _ = await _setup(tmp_path, monkeypatch, settings={})
    try:
        review = _load()
        monkeypatch.setattr(review.sys, "argv", ["review.py"])
        assert await review.main() == 0

        printed = capsys.readouterr().out
        assert "performance review" in printed
        assert "no delivery attempts recorded" in printed
    finally:
        await db_module.close_db()
