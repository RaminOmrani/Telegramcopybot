"""تست دور پانزدهم: پنل وب مدیریت."""
from __future__ import annotations

import pytest

from tests.test_copier import _setup


# ------------------------------------------------------------ ورود
def test_a_login_link_works_once_and_then_burns():
    from telkap.web import auth

    auth.reset()
    token = auth.issue_login_token(7)
    assert auth.consume_login_token(token) == 7
    assert auth.consume_login_token(token) is None      # بار دوم نه
    auth.reset()


def test_an_expired_login_link_is_refused(monkeypatch):
    from telkap.web import auth

    auth.reset()
    token = auth.issue_login_token(7)

    real = auth.time.time
    monkeypatch.setattr(
        auth.time, "time", lambda: real() + auth.LOGIN_TTL_SECONDS + 1
    )
    assert auth.consume_login_token(token) is None
    auth.reset()


def test_an_invented_token_is_refused():
    from telkap.web import auth

    auth.reset()
    assert auth.consume_login_token("چیزی-که-ما-نساخته‌ایم") is None
    assert auth.consume_login_token("") is None


def test_a_session_ends_when_it_expires(monkeypatch):
    from telkap.web import auth

    auth.reset()
    sid = auth.start_session(7)
    assert auth.get_session(sid).user_id == 7

    real = auth.time.time
    monkeypatch.setattr(
        auth.time, "time", lambda: real() + auth.SESSION_TTL_SECONDS + 1
    )
    assert auth.get_session(sid) is None
    auth.reset()


def test_removing_an_admin_closes_their_open_sessions():
    """نشستِ باز نباید کسی را بعد از عزل هم داخل نگه دارد."""
    from telkap.web import auth

    auth.reset()
    mine = auth.start_session(7)
    someone_else = auth.start_session(9)

    auth.forget_user(7)
    assert auth.get_session(mine) is None
    assert auth.get_session(someone_else) is not None
    auth.reset()


def test_the_csrf_token_must_match():
    from telkap.web import auth

    auth.reset()
    sid = auth.start_session(7)
    session = auth.get_session(sid)

    assert auth.check_csrf(session, session.csrf) is True
    assert auth.check_csrf(session, "چیز دیگری") is False
    assert auth.check_csrf(session, "") is False
    auth.reset()


def test_two_sessions_do_not_share_a_csrf_token():
    from telkap.web import auth

    auth.reset()
    first = auth.get_session(auth.start_session(7))
    second = auth.get_session(auth.start_session(9))
    assert first.csrf != second.csrf
    auth.reset()


# ------------------------------------------------------- ساخت صفحه
def test_user_written_text_cannot_inject_html():
    """نام کاربر و یادداشت رسید را خودِ کاربر نوشته و قابل اعتماد نیست."""
    from telkap.web.render import esc, page

    nasty = "<script>alert('x')</script>"
    assert "<script>" not in esc(nasty)
    assert "&lt;script&gt;" in esc(nasty)
    assert "<script>" not in page("t", "<p>ok</p>", who=nasty)


def test_a_page_carries_its_own_direction_and_charset():
    from telkap.web.render import page

    html = page("نمای کلی", "<p>سلام</p>")
    assert "dir='rtl'" in html
    assert "charset='utf-8'" in html
    assert "noindex" in html          # پنل نباید در گوگل بیفتد


def test_the_active_tab_is_marked():
    from telkap.web.render import page

    html = page("رسیدها", "", active="/payments")
    assert 'href="/payments" class="on"' in html
    assert 'href="/users" class=""' in html


def test_an_empty_table_says_so_instead_of_showing_nothing():
    from telkap.web.render import table

    assert "رسیدی نیست" in table(["a"], [], empty="رسیدی نیست")
    assert "<tbody>" not in table(["a"], [], empty="رسیدی نیست")


# ------------------------------------------------- روشن و خاموش بودن
@pytest.mark.asyncio
async def test_the_panel_stays_shut_until_it_is_turned_on(tmp_path, monkeypatch):
    """پیش‌فرض خاموش است؛ کسی که .env را دست نزده نباید پورتی باز ببیند."""
    db_module, _ = await _setup(tmp_path, monkeypatch, settings={})
    try:
        from telkap import config
        from telkap.web import server

        assert config.get_settings().web_enabled is False
        await server.start_panel(bot=None)
        assert server._runner is None
    finally:
        await server.stop_panel()
        await db_module.close_db()


def test_the_env_flag_only_accepts_a_real_yes():
    from telkap.config import _flag

    assert _flag("true") is True
    assert _flag("TRUE") is True
    assert _flag("1") is True
    assert _flag("yes") is True
    assert _flag("on") is True
    assert _flag("false") is False
    assert _flag("") is False
    assert _flag("شاید") is False


