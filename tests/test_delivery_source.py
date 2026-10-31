"""تست «این پست از کدام راه آمد».

<b>چرا این تفکیک لازم شد — و چرا نبودنش تقریباً ما را فریب داد.</b>

جارو تورِ ایمنی است: هر سه دقیقه سراغ مبدأها می‌رود و پستِ جامانده را
برمی‌دارد. کارش را هم درست انجام می‌دهد. مشکل دقیقاً همین است — چون
<b>بی‌صدا</b> درست کار می‌کند، وقتی جریانِ آپدیت‌های لحظه‌ای کاملاً
بمیرد هیچ‌کس خبردار نمی‌شود. پست‌ها می‌روند، فقط دیرتر. از بیرون شبیه
«سرویس کمی کند است» به نظر می‌رسد، نه شبیه یک خرابیِ اساسی.

و از روی عدد بود که لو رفت: تأخیرِ دیده‌شده (میانه ۱:۲۵، صدک۹۰ ۲:۴۷)
تقریباً <b>دقیقاً</b> همان چیزی است که یک جاروی سه‌دقیقه‌ای پیش‌بینی
می‌کند (۱:۳۰ و ۲:۴۲). یعنی احتمال قوی این بود که تقریباً همه‌ی پست‌ها
از تور ایمنی می‌آیند.

ولی «احتمال قوی» همان حدس است، و حدس‌های قبلی‌مان درست از آب درنیامدند.
این تست‌ها همان چیزی را نگه می‌دارند که جای حدس را گرفت: هر ردیفِ
تأخیر خودش می‌گوید چه چیزی خبرمان کرد.
"""
from __future__ import annotations

import pytest

from tests.test_copier import FakeClient, FakeManager, FakeMessage, _setup


def _posted(msg_id: int, *, seconds_ago: int = 5) -> FakeMessage:
    """پیامی با زمان انتشار — بدون آن، ردیف تأخیر اصلاً ساخته نمی‌شود."""
    from datetime import UTC, datetime, timedelta

    return FakeMessage(
        id=msg_id,
        message="پست",
        date=datetime.now(UTC) - timedelta(seconds=seconds_ago),
    )


async def _timings(db_module) -> list:
    from sqlalchemy import select

    from telkap.models import DeliveryTiming

    async with db_module.get_session() as db:
        return list((await db.execute(select(DeliveryTiming))).scalars())


@pytest.mark.asyncio
async def test_a_post_delivered_by_the_live_stream_says_so(tmp_path, monkeypatch):
    """<b>پیش‌فرض باید راهِ اصلی باشد.</b>

    هندلرِ آپدیت هیچ برچسبی نمی‌دهد چون راهِ عادی همان است؛ اگر
    پیش‌فرض چیز دیگری بود، سالم‌ترین حالت هم مشکوک ثبت می‌شد.
    """
    db_module, task_id = await _setup(tmp_path, monkeypatch, settings={})
    try:
        from telkap.models import DeliveryTiming
        from telkap.services.copier import Copier

        copier = Copier(FakeManager(FakeClient()))
        assert await copier.process(7, task_id, [_posted(5)])

        rows = await _timings(db_module)
        assert len(rows) == 1
        assert rows[0].via == DeliveryTiming.VIA_UPDATE
    finally:
        await db_module.close_db()


@pytest.mark.asyncio
async def test_a_post_the_sweeper_found_is_tagged_as_swept(tmp_path, monkeypatch):
    """<b>قلبِ این کار.</b>

    بدون این برچسب، پستی که جارو دو دقیقه بعد پیدا کرده از پستی که
    آپدیت در دو ثانیه آورده قابل تشخیص نیست — و آمار فقط می‌گوید «دو
    دقیقه»، بی‌آنکه بگوید چرا. همان ابهام بود که خرابیِ آپدیت‌ها را
    شش روز پنهان نگه داشت.
    """
    db_module, task_id = await _setup(tmp_path, monkeypatch, settings={})
    try:
        from telkap.models import DeliveryTiming
        from telkap.services.copier import Copier
        from telkap.services.sweeper import Sweeper
        from tests.test_sweeper import _FakeClient, _FakeManager, _mark_sent

        # جارو عمداً روی کاری که هنوز هیچ پستی نفرستاده کار نمی‌کند،
        # وگرنه بار اول کلِ آرشیو را می‌ریزد در مقصد.
        await _mark_sent(db_module, task_id, 10)

        copier = Copier(FakeManager(FakeClient()))
        manager = _FakeManager(
            _FakeClient([_posted(11, seconds_ago=150)]),
            7, -1001, [task_id],
        )
        # جارو از مدیرِ خودش مشتری می‌گیرد؛ موتور کپی از مدیرِ خودش
        # می‌فرستد — همان چیدمانِ واقعی.
        await Sweeper(manager, copier)._sweep(7, -1001)

        rows = await _timings(db_module)
        assert len(rows) == 1, "پستِ جامانده اصلاً ثبت نشد"
        assert rows[0].via == DeliveryTiming.VIA_SWEEP, (
            "جارو پست را فرستاد ولی آمار می‌گوید آپدیتِ لحظه‌ای آوردش — "
            "یعنی دقیقاً همان چیزی که این ستون برای دیدنش ساخته شد، دیده نمی‌شود"
        )
    finally:
        await db_module.close_db()


