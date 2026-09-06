"""تست احراز هویت مینی‌اپ.

<b>چرا این فایل سخت‌گیر است.</b> پنل پشت نام کاربری و رمز و کد
دومرحله‌ای است. مینی‌اپ هیچ‌کدام را ندارد — تنها چیزی که «من کاربر
شماره‌ی فلانم» را از یک ادعای ساده جدا می‌کند، همین امضاست. هر سوراخی
اینجا یعنی هرکس می‌تواند داده‌ی هر مشتری‌ای را ببیند و کارهایش را
خاموش کند.
"""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import time
from types import SimpleNamespace
from urllib.parse import urlencode

import pytest
import pytest_asyncio
from aiohttp.test_utils import TestClient, TestServer

from telkap.web import miniapp
from tests.test_copier import _setup

TOKEN = "123456:AAH-fake-token-for-tests"


def _sign(pairs: dict, token: str = TOKEN) -> str:
    """همان چیزی که تلگرام می‌سازد — تا تست واقعاً چیزی را بسنجد."""
    check = "\n".join(f"{key}={pairs[key]}" for key in sorted(pairs))
    secret = hmac.new(b"WebAppData", token.encode(), hashlib.sha256).digest()
    signature = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
    return urlencode({**pairs, "hash": signature})


def _fresh(user_id: int = 7, **extra) -> str:
    """initData تازه و درست‌امضا برای یک کاربر."""
    return _sign({
        "auth_date": str(int(time.time())),
        "query_id": "AAB",
        "user": json.dumps({"id": user_id, "first_name": "رامین"}),
        **extra,
    })


async def _client(monkeypatch):
    """یک سرورِ واقعی با همان build_app، و توکنِ تست به‌جای توکن واقعی.

    <b>چرا سرور واقعی و نه صدا زدن مستقیمِ تابع.</b> چیزی که اینجا
    سنجیده می‌شود «آیا مسیر پشتِ احراز هویت است» است — و آن را فقط
    عبور از خودِ روتر و میدل‌ور نشان می‌دهد، نه صدا زدن هندلر.
    """
    from telkap.web import miniapp as app_mod
    from telkap.web import server

    monkeypatch.setattr(
        app_mod, "get_settings", lambda: SimpleNamespace(bot_token=TOKEN)
    )
    client = TestClient(TestServer(server.build_app(bot=None)))
    await client.start_server()
    _CLIENTS.append(client)
    return client


_CLIENTS: list = []


# asyncio_mode=strict است: فیکسچرِ async با pytest.fixture اصلاً
# اجرا نمی‌شود و بی‌صدا رد می‌شود.
@pytest_asyncio.fixture(autouse=True)
async def _close_clients():
    """سرورِ تست باید بسته شود، وگرنه نشستِ رهاشده موقع بسته شدن حلقه
    سر و صدا می‌کند و خطای واقعیِ تستِ بعدی را زیر خودش گم می‌کند."""
    yield
    while _CLIENTS:
        await _CLIENTS.pop().close()
    await asyncio.sleep(0)


def test_a_properly_signed_payload_is_accepted():
    data = miniapp.check(_fresh(), TOKEN)

    assert data is not None
    assert data["user"]["id"] == 7
    assert miniapp.user_id_from(_fresh(99), TOKEN) == 99


def test_an_unsigned_claim_is_refused():
    """<b>همان حمله‌ای که کل این کد برای جلوگیری از آن است.</b>

    رشته‌ی initData در اختیار مرورگر است؛ هرکس می‌تواند بنویسد «من
    کاربر شماره‌ی فلانم». بدون امضا، این ادعا باید بی‌ارزش باشد.
    """
    naked = urlencode({
        "auth_date": str(int(time.time())),
        "user": json.dumps({"id": 7}),
    })

    assert miniapp.check(naked, TOKEN) is None
    assert miniapp.user_id_from(naked, TOKEN) is None


def test_a_payload_signed_with_another_token_is_refused():
    """<b>امضای رباتِ دیگری نباید اینجا کار کند.</b>

    وگرنه هرکس با ساختنِ یک ربات، کلیدِ ورود به داده‌ی مشتری‌های ما را
    داشت.
    """
    other = "999:someone-elses-bot"
    signed = _sign(
        {
            "auth_date": str(int(time.time())),
            "user": json.dumps({"id": 7}),
        },
        token=other,
    )

    assert miniapp.check(signed, other) is not None   # با توکن خودش، بله
    assert miniapp.check(signed, TOKEN) is None       # با توکن ما، نه


def test_telegrams_own_signature_field_does_not_break_the_check():
    """<b>همان چیزی که مینی‌اپ را روی سرور واقعی از کار انداخت.</b>

    تلگرام بعداً فیلد <code>signature</code> را اضافه کرد — یک امضای
    Ed25519 برای سرویس‌های ثالث. آن فیلد جزو رشته‌ی امضای HMAC نیست و
    باید مثل <code>hash</code> بیرون بماند. تا وقتی بیرون نمی‌ماند،
    <b>هر</b> initDataیی که تلگرام می‌فرستاد رد می‌شد و اپ می‌گفت
    «شناسایی نشدید» — بی‌هیچ ردی در لاگ، چون از نظر کد فقط یک امضای
    غلط بود.
    """
    fields = {
        "auth_date": str(int(time.time())),
        "query_id": "AAB",
        "user": json.dumps({"id": 7, "first_name": "رامین"}),
    }
    # تلگرام signature را بیرون از رشته‌ی امضا می‌گذارد و بعد به
    # پارامترها اضافه‌اش می‌کند
    signed = _sign(fields) + "&signature=" + "a" * 86

    data = miniapp.check(signed, TOKEN)

    assert data is not None
    assert miniapp.user_id_from(signed, TOKEN) == 7


