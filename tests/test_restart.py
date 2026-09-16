"""تست بازیابی پس از ری‌استارت.

<b>چرا این فایل هست.</b> ربات برای هر به‌روزرسانی ری‌استارت می‌شود.
اگر بعد از ری‌استارت کارها برنگردند، هیچ نشانه‌ای وجود ندارد: کارِ
فعالی که هندلر ندارد نه خطا می‌دهد نه لاگ، و از بیرون دقیقاً شبیه
«مبدا چیزی منتشر نکرده» است. همان چیزی که مشتری آن را «ربات کار
نمی‌کند» می‌بیند.
"""
from __future__ import annotations

import asyncio

import pytest

from tests.test_copier import _setup


class _Client:
    """کلاینت ساختگی؛ فقط چیزهایی که مدیر ازش می‌خواهد."""

    def __init__(self, *, connected: bool = True) -> None:
        self._connected = connected
        self.caught_up = 0

    def is_connected(self) -> bool:
        return self._connected

    async def catch_up(self) -> None:
        self.caught_up += 1


async def _task(db_module, user_id: int, task_id_holder: list, *, source_id: int):
    from telkap.models import Task

    async with db_module.get_session() as db:
        task = Task(
            user_id=user_id,
            title="کار",
            source_ref="@s",
            source_id=source_id,
            dest_ref="@d",
            dest_id=-999,
            enabled=True,
        )
        db.add(task)
        await db.commit()
        await db.refresh(task)
        task_id_holder.append(task.id)
        return task.id


@pytest.mark.asyncio
async def test_a_live_task_with_no_handler_is_reported(tmp_path, monkeypatch):
    """<b>تنها راهِ دیدنِ اختلاف.</b>

    «سه کار فعال داریم ولی فقط یکی گوش داده می‌شود» چیزی است که باید
    دیده شود؛ بدون این شمارش، هیچ‌کس خبردار نمی‌شود.
    """
    db_module, _ = await _setup(tmp_path, monkeypatch, settings={})
    try:
        from telkap.services.userbot import UserRuntime, manager

        ids: list[int] = []
        armed_id = await _task(db_module, 7, ids, source_id=-100)
        await _task(db_module, 7, ids, source_id=-200)      # بی‌هندلر

        manager._runtimes.clear()
        runtime = UserRuntime(client=_Client())
        runtime.source_map[-100] = [armed_id]
        manager._runtimes[7] = runtime

        enabled, armed = await manager.listening_summary()

        # _setup هم یک کار می‌سازد، پس عددها را نسبی می‌سنجیم
        assert enabled >= 2
        assert armed == 1
        assert armed < enabled
    finally:
        manager._runtimes.clear()
        await db_module.close_db()


@pytest.mark.asyncio
async def test_healing_reconnects_only_what_is_broken(tmp_path, monkeypatch):
    """<b>اکانتی که برنگشته نباید تا ری‌استارت بعدی مرده بماند.</b>

    و اکانتِ سالم نباید بی‌دلیل دوباره وصل شود — وصل کردن دوباره
    توجه تلگرام را جلب می‌کند.
    """
    db_module, _ = await _setup(tmp_path, monkeypatch, settings={})
    try:
        from telkap.models import User
        from telkap.services.userbot import UserRuntime, manager

        async with db_module.get_session() as db:
            if await db.get(User, 8) is None:
                db.add(User(id=8, first_name="دومی"))
                await db.commit()

        ids: list[int] = []
        healthy_task = await _task(db_module, 7, ids, source_id=-100)
        await _task(db_module, 8, ids, source_id=-200)

        manager._runtimes.clear()
        good = UserRuntime(client=_Client())
        good.source_map[-100] = [healthy_task]
        manager._runtimes[7] = good
        # کاربر ۸ اصلاً رانتایمی ندارد — همان حالتِ «برنگشته»

        touched: list[int] = []

        async def fake_reload(user_id: int) -> int:
            touched.append(user_id)
            return 1

        monkeypatch.setattr(manager, "reload_user", fake_reload)

        fixed = await manager.heal()

        assert 8 in touched          # خرابه وصل شد
        assert 7 not in touched      # سالم دست نخورد
        assert fixed == 1
    finally:
        manager._runtimes.clear()
        await db_module.close_db()


@pytest.mark.asyncio
async def test_one_stuck_account_does_not_block_the_others(tmp_path, monkeypatch):
    """<b>قبلاً یکی‌یکی و بی‌مهلت بود.</b>

    یک اکانت که اتصالش گیر می‌کرد، جلوی بازیابیِ همه‌ی اکانت‌های بعد
    از خودش را می‌گرفت — و چون این پیش از بالا آمدن ربات انجام
    می‌شود، کلِ سرویس بالا نمی‌آمد.
    """
    db_module, _ = await _setup(tmp_path, monkeypatch, settings={})
    try:
        from telkap.models import User
        from telkap.services import userbot
        from telkap.services.userbot import manager

        async with db_module.get_session() as db:
            for uid in (8, 9):
                if await db.get(User, uid) is None:
                    db.add(User(id=uid, first_name="ک"))
            for uid in (7, 8, 9):
                person = await db.get(User, uid)
                person.session_enc = "x"
            await db.commit()

        monkeypatch.setattr(userbot, "RESTORE_TIMEOUT", 0.2)
        done: list[int] = []

        async def slow_for_eight(user_id: int) -> int:
            if user_id == 8:
                await asyncio.sleep(5)      # گیر کرده
            done.append(user_id)
            return 1

        monkeypatch.setattr(manager, "reload_user", slow_for_eight)

        restored, failed = await manager.restore_all()

        assert sorted(done) == [7, 9]       # بقیه رد شدند
        assert failed == [8]
        assert restored == 2
    finally:
        manager._runtimes.clear()
        await db_module.close_db()


@pytest.mark.asyncio
async def test_missed_posts_are_asked_for_after_handlers_are_ready(
    tmp_path, monkeypatch
):
    """<b>جدی‌ترین ایرادی که داشتیم.</b>

    تلگرام به‌روزرسانی را فقط به کلاینتِ متصل می‌فرستد. پست‌هایی که
    در همان چند ثانیه‌ی ری‌استارت منتشر می‌شدند برای همیشه گم
    می‌شدند — نه کپی، نه خطا، نه ردی در لاگ.

    و ترتیبش مهم است: خودِ مستند تلethon می‌گوید catch_up را باید
    <b>پس از</b> ثبت هندلرها صدا زد، وگرنه آنچه می‌آورد هندلری برای
    پردازش ندارد.
    """
    import inspect

    from telkap.services.userbot import UserbotManager

    source = inspect.getsource(UserbotManager.reload_user)

    assert "_catch_up" in source
    # پس از add_event_handler، نه پیش از آن
    assert source.index("add_event_handler") < source.index("await self._catch_up")


@pytest.mark.asyncio
async def test_a_slow_catch_up_is_abandoned_not_waited_on(tmp_path, monkeypatch):
    """بدون مهلت، یک کاربرِ گیرکرده جلوی بازیابیِ بقیه را می‌گرفت."""
    from telkap.services import userbot
    from telkap.services.userbot import manager

    monkeypatch.setattr(userbot, "CATCH_UP_TIMEOUT", 0.1)

    class _Slow:
        async def catch_up(self):
            await asyncio.sleep(5)

    # نباید استثنا بدهد و نباید پنج ثانیه طول بکشد
    started = asyncio.get_event_loop().time()
    await manager._catch_up(7, _Slow())
    assert asyncio.get_event_loop().time() - started < 2
