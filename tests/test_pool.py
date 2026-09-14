"""تست استخرِ اکانت‌های سرویس.

<b>چرا این استخر هست.</b> در حالت ساده مشتری هیچ اکانتی وصل نمی‌کند؛
مبدأ عمومی را اکانت خودمان می‌خواند. با یک اکانت، بن شدنش یعنی توقفِ
همزمانِ همه‌ی مشتری‌های حالت ساده — پس از اول چند اکانت، با پخش شدنِ
بار.

<b>و چرا تستش روی حالت‌های خرابی تمرکز دارد.</b> مسیر خوش‌بینانه
(«اکانت سالم هست، مبدأ را بده») خودش را سرِ اولین اجرا نشان می‌دهد.
چیزی که ماه‌ها بی‌صدا می‌ماند این است: اکانتی بن می‌شود و مبدأهایش
<b>هیچ‌وقت</b> به اکانت دیگری نمی‌روند. پست‌ها نمی‌آیند و هیچ خطایی هم
هیچ‌جا نیست.
"""
from __future__ import annotations

import pytest

from tests.test_copier import _setup


async def _account(db_module, label: str, *, sources: int = 0, **kwargs):
    from telkap.models import ServiceAccount

    async with db_module.get_session() as db:
        account = ServiceAccount(
            label=label,
            session_enc="enc",
            sources=sources,
            **kwargs,
        )
        db.add(account)
        await db.commit()
        await db.refresh(account)
        return account.id


@pytest.mark.asyncio
async def test_a_source_goes_to_the_least_loaded_account(tmp_path, monkeypatch):
    """بار باید پخش شود، وگرنه استخر فقط اسمش استخر است."""
    db_module, _ = await _setup(tmp_path, monkeypatch, settings={})
    try:
        from telkap.services import pool

        busy = await _account(db_module, "busy", sources=9)
        free = await _account(db_module, "free", sources=1)

        chosen = await pool.lease(-100123, "@ch")
        assert chosen.id == free
        assert chosen.id != busy
    finally:
        await db_module.close_db()


@pytest.mark.asyncio
async def test_the_same_source_always_lands_on_the_same_account(tmp_path, monkeypatch):
    """<b>چرا اجاره ثابت می‌ماند.</b>

    چند اکانتِ مختلف که پشت سر هم سراغ یک کانال می‌روند، الگویی
    می‌سازند که از یک خواننده‌ی ثابت مشکوک‌تر است — و شمارشِ بار هم
    بی‌معنا می‌شود.
    """
    db_module, _ = await _setup(tmp_path, monkeypatch, settings={})
    try:
        from telkap.services import pool

        await _account(db_module, "one")
        await _account(db_module, "two")

        first = await pool.lease(-100123, "@ch")
        second = await pool.lease(-100123, "@ch")
        assert first.id == second.id
    finally:
        await db_module.close_db()


@pytest.mark.asyncio
async def test_two_tasks_sharing_a_source_do_not_lease_it_twice(tmp_path, monkeypatch):
    """اجاره مالِ <b>مبدأ</b> است نه کار؛ وگرنه یک کانال از دو اکانت
    خوانده می‌شود و شمارش هم دو برابر می‌شود."""
    db_module, _ = await _setup(tmp_path, monkeypatch, settings={})
    try:
        from telkap.models import ServiceAccount
        from telkap.services import pool

        account_id = await _account(db_module, "only")
        await pool.lease(-100123, "@ch")
        await pool.lease(-100123, "@ch")

        async with db_module.get_session() as db:
            account = await db.get(ServiceAccount, account_id)
        assert account.sources == 1
    finally:
        await db_module.close_db()