def test_changing_one_character_breaks_the_signature():
    """امضا باید روی <b>همه‌ی</b> فیلدها باشد، نه فقط بعضی‌شان."""
    signed = _fresh(7)
    tampered = signed.replace("%22id%22%3A+7", "%22id%22%3A+8")

    assert tampered != signed
    assert miniapp.check(tampered, TOKEN) is None


def test_an_old_payload_is_refused():
    """<b>initData کهنه نباید تا ابد کلیدِ ورود بماند.</b>"""
    old = _sign({
        "auth_date": str(int(time.time()) - miniapp.MAX_AGE_SECONDS - 60),
        "user": json.dumps({"id": 7}),
    })

    assert miniapp.check(old, TOKEN) is None


def test_a_payload_from_the_future_or_without_a_date_is_refused():
    assert miniapp.check(_sign({"user": "{}"}), TOKEN) is None          # بدون تاریخ
    assert miniapp.check(_sign({"auth_date": "0", "user": "{}"}), TOKEN) is None
    assert miniapp.check(_sign({"auth_date": "خیر", "user": "{}"}), TOKEN) is None


def test_garbage_never_raises():
    """ورودی از بیرون می‌آید؛ خطای ۵۰۰ خودش یک نشانه است."""
    for junk in ("", "&&&", "hash=", "a=1", "%%%", "user=1&hash=zz"):
        assert miniapp.check(junk, TOKEN) is None
    assert miniapp.check(_fresh(), "") is None


def test_a_payload_without_a_user_gives_no_identity():
    signed = _sign({"auth_date": str(int(time.time())), "query_id": "AAB"})

    assert miniapp.check(signed, TOKEN) is not None    # امضا درست است
    assert miniapp.user_id_from(signed, TOKEN) is None  # ولی کسی نیست


def test_a_broken_user_field_does_not_crash_the_check():
    signed = _sign({
        "auth_date": str(int(time.time())),
        "user": "این JSON نیست",
    })

    data = miniapp.check(signed, TOKEN)
    assert data is not None and data["user"] is None
    assert miniapp.user_id_from(signed, TOKEN) is None


def test_the_identity_never_travels_in_the_url():
    """<b>نشانی در لاگ سرور و تاریخچه‌ی مرورگر می‌نشیند.</b>

    چیزی که هویت را اثبات می‌کند نباید آنجا باشد. اگر روزی کسی
    initData را به query string ببرد، این تست می‌ایستد.
    """
    import inspect

    source = inspect.getsource(miniapp._init_data)

    assert "headers" in source
    assert "query" not in source


# --------------------------------------------------- مسیرهای رابط JSON
@pytest.mark.asyncio
async def test_the_api_refuses_an_unsigned_request(tmp_path, monkeypatch):
    """<b>هر مسیر خودش می‌سنجد، نه یک میدل‌ور.</b>

    مینی‌اپ بیرونِ ورودِ پنل است؛ اگر مسیری فراموش کند بسنجد، بی‌صدا
    داده‌ی هر مشتری‌ای را به هرکس می‌دهد.
    """
    db_module, _ = await _setup(tmp_path, monkeypatch, settings={})
    try:
        client = await _client(monkeypatch)

        for path in ("/app/api/me", "/app/api/tasks", "/app/api/quote/month"):
            response = await client.get(path)
            assert response.status == 401, path

        response = await client.post("/app/api/tasks/1/toggle")
        assert response.status == 401
    finally:
        await db_module.close_db()


@pytest.mark.asyncio
async def test_a_signed_request_sees_only_its_own_data(tmp_path, monkeypatch):
    db_module, _ = await _setup(tmp_path, monkeypatch, settings={})
    try:
        from telkap.models import Task, User

        # شناسه‌ها را خودِ دیتابیس می‌دهد؛ _setup هم کار می‌سازد و
        # عددِ ثابت با آن برخورد می‌کند.
        async with db_module.get_session() as db:
            if await db.get(User, 8) is None:
                db.add(User(id=8, first_name="دیگری"))
            mine_task = Task(
                user_id=7, title="مالِ من",
                source_ref="@mine", source_id=-100,
                dest_ref="@mine_out", dest_id=-101, enabled=True,
            )
            theirs = Task(
                user_id=8, title="مالِ او",
                source_ref="@theirs", source_id=-200,
                dest_ref="@theirs_out", dest_id=-201, enabled=True,
            )
            db.add_all([mine_task, theirs])
            await db.commit()
            await db.refresh(mine_task)
            await db.refresh(theirs)
            theirs_id = theirs.id

        client = await _client(monkeypatch)
        mine = {"X-Telegram-Init-Data": _fresh(7)}

        body = await (await client.get("/app/api/tasks", headers=mine)).json()
        titles = [task["title"] for task in body["tasks"]]
        assert "مالِ من" in titles
        assert "مالِ او" not in titles

        # و کارِ کسِ دیگر، حتی با شناسه‌ی درست، پیدا نمی‌شود
        response = await client.post(
            f"/app/api/tasks/{theirs_id}/toggle", headers=mine
        )
        assert response.status == 404

        async with db_module.get_session() as db:
            assert (await db.get(Task, theirs_id)).enabled is True    # دست نخورده
    finally:
        await db_module.close_db()


