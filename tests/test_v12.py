"""تست دور دوازدهم: بازنویسی کانفیگ، فایل پیوست، فیلتر تعامل، و تکراری بین مبداها."""
from __future__ import annotations

import base64
import json

import pytest

from tests.test_copier import FakeClient, FakeManager, FakeMessage, _setup


def _vmess(name: str, host: str = "1.2.3.4") -> str:
    payload = {
        "v": "2", "ps": name, "add": host, "port": "443",
        "id": "11111111-2222-3333-4444-555555555555", "aid": "0",
        "net": "ws", "type": "none", "host": host, "path": "/", "tls": "tls",
    }
    blob = base64.b64encode(
        json.dumps(payload, ensure_ascii=False).encode("utf-8")
    ).decode()
    return f"vmess://{blob}"


def _read_vmess(link: str) -> dict:
    body = link.split("://", 1)[1]
    return json.loads(base64.b64decode(body + "=" * ((-len(body)) % 4)))


# --------------------------------------------------------------- vmess
def test_vmess_name_is_changed_inside_the_encoded_payload():
    """همان کاری که «جایگزینی کلمات» از پسش برنمی‌آید."""
    from telkap.services.configs import rewrite_link

    original = _vmess("@OldChannel")
    assert "@OldChannel" not in original      # اسم در متن دیده نمی‌شود

    out = rewrite_link(original, "@NewChannel")
    assert _read_vmess(out)["ps"] == "@NewChannel"


def test_rewriting_vmess_keeps_everything_else_untouched():
    """کانفیگ خراب از کانفیگ با نام قدیمی بدتر است."""
    from telkap.services.configs import rewrite_link

    before = _read_vmess(_vmess("@Old"))
    after = _read_vmess(rewrite_link(_vmess("@Old"), "@New"))
    for key in ("add", "port", "id", "aid", "net", "type", "host", "path", "tls"):
        assert after[key] == before[key]


def test_a_broken_vmess_is_left_exactly_as_it_was():
    from telkap.services.configs import rewrite_link

    junk = "vmess://this-is-not-base64-json!!!"
    assert rewrite_link(junk, "@New") == junk


# ------------------------------------------------- پروتکل‌های URLدار
@pytest.mark.parametrize(
    "link",
    [
        "vless://uuid@host.com:443?type=ws&security=tls#OldName",
        "trojan://pass@host.com:443?sni=x#OldName",
        "hysteria2://pass@host.com:443#OldName",
        "tuic://uuid:pass@host.com:443#OldName",
    ],
)
def test_url_style_configs_get_the_new_name(link):
    from urllib.parse import unquote

    from telkap.services.configs import rewrite_link

    out = rewrite_link(link, "@MyChannel")
    head, _, fragment = out.partition("#")
    assert head == link.split("#")[0]          # خود لینک دست‌نخورده
    assert unquote(fragment) == "@MyChannel"


def test_a_config_without_a_name_gets_one():
    from telkap.services.configs import rewrite_link

    out = rewrite_link("trojan://pass@host.com:443", "@Chan")
    assert out.endswith("#%40Chan")


# ------------------------------------------------------------ در متن
def test_every_config_in_a_post_is_renamed():
    from telkap.services.configs import rewrite_text

    text = (
        "کانفیگ‌های امروز 🔥\n\n"
        f"{_vmess('@Old', 'a.com')}\n"
        "vless://u@b.com:443#Old\n\n"
        "@OldChannel"
    )
    out, changed = rewrite_text(text, "@New")
    assert changed == 2
    assert "امروز 🔥" in out                    # متن معمولی دست‌نخورده
    assert _read_vmess(out.split("\n")[2])["ps"] == "@New"


def test_text_without_configs_comes_back_identical():
    from telkap.services.configs import rewrite_text

    text = "یک خبر معمولی با لینک https://example.com"
    assert rewrite_text(text, "@New") == (text, 0)


def test_an_empty_tag_changes_nothing():
    from telkap.services.configs import rewrite_text

    text = f"{_vmess('@Old')}"
    assert rewrite_text(text, "")[1] == 0
    assert rewrite_text(text, "   ")[1] == 0


def test_the_tag_cannot_break_the_link():
    """`#` وسط تگ، نام کانفیگ را دو تکه می‌کرد."""
    from telkap.services.configs import clean_tag

    assert "#" not in clean_tag("@Chan #best")
    assert "\n" not in clean_tag("خط اول\nخط دوم")


