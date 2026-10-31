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
        assert "SUCCESS RATE, whole window : 90.0%" in printed, (
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


def test_error_text_is_grouped_by_shape_not_by_its_numbers():
    """«A wait of 31 seconds» و «A wait of 287 seconds» یک خرابی‌اند.

    اگر عینِ متن شمرده شود، هر کدام یک ردیفِ «۱x» می‌شود و الگویی که
    هزار بار تکرار شده هیچ‌جا به چشم نمی‌آید.
    """
    review = _load()
    assert review._shape("-1001234: A wait of 31 seconds") == review._shape(
        "-1009999: A wait of 287 seconds"
    )
    assert review._shape(None) == "(no reason recorded)"


@pytest.mark.asyncio
async def test_send_failures_show_up_with_their_reason(tmp_path, monkeypatch, capsys):
    """<b>اشکالی که اولین اجرای واقعی نشانش داد.</b>

    شکستِ ارسال با نام‌های retry_* در لاگ ثبت می‌شود، ولی گزارش فقط
    skip/error/pause را می‌خواند. روزی که ۸۶ پست نرفت، بخشِ «چه خراب
    شد» فقط «پست تکراری» نشان داد — دلیلِ واقعی دقیقاً همان‌جا که
    لازمش داشتیم غایب بود.
    """
    db_module, task_id = await _setup(tmp_path, monkeypatch, settings={})
    try:
        from telkap.models import ActivityLog

        async with db_module.get_session() as db:
            for seconds in (31, 45, 287):
                db.add(ActivityLog(
                    user_id=7, task_id=task_id, event="retry_queued",
                    detail=f"-1002: A wait of {seconds} seconds is required",
                ))
            db.add(ActivityLog(
                user_id=7, task_id=task_id, event="skip", detail="پست تکراری",
            ))
            await db.commit()

        review = _load()
        monkeypatch.setattr(review.sys, "argv", ["review.py"])
        await review.main()

        printed = capsys.readouterr().out
        assert "A wait of # seconds" in printed, "دلیلِ شکست در گزارش نیامد"
        assert "3x" in printed, "سه شکستِ هم‌شکل جدا جدا شمرده شدند"
        failed_part = printed.split("why posts FAILED")[1].split("why posts were SKIPPED")[0]
        assert "تکراری" not in failed_part, "ردشده در فهرستِ شکست‌ها آمد"
    finally:
        await db_module.close_db()


@pytest.mark.asyncio
async def test_an_old_fixed_incident_does_not_bury_how_it_is_now(
    tmp_path, monkeypatch, capsys
):
    """<b>«۵۶٪ موفقیت» که در واقع ۹۸٪ بود.</b>

    ۹۷٪ همه‌ی شکست‌های اولین گزارشِ واقعی مالِ پنج روز در اول سپتامبر
    بود — حلقه‌ای که بعداً بسته شد. عددِ کلِ بازه آن را با امروز قاطی
    می‌کرد و نتیجه، تصویری بود که هیچ ربطی به وضع فعلی نداشت.
    """
    db_module, task_id = await _setup(tmp_path, monkeypatch, settings={})
    try:
        from telkap.models import DailyStat

        today = datetime.now(UTC).date()
        async with db_module.get_session() as db:
            old = today - timedelta(days=40)
            db.add(DailyStat(task_id=task_id, user_id=7, day=f"{old:%Y-%m-%d}",
                             copied=50, skipped=0, failed=5000))
            for back in range(0, 5):
                day = today - timedelta(days=back)
                db.add(DailyStat(task_id=task_id, user_id=7, day=f"{day:%Y-%m-%d}",
                                 copied=200, skipped=0, failed=1))
            await db.commit()

        review = _load()
        monkeypatch.setattr(review.sys, "argv", ["review.py", "--days", "90"])
        await review.main()

        printed = capsys.readouterr().out
        assert "last 7 days  : 99.5%" in printed, "وضعِ فعلی جدا نشان داده نشد"
        assert "old incident" in printed, "بخش ۸ حادثه‌ی قدیمی را خرابیِ امروز خواند"
    finally:
        await db_module.close_db()