@pytest.mark.asyncio
async def test_someone_who_never_started_the_bot_gets_a_state_not_an_error(
    tmp_path, monkeypatch
):
    """<b>«هنوز نیامده» خطا نیست، یک حالت است.</b>

    اپ باید بتواند بگوید «اول ربات را باز کنید»، نه اینکه صفحه‌ی خطا
    نشان بدهد.
    """
    db_module, _ = await _setup(tmp_path, monkeypatch, settings={})
    try:
        client = await _client(monkeypatch)

        response = await client.get(
            "/app/api/me", headers={"X-Telegram-Init-Data": _fresh(4242)}
        )

        assert response.status == 200
        assert (await response.json()) == {"known": False}
    finally:
        await db_module.close_db()


@pytest.mark.asyncio
async def test_the_plans_route_is_the_only_one_open(tmp_path, monkeypatch):
    """قیمت‌ها عمومی‌اند و روی صفحه‌ی فروش هم هستند؛ پنهان کردنشان بی‌معناست."""
    db_module, _ = await _setup(tmp_path, monkeypatch, settings={})
    try:
        from telkap.plans import POPULAR_CODE

        client = await _client(monkeypatch)
        response = await client.get("/app/api/plans")

        assert response.status == 200
        body = await response.json()
        assert body["popular"] == POPULAR_CODE
        assert any(plan["code"] == "month" for plan in body["plans"])
    finally:
        await db_module.close_db()


# ------------------------------------------------------- نشانی مینی‌اپ
def test_the_app_url_is_a_neighbour_of_the_panel_not_a_child(monkeypatch):
    """<b>یک زیردامنه، سه مسیر.</b>

    WEB_BASE_URL به «/panel» ختم می‌شود چون لینک ورود و بازگشتِ درگاه
    از آن ساخته می‌شوند. مینی‌اپ همسایه‌ی پنل است، نه زیرمجموعه‌اش؛
    «/panel/app» هیچ‌جا وجود ندارد.
    """
    from telkap.web import miniapp as app_mod

    monkeypatch.setattr(
        app_mod,
        "get_settings",
        lambda: SimpleNamespace(web_base_url="https://forwardbot.example/panel"),
    )

    assert app_mod.public_url() == "https://forwardbot.example/app"


def test_no_app_button_without_https(monkeypatch):
    """<b>تلگرام فقط https را می‌پذیرد.</b>

    دکمه‌ای که با خطای تلگرام باز نشود، از نبودنش بدتر است — کاربر
    فکر می‌کند سرویس خراب است.
    """
    from telkap.web import miniapp as app_mod

    for base in ("", "http://forwardbot.example/panel", "forwardbot.example"):
        monkeypatch.setattr(
            app_mod, "get_settings", lambda base=base: SimpleNamespace(web_base_url=base)
        )
        assert app_mod.public_url() == "", base


def test_the_app_page_is_really_there():
    """مسیری که nginx به آن اشاره می‌کند ولی فایلش نیست، ۴۰۴ می‌دهد."""
    from pathlib import Path

    page = Path(__file__).parent.parent / "site" / "app" / "index.html"

    assert page.exists()
    text = page.read_text(encoding="utf-8")
    # هویت در سرصفحه می‌رود، نه در نشانی
    assert "X-Telegram-Init-Data" in text
    # و فونت از سرور خودمان، نه CDN
    assert "/fonts/Vazirmatn-Regular.woff2" in text
    assert "fonts.googleapis.com" not in text


# ------------------------------------------------ ساخت و ویرایش کار از اپ
@pytest.mark.asyncio
async def test_only_known_settings_are_accepted(tmp_path, monkeypatch):
    """<b>ورودی از بیرون می‌آید و فهرست کلیدها بسته است.</b>

    اگر هر کلیدی پذیرفته می‌شد، کسی می‌توانست کلیدهایی بنویسد که ما
    هرگز اعتبارسنجی‌شان نکرده‌ایم — یا کلیدهای داخلیِ آینده را از
    بیرون بنشاند.
    """
    from telkap.web.miniapp import _clean_settings

    cfg, problems = _clean_settings({"remove_links": True}, {})
    assert cfg["remove_links"] is True and problems == []

    _cfg, problems = _clean_settings({"is_admin": True}, {})
    assert problems == ["is_admin"]

    # و مقدار خارج از دامنه بریده می‌شود، نه اینکه هرچه آمد بنشیند
    cfg, _ = _clean_settings({"delay_seconds": 10 ** 9}, {})
    assert cfg["delay_seconds"] == 86_400
    cfg, _ = _clean_settings({"delay_seconds": -5}, {})
    assert cfg["delay_seconds"] == 0

    # گزینه‌ای که در فهرست نیست، رد می‌شود
    _cfg, problems = _clean_settings({"order_mode": "هرچه"}, {})
    assert problems == ["order_mode"]