# ------------------------------------------------- در خط لوله‌ی متن
def test_configs_survive_the_link_remover():
    """«حذف لینک‌ها» نباید خودِ کانفیگ را بخورد."""
    from telkap.services.transform import apply_transforms

    text = f"ببینید {_vmess('@Old')} و https://spam.example"
    out = apply_transforms(
        text,
        {"rewrite_configs": True, "config_tag": "@New", "remove_links": True},
    )
    assert "spam.example" not in out
    assert "vmess://" in out
    assert _read_vmess(out.split()[1])["ps"] == "@New"


def test_the_tag_falls_back_to_the_signature():
    from telkap.services.transform import config_tag

    assert config_tag({"config_tag": "@A", "signature": "@B"}) == "@A"
    assert config_tag({"signature": "@B", "footer": "@C"}) == "@B"
    assert config_tag({"footer": "@C"}) == "@C"
    assert config_tag({}) == ""


def test_configs_are_untouched_while_the_switch_is_off():
    from telkap.services.transform import apply_transforms

    text = _vmess("@Old")
    assert apply_transforms(text, {"config_tag": "@New"}) == text


# ------------------------------------------------------- فایل پیوست
def test_a_text_file_of_configs_is_rewritten():
    from telkap.services.docedit import rewrite_bytes

    raw = f"{_vmess('@Old')}\nvless://u@h:443#Old\n".encode()
    out, changed = rewrite_bytes(raw, "@New")
    assert changed == 2
    assert b"@Old" not in out


def test_a_base64_subscription_file_is_rewritten():
    from telkap.services.docedit import rewrite_bytes

    inner = f"{_vmess('@Old')}\nvless://u@h:443#Old"
    raw = base64.b64encode(inner.encode()).decode().encode()

    out, changed = rewrite_bytes(raw, "@New")
    assert changed == 2
    # خروجی هم باید base64 بماند، وگرنه برنامه‌ی کاربر نمی‌خواندش
    decoded = base64.b64decode(out + b"=" * ((-len(out)) % 4)).decode()
    assert "vmess://" in decoded and "@Old" not in decoded


def test_a_json_config_file_gets_its_name_fields_replaced():
    from telkap.services.docedit import rewrite_bytes

    raw = json.dumps(
        {"remarks": "@Old", "outbounds": [{"tag": "@Old", "port": 443}]}
    ).encode()
    out, changed = rewrite_bytes(raw, "@New")
    assert changed == 2
    data = json.loads(out)
    assert data["remarks"] == "@New"
    assert data["outbounds"][0]["tag"] == "@New"
    assert data["outbounds"][0]["port"] == 443     # بقیه دست‌نخورده


def test_an_unknown_binary_file_is_never_touched():
    from telkap.services.docedit import rewrite_bytes

    raw = bytes(range(256))
    assert rewrite_bytes(raw, "@New") == (raw, 0)


def test_a_file_with_nothing_to_change_is_left_alone():
    from telkap.services.docedit import rewrite_bytes

    raw = b"just some notes, no configs here"
    assert rewrite_bytes(raw, "@New") == (raw, 0)


def test_a_huge_file_is_not_opened():
    from telkap.services.docedit import MAX_EDIT_BYTES, rewrite_bytes

    raw = b"x" * (MAX_EDIT_BYTES + 1)
    assert rewrite_bytes(raw, "@New") == (raw, 0)


def test_an_encrypted_config_file_is_only_renamed(tmp_path):
    """`.npvt` بدنه‌اش رمز است؛ نه می‌شود بازنویسی‌اش کرد، نه باید خرابش کرد."""
    from telkap.services.docedit import rewrite_file

    # ساختار واقعی: سرآیند NPVT1 و بدنه‌ی base64 رمزنگاری‌شده
    sealed = b"NPVT1\n1UDjs5b4eJWenYEWkz8KZB0=,Z85xi0Zpr3/zGtIWoiIC14cbvbjy"
    src = tmp_path / "OldChannel.npvt"
    src.write_bytes(sealed)

    out, changed = rewrite_file(src, "@MyChannel")
    assert out.name == "@MyChannel.npvt"
    assert changed == 1
    assert out.read_bytes() == sealed       # یک بایت هم عوض نشده
    assert not src.exists()


def test_an_encrypted_file_is_detected_even_with_a_disguised_name():
    from telkap.services.docedit import is_sealed

    assert is_sealed("anything.bin", b"NPVT1\ndata") is True
    assert is_sealed("configs.npvt") is True
    assert is_sealed("configs.txt", b"vmess://abc") is False


def test_a_sealed_file_without_a_tag_is_left_alone(tmp_path):
    from telkap.services.docedit import rewrite_file

    src = tmp_path / "OldChannel.npvt"
    src.write_bytes(b"NPVT1\nsealed")
    out, changed = rewrite_file(src, "   ")
    assert out == src and changed == 0


