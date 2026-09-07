"""تست قابلیت‌های دور سوم: کش، تشخیص تبلیغ امتیازی، فیلترهای گروه،
کپی تنظیمات و خروجی/ورودی فایل."""
from __future__ import annotations

import json

import pytest

from tests.test_copier import FakeClient, FakeManager, FakeMessage, _setup


# ------------------------------------------------------------------- کش
@pytest.mark.asyncio
async def test_cache_serves_repeat_reads(tmp_path, monkeypatch):
    db_module, task_id = await _setup(tmp_path, monkeypatch, settings={"footer": "@a"})
    try:
        from telkap.services import cache

        first = await cache.get_task(task_id)
        second = await cache.get_task(task_id)
        assert first is second  # همان شیء، یعنی از کش آمده
        assert first.cfg["footer"] == "@a"
        assert cache.stats()["tasks"] == 1
    finally:
        await db_module.close_db()


@pytest.mark.asyncio
async def test_cache_invalidation_picks_up_changes(tmp_path, monkeypatch):
    db_module, task_id = await _setup(tmp_path, monkeypatch, settings={"footer": "@old"})
    try:
        from telkap.models import Task
        from telkap.services import cache

        assert (await cache.get_task(task_id)).cfg["footer"] == "@old"

        async with db_module.get_session() as session:
            task = await session.get(Task, task_id)
            task.settings = {"footer": "@new"}
            await session.commit()

        # بدون باطل‌سازی، مقدار قدیمی برمی‌گردد
        assert (await cache.get_task(task_id)).cfg["footer"] == "@old"

        cache.invalidate_task(task_id)
        assert (await cache.get_task(task_id)).cfg["footer"] == "@new"
    finally:
        await db_module.close_db()


@pytest.mark.asyncio
async def test_cache_reflects_settings_change_end_to_end(tmp_path, monkeypatch):
    """تغییر تنظیمات از مسیر واقعی باید بلافاصله روی کپی اثر بگذارد."""
    db_module, task_id = await _setup(
        tmp_path, monkeypatch, settings={"skip_duplicates": False}
    )
    try:
        from telkap.handlers.settings import _save_settings
        from telkap.services import cache
        from telkap.services.copier import Copier
        from telkap.services.defaults import merged_settings

        client = FakeClient()
        copier = Copier(FakeManager(client))

        await copier.process(7, task_id, [FakeMessage(id=1, message="خبر")])
        assert client.sent[0].text == "خبر"

        cfg = merged_settings({"skip_duplicates": False})
        cfg["footer"] = "@mychannel"
        await _save_settings(task_id, cfg)

        await copier.process(7, task_id, [FakeMessage(id=2, message="خبر")])
        assert client.sent[1].text.endswith("@mychannel")
        assert cache.stats()["tasks"] >= 1
    finally:
        await db_module.close_db()


@pytest.mark.asyncio
async def test_disabled_task_via_cache_stops_copying(tmp_path, monkeypatch):
    db_module, task_id = await _setup(tmp_path, monkeypatch, settings={})
    try:
        from telkap.models import Task
        from telkap.services import cache
        from telkap.services.copier import Copier

        client = FakeClient()
        copier = Copier(FakeManager(client))

        async with db_module.get_session() as session:
            task = await session.get(Task, task_id)
            task.enabled = False
            await session.commit()
        cache.invalidate_task(task_id)

        assert await copier.process(7, task_id, [FakeMessage(id=1, message="خبر")]) is False
        assert client.sent == []
    finally:
        await db_module.close_db()


# ------------------------------------------------- تشخیص تبلیغ امتیازی
@pytest.mark.parametrize(
    "text,sensitivity,expected",
    [
        # تبلیغ آشکار، در هر سه سطح رد می‌شود
        ("جهت تبلیغات با ادمین در ارتباط باشید @ads", "low", True),
        ("جهت تبلیغات با ادمین در ارتباط باشید @ads", "high", True),
        # متن کاملاً عادی، در هیچ سطحی رد نمی‌شود
        ("امروز هوا آفتابی است و دما ۲۵ درجه", "high", False),
        ("", "high", False),
        # یک واژه‌ی ضعیف به‌تنهایی کافی نیست
        ("قیمت بلیط بازی اعلام شد", "medium", False),
        # نشانه‌های تجاری + شماره تماس
        ("ثبت سفارش با تخفیف ویژه ۰۹۱۲۱۲۳۴۵۶۷", "medium", True),
        # شماره کارت نشانه‌ی قوی است
        ("واریز به کارت 6037-9912-3456-7890", "medium", True),
    ],
)
def test_ad_detection_by_sensitivity(text, sensitivity, expected):
    from telkap.services.transform import looks_like_ad

    assert looks_like_ad(text, sensitivity) is expected


def test_high_sensitivity_catches_more_than_low():
    from telkap.services.transform import looks_like_ad

    borderline = "تخفیف ویژه فروش امروز"
    assert looks_like_ad(borderline, "high") is True
    assert looks_like_ad(borderline, "low") is False


def test_ad_reason_explains_decision():
    from telkap.services.transform import ad_reason, ad_score

    text = "جهت تبلیغات: ۰۹۱۲۱۲۳۴۵۶۷ با کد تخفیف"
    score, reasons = ad_score(text)
    assert score >= 4
    assert reasons
    explanation = ad_reason(text)
    assert "امتیاز" in explanation
    assert "شماره تماس" in explanation