@pytest.mark.asyncio
async def test_settings_of_someone_elses_task_cannot_be_touched(
    tmp_path, monkeypatch
):
    db_module, _ = await _setup(tmp_path, monkeypatch, settings={})
    try:
        from telkap.models import Task, User

        async with db_module.get_session() as db:
            if await db.get(User, 8) is None:
                db.add(User(id=8, first_name="دیگری"))
            theirs = Task(
                user_id=8, title="مالِ او",
                source_ref="@t", source_id=-200,
                dest_ref="@t2", dest_id=-201, enabled=True,
                settings={"remove_links": False},
            )
            db.add(theirs)
            await db.commit()
            await db.refresh(theirs)
            theirs_id = theirs.id

        client = await _client(monkeypatch)
        mine = {"X-Telegram-Init-Data": _fresh(7)}

        for path in (f"/app/api/tasks/{theirs_id}", ):
            assert (await client.get(path, headers=mine)).status == 404

        response = await client.post(
            f"/app/api/tasks/{theirs_id}/settings",
            headers=mine,
            json={"remove_links": True},
        )
        assert response.status == 404

        response = await client.post(
            f"/app/api/tasks/{theirs_id}/delete", headers=mine
        )
        assert response.status == 404

        async with db_module.get_session() as db:
            row = await db.get(Task, theirs_id)
            assert row is not None                      # حذف نشده
            assert row.settings["remove_links"] is False  # عوض نشده
    finally:
        await db_module.close_db()


@pytest.mark.asyncio
async def test_creating_a_task_respects_the_plan_limit(tmp_path, monkeypatch):
    """<b>سقف طرح باید همان‌جایی که ربات رعایتش می‌کند، اینجا هم باشد.</b>

    وگرنه اپ راهِ دور زدنِ سقف می‌شد.
    """
    db_module, _ = await _setup(tmp_path, monkeypatch, settings={})
    try:
        client = await _client(monkeypatch)
        mine = {"X-Telegram-Init-Data": _fresh(7)}

        response = await client.post(
            "/app/api/tasks",
            headers=mine,
            json={"source": "-100", "dest": "-101"},
        )

        # کاربر تست اکانتِ متصل ندارد؛ همان‌جا جلویش گرفته می‌شود
        assert response.status in (402, 409)
        body = await response.json()
        assert "error" in body
    finally:
        await db_module.close_db()


@pytest.mark.asyncio
async def test_a_task_cannot_copy_a_channel_onto_itself(tmp_path, monkeypatch):
    """حلقه‌ی بی‌پایان، و هیچ‌کس هم نمی‌خواهدش."""
    db_module, _ = await _setup(tmp_path, monkeypatch, settings={})
    try:
        from telkap.models import User

        async with db_module.get_session() as db:
            person = await db.get(User, 7)
            person.session_enc = "x"           # وانمود کن وصل است
            await db.commit()

        client = await _client(monkeypatch)
        response = await client.post(
            "/app/api/tasks",
            headers={"X-Telegram-Init-Data": _fresh(7)},
            json={"source": "-100", "dest": "-100"},
        )

        # یا سقف/اشتراک جلویش را می‌گیرد یا خودِ شرط؛ در هر حال ساخته نمی‌شود
        assert response.status >= 400
    finally:
        await db_module.close_db()


@pytest.mark.asyncio
async def test_the_chats_route_says_when_no_account_is_connected(
    tmp_path, monkeypatch
):
    """<b>«وصل نیست» یک حالت است، نه خطای عمومی.</b>

    اپ با همین جواب می‌تواند دکمه‌ی «اتصال در ربات» نشان بدهد به‌جای
    یک پیام خطای بی‌فایده.
    """
    db_module, _ = await _setup(tmp_path, monkeypatch, settings={})
    try:
        client = await _client(monkeypatch)
        response = await client.get(
            "/app/api/chats", headers={"X-Telegram-Init-Data": _fresh(7)}
        )

        assert response.status == 409
        assert "وصل نیست" in (await response.json())["error"]
    finally:
        await db_module.close_db()


@pytest.mark.asyncio
async def test_wallet_and_stats_are_per_person(tmp_path, monkeypatch):
    db_module, _ = await _setup(tmp_path, monkeypatch, settings={})
    try:
        from telkap.services import wallet

        await wallet.credit(7, 50_000, note="مالِ من")

        client = await _client(monkeypatch)

        mine = await (await client.get(
            "/app/api/wallet", headers={"X-Telegram-Init-Data": _fresh(7)}
        )).json()
        assert mine["balance"] == 50_000

        theirs = await (await client.get(
            "/app/api/wallet", headers={"X-Telegram-Init-Data": _fresh(8)}
        )).json()
        assert theirs["balance"] == 0

        stats = await (await client.get(
            "/app/api/stats", headers={"X-Telegram-Init-Data": _fresh(7)}
        )).json()
        assert "speed" in stats and "daily" in stats
    finally:
        await db_module.close_db()


def test_the_nginx_config_sends_the_api_to_the_bot():
    """<b>باگی که بی‌صدا بود و پیامش گمراه‌کننده.</b>

    در nginx طولانی‌ترین پیشوند برنده است. بدون یک بلوک جدا برای
    «/app/api»، آن مسیر هم زیر «location /app» می‌افتاد و try_files
    به index.html می‌رسید — یعنی رابط JSON با کد ۲۰۰ یک صفحه‌ی HTML
    برمی‌گرداند. اپ آن را «کاربر ناشناس» می‌خواند و می‌گفت «هنوز
    ربات را استارت نکرده‌اید»، که هیچ ربطی به علت نداشت.

    اسکریپت bash است و تست واحد ندارد، ولی نبودنِ این بلوک همان یک
    خط است و همین‌جا گرفته می‌شود.
    """
    from pathlib import Path

    script = (
        Path(__file__).parent.parent / "deploy" / "web-setup.sh"
    ).read_text(encoding="utf-8")

    assert "location /app/api {" in script
    assert "location /app {" in script
    # و باید پیش از فایل‌های ثابت بیاید تا خواندنش گمراه نکند
    assert script.index("location /app/api {") < script.index("location /app {")
    # و واقعاً به ربات پراکسی شود، نه try_files
    api_block = script.split("location /app/api {")[1].split("}")[0]
    assert "proxy_pass" in api_block
    assert "try_files" not in api_block