@pytest.mark.asyncio
async def test_a_banned_account_hands_its_sources_to_another(tmp_path, monkeypatch):
    """<b>خرابیِ بی‌صدایی که این تست جلویش را می‌گیرد.</b>

    اگر اجاره روی اکانتِ بسته‌شده بماند، هر مبدأیی که رویش بود برای
    همیشه ساکت می‌ماند: پست‌ها نمی‌آیند، هیچ خطایی هم هیچ‌جا نیست، و
    از بیرون شبیه «سرویس کار نمی‌کند» دیده می‌شود.
    """
    db_module, _ = await _setup(tmp_path, monkeypatch, settings={})
    try:
        from telkap.services import pool

        doomed = await _account(db_module, "doomed")
        spare = await _account(db_module, "spare")

        assert (await pool.lease(-100123, "@ch")).id == doomed

        await pool.mark_banned(doomed, "تست")

        again = await pool.lease(-100123, "@ch")
        assert again.id == spare, "مبدأ روی اکانت بسته‌شده جا ماند"
    finally:
        await db_module.close_db()


@pytest.mark.asyncio
async def test_an_account_switched_off_by_hand_also_hands_its_sources_over(
    tmp_path, monkeypatch
):
    """<b>همان خرابی، از راهی که پاک‌سازیِ خودکار نمی‌گیرد.</b>

    `mark_banned` اجاره‌ها را خودش برمی‌دارد، پس آن مسیر امن است. ولی
    ادمین می‌تواند اکانتی را از پنل خاموش کند و آن‌وقت هیچ‌چیز
    اجاره‌ها را جابه‌جا نمی‌کند — مبدأهایش ساکت می‌مانند و علتش هیچ‌جا
    نوشته نمی‌شود.

    (نسخه‌ی اولِ این تست از راه `mark_banned` می‌رفت و به همین دلیل
    <b>بدون</b> نگهبان هم سبز می‌شد؛ یعنی چیزی را که ادعا می‌کرد
    نمی‌سنجید.)
    """
    db_module, _ = await _setup(tmp_path, monkeypatch, settings={})
    try:
        from telkap.models import ServiceAccount
        from telkap.services import pool

        first = await _account(db_module, "first")
        spare = await _account(db_module, "spare")

        assert (await pool.lease(-100123, "@ch")).id == first

        # خاموش کردنِ دستی — بدون اینکه کسی اجاره‌ها را جمع کند
        async with db_module.get_session() as db:
            account = await db.get(ServiceAccount, first)
            account.enabled = False
            await db.commit()

        again = await pool.lease(-100123, "@ch")
        assert again.id == spare, "مبدأ روی اکانتِ خاموش جا ماند"

        # و شمارشِ اکانتِ خاموش هم باید کم شده باشد، وگرنه وقتی ادمین
        # دوباره روشنش کند با باری که ندارد برمی‌گردد
        async with db_module.get_session() as db:
            account = await db.get(ServiceAccount, first)
        assert account.sources == 0
    finally:
        await db_module.close_db()


@pytest.mark.asyncio
async def test_an_account_in_flood_wait_is_skipped(tmp_path, monkeypatch):
    db_module, _ = await _setup(tmp_path, monkeypatch, settings={})
    try:
        from telkap.services import pool

        flooded = await _account(db_module, "flooded")
        healthy = await _account(db_module, "healthy", sources=50)

        await pool.mark_flood(flooded, 600)

        assert (await pool.lease(-100999, "@ch")).id == healthy
    finally:
        await db_module.close_db()


@pytest.mark.asyncio
async def test_a_finished_flood_wait_brings_the_account_back(tmp_path, monkeypatch):
    """محدودیتِ موقت نباید دائمی شود؛ وگرنه استخر قطره‌قطره خالی می‌شود."""
    db_module, _ = await _setup(tmp_path, monkeypatch, settings={})
    try:
        from telkap.services import pool

        account_id = await _account(db_module, "one")
        await pool.mark_flood(account_id, 600)
        await pool.revive(account_id)

        assert (await pool.lease(-100123, "@ch")).id == account_id
    finally:
        await db_module.close_db()


