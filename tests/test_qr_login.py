"""تست ورود با کد QR.

<b>چرا این راه اضافه شد.</b> مشتری‌ها برای تست نکردن یک دلیل می‌آورند:
«کد ورود تلگرامم را به یک ربات بدهم؟» — و حق دارند، چون در فرهنگ
عمومی خواستنِ کد ورود نشانه‌ی کلاهبرداری است. همان تردید، پیش از هر
تستی، فروش را می‌بندد.

QR همان دسترسی را می‌دهد ولی از راهی که کاربر به آن عادت دارد: دقیقاً
همان جریانی که برای ورود به تلگرام دسکتاپ استفاده می‌شود.

<b>و چون این مسیرِ ورود است، اشتباهش گران است:</b> کدی که تازه نشود،
یا رمز دو مرحله‌ای که راهی برای وارد کردن نداشته باشد، کاربر را در
بن‌بستِ بی‌صدا می‌گذارد — همان چیزی که می‌خواستیم برداریم.
"""
from __future__ import annotations

import pytest

from tests.test_copier import _setup


class _FakeQR:
    def __init__(self, *, scans_after: int = 1, needs_password: bool = False) -> None:
        self.url = "tg://login?token=FIRST"
        self.recreated = 0
        self.waits = 0
        self._scans_after = scans_after
        self._needs_password = needs_password

    async def recreate(self) -> None:
        self.recreated += 1
        self.url = f"tg://login?token=AFTER{self.recreated}"

    async def wait(self, timeout=None):
        from telethon.errors import SessionPasswordNeededError

        self.waits += 1
        if self.waits < self._scans_after:
            raise TimeoutError
        if self._needs_password:
            raise SessionPasswordNeededError(request=None)
        return True


class _FakeClient:
    def __init__(self, qr: _FakeQR) -> None:
        self._qr = qr
        self.disconnected = False

    async def connect(self) -> None:
        return None

    async def qr_login(self):
        return self._qr

    async def get_me(self):
        from types import SimpleNamespace

        return SimpleNamespace(
            id=555, first_name="رامین", last_name=None,
            username="ramin", premium=False, phone="989120000000",
        )

    async def disconnect(self) -> None:
        self.disconnected = True

    def is_connected(self) -> bool:
        return True

    def remove_event_handler(self, handler) -> None:
        return None

    def add_event_handler(self, handler, event=None) -> None:
        return None

    async def catch_up(self) -> None:
        return None

    class _Session:
        @staticmethod
        def save() -> str:
            return "SESSION-STRING"

    session = _Session()


def _install(monkeypatch, qr: _FakeQR) -> _FakeClient:
    from telkap.services.userbot import manager

    client = _FakeClient(qr)
    monkeypatch.setattr(manager, "_new_client", lambda: client)
    return client


@pytest.mark.asyncio
async def test_a_scan_finishes_the_login_without_any_code(tmp_path, monkeypatch):
    """<b>کلِ نکته‌ی این کار.</b> نه شماره‌ای پرسیده می‌شود، نه کدی."""
    db_module, _ = await _setup(tmp_path, monkeypatch, settings={})
    try:
        from telkap.models import User
        from telkap.services.userbot import manager

        _install(monkeypatch, _FakeQR(scans_after=1))

        url = await manager.start_qr_login(7)
        assert url.startswith("tg://login?token=")

        assert await manager.qr_result(7, 1) == "done"

        async with db_module.get_session() as db:
            person = await db.get(User, 7)
        assert person.session_enc, "سشن ذخیره نشد"
        assert person.account_id == 555
    finally:
        manager._pending.clear()
        await db_module.close_db()