def test_the_api_prefix_the_app_calls_matches_the_one_nginx_proxies():
    """اگر یکی عوض شود و دیگری نه، همان باگ برمی‌گردد."""
    from pathlib import Path

    from telkap.web.miniapp import API_PREFIX

    root = Path(__file__).parent.parent
    page = (root / "site" / "app" / "index.html").read_text(encoding="utf-8")
    script = (root / "deploy" / "web-setup.sh").read_text(encoding="utf-8")

    assert f"fetch('{API_PREFIX}'" in page
    assert f"location {API_PREFIX} {{" in script


def test_every_setting_the_app_accepts_is_a_real_setting():
    """<b>غلط املایی در نام تنظیم، بی‌صداترین باگ ممکن است.</b>

    یک کلید نادرست در فهرست‌های مجاز، هم ذخیره می‌شود و هم برمی‌گردد
    و اپ سوئیچش را درست نشان می‌دهد — ولی کپی‌کننده هرگز نگاهش
    نمی‌کند. کاربر گزینه را روشن می‌کند و هیچ اتفاقی نمی‌افتد.
    """
    from telkap.services.defaults import DEFAULT_SETTINGS
    from telkap.web import miniapp

    known = set(
        (
            *miniapp.BOOL_SETTINGS, *miniapp.TEXT_SETTINGS,
            *miniapp.INT_SETTINGS, *miniapp.CHOICE_SETTINGS,
            *miniapp.LIST_SETTINGS,
        )
    )
    assert known <= set(DEFAULT_SETTINGS), sorted(known - set(DEFAULT_SETTINGS))


def test_the_choices_match_the_modules_that_own_them():
    """گزینه‌ها اینجا دوباره نوشته شده‌اند تا وب سبک بماند؛ اگر منبع
    اصلی عوض شود و اینجا نه، کاربر گزینه‌ای می‌بیند که کار نمی‌کند."""
    from telkap.services.aiskills import LANGUAGES, STYLES
    from telkap.services.watermark import POSITIONS
    from telkap.web.miniapp import CHOICE_SETTINGS

    assert set(CHOICE_SETTINGS["watermark_position"]) == set(POSITIONS)
    assert set(CHOICE_SETTINGS["ai_style"]) == set(STYLES)
    assert set(CHOICE_SETTINGS["ai_language"]) == set(LANGUAGES)


def test_media_kinds_outside_the_known_set_are_dropped():
    """«allowed_media» به کپی‌کننده می‌گوید چه چیزی رد شود؛ یک مقدار
    ناشناس در آن یعنی فیلتری که هیچ‌وقت مطابقت نمی‌کند."""
    from telkap.web.miniapp import _clean_settings

    cfg, problems = _clean_settings(
        {"allowed_media": ["photo", "video", "قاچاقی", "photo"]}, {}
    )
    assert problems == []
    assert cfg["allowed_media"] == ["photo", "video"]


def test_a_list_setting_that_is_not_a_list_is_refused():
    from telkap.web.miniapp import _clean_settings

    _, problems = _clean_settings({"route_words": "تخفیف"}, {})
    assert problems == ["route_words"]


def test_route_words_are_capped_in_count_and_length():
    """ورودی از بیرون می‌آید و مستقیم روی هر پست اجرا می‌شود."""
    from telkap.web.miniapp import MAX_LIST_ITEM_CHARS, MAX_LIST_ITEMS, _clean_settings

    cfg, problems = _clean_settings(
        {"route_words": [f"واژه{n}" for n in range(200)] + ["ب" * 500]}, {}
    )
    assert problems == []
    assert len(cfg["route_words"]) <= MAX_LIST_ITEMS
    assert max(len(word) for word in cfg["route_words"]) <= MAX_LIST_ITEM_CHARS


def _app_page() -> str:
    from pathlib import Path

    return (
        Path(__file__).parent.parent / "site" / "app" / "index.html"
    ).read_text(encoding="utf-8")


def _between(text: str, start: str, end: str) -> str:
    body = text.split(start, 1)[1]
    return body.split(end, 1)[0]


def test_every_setting_the_page_offers_is_one_the_server_accepts():
    """<b>سکوتِ کامل، اگر یکی از این دو طرف عوض شود.</b>

    صفحه یک سوئیچ نشان می‌دهد، کاربر می‌زندش، و سرور کلید ناشناس را
    با ۴۰۰ رد می‌کند. تنها نشانه‌اش یک پیام خطای کوتاه است که کاربر
    به «اینترنت» ربطش می‌دهد. پس هر کلیدی که در صفحه هست باید در
    فهرست مجاز سرور هم باشد.
    """
    import re

    from telkap.web import miniapp

    allowed = set(
        (
            *miniapp.BOOL_SETTINGS, *miniapp.TEXT_SETTINGS,
            *miniapp.INT_SETTINGS, *miniapp.CHOICE_SETTINGS,
            *miniapp.LIST_SETTINGS,
        )
    )
    sections = _between(_app_page(), "var SECTIONS = [", "\n  ];")
    keys = set(re.findall(r"\bk: '([a-z_]+)'", sections))

    assert keys, "کلیدی پیدا نشد؛ ساختار صفحه عوض شده"
    assert keys <= allowed, sorted(keys - allowed)


