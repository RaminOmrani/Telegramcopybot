"""تست بخش وبِ نماینده‌ها.

<b>چرا این فایل سخت‌گیر است.</b> تا امروز پنل یک نگهبان داشت: «مدیر
هستی یا نه». حالا یک گروهِ دوم هم وارد می‌شود که مدیر نیست — و هر
اشتباهی اینجا یعنی نماینده‌ای که رسیدهای مالی، فهرست کاربران و
تنظیمات درگاه پرداخت را می‌بیند.

پس دو چیز جداگانه سنجیده می‌شود: نماینده به بخش خودش <b>می‌رسد</b>،
و به هیچ‌جای دیگر <b>نمی‌رسد</b>.
"""
from __future__ import annotations

import pytest
import pytest_asyncio
from aiohttp.test_utils import TestClient, TestServer

from telkap.web import auth
from telkap.web.render import url
from tests.test_copier import _setup

_CLIENTS: list = []


async def _serve(monkeypatch):
    from telkap.web import server

    client = TestClient(TestServer(server.build_app(bot=None)))
    await client.start_server()
    _CLIENTS.append(client)
    return client


# asyncio_mode=strict است: فیکسچرِ async با pytest.fixture اصلاً اجرا
# نمی‌شود و بی‌صدا رد می‌شود — یعنی سرورها بسته نمی‌مانند و خطای
# تستِ بعدی زیر سر و صدایشان گم می‌شود.
@pytest_asyncio.fixture(autouse=True)
async def _close_clients():
    yield
    while _CLIENTS:
        await _CLIENTS.pop().close()


async def _person(db_module, user_id: int, *, is_reseller: bool):
    from telkap.models import User

    async with db_module.get_session() as db:
        person = await db.get(User, user_id)
        if person is None:
            person = User(id=user_id, first_name="نماینده")
            db.add(person)
        person.is_reseller = is_reseller
        person.reseller_discount = 25
        await db.commit()


def _cookies(token: str) -> dict:
    return {auth.COOKIE_NAME: token}


@pytest.mark.asyncio
async def test_a_reseller_can_open_their_own_pages(tmp_path, monkeypatch):
    db_module, _ = await _setup(tmp_path, monkeypatch, settings={})
    try:
        await _person(db_module, 21, is_reseller=True)
        client = await _serve(monkeypatch)
        token = await auth.start_session(21)

        for path in ("/agent", "/agent/customers", "/agent/sales"):
            response = await client.get(url(path), cookies=_cookies(token))
            assert response.status == 200, path
            body = await response.text()
            assert "پنل نمایندگی" in body
    finally:
        auth.reset()
        await db_module.close_db()


@pytest.mark.asyncio
async def test_a_reseller_cannot_reach_a_single_admin_page(tmp_path, monkeypatch):
    """<b>مهم‌ترین تستِ این فایل.</b>

    نگهبان روی پیشوند است نه روی تک‌تک صفحه‌ها، پس کافی است یکی از
    صفحه‌های مدیریتی از قلم بیفتد تا همه‌شان باز شوند.

    <b>و چرا دقیقاً «۳۰۲ به /agent» سنجیده می‌شود و نه «هرچه جز
    ۲۰۰».</b> نسخه‌ی اول همان «هرچه جز ۲۰۰» را می‌سنجید و با یک
    سوراخِ عمدی در میدل‌ور هم سبز ماند: نماینده از میدل‌ور رد می‌شد و
    بعد بررسیِ توانایی‌ها ۴۰۳ می‌داد. یعنی تست، لایه‌ی دومِ دفاع را
    می‌دید و خیال می‌کرد لایه‌ی اول کار می‌کند. حالا رفتارِ درست
    میخکوب شده و همان سوراخ، تست را می‌شکند.
    """
    db_module, _ = await _setup(tmp_path, monkeypatch, settings={})
    try:
        await _person(db_module, 22, is_reseller=True)
        client = await _serve(monkeypatch)
        token = await auth.start_session(22)

        for path in (
            "/", "/payments", "/users", "/finance", "/resellers",
            "/settings", "/timings", "/activity", "/tasks",
        ):
            response = await client.get(
                url(path), cookies=_cookies(token), allow_redirects=False
            )
            assert response.status == 302, f"{path} → {response.status}"
            assert response.headers["Location"] == url("/agent"), path
    finally:
        auth.reset()
        await db_module.close_db()


@pytest.mark.asyncio
async def test_someone_who_is_neither_is_shown_the_door(tmp_path, monkeypatch):
    """کسی که نه مدیر است نه نماینده، نباید حتی بخش نمایندگی را ببیند."""
    db_module, _ = await _setup(tmp_path, monkeypatch, settings={})
    try:
        await _person(db_module, 23, is_reseller=False)
        client = await _serve(monkeypatch)
        token = await auth.start_session(23)

        response = await client.get(url("/agent"), cookies=_cookies(token))
        assert response.status == 403
    finally:
        auth.reset()
        await db_module.close_db()


@pytest.mark.asyncio
async def test_the_agent_area_still_needs_a_login(tmp_path, monkeypatch):
    """بدون نشست، هیچ‌کدام از این صفحه‌ها باز نمی‌شوند."""
    db_module, _ = await _setup(tmp_path, monkeypatch, settings={})
    try:
        client = await _serve(monkeypatch)
        for path in ("/agent", "/agent/customers", "/agent/sales"):
            response = await client.get(url(path), allow_redirects=False)
            assert response.status in (302, 303, 307), path
            assert response.headers["Location"] == url("/login"), path
    finally:
        auth.reset()
        await db_module.close_db()


@pytest.mark.asyncio
async def test_a_reseller_only_sees_their_own_customers(tmp_path, monkeypatch):
    """<b>داده‌ی نماینده‌ی دیگر نباید اینجا پیدا شود.</b>"""
    db_module, _ = await _setup(tmp_path, monkeypatch, settings={})
    try:
        from telkap.models import User

        await _person(db_module, 24, is_reseller=True)
        await _person(db_module, 25, is_reseller=True)
        async with db_module.get_session() as db:
            db.add(User(id=901, first_name="مالِ بیست‌وچهار", owned_by=24))
            db.add(User(id=902, first_name="مالِ بیست‌وپنج", owned_by=25))
            await db.commit()

        client = await _serve(monkeypatch)
        token = await auth.start_session(24)

        body = await (await client.get(
            url("/agent/customers"), cookies=_cookies(token)
        )).text()

        assert "مالِ بیست‌وچهار" in body
        assert "مالِ بیست‌وپنج" not in body
    finally:
        auth.reset()
        await db_module.close_db()