@pytest.mark.asyncio
async def test_a_banned_account_never_comes_back_on_its_own(tmp_path, monkeypatch):
    """«بسته شد» با «شلوغ است» فرق دارد. برگرداندنِ خودکارِ اکانتِ
    بسته‌شده فقط باعث می‌شود هر دور دوباره به همان دیوار بخوریم."""
    db_module, _ = await _setup(tmp_path, monkeypatch, settings={})
    try:
        from telkap.models import ServiceAccount
        from telkap.services import pool

        account_id = await _account(db_module, "one")
        await pool.mark_banned(account_id, "تست")
        await pool.revive(account_id)

        async with db_module.get_session() as db:
            account = await db.get(ServiceAccount, account_id)
        assert account.state == ServiceAccount.STATE_BANNED
        assert not account.enabled
    finally:
        await db_module.close_db()


@pytest.mark.asyncio
async def test_with_no_accounts_the_failure_is_loud(tmp_path, monkeypatch):
    """<b>سکوت اینجا بدترین حالت است.</b>

    اگر نبودنِ اکانت بی‌صدا رد شود، کارهای حالت ساده هیچ‌وقت اجرا
    نمی‌شوند و از بیرون شبیه کندیِ سرویس دیده می‌شود، نه شبیه «هیچ
    خواننده‌ای نداریم».
    """
    db_module, _ = await _setup(tmp_path, monkeypatch, settings={})
    try:
        from telkap.services import pool

        with pytest.raises(pool.NoAccount):
            await pool.lease(-100123, "@ch")
    finally:
        await db_module.close_db()


@pytest.mark.asyncio
async def test_releasing_a_source_frees_the_slot(tmp_path, monkeypatch):
    db_module, _ = await _setup(tmp_path, monkeypatch, settings={})
    try:
        from telkap.models import ServiceAccount
        from telkap.services import pool

        account_id = await _account(db_module, "one")
        await pool.lease(-100123, "@ch")
        await pool.release(-100123)

        async with db_module.get_session() as db:
            account = await db.get(ServiceAccount, account_id)
        assert account.sources == 0
    finally:
        await db_module.close_db()


@pytest.mark.asyncio
async def test_capacity_says_how_much_room_is_left(tmp_path, monkeypatch):
    """<b>چرا این عدد لازم است.</b>

    ظرفیت که پر شود، کارهای تازه‌ی حالت ساده ساخته نمی‌شوند. فهمیدنش
    از روی شکایتِ مشتری، دیر است.
    """
    db_module, _ = await _setup(tmp_path, monkeypatch, settings={})
    try:
        from telkap.services import pool

        await _account(db_module, "one")
        await _account(db_module, "two")
        await pool.lease(-100123, "@ch")

        used, total = await pool.capacity()
        assert used == 1
        assert total == 2 * pool.SOURCES_PER_ACCOUNT
    finally:
        await db_module.close_db()


@pytest.mark.asyncio
async def test_an_account_without_a_session_is_not_offered(tmp_path, monkeypatch):
    """اکانتی که هنوز وارد نشده، اکانت نیست."""
    db_module, _ = await _setup(tmp_path, monkeypatch, settings={})
    try:
        from telkap.models import ServiceAccount
        from telkap.services import pool

        async with db_module.get_session() as db:
            db.add(ServiceAccount(label="empty", session_enc=None))
            await db.commit()

        with pytest.raises(pool.NoAccount):
            await pool.lease(-100123, "@ch")
    finally:
        await db_module.close_db()


def test_existing_tasks_are_not_migrated_into_the_new_mode():
    """<b>خطی که اگر اشتباه نوشته شود، همه‌ی کارهای امروز را می‌خواباند.</b>

    کارهای موجود با اکانت خودِ مشتری کار می‌کنند. اگر مهاجرت پیش‌فرض
    را «ساده» بگذارد، فردا همه‌شان سراغ رباتی می‌روند که در مقصدشان
    ادمین نیست — و همه با هم ساکت می‌شوند.
    """
    from pathlib import Path

    source = (Path(__file__).parent.parent / "telkap" / "db.py").read_text(
        encoding="utf-8"
    )
    assert '("mode", "VARCHAR(8) DEFAULT \'full\'")' in source