def test_every_key_a_preset_writes_is_one_the_server_accepts():
    """الگو یک درخواست است با ده‌ها کلید؛ یک کلید بد یعنی کل الگو
    رد می‌شود، نه فقط همان یکی."""
    import re

    from telkap.web import miniapp

    allowed = set(
        (
            *miniapp.BOOL_SETTINGS, *miniapp.TEXT_SETTINGS,
            *miniapp.INT_SETTINGS, *miniapp.CHOICE_SETTINGS,
            *miniapp.LIST_SETTINGS,
        )
    )
    presets = _between(_app_page(), "var PRESETS = [", "\n  ];")
    keys = set(re.findall(r"\b([a-z_]+):\s", presets))

    assert keys, "الگویی پیدا نشد؛ ساختار صفحه عوض شده"
    assert keys <= allowed, sorted(keys - allowed)


def test_every_preset_value_passes_the_servers_own_validation():
    """اگر مقدارِ یک الگو خارج از دامنه باشد، الگو با ۴۰۰ برمی‌گردد."""
    import json
    import re

    from telkap.web.miniapp import _clean_settings

    presets = _between(_app_page(), "var PRESETS = [", "\n  ];")
    posted = {}
    for key, raw in re.findall(r"\b([a-z_]+):\s*('[^']*'|true|false|\d+)", presets):
        posted[key] = json.loads(raw.replace("'", '"'))

    _, problems = _clean_settings(posted, {})
    assert problems == []


def test_the_media_chips_cover_every_media_kind():
    """نوعی که در صفحه نباشد، هیچ‌وقت نمی‌شود روشنش کرد — و چون
    «allowed_media» فهرستِ اجازه است، نبودنش یعنی آن نوع برای همیشه
    از این صفحه خاموش می‌ماند."""
    import re

    from telkap.services.defaults import MEDIA_KINDS

    media = _between(_app_page(), "k: 'allowed_media'", "] },")
    shown = set(re.findall(r"\['([a-z_]+)',", media))

    assert shown == set(MEDIA_KINDS), sorted(set(MEDIA_KINDS) ^ shown)


def _signed(token: str, *, auth_date: int, user_id: int = 5) -> str:
    """initDataیی که واقعاً با این توکن امضا شده."""
    import hashlib
    import hmac
    import json
    from urllib.parse import urlencode

    pairs = {"auth_date": str(auth_date), "user": json.dumps({"id": user_id})}
    check_string = "\n".join(f"{k}={pairs[k]}" for k in sorted(pairs))
    secret = hmac.new(b"WebAppData", token.encode(), hashlib.sha256).digest()
    pairs["hash"] = hmac.new(secret, check_string.encode(), hashlib.sha256).hexdigest()
    return urlencode(pairs)


def test_diagnose_separates_the_three_reasons_auth_can_fail():
    """<b>سه علتِ کاملاً متفاوت، تا امروز یک پیام می‌دادند.</b>

    «شناسایی نشدید» نه به کاربر چیزی می‌گفت نه به ما؛ پیدا کردن علت
    به حدس زدن می‌گذشت و یک بار هم حدسمان غلط بود. هر علت باید
    درمانِ خودش را نام ببرد.
    """
    from telkap.web.miniapp import diagnose

    token = "123456:TEST"
    now = 1_800_000_000

    # ۱) اصلاً از داخل تلگرام باز نشده
    assert "تلگرام" in diagnose("", token)

    # ۲) امضا با توکنِ دیگری زده شده
    other = diagnose(_signed("999:OTHER", auth_date=now - 10), token, now=now)
    assert "توکن" in other

    # ۳) صفحه از دیروز باز مانده
    stale = diagnose(_signed(token, auth_date=now - 90_000), token, now=now)
    assert "دوباره" in stale

    # و وقتی همه‌چیز درست است، حرفی برای گفتن نیست
    assert diagnose(_signed(token, auth_date=now - 10), token, now=now) == ""


def test_diagnose_never_leaks_the_token_or_the_right_signature():
    """این مسیر هویت نمی‌خواهد، پس هرکسی می‌تواند صدایش بزند."""
    from telkap.web.miniapp import diagnose

    token = "123456:SECRETTOKEN"
    now = 1_800_000_000
    for text in (
        diagnose("", token),
        diagnose("junk", token, now=now),
        diagnose(_signed("999:OTHER", auth_date=now - 10), token, now=now),
    ):
        assert "SECRETTOKEN" not in text
        assert "123456" not in text


def _sign_with(token: str, pairs: dict, *, skip=("hash",)) -> str:
    """initData واقعی: امضا روی همان فیلدهایی که فرستاده می‌شوند."""
    import hashlib
    import hmac
    from urllib.parse import urlencode

    fields = {k: v for k, v in pairs.items() if k not in skip}
    check_string = "\n".join(f"{k}={fields[k]}" for k in sorted(fields))
    secret = hmac.new(b"WebAppData", token.encode(), hashlib.sha256).digest()
    out = dict(pairs)
    out["hash"] = hmac.new(secret, check_string.encode(), hashlib.sha256).hexdigest()
    return urlencode(out)