# ------------------------------------------------------ مسیرها و دسترسی
def test_every_page_but_the_gate_needs_a_session():
    """اگر مسیری از میدل‌ور معاف شود، بی‌صدا عمومی می‌شود."""
    from telkap.web import server

    app = server.build_app(bot=None)
    paths = {
        route.resource.canonical
        for route in app.router.routes()
        if route.resource is not None
    }

    # فهرست معافیت‌ها صریح است و اینجا مو‌به‌مو سنجیده می‌شود. اگر روزی
    # مسیری به آن اضافه شود، این تست می‌ایستد و کسی مجبور می‌شود
    # تصمیمش را توضیح بدهد — به‌جای اینکه پنل بی‌صدا عمومی شود.
    assert server.PUBLIC_PATHS == {"/enter", "/healthz", "/pay/zarinpal"}

    # و هرچه معاف نیست باید پشت ورود بماند
    assert paths - server.PUBLIC_PATHS == {
        "/",
        "/payments",
        "/payments/{id}/approve",
        "/payments/{id}/reject",
        "/receipt/{id}",
        "/users",
        "/users/{id}",
        "/users/{id}/grant",
        "/users/{id}/days",
        "/users/{id}/ban",
        "/users/{id}/revoke",
        "/tasks",
        "/tasks/{id}/toggle",
        "/settings",
        "/settings/card",
        "/settings/crypto",
        "/settings/autorate",
        "/settings/ratenow",
        "/settings/zarinpal",
        "/logout",
    }


def test_changing_state_is_never_a_get():
    """<b>هیچ کارِ اثرگذاری نباید با باز کردن یک لینک انجام شود.</b>

    درخواست GET را هر چیزی می‌سازد — یک تصویر در یک صفحه‌ی دیگر، یک
    پیش‌نمایش لینک، خزنده‌ی مرورگر. اگر تأیید رسید یا مسدود کردن کاربر
    با GET ممکن باشد، هیچ‌کدام از آن‌ها لازم نیست عمدی باشد.

    فهرست پایین صریح است تا مسیرِ اثرگذارِ تازه‌ای که یادش برود POST
    باشد، همین‌جا گیر بیفتد نه بعداً.
    """
    from telkap.web import server

    app = server.build_app(bot=None)
    changing = (
        "/approve", "/reject", "/grant", "/days", "/ban", "/revoke", "/toggle",
    )
    for route in app.router.routes():
        path = route.resource.canonical if route.resource else ""
        if path.endswith(changing) or path.startswith("/settings/"):
            assert route.method == "POST", path


def test_every_writing_route_checks_the_csrf_token():
    """<b>توکنِ جاافتاده هیچ نشانه‌ای ندارد.</b>

    فرم بدون آن کاملاً درست کار می‌کند — تا روزی که سایتی دیگر از
    مرورگرِ ادمینِ واردشده همان درخواست را بفرستد. تنها راهِ گرفتنش
    شمردنِ خودِ مسیرهاست.
    """
    import inspect

    from telkap.web import server

    app = server.build_app(bot=None)
    for route in app.router.routes():
        if route.method != "POST":
            continue
        source = inspect.getsource(route.handler)
        assert "_guard_post" in source, route.resource.canonical


# ------------------------------------- یکی بودن متن ربات و پنل
@pytest.mark.asyncio
async def test_both_the_bot_and_the_panel_send_the_same_notice(tmp_path, monkeypatch):
    """رسیدی که از پنل تأیید شود باید همان خبری را بدهد که از ربات."""
    db_module, _ = await _setup(tmp_path, monkeypatch, settings={})
    try:
        from telkap.models import PaymentRequest
        from telkap.services import payments

        request = PaymentRequest(id=5, user_id=7, plan_code="week", amount_toman=1000)
        notice = await payments.approval_notice(request, sub=None)
        assert "تأیید شد" in notice

        refusal = payments.rejection_notice(request)
        assert "تأیید نشد" in refusal
        assert "5" in refusal          # کد پیگیری در پیام هست
    finally:
        await db_module.close_db()


def test_a_hash_in_a_flash_message_survives_the_redirect():
    """<b>«#» در نشانی، شروعِ قطعه است — نه یک نویسه‌ی معمولی.</b>

    پیام‌های این پنل «#» دارند: «رسید #12 تأیید شد». بدون کدگذاری،
    مرورگر از «#» به بعد را دور می‌ریزد و اصلاً به سرور نمی‌فرستد؛
    ادمین پیامِ بریده‌ی «رسید » را می‌دید و نمی‌فهمید چه شد.

    «&» هم همین‌طور: پیام را به دو پارامتر می‌شکند.
    """
    from urllib.parse import parse_qs, urlparse

    from telkap.web import server

    for message in ["رسید #12 تأیید شد.", "الف & ب", "کار #3 خاموش شد."]:
        found = server._back("/payments", ok=message)
        parts = urlparse(found.location)

        assert parts.fragment == ""          # چیزی به قطعه نرفته
        assert parse_qs(parts.query)["ok"] == [message]
