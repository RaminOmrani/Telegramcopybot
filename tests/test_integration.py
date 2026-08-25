"""تست‌های یکپارچه: دیتابیس، اشتراک، واترمارک و محدودکننده‌ی نرخ."""
from __future__ import annotations

import asyncio
import os

import pytest

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///data/test_telkap.db")


@pytest.fixture(scope="module")
def anyio_backend():
    return "asyncio"


@pytest.fixture(scope="module")
def event_loop():
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest.mark.asyncio
async def test_full_flow(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{tmp_path}/t.db")

    import telkap.config as config

    config.settings = config.load_settings()

    from telkap import db as db_module
    from telkap.models import Rule, Task, User
    from telkap.plans import FEAT_HISTORY, FEAT_WATERMARK
    from telkap.services import subscription
    from telkap.services.defaults import merged_settings
    from telkap.services.filters import MessageFacts, should_copy
    from telkap.services.transform import apply_transforms

    await db_module.init_db()
    try:
        # --- کاربر ---
        async with db_module.get_session() as session:
            session.add(User(id=555, first_name="تست"))
            await session.commit()

        # --- اشتراک ---
        assert await subscription.active_plan_for(555) is None
        sub = await subscription.grant(555, "month")
        assert sub is not None
        plan = await subscription.active_plan_for(555)
        assert plan is not None and plan.code == "month"
        assert plan.has(FEAT_WATERMARK) and plan.has(FEAT_HISTORY)
        assert await subscription.remaining_days(555) == 30

        # تمدید از انتهای اشتراک فعلی انباشته می‌شود
        await subscription.grant(555, "week")
        assert await subscription.remaining_days(555) == 37

        # --- کار کپی و قواعد ---
        async with db_module.get_session() as session:
            task = Task(
                user_id=555,
                title="تست",
                source_ref="@src",
                dest_ref="@dst",
                settings={"remove_links": True, "footer": "@mychannel"},
            )
            session.add(task)
            await session.commit()
            await session.refresh(task)
            task_id = task.id
            session.add(Rule(task_id=task_id, kind="replace", pattern="الماس", replacement="جواهر"))
            session.add(Rule(task_id=task_id, kind="block", pattern="قمار"))
            await session.commit()

        async with db_module.get_session() as session:
            loaded = await session.get(Task, task_id)
            cfg = merged_settings(loaded.settings)
            rules = list(loaded.rules)

        # تنظیمات ذخیره‌شده روی پیش‌فرض‌ها سوار شده‌اند
        assert cfg["remove_links"] is True
        assert cfg["skip_duplicates"] is True  # پیش‌فرض
        assert len(rules) == 2

        # پست ممنوعه رد می‌شود
        assert not should_copy(MessageFacts(text="سایت قمار"), cfg, rules).allowed

        # پست مجاز پردازش می‌شود
        facts = MessageFacts(text="فروش الماس در https://shop.example.com\n\n@sourcechan")
        assert should_copy(facts, cfg, rules).allowed
        out = apply_transforms(facts.text, cfg, rules)
        assert "جواهر" in out
        assert "الماس" not in out
        assert "shop.example.com" not in out
        assert "@sourcechan" not in out
        assert out.endswith("@mychannel")

        # --- لاگ فعالیت ---
        await db_module.log_activity(user_id=555, task_id=task_id, event="test", detail="ok")
        from sqlalchemy import select

        from telkap.models import ActivityLog

        async with db_module.get_session() as session:
            rows = await session.execute(select(ActivityLog).where(ActivityLog.user_id == 555))
            entries = list(rows.scalars())
        assert any(e.event == "test" for e in entries)

        # --- حذف آبشاری ---
        async with db_module.get_session() as session:
            loaded = await session.get(Task, task_id)
            await session.delete(loaded)
            await session.commit()
            remaining = await session.execute(select(Rule).where(Rule.task_id == task_id))
            assert list(remaining.scalars()) == []
    finally:
        await db_module.close_db()


@pytest.mark.asyncio
async def test_rate_limiter_spaces_out_sends():
    from telkap.services.ratelimit import HourlyQuota, RateLimiter

    limiter = RateLimiter(per_minute=3)
    loop = asyncio.get_running_loop()
    start = loop.time()
    for _ in range(3):
        await limiter.acquire("dest")
    # سه ارسال اول باید فوری باشند
    assert loop.time() - start < 0.5

    quota = HourlyQuota()
    assert all(quota.allow(1, 2) for _ in range(2))
    assert not quota.allow(1, 2)
    assert quota.allow(1, 0)  # صفر یعنی نامحدود
    quota.forget(1)
    assert quota.allow(1, 2)


def test_watermark_writes_image(tmp_path):
    from PIL import Image

    from telkap.services.watermark import add_text_watermark

    src = tmp_path / "in.jpg"
    Image.new("RGB", (800, 600), (30, 60, 120)).save(src)

    dest = tmp_path / "out.jpg"
    result = add_text_watermark(src, dest, "@mychannel", position="bottom-right", opacity=70)

    assert result == dest and dest.exists()
    with Image.open(dest) as img:
        assert img.size == (800, 600)

    # متن خالی یعنی بدون تغییر
    assert add_text_watermark(src, tmp_path / "x.jpg", "  ") == src

    # فایل خراب نباید استثنا بدهد
    broken = tmp_path / "broken.jpg"
    broken.write_bytes(b"not an image")
    assert add_text_watermark(broken, tmp_path / "y.jpg", "@x") == broken


def test_logo_watermark_writes_image(tmp_path):
    from PIL import Image

    from telkap.services.watermark import add_logo_watermark

    src = tmp_path / "in.jpg"
    Image.new("RGB", (800, 600), (30, 60, 120)).save(src)

    logo = tmp_path / "logo.png"
    Image.new("RGBA", (200, 200), (255, 0, 0, 255)).save(logo)

    dest = tmp_path / "out.jpg"
    result = add_logo_watermark(src, dest, logo, position="top-left", opacity=80)

    assert result == dest and dest.exists()
    with Image.open(dest) as img:
        assert img.size == (800, 600)

    # لوگوی ناموجود یا خراب نباید کپی را متوقف کند
    assert add_logo_watermark(src, tmp_path / "x.jpg", tmp_path / "nope.png") == src
    broken = tmp_path / "broken.png"
    broken.write_bytes(b"not an image")
    assert add_logo_watermark(src, tmp_path / "z.jpg", broken) == src


def test_watermark_ready_and_dispatch(tmp_path):
    from PIL import Image

    from telkap.services.watermark import apply_watermark, watermark_ready

    assert not watermark_ready({})
    assert not watermark_ready({"watermark_enabled": True, "watermark_text": "  "})
    assert watermark_ready({"watermark_enabled": True, "watermark_text": "@ch"})

    # حالت لوگو فقط وقتی آماده است که فایل لوگو واقعاً روی دیسک باشد
    cfg = {"watermark_enabled": True, "watermark_kind": "logo", "watermark_logo": ""}
    assert not watermark_ready(cfg)
    cfg["watermark_logo"] = str(tmp_path / "missing.png")
    assert not watermark_ready(cfg)

    logo = tmp_path / "logo.png"
    Image.new("RGBA", (120, 120), (0, 200, 0, 255)).save(logo)
    cfg["watermark_logo"] = str(logo)
    assert watermark_ready(cfg)

    src = tmp_path / "base.jpg"
    Image.new("RGB", (640, 480), (10, 10, 10)).save(src)
    dest = tmp_path / "final.jpg"
    assert apply_watermark(src, dest, cfg) == dest and dest.exists()


def test_crypto_roundtrip():
    from telkap.crypto import decrypt, encrypt

    secret = "1BVtsOK...session-string"
    token = encrypt(secret)
    assert token != secret
    assert decrypt(token) == secret
    assert decrypt("garbage") is None


def test_pin_hash_is_salted_per_user():
    from telkap.handlers.common import hash_pin, parse_int

    assert hash_pin(1, "1234") != hash_pin(2, "1234")
    assert hash_pin(1, "1234") == hash_pin(1, "1234")
    assert parse_int("۱۲۳") == 123
    assert parse_int("1,000") == 1000
    assert parse_int("abc") is None