def test_a_field_with_an_empty_value_does_not_break_the_signature():
    """<b>باگی که مینی‌اپ را برای همیشه از کار انداخته بود.</b>

    parse_qsl بدون keep_blank_values هر فیلدِ خالی را — مثلاً
    «start_param=» — بی‌صدا دور می‌ریزد. ولی تلگرام امضایش را روی
    همه‌ی فیلدهایی که فرستاده حساب کرده، از جمله خالی‌ها. رشته‌ی ما
    کوتاه‌تر می‌شد و امضا هیچ‌وقت نمی‌خواند؛ نتیجه‌اش «شناسایی نشدید»
    بود، بی هیچ ردی در لاگ.
    """
    import json
    import time

    from telkap.web.miniapp import user_id_from

    token = "123456:TEST"
    init_data = _sign_with(token, {
        "auth_date": str(int(time.time())),
        "start_param": "",
        "user": json.dumps({"id": 42}),
    })

    assert user_id_from(init_data, token) == 42


def test_init_data_is_accepted_whether_or_not_signature_joins_the_check_string():
    """<b>جزئیاتی که یک بار عوض شده و ممکن است دوباره عوض شود.</b>

    مستندات می‌گویند «signature» مثل «hash» بیرون می‌ماند. اگر روزی
    فرق کند، هزینه‌اش این است که هیچ‌کس نمی‌تواند وارد شود — پس هر
    دو حالت باید پذیرفته شوند.
    """
    import json
    import time

    from telkap.web.miniapp import user_id_from

    token = "123456:TEST"
    fields = {
        "auth_date": str(int(time.time())),
        "signature": "ed25519-thing",
        "user": json.dumps({"id": 7}),
    }

    # همان‌طور که مستند شده: بدون signature
    assert user_id_from(_sign_with(token, fields, skip=("hash", "signature")), token) == 7
    # و اگر روزی با signature امضا شد، باز هم باید کار کند
    assert user_id_from(_sign_with(token, fields, skip=("hash",)), token) == 7


def test_a_forged_signature_is_still_refused():
    """پذیرفتن دو رشته نباید به معنی پذیرفتن هر رشته‌ای باشد."""
    import json
    import time

    from telkap.web.miniapp import user_id_from

    forged = _sign_with("999:SOMEONE-ELSE", {
        "auth_date": str(int(time.time())),
        "user": json.dumps({"id": 42}),
    })

    assert user_id_from(forged, "123456:TEST") is None


def _upload(raw: bytes, filename: str):
    """یک آپلود multipart واقعی — همان چیزی که مرورگر می‌فرستد."""
    from aiohttp import FormData

    form = FormData()
    form.add_field("logo", raw, filename=filename, content_type="image/png")
    return form


async def _own_task(db_module, user_id: int = 7, **kw) -> int:
    from telkap.models import Task, User

    async with db_module.get_session() as db:
        if await db.get(User, user_id) is None:
            db.add(User(id=user_id, first_name="رامین"))
        fields = {
            "user_id": user_id, "title": "کار من",
            "source_ref": "@s", "source_id": -300,
            "dest_ref": "@d", "dest_id": -301, "enabled": True,
            "settings": {},
        }
        fields.update(kw)
        task = Task(**fields)
        db.add(task)
        await db.commit()
        await db.refresh(task)
        return task.id


@pytest.mark.asyncio
async def test_rules_can_be_added_and_removed_from_the_app(tmp_path, monkeypatch):
    """قواعد متنی همان چیزی‌اند که هر روز عوض می‌شوند."""
    db_module, _ = await _setup(tmp_path, monkeypatch, settings={})
    try:
        task_id = await _own_task(db_module)
        client = await _client(monkeypatch)
        mine = {"X-Telegram-Init-Data": _fresh(7)}

        added = await client.post(
            f"/app/api/tasks/{task_id}/rules", headers=mine,
            json={"kind": "replace", "pattern": "رقیب", "replacement": "ما"},
        )
        assert added.status == 200
        rule_id = (await added.json())["id"]

        listed = await (await client.get(
            f"/app/api/tasks/{task_id}/rules", headers=mine
        )).json()
        assert [r["pattern"] for r in listed["rules"]] == ["رقیب"]

        gone = await client.post(
            f"/app/api/tasks/{task_id}/rules/{rule_id}/delete", headers=mine
        )
        assert gone.status == 200
        empty = await (await client.get(
            f"/app/api/tasks/{task_id}/rules", headers=mine
        )).json()
        assert empty["rules"] == []
    finally:
        await db_module.close_db()


@pytest.mark.asyncio
async def test_a_broken_regex_is_refused_at_the_door(tmp_path, monkeypatch):
    """<b>وگرنه هر پست روی همان الگو خطا می‌دهد.</b>

    الگوی خرابی که ذخیره شود، کار را بی‌صدا از کار می‌اندازد و علتش
    فقط در لاگ می‌ماند. همان لحظه باید گفته شود.
    """
    db_module, _ = await _setup(tmp_path, monkeypatch, settings={})
    try:
        task_id = await _own_task(db_module)
        client = await _client(monkeypatch)
        mine = {"X-Telegram-Init-Data": _fresh(7)}

        bad = await client.post(
            f"/app/api/tasks/{task_id}/rules", headers=mine,
            json={"kind": "regex", "pattern": "([a-z"},
        )
        assert bad.status == 400
        assert "regex" in (await bad.json())["error"]
    finally:
        await db_module.close_db()