@pytest.mark.asyncio
async def test_the_sweep_share_is_the_number_that_exposes_a_dead_stream(
    tmp_path, monkeypatch
):
    """<b>یک عدد که جای آن حساب دستی را می‌گیرد.</b>"""
    db_module, task_id = await _setup(tmp_path, monkeypatch, settings={})
    try:
        from telkap.models import DeliveryTiming
        from telkap.services import timings

        async with db_module.get_session() as db:
            for index in range(9):
                db.add(DeliveryTiming(
                    task_id=task_id, user_id=7, source_msg_id=index,
                    seconds=90, via=DeliveryTiming.VIA_SWEEP,
                ))
            db.add(DeliveryTiming(
                task_id=task_id, user_id=7, source_msg_id=99,
                seconds=2, via=DeliveryTiming.VIA_UPDATE,
            ))
            await db.commit()

        data = await timings.report(days=1)
        assert data.sweep_share == 90
    finally:
        await db_module.close_db()


@pytest.mark.asyncio
async def test_waiting_the_user_asked_for_is_not_counted_as_our_slowness(
    tmp_path, monkeypatch
):
    """<b>صف تأیید تأخیرِ ما نیست.</b>

    پستی که کاربر خودش خواسته تا تأییدش نکند نرود، ممکن است ساعت‌ها
    منتظر بماند. اگر آن ساعت‌ها در «سهم جارو» بیاید، عدد رقیق می‌شود و
    یک جریانِ مرده سالم به نظر می‌رسد.
    """
    db_module, task_id = await _setup(tmp_path, monkeypatch, settings={})
    try:
        from telkap.models import DeliveryTiming
        from telkap.services import timings

        async with db_module.get_session() as db:
            db.add(DeliveryTiming(
                task_id=task_id, user_id=7, source_msg_id=1,
                seconds=90, via=DeliveryTiming.VIA_SWEEP,
            ))
            for index in range(9):
                db.add(DeliveryTiming(
                    task_id=task_id, user_id=7, source_msg_id=100 + index,
                    seconds=7200, via=DeliveryTiming.VIA_QUEUE,
                ))
            await db.commit()

        data = await timings.report(days=1)
        assert data.sweep_share == 100, "انتظارِ خواسته‌شده عدد را رقیق کرد"
    finally:
        await db_module.close_db()


@pytest.mark.asyncio
async def test_copying_an_old_archive_does_not_poison_the_speed_report(
    tmp_path, monkeypatch
):
    """<b>ردیفی که «بدترین» را به ۵۸ ساعت برده بود.</b>

    در کپیِ آرشیو، «تأخیر» برابر عمرِ خودِ پست است — چند ساعت یا چند
    ماه — و هیچ چیزی درباره‌ی سرعتِ ما نمی‌گوید. چند ردیف از این نوع
    کافی است تا آمارِ سرعت بی‌معنا شود و کسی که نگاهش می‌کند دنبال
    مشکلی بگردد که وجود ندارد.
    """
    db_module, task_id = await _setup(tmp_path, monkeypatch, settings={})
    try:
        from telkap.models import DeliveryTiming
        from telkap.services import timings

        async with db_module.get_session() as db:
            db.add(DeliveryTiming(
                task_id=task_id, user_id=7, source_msg_id=1,
                seconds=3, via=DeliveryTiming.VIA_UPDATE,
            ))
            db.add(DeliveryTiming(
                task_id=task_id, user_id=7, source_msg_id=2,
                seconds=208_845, via=DeliveryTiming.VIA_HISTORY,
            ))
            await db.commit()

        data = await timings.report(days=1)
        assert data.overall.count == 1
        assert data.overall.worst == 3, "کپی آرشیو در «بدترین» آمد"
        # ولی پاک هم نشده — در تفکیکِ راه‌ها هنوز دیده می‌شود
        assert any(
            bucket.label == DeliveryTiming.VIA_HISTORY for bucket in data.by_via
        )
    finally:
        await db_module.close_db()


