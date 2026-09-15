"""تست افزودنِ اکانت سرویس.

<b>چرا جدا از ورودِ مشتری‌ها.</b> ظاهرِ هر دو یکی است — یک QR، یک اسکن —
ولی مقصدشان نه: آنجا سشن روی ردیفِ کاربر می‌نشیند و رانتایمی با
هندلرهای آپدیت ساخته می‌شود؛ اکانت سرویس هیچ‌کدام را نمی‌خواهد.

<b>و چرا این تست‌ها روی «دو بار» تمرکز دارند.</b> یک اکانت که دو ردیف
بگیرد از بیرون بی‌خطر به نظر می‌رسد — فقط یک سطر اضافه در فهرست — ولی
استخر آن را دو خواننده می‌شمارد و دو برابر بار روی همان یک اکانت
می‌ریزد. یعنی دقیقاً همان محدودیتِ نرخی که استخر برای دوری از آن ساخته
شد، از دلِ خودش بیرون می‌آید.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from tests.test_copier import _setup


class _FakeQR:
    def __init__(self, *, needs_password: bool = False) -> None:
        self.url = "tg://login?token=SERVICE"
        self.recreated = 0
        self._needs_password = needs_password

    async def recreate(self) -> None:
        self.recreated += 1
        self.url = f"tg://login?token=AFTER{self.recreated}"

    async def wait(self, timeout=None):
        from telethon.errors import SessionPasswordNeededError

        if self._needs_password:
            raise SessionPasswordNeededError(request=None)
        return True


class _FakeClient:
    def __init__(self, qr: _FakeQR, account_id: int = 555) -> None:
        self._qr = qr
        self._account_id = account_id
        self.disconnected = False

    async def connect(self) -> None:
        return None

    async def qr_login(self):
        return self._qr

    async def sign_in(self, password=None):
        return None

    async def get_me(self):
        return SimpleNamespace(
            id=self._account_id, first_name="خواننده", last_name=None,
            username="reader", phone="989120000000",
        )

    async def disconnect(self) -> None:
        self.disconnected = True

    class _Session:
        @staticmethod
        def save() -> str:
            return "SERVICE-SESSION"

    session = _Session()


def _install(monkeypatch, client) -> None:
    from telkap.services import svclogin

    monkeypatch.setattr(svclogin, "_new_client", lambda: client)


@pytest.mark.asyncio
async def test_a_scan_creates_a_usable_service_account(tmp_path, monkeypatch):
    db_module, _ = await _setup(tmp_path, monkeypatch, settings={})
    try:
        from sqlalchemy import select

        from telkap.models import ServiceAccount
        from telkap.services import svclogin

        _install(monkeypatch, _FakeClient(_FakeQR()))

        url = await svclogin.start(99, "خواننده ۱")
        assert url.startswith("tg://login?token=")
        assert await svclogin.result(99, 1) == "done"

        async with db_module.get_session() as db:
            account = (await db.execute(select(ServiceAccount))).scalars().first()
        assert account is not None
        assert account.session_enc, "سشن ذخیره نشد"
        assert account.account_id == 555
        assert account.label == "خواننده ۱"
        assert account.enabled and account.state == ServiceAccount.STATE_OK
    finally:
        svclogin._pending.clear()
        await db_module.close_db()


@pytest.mark.asyncio
async def test_the_session_is_never_stored_in_the_clear(tmp_path, monkeypatch):
    """<b>این اکانت‌ها مالِ خودمان‌اند ولی همان ارزش را دارند.</b>

    هرکس سشن را بخواند، اکانت را دارد — و این یکی اکانتِ زیرساختِ
    سرویس است، نه یکی از مشتری‌ها.
    """
    db_module, _ = await _setup(tmp_path, monkeypatch, settings={})
    try:
        from sqlalchemy import select

        from telkap.crypto import decrypt
        from telkap.models import ServiceAccount
        from telkap.services import svclogin

        _install(monkeypatch, _FakeClient(_FakeQR()))
        await svclogin.start(99)
        await svclogin.result(99, 1)

        async with db_module.get_session() as db:
            account = (await db.execute(select(ServiceAccount))).scalars().first()

        assert account.session_enc != "SERVICE-SESSION"
        assert decrypt(account.session_enc) == "SERVICE-SESSION"
    finally:
        svclogin._pending.clear()
        await db_module.close_db()


@pytest.mark.asyncio
async def test_adding_the_same_account_twice_does_not_double_the_pool(
    tmp_path, monkeypatch
):
    """<b>خرابی‌ای که شبیه خرابی به نظر نمی‌رسد.</b>

    دو ردیف برای یک اکانت فقط یک سطر اضافه در فهرست است — ولی استخر
    دو خواننده می‌شمارد و دو برابر بار روی همان یک اکانت می‌ریزد.
    نتیجه‌اش دقیقاً همان محدودیتِ نرخی است که استخر برای دوری از آن
    ساخته شد.
    """
    db_module, _ = await _setup(tmp_path, monkeypatch, settings={})
    try:
        from sqlalchemy import func, select

        from telkap.models import ServiceAccount
        from telkap.services import svclogin

        for label in ("بار اول", "بار دوم"):
            _install(monkeypatch, _FakeClient(_FakeQR()))
            await svclogin.start(99, label)
            await svclogin.result(99, 1)

        async with db_module.get_session() as db:
            count = await db.scalar(select(func.count(ServiceAccount.id)))
        assert count == 1, "همان اکانت دو ردیف گرفت"
    finally:
        svclogin._pending.clear()
        await db_module.close_db()


@pytest.mark.asyncio
async def test_re_adding_a_banned_account_brings_it_back(tmp_path, monkeypatch):
    """اکانتی که بسته شده و دوباره وارد می‌شود، باید از نو سالم شمرده
    شود — وگرنه تنها راهش حذفِ دستی است و کسی نمی‌داند چرا کار نمی‌کند."""
    db_module, _ = await _setup(tmp_path, monkeypatch, settings={})
    try:
        from sqlalchemy import select

        from telkap.models import ServiceAccount
        from telkap.services import pool, svclogin

        _install(monkeypatch, _FakeClient(_FakeQR()))
        await svclogin.start(99)
        await svclogin.result(99, 1)

        async with db_module.get_session() as db:
            account = (await db.execute(select(ServiceAccount))).scalars().first()
            account_id = account.id
        await pool.mark_banned(account_id, "تست")

        _install(monkeypatch, _FakeClient(_FakeQR()))
        await svclogin.start(99)
        await svclogin.result(99, 1)

        async with db_module.get_session() as db:
            account = await db.get(ServiceAccount, account_id)
        assert account.enabled
        assert account.state == ServiceAccount.STATE_OK
        assert account.note == ""
    finally:
        svclogin._pending.clear()
        await db_module.close_db()


@pytest.mark.asyncio
async def test_two_step_password_is_offered_not_swallowed(tmp_path, monkeypatch):
    db_module, _ = await _setup(tmp_path, monkeypatch, settings={})
    try:
        from telkap.services import svclogin

        _install(monkeypatch, _FakeClient(_FakeQR(needs_password=True)))
        await svclogin.start(99)

        assert await svclogin.result(99, 1) == "password"
        assert svclogin.pending(99).needs_password
    finally:
        svclogin._pending.clear()
        await db_module.close_db()


@pytest.mark.asyncio
async def test_an_unscanned_code_is_refreshed(tmp_path, monkeypatch):
    db_module, _ = await _setup(tmp_path, monkeypatch, settings={})
    try:
        from telkap.services import svclogin

        qr = _FakeQR()
        _install(monkeypatch, _FakeClient(qr))
        first = await svclogin.start(99)
        second = await svclogin.refresh(99)

        assert second and second != first
        assert qr.recreated == 1
    finally:
        svclogin._pending.clear()
        await db_module.close_db()


@pytest.mark.asyncio
async def test_the_login_client_is_not_left_hanging(tmp_path, monkeypatch):
    """<b>دو کلاینت روی یک سشن، دردسرِ بی‌دلیل است.</b>

    استخر خودش هر وقت لازم شد از روی سشن وصل می‌شود. کلاینتِ ورود اگر
    باز بماند، همان اکانت دو اتصال همزمان دارد.
    """
    db_module, _ = await _setup(tmp_path, monkeypatch, settings={})
    try:
        from telkap.services import svclogin

        client = _FakeClient(_FakeQR())
        _install(monkeypatch, client)
        await svclogin.start(99)
        await svclogin.result(99, 1)

        assert client.disconnected
    finally:
        svclogin._pending.clear()
        await db_module.close_db()


@pytest.mark.asyncio
async def test_asking_after_the_flow_is_gone_says_so(tmp_path, monkeypatch):
    db_module, _ = await _setup(tmp_path, monkeypatch, settings={})
    try:
        from telkap.services import svclogin

        svclogin._pending.clear()
        assert await svclogin.result(99, 0.01) == "gone"
        assert await svclogin.refresh(99) is None
    finally:
        await db_module.close_db()


def test_the_pool_screen_is_locked_to_system_admins():
    """<b>اینجا اکانت‌های زنده اضافه و حذف می‌شوند.</b>

    قفل روی خودِ روتر است تا با اضافه شدن هر هندلرِ تازه، گارد جا
    نماند — همان الگوی بقیه‌ی بخش‌های ادمین.
    """
    from telkap.handlers import LOCKED, admin_pool
    from telkap.services import roles

    assert (admin_pool.router, roles.CAP_SYSTEM) in LOCKED