@pytest.mark.asyncio
async def test_rules_of_someone_elses_task_are_out_of_reach(tmp_path, monkeypatch):
    """همان قفلی که روی تنظیمات هست باید روی قواعد هم باشد."""
    db_module, _ = await _setup(tmp_path, monkeypatch, settings={})
    try:
        from telkap.models import Rule

        theirs = await _own_task(db_module, user_id=8, title="مالِ او")
        async with db_module.get_session() as db:
            rule = Rule(task_id=theirs, kind="block", pattern="خصوصی")
            db.add(rule)
            await db.commit()
            await db.refresh(rule)
            rule_id = rule.id

        client = await _client(monkeypatch)
        mine = {"X-Telegram-Init-Data": _fresh(7)}

        assert (await client.get(
            f"/app/api/tasks/{theirs}/rules", headers=mine
        )).status == 404
        assert (await client.post(
            f"/app/api/tasks/{theirs}/rules", headers=mine,
            json={"kind": "block", "pattern": "x"},
        )).status == 404
        assert (await client.post(
            f"/app/api/tasks/{theirs}/rules/{rule_id}/delete", headers=mine
        )).status == 404

        # و قاعده هنوز سر جایش است
        async with db_module.get_session() as db:
            assert await db.get(Rule, rule_id) is not None
    finally:
        await db_module.close_db()


@pytest.mark.asyncio
async def test_cloning_replaces_rules_instead_of_merging_them(tmp_path, monkeypatch):
    """اگر ادغام می‌شد، هیچ‌کس نمی‌فهمید کدام قاعده از کجا آمده."""
    db_module, _ = await _setup(tmp_path, monkeypatch, settings={})
    try:
        from telkap.models import Rule

        source = await _own_task(
            db_module, title="الگو", settings={"remove_links": True}
        )
        target = await _own_task(db_module, title="هدف", settings={})
        async with db_module.get_session() as db:
            db.add(Rule(task_id=source, kind="block", pattern="تبلیغ"))
            db.add(Rule(task_id=target, kind="block", pattern="قدیمی"))
            await db.commit()

        client = await _client(monkeypatch)
        mine = {"X-Telegram-Init-Data": _fresh(7)}

        done = await client.post(
            f"/app/api/tasks/{target}/clone", headers=mine, json={"from": source}
        )
        assert done.status == 200

        detail = await (await client.get(
            f"/app/api/tasks/{target}", headers=mine
        )).json()
        assert detail["settings"]["remove_links"] is True

        rules = await (await client.get(
            f"/app/api/tasks/{target}/rules", headers=mine
        )).json()
        assert [r["pattern"] for r in rules["rules"]] == ["تبلیغ"]
    finally:
        await db_module.close_db()


@pytest.mark.asyncio
async def test_a_file_that_is_not_an_image_is_refused(tmp_path, monkeypatch):
    """<b>تنها مسیری که ورودی باینری می‌گیرد.</b>

    نه نام فایل باور می‌شود نه Content-Type؛ محتوا باید واقعاً باز شود.
    """
    db_module, _ = await _setup(tmp_path, monkeypatch, settings={})
    try:
        task_id = await _own_task(db_module)
        client = await _client(monkeypatch)

        response = await client.post(
            f"/app/api/tasks/{task_id}/logo",
            headers={"X-Telegram-Init-Data": _fresh(7)},
            data=_upload(b"<?php echo 1; ?>", "logo.png"),
        )
        assert response.status == 400
        assert "تصویر" in (await response.json())["error"]
    finally:
        await db_module.close_db()


@pytest.mark.asyncio
async def test_a_real_image_is_stored_under_the_task_id(tmp_path, monkeypatch):
    """مسیر از آیدی کار ساخته می‌شود، نه از نامی که فرستنده داده —
    وگرنه «../../» راهی برای نوشتن روی هر فایلی بود."""
    db_module, _ = await _setup(tmp_path, monkeypatch, settings={})
    try:
        import io

        from PIL import Image

        task_id = await _own_task(db_module)
        client = await _client(monkeypatch)
        # _client تنظیماتِ ساختگی می‌گذارد؛ این مسیر به پوشه‌ی دانلود
        # هم نیاز دارد و باید داخل tmp_path بماند
        monkeypatch.setattr(
            miniapp, "get_settings",
            lambda: SimpleNamespace(bot_token=TOKEN, download_dir=tmp_path / "dl"),
        )

        buffer = io.BytesIO()
        Image.new("RGBA", (40, 20), (255, 0, 0, 128)).save(buffer, "PNG")

        response = await client.post(
            f"/app/api/tasks/{task_id}/logo",
            headers={"X-Telegram-Init-Data": _fresh(7)},
            data=_upload(buffer.getvalue(), "../../evil.png"),
        )
        assert response.status == 200

        from telkap.models import Task

        async with db_module.get_session() as db:
            task = await db.get(Task, task_id)
        stored = task.settings["watermark_logo"]
        assert stored.endswith(f"task-{task_id}.png")
        assert ".." not in stored
        assert task.settings["watermark_enabled"] is True
    finally:
        await db_module.close_db()