def test_only_promising_files_are_opened():
    from telkap.services.docedit import worth_opening

    assert worth_opening("configs.txt")
    assert worth_opening("sub.npv4")
    assert worth_opening("pack.zip")
    assert not worth_opening("photo.jpg")
    assert not worth_opening("clip.mp4")


def test_the_new_file_name_follows_the_pattern():
    from telkap.services.docedit import new_name

    assert new_name("old.txt", "{tag}", "@Chan") == "@Chan.txt"
    assert new_name("old.txt", "{name} - {tag}", "@Chan") == "old - @Chan.txt"
    # الگوی خراب نباید ارسال را بشکند
    assert new_name("old.txt", "{nope}", "@Chan").endswith(".txt")
    # کاراکترهای غیرمجاز در نام فایل حذف می‌شوند
    assert "/" not in new_name("old.txt", "{tag}", "a/b")


def test_a_zip_is_repacked_only_when_something_changed():
    import io
    import zipfile

    from telkap.services.docedit import rewrite_bytes

    def build(inner: bytes) -> bytes:
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr("config.txt", inner)
            zf.writestr("logo.png", b"\x89PNG binary")
        return buf.getvalue()

    untouched = build(b"nothing to see")
    assert rewrite_bytes(untouched, "@New") == (untouched, 0)

    packed, changed = rewrite_bytes(build(_vmess("@Old").encode()), "@New")
    assert changed == 1
    with zipfile.ZipFile(io.BytesIO(packed)) as zf:
        assert zf.read("logo.png") == b"\x89PNG binary"    # عضو دودویی سالم
        assert b"@Old" not in zf.read("config.txt")


# ------------------------------------------------------- فیلتر تعامل
class _Reaction:
    def __init__(self, count: int) -> None:
        self.count = count


class _Reactions:
    def __init__(self, *counts: int) -> None:
        self.results = [_Reaction(c) for c in counts]


def test_engagement_numbers_are_read_from_the_message():
    from telkap.services.copier import engagement_of

    msg = FakeMessage(id=1)
    msg.views, msg.forwards, msg.reactions = 120, 4, _Reactions(3, 5)
    assert engagement_of(msg) == (120, 8, 4)


def test_a_message_without_counters_reads_as_zero():
    from telkap.services.copier import engagement_of

    assert engagement_of(FakeMessage(id=1)) == (0, 0, 0)


def test_the_threshold_says_which_one_failed():
    from telkap.services.copier import engagement_ok

    msg = FakeMessage(id=1)
    msg.views, msg.forwards, msg.reactions = 50, 0, _Reactions(1)

    assert engagement_ok(msg, {})[0] is True             # بدون حد نصاب
    assert engagement_ok(msg, {"min_views": 50})[0] is True

    ok, why = engagement_ok(msg, {"min_views": 100})
    assert not ok and "بازدید" in why

    ok, why = engagement_ok(msg, {"min_reactions": 10})
    assert not ok and "واکنش" in why


@pytest.mark.asyncio
async def test_a_quiet_post_is_dropped_when_the_wait_is_over(tmp_path, monkeypatch):
    db_module, task_id = await _setup(
        tmp_path, monkeypatch, settings={"min_views": 100}
    )
    try:
        from telkap.models import PendingPost
        from telkap.services.copier import Copier

        client = FakeClient()
        copier = Copier(FakeManager(client))
        quiet = FakeMessage(id=1, message="کسی ندید")
        quiet.views = 3

        sent = await copier.process(
            7, task_id, [quiet], released=PendingPost.REASON_SCHEDULE
        )
        assert sent is False
        assert client.sent == []
    finally:
        await db_module.close_db()


@pytest.mark.asyncio
async def test_a_popular_post_goes_through(tmp_path, monkeypatch):
    db_module, task_id = await _setup(
        tmp_path, monkeypatch, settings={"min_views": 100}
    )
    try:
        from telkap.models import PendingPost
        from telkap.services.copier import Copier

        client = FakeClient()
        copier = Copier(FakeManager(client))
        popular = FakeMessage(id=1, message="همه دیدند")
        popular.views = 5000

        assert await copier.process(
            7, task_id, [popular], released=PendingPost.REASON_SCHEDULE
        ) is True
        assert len(client.sent) == 1
    finally:
        await db_module.close_db()