@pytest.mark.asyncio
async def test_an_existing_database_gains_the_column_without_lying_about_the_past(
    tmp_path, monkeypatch
):
    """<b>دو چیز با هم: مهاجرت واقعاً اجرا شود، و گذشته را از خودش نسازد.</b>

    ستونِ تازه روی جدولِ موجود اضافه نمی‌شود مگر اینکه صریح بگوییم —
    و اگر نگوییم، سرویس روی دیتابیسِ واقعی با «no such column» بالا
    نمی‌آید؛ چیزی که در تست‌های معمولی دیده نمی‌شود، چون آن‌ها هر بار
    جدولِ تازه می‌سازند.

    و مقدارِ ردیف‌های قدیمی باید <b>خالی</b> بماند، نه «آپدیت لحظه‌ای».
    آن ردیف‌ها نمی‌دانند از کدام راه آمده‌اند؛ اگر مقدارِ خوش‌بینانه
    بگیرند، آمارِ گذشته می‌گوید جریان سالم بوده — دقیقاً همان ادعایی
    که این ستون قرار است بسنجد.
    """
    import sqlite3

    path = tmp_path / "old.db"
    old = sqlite3.connect(path)
    old.execute(
        "CREATE TABLE delivery_timings ("
        " id INTEGER PRIMARY KEY, task_id INTEGER, user_id BIGINT,"
        " source_msg_id BIGINT, published_at TIMESTAMP, seconds INTEGER,"
        " path VARCHAR(16), media_kind VARCHAR(16), size_bytes BIGINT,"
        " created_at TIMESTAMP)"
    )
    old.execute(
        "INSERT INTO delivery_timings (id, task_id, seconds, path)"
        " VALUES (1, 3, 42, 'direct')"
    )
    old.commit()
    old.close()

    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{path}")
    import telkap.config as config

    config.settings = config.load_settings()

    from telkap import db as db_module

    await db_module.init_db()
    try:
        from sqlalchemy import select

        from telkap.models import DeliveryTiming

        async with db_module.get_session() as db:
            row = await db.scalar(
                select(DeliveryTiming).where(DeliveryTiming.id == 1)
            )
        assert row is not None, "ستون اضافه نشد و خواندنِ ردیف قدیمی شکست"
        assert row.via == "", f"ردیف قدیمی ادعای {row.via!r} کرد"
    finally:
        await db_module.close_db()


def test_every_way_in_has_a_label():
    """راهی که برچسب فارسی نداشته باشد، در پنل به شکل `sweep` دیده
    می‌شود — و کسی که پنل را نگاه می‌کند معنایش را نمی‌داند."""
    from telkap.models import DeliveryTiming
    from telkap.services.copier import VIA_LABELS

    for name in (
        DeliveryTiming.VIA_UPDATE, DeliveryTiming.VIA_SWEEP,
        DeliveryTiming.VIA_RETRY, DeliveryTiming.VIA_QUEUE,
        DeliveryTiming.VIA_HISTORY,
    ):
        assert VIA_LABELS.get(name), name


# ------------------------------------------------- هشدارِ «جارو دارد می‌کشدش»


class _Silent:
    """جارویی که هر دور چیزی پیدا می‌کند — یعنی آپدیتی نمی‌رسد."""

    def __init__(self, picked: int = 3) -> None:
        self.picked = picked

    async def run_once(self) -> int:
        return self.picked


@pytest.mark.asyncio
async def test_a_stream_that_stays_silent_raises_an_alarm(monkeypatch):
    """<b>چرا هشدار لازم است و لاگ کافی نیست.</b>

    جارو از روزِ اول همین را در لاگ می‌نوشت و شش روز کسی ندید — چون
    وقتی پست‌ها می‌روند، کسی دنبال لاگ نمی‌گردد. چیزی که بی‌صدا خراب
    می‌شود باید خودش صدا کند.
    """
    from telkap.services import alerts
    from telkap.services.sweeper import SILENT_ROUNDS, Sweeper

    alerts.reset()
    said: list[str] = []

    async def fake_send(text, **kwargs):
        said.append(text)
        return 1

    monkeypatch.setattr(alerts, "send", fake_send)

    sweeper = Sweeper(object(), object())
    sweeper.run_once = _Silent().run_once

    for _ in range(SILENT_ROUNDS - 1):
        await sweeper.watch_round()
    assert not said, "زودتر از حد هشدار داد؛ یک تکِ گذرا خرابی نیست"

    await sweeper.watch_round()
    assert said, "جارو چند دور پشت سر هم سرویس را نگه داشت و کسی خبردار نشد"
    assert "آپدیت" in said[0]


@pytest.mark.asyncio
async def test_a_single_quiet_round_resets_the_suspicion(monkeypatch):
    """یک پستِ جامانده اتفاق است، نه خرابی. اگر دورِ بعد چیزی نبود،
    شمارش باید از صفر شروع شود — وگرنه هشدار در طول روز جمع می‌شود و
    به ادمین دروغ می‌گوید."""
    from telkap.services import alerts
    from telkap.services.sweeper import SILENT_ROUNDS, Sweeper

    alerts.reset()
    said: list[str] = []

    async def fake_send(text, **kwargs):
        said.append(text)
        return 1

    monkeypatch.setattr(alerts, "send", fake_send)

    sweeper = Sweeper(object(), object())
    quiet = _Silent(0)
    noisy = _Silent(2)

    for _ in range(SILENT_ROUNDS * 3):
        sweeper.run_once = noisy.run_once
        for _ in range(SILENT_ROUNDS - 1):
            await sweeper.watch_round()
        sweeper.run_once = quiet.run_once
        await sweeper.watch_round()

    assert not said