def test_filter_reports_ad_reason():
    from telkap.services.defaults import merged_settings
    from telkap.services.filters import MessageFacts, should_copy

    cfg = merged_settings({"block_ads": True, "ad_sensitivity": "medium"})
    decision = should_copy(MessageFacts(text="جهت تبلیغات با ما تماس بگیرید @x"), cfg)
    assert not decision.allowed
    assert "امتیاز" in decision.reason


# ------------------------------------------------------- فیلترهای گروه
def test_skip_bots_and_replies():
    from telkap.services.defaults import merged_settings
    from telkap.services.filters import MessageFacts, should_copy

    bot_msg = MessageFacts(text="سلام", from_bot=True)
    reply_msg = MessageFacts(text="سلام", is_reply=True)

    off = merged_settings(None)
    assert should_copy(bot_msg, off).allowed
    assert should_copy(reply_msg, off).allowed

    assert not should_copy(bot_msg, merged_settings({"skip_bots": True})).allowed
    assert not should_copy(reply_msg, merged_settings({"skip_replies": True})).allowed
    # فیلتر ربات نباید پیام انسان را رد کند
    assert should_copy(MessageFacts(text="سلام"), merged_settings({"skip_bots": True})).allowed


def test_build_facts_detects_bot_and_reply():
    from telkap.services.copier import build_facts

    class Sender:
        bot = True

    msg = FakeMessage(id=1, message="سلام")
    msg.sender = Sender()
    msg.reply_to = object()
    facts = build_facts(msg)
    assert facts.from_bot is True
    assert facts.is_reply is True

    plain = build_facts(FakeMessage(id=2, message="سلام"))
    assert plain.from_bot is False
    assert plain.is_reply is False


# --------------------------------------------------------- کپی تنظیمات
@pytest.mark.asyncio
async def test_clone_copies_settings_and_rules(tmp_path, monkeypatch):
    db_module, source_id = await _setup(
        tmp_path,
        monkeypatch,
        settings={"footer": "@src", "remove_links": True},
        rules=[("replace", "الف", "ب"), ("block", "بد", "")],
    )
    try:
        from sqlalchemy import select

        from telkap.models import Rule, Task
        from telkap.services import cache
        from telkap.services.defaults import merged_settings

        # کار دوم با تنظیمات متفاوت
        async with db_module.get_session() as session:
            other = Task(
                user_id=7,
                title="دوم",
                source_ref="@s2",
                dest_ref="@d2",
                settings={"footer": "@other"},
            )
            session.add(other)
            await session.commit()
            await session.refresh(other)
            target_id = other.id
            session.add(Rule(task_id=target_id, kind="block", pattern="قدیمی"))
            await session.commit()

        # همان منطقی که هندلر کپی اجرا می‌کند
        async with db_module.get_session() as session:
            src = await session.get(Task, source_id)
            src_rules = [
                (r.kind, r.pattern, r.replacement, r.enabled)
                for r in (
                    await session.execute(select(Rule).where(Rule.task_id == source_id))
                ).scalars()
            ]
            dst = await session.get(Task, target_id)
            dst.settings = dict(merged_settings(src.settings))
            from sqlalchemy import delete

            await session.execute(delete(Rule).where(Rule.task_id == target_id))
            for kind, pattern, repl, enabled in src_rules:
                session.add(
                    Rule(
                        task_id=target_id,
                        kind=kind,
                        pattern=pattern,
                        replacement=repl,
                        enabled=enabled,
                    )
                )
            await session.commit()
        cache.invalidate_task(target_id)

        snapshot = await cache.get_task(target_id)
        assert snapshot.cfg["footer"] == "@src"
        assert snapshot.cfg["remove_links"] is True
        kinds = sorted(r.kind for r in snapshot.rules)
        assert kinds == ["block", "replace"]
        # قاعده‌ی قدیمی مقصد پاک شده است
        assert all(r.pattern != "قدیمی" for r in snapshot.rules)
    finally:
        await db_module.close_db()


# ------------------------------------------------------ ورودی/خروجی فایل
def test_import_rejects_unknown_settings_keys():
    """فایل ورودی نباید بتواند کلید دلخواه وارد تنظیمات کند."""
    from telkap.services.defaults import DEFAULT_SETTINGS, merged_settings

    payload = json.loads(
        json.dumps(
            {
                "version": 1,
                "settings": {"footer": "@ok", "__evil__": "x", "remove_links": True},
                "rules": [],
            }
        )
    )
    clean = {k: v for k, v in payload["settings"].items() if k in DEFAULT_SETTINGS}
    assert "__evil__" not in clean
    assert clean["footer"] == "@ok"

    merged = merged_settings(clean)
    assert "__evil__" not in merged
    assert merged["remove_links"] is True
    # کلیدهای دست‌نخورده پیش‌فرض خود را نگه می‌دارند
    assert merged["skip_duplicates"] is True


def test_import_filters_invalid_rules():
    from telkap.models import Rule

    valid_kinds = {Rule.KIND_REPLACE, Rule.KIND_REGEX, Rule.KIND_BLOCK, Rule.KIND_ALLOW}
    raw = [
        {"kind": "replace", "pattern": "الف", "replacement": "ب"},
        {"kind": "evil", "pattern": "x"},          # نوع نامعتبر
        {"kind": "block"},                          # بدون الگو
        {"kind": "allow", "pattern": "خوب"},
        "not-a-dict",
    ]
    rules = [
        r for r in raw
        if isinstance(r, dict) and r.get("kind") in valid_kinds and r.get("pattern")
    ]
    assert len(rules) == 2
    assert {r["kind"] for r in rules} == {"replace", "allow"}