@pytest.mark.asyncio
async def test_a_fresh_post_waits_before_being_judged(tmp_path, monkeypatch):
    """بدون انتظار، هر پست تازه با بازدید صفر رد می‌شد."""
    db_module, task_id = await _setup(
        tmp_path,
        monkeypatch,
        settings={"min_views": 100, "engagement_wait_minutes": 45},
    )
    try:
        from telkap.models import PendingPost
        from telkap.services import pending
        from telkap.services.copier import Copier

        client = FakeClient()
        copier = Copier(FakeManager(client))
        fresh = FakeMessage(id=1, message="تازه رسیده")
        fresh.views = 0

        assert await copier.process(7, task_id, [fresh]) is False
        assert client.sent == []                      # نه منتشر شد، نه دور ریخته

        queued = await pending.listing(7, reason=PendingPost.REASON_SCHEDULE)
        assert len(queued) == 1 and queued[0].release_at is not None
    finally:
        await db_module.close_db()


@pytest.mark.asyncio
async def test_engagement_and_approval_chain_together(tmp_path, monkeypatch):
    """پستی که انتظار تعاملش تمام شده هنوز باید به صف تأیید برود."""
    db_module, task_id = await _setup(
        tmp_path,
        monkeypatch,
        settings={"min_views": 10, "approval": True},
    )
    try:
        from telkap.models import PendingPost
        from telkap.services import pending
        from telkap.services.copier import Copier

        client = FakeClient()
        copier = Copier(FakeManager(client))
        popular = FakeMessage(id=1, message="پرطرفدار")
        popular.views = 900

        assert await copier.process(
            7, task_id, [popular], released=PendingPost.REASON_SCHEDULE
        ) is False
        assert client.sent == []

        waiting = await pending.listing(7, reason=PendingPost.REASON_APPROVAL)
        assert len(waiting) == 1
    finally:
        await db_module.close_db()


@pytest.mark.asyncio
async def test_an_approved_post_is_not_sent_back_for_approval(tmp_path, monkeypatch):
    db_module, task_id = await _setup(
        tmp_path,
        monkeypatch,
        settings={"min_views": 10, "approval": True},
    )
    try:
        from telkap.models import PendingPost
        from telkap.services.copier import Copier

        client = FakeClient()
        copier = Copier(FakeManager(client))
        popular = FakeMessage(id=1, message="پرطرفدار")
        popular.views = 900

        assert await copier.process(
            7, task_id, [popular], released=PendingPost.REASON_APPROVAL
        ) is True
        assert len(client.sent) == 1
    finally:
        await db_module.close_db()


# --------------------------------------------- تکراری بین چند مبدا
@pytest.mark.asyncio
async def test_the_same_news_from_two_sources_lands_once(tmp_path, monkeypatch):
    db_module, task_id = await _setup(
        tmp_path, monkeypatch, settings={"skip_cross_duplicates": True}
    )
    try:
        from telkap.models import Task
        from telkap.services import cache
        from telkap.services.copier import Copier

        # کار دومی از مبدای دیگر، به همان کانال مقصد
        async with db_module.get_session() as db:
            other = Task(
                user_id=7, title="مبدا دوم", source_ref="@src2", source_id=-1003,
                dest_ref="@dst", dest_id=-1002,
                settings={"skip_cross_duplicates": True},
            )
            db.add(other)
            await db.commit()
            await db.refresh(other)
            second_id = other.id

        client = FakeClient()
        copier = Copier(FakeManager(client))

        assert await copier.process(7, task_id, [FakeMessage(id=1, message="خبر مشترک")])
        assert len(client.sent) == 1

        # همان محتوا، از کار و مبدای دیگر
        cache.invalidate_task(second_id)
        assert await copier.process(
            7, second_id, [FakeMessage(id=9, message="خبر مشترک")]
        ) is False
        assert len(client.sent) == 1        # دوباره نرفت
    finally:
        await db_module.close_db()


@pytest.mark.asyncio
async def test_without_the_toggle_both_sources_publish(tmp_path, monkeypatch):
    db_module, task_id = await _setup(tmp_path, monkeypatch, settings={})
    try:
        from telkap.models import Task
        from telkap.services.copier import Copier

        async with db_module.get_session() as db:
            other = Task(
                user_id=7, title="مبدا دوم", source_ref="@src2", source_id=-1003,
                dest_ref="@dst", dest_id=-1002, settings={},
            )
            db.add(other)
            await db.commit()
            await db.refresh(other)
            second_id = other.id

        client = FakeClient()
        copier = Copier(FakeManager(client))
        await copier.process(7, task_id, [FakeMessage(id=1, message="خبر مشترک")])
        await copier.process(7, second_id, [FakeMessage(id=9, message="خبر مشترک")])
        assert len(client.sent) == 2
    finally:
        await db_module.close_db()