@pytest.mark.asyncio
async def test_the_phone_is_taken_from_the_account_not_left_empty(tmp_path, monkeypatch):
    """<b>در ورودِ QR شماره‌ای پرسیده نمی‌شود.</b>

    نسخه‌ی اول شماره‌ی ذخیره‌شده را با رشته‌ی خالی بازنویسی می‌کرد —
    یعنی ورود با QR، شماره‌ی کاربرِ قبلاً واردشده را پاک می‌کرد.
    """
    db_module, _ = await _setup(tmp_path, monkeypatch, settings={})
    try:
        from telkap.models import User
        from telkap.services.userbot import manager

        _install(monkeypatch, _FakeQR(scans_after=1))
        await manager.start_qr_login(7)
        await manager.qr_result(7, 1)

        async with db_module.get_session() as db:
            person = await db.get(User, 7)
        assert person.phone == "989120000000"
    finally:
        manager._pending.clear()
        await db_module.close_db()


@pytest.mark.asyncio
async def test_an_unscanned_code_is_refreshed_not_left_to_rot(tmp_path, monkeypatch):
    """<b>کدی که تازه نشود، بن‌بستِ بی‌صداست.</b>

    کاربری که برود گوشی‌اش را بردارد برمی‌گردد و کدی را اسکن می‌کند
    که دیگر کار نمی‌کند — و هیچ خطایی هم نمی‌بیند، فقط هیچ اتفاقی
    نمی‌افتد.
    """
    db_module, _ = await _setup(tmp_path, monkeypatch, settings={})
    try:
        from telkap.services.userbot import manager

        qr = _FakeQR(scans_after=99)
        _install(monkeypatch, qr)

        first = await manager.start_qr_login(7)
        assert await manager.qr_result(7, 0.01) == "wait"

        second = await manager.refresh_qr(7)
        assert second and second != first
        assert qr.recreated == 1
    finally:
        manager._pending.clear()
        await db_module.close_db()


@pytest.mark.asyncio
async def test_two_step_password_is_reported_not_swallowed(tmp_path, monkeypatch):
    """اکانتی که رمز دو مرحله‌ای دارد باید راهی برای ادامه داشته باشد."""
    db_module, _ = await _setup(tmp_path, monkeypatch, settings={})
    try:
        from telkap.services.userbot import manager

        _install(monkeypatch, _FakeQR(scans_after=1, needs_password=True))
        await manager.start_qr_login(7)

        assert await manager.qr_result(7, 1) == "password"
        pending = manager.pending(7)
        assert pending is not None and pending.needs_password
    finally:
        manager._pending.clear()
        await db_module.close_db()


@pytest.mark.asyncio
async def test_asking_after_the_flow_is_gone_says_so(tmp_path, monkeypatch):
    db_module, _ = await _setup(tmp_path, monkeypatch, settings={})
    try:
        from telkap.services.userbot import manager

        manager._pending.clear()
        assert await manager.qr_result(7, 0.01) == "gone"
        assert await manager.refresh_qr(7) is None
    finally:
        await db_module.close_db()


def test_the_qr_image_is_a_real_png():
    """نشانیِ خام را نمی‌شود اسکن کرد."""
    from telkap.handlers.account import _qr_png

    raw = _qr_png("tg://login?token=ABC")
    assert raw.startswith(b"\x89PNG"), "خروجی PNG نیست"
    assert len(raw) > 200


def test_what_the_text_promises_is_what_the_bot_does():
    """<b>متنِ اعتمادساز نباید چیزی بگوید که درست نیست.</b>

    این متن دقیقاً برای برداشتنِ تردید نوشته شده؛ یک ادعای نادرست در
    آن، همان اعتماد را بدتر از اول خراب می‌کند.
    """
    from telkap.texts import LOGIN_CHOICE

    # راه قطع کردن باید گفته شود — و همان‌جایی که واقعاً هست
    assert "دستگاه‌ها" in LOGIN_CHOICE
    # و ادعای رمزنگاری، که واقعاً انجام می‌شود
    assert "رمزنگاری" in LOGIN_CHOICE
    # هیچ‌جا نباید ادعا شود که دسترسی محدودتری می‌گیریم
    for false_claim in ("فقط خواندن", "دسترسی محدود", "بدون دسترسی"):
        assert false_claim not in LOGIN_CHOICE, false_claim
