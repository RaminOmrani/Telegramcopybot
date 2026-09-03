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


@pytest.mark.asyncio
async def test_a_session_survives_a_restart(tmp_path, monkeypatch):
    """<b>چرا نشست‌ها به دیتابیس رفتند.</b>

    نشست حافظه‌ای با هر ری‌استارت ربات می‌مرد — و ربات برای هر
    به‌روزرسانی ری‌استارت می‌شود. کسی که پنل را باز نگه می‌دارد نباید
    هر بار دوباره وارد شود.
    """
    from telkap.web import auth

    await _setup(tmp_path, monkeypatch, settings={})
    token = await auth.start_session(7)

    # ری‌استارت یعنی حافظه‌ی درون‌پروسه خالی می‌شود
    auth.reset()

    session = await auth.get_session_for(token)
    assert session is not None
    assert session.user_id == 7


@pytest.mark.asyncio
async def test_an_expired_session_is_refused(tmp_path, monkeypatch):
    from datetime import timedelta

    from telkap.models import WebSession, utcnow
    from telkap.web import auth

    db_module, _ = await _setup(tmp_path, monkeypatch, settings={})
    token = await auth.start_session(7)

    async with db_module.get_session() as db:
        row = await db.scalar(
            __import__("sqlalchemy").select(WebSession)
        )
        row.expires_at = utcnow() - timedelta(seconds=1)
        await db.commit()

    assert await auth.get_session_for(token) is None


@pytest.mark.asyncio
async def test_removing_an_admin_closes_their_open_sessions(tmp_path, monkeypatch):
    """نشستِ باز نباید کسی را بعد از عزل هم داخل نگه دارد."""
    from telkap.web import auth

    await _setup(tmp_path, monkeypatch, settings={})
    mine = await auth.start_session(7)
    someone_else = await auth.start_session(9)

    await auth.end_all(7)

    assert await auth.get_session_for(mine) is None
    assert await auth.get_session_for(someone_else) is not None


@pytest.mark.asyncio
async def test_the_cookie_value_itself_is_never_stored(tmp_path, monkeypatch):
    """<b>دیتابیسِ لو‌رفته نباید کلیدِ ورودِ آماده بدهد.</b>"""
    from telkap.models import WebSession
    from telkap.web import auth

    db_module, _ = await _setup(tmp_path, monkeypatch, settings={})
    token = await auth.start_session(7)

    async with db_module.get_session() as db:
        row = await db.scalar(__import__("sqlalchemy").select(WebSession))

    assert token not in row.token_hash
    assert len(row.token_hash) == 64          # SHA-256 هگز


@pytest.mark.asyncio
async def test_the_csrf_token_must_match(tmp_path, monkeypatch):
    from telkap.web import auth

    await _setup(tmp_path, monkeypatch, settings={})
    session = await auth.get_session_for(await auth.start_session(7))

    assert auth.check_csrf(session, session.csrf) is True
    assert auth.check_csrf(session, "چیز دیگری") is False
    assert auth.check_csrf(session, "") is False


@pytest.mark.asyncio
async def test_two_sessions_do_not_share_a_csrf_token(tmp_path, monkeypatch):
    from telkap.web import auth

    await _setup(tmp_path, monkeypatch, settings={})
    first = await auth.get_session_for(await auth.start_session(7))
    second = await auth.get_session_for(await auth.start_session(9))

    assert first.csrf != second.csrf


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


def test_the_active_section_is_marked():
    """کسی که پنل را باز نگه می‌دارد باید همیشه بداند کجاست."""
    from telkap.web.render import page, url

    html = page("رسیدها", "", active="/payments")
    assert f"href='{url('/payments')}' class='on'" in html
    assert f"href='{url('/users')}' class=''" in html


def test_every_link_in_a_page_stays_inside_the_panel():
    """<b>پنل تنها ساکنِ این زیردامنه نیست.</b>

    «/» صفحه‌ی فروش است و «/app» مینی‌اپ. لینکی که پیشوند نگرفته باشد
    آدم را از پنل بیرون می‌اندازد — و چون صفحه بالا می‌آید و فقط
    لینکش غلط است، تا وقتی کسی رویش کلیک نکند دیده نمی‌شود.
    """
    import re

    from telkap.web.render import PREFIX, login, page

    body = (
        "<a href='/users/9'>کاربر</a>"
        "<form action='/settings/card'></form>"
        "<a href='https://example.com'>بیرون</a>"
    )
    for html in (page("t", body, who="7", path="/"), login()):
        stray = [
            m.group(0)
            for m in re.finditer(r"""(?:href|action)=['"]/[^'"]*""", html)
            if not m.group(0).split("=", 1)[1][1:].startswith(PREFIX + "/")
        ]
        assert stray == [], stray

    # و نشانی بیرونی دست‌نخورده می‌ماند
    assert "href='https://example.com'" in page("t", body)


def test_the_font_url_gets_the_prefix_too():
    """<b>اگر جا بماند، صفحه بالا می‌آید ولی بی‌فونت.</b>

    و چون خطایی هم داده نمی‌شود، علتش دیر پیدا می‌شود.
    """
    from telkap.web.render import PREFIX, page

    assert f"url('{PREFIX}/static/fonts/Vazirmatn-Regular.woff2')" in page("t", "")


def test_every_section_in_the_menu_has_a_real_page():
    """<b>منویی که به ۴۰۴ برسد، از نبودنِ آن گزینه بدتر است.</b>"""
    from telkap.web import server
    from telkap.web.render import NAV, url

    app = server.build_app(bot=None)
    paths = {
        route.resource.canonical
        for route in app.router.routes()
        if route.resource is not None and route.method == "GET"
    }
    for _group, items in NAV:
        for href, _icon, _label, _badge in items:
            assert url(href) in paths, href


def test_the_font_is_served_from_our_own_server():
    """<b>فونتی که از CDN خارجی بیاید، برای کاربر ایرانی نمی‌آید.</b>

    و صفحه‌ای که فونتش نیامده با فونت پیش‌فرض ویندوز رندر می‌شود، که
    برای فارسی بد است — دقیقاً همان چیزی که باید درست می‌شد.
    """
    from telkap.web.render import CSS

    assert "/static/fonts/Vazirmatn-Regular.woff2" in CSS
    assert "fonts.googleapis.com" not in CSS
    assert "cdn." not in CSS


def test_the_font_files_are_really_there():
    """مسیری که در CSS هست ولی فایلش نیست، بی‌صدا به فونت پیش‌فرض می‌افتد."""
    from telkap.web.server import STATIC_DIR

    for weight in ("Regular", "Medium", "SemiBold", "Bold"):
        assert (STATIC_DIR / "fonts" / f"Vazirmatn-{weight}.woff2").exists()


def test_the_theme_follows_the_person_not_their_windows():
    """<b>«تم سفیده» شکایتِ واقعی بود و علتش همین بود.</b>

    نمای روشن با <code>prefers-color-scheme</code> بالا می‌آمد، و
    ویندوزِ پیش‌فرض روشن است — پس پنل برای اغلب آدم‌ها سفید می‌ماند
    بی‌آنکه کسی خواسته باشد. حالا تیره پیش‌فرض است و روشن یک انتخابِ
    صریح.
    """
    from telkap.web.render import CSS

    assert "color-scheme: dark" in CSS
    assert ':root[data-theme="light"]' in CSS
    # توضیحش در خودِ CSS هست؛ آنچه نباید باشد، قاعده است نه توضیح
    assert "@media (prefers-color-scheme" not in CSS


def test_the_chosen_theme_is_stamped_on_the_page_itself():
    """<b>تم باید از همان اولِ رندر درست باشد.</b>

    اگر جاوااسکریپت بعد از بارگذاری عوضش می‌کرد، هر بار یک پرشِ رنگ
    دیده می‌شد — صفحه تیره می‌آمد و بعد سفید می‌شد.
    """
    from telkap.web.render import login, page

    assert "data-theme='light'" in page("t", "", theme="light")
    assert "data-theme='dark'" in page("t", "", theme="dark")
    assert "data-theme='light'" in login(theme="light")


def test_a_tampered_theme_cookie_cannot_reach_the_html():
    """مقدار کوکی مستقیم داخل یک صفت HTML می‌نشیند و کوکی دستکاری‌شدنی است."""
    from telkap.web.render import clean_theme, page

    assert clean_theme("light") == "light"
    assert clean_theme("dark") == "dark"
    assert clean_theme(None) == "dark"
    assert clean_theme("' onload='alert(1)") == "dark"
    assert "onload" not in page("t", "", theme="' onload='alert(1)")


def test_the_theme_switch_comes_back_to_the_page_you_were_on():
    from telkap.web.render import page, theme_switch

    link = theme_switch("dark", "/users?q=%D8%B1")

    assert "to=light" in link
    assert "back=%2Fusers" in link                # نشانی کدگذاری شده
    # و روی خودِ صفحه هم هست، نه فقط در تابع
    assert "/theme?" in page("کاربران", "", who="7", path="/users")


def test_the_theme_switch_will_not_send_you_to_another_site():
    """<b>بدون این، پنل یک تغییرمسیرِ آماده برای دیگران بود.</b>

    لینکی که دامنه‌ی شما را دارد ولی آدم را جای دیگری می‌برد، دقیقاً
    همان چیزی است که در ایمیل‌های تقلبی به کار می‌آید.
    """
    from telkap.web.server import _safe_back

    assert _safe_back("/users") == "/users"
    assert _safe_back("/users?q=1") == "/users?q=1"
    assert _safe_back("https://example.com") == "/"
    assert _safe_back("//example.com") == "/"
    assert _safe_back("") == "/"


def test_a_chart_bar_is_drawn_inside_a_container_with_a_real_height():
    """<b>نمودار بی‌ارتفاع، خطا نمی‌دهد؛ فقط یک خط صاف می‌شود.</b>

    ارتفاع درصدی فقط وقتی معنا دارد که ظرف ارتفاعِ معلوم داشته باشد.
    میله داخل <code>.track</code> می‌نشیند که با position مطلق
    کشیده می‌شود؛ اگر روزی این لایه برداشته شود همه‌ی میله‌ها به
    min-height می‌افتند و نمودار بی‌صدا از کار می‌افتد.
    """
    from telkap.web.render import CSS, chart

    html = chart([("شنبه", 10), ("یکشنبه", 40)], unit="تومان")

    assert html.count("<div class='track'>") == 2
    assert "height:100%" in html                 # بلندترین میله، کاملِ ظرف
    assert ".chart .track { position: relative" in CSS
    assert "position: absolute" in CSS.split(".chart .fill {")[1].split("}")[0]


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
    from telkap.web.render import PREFIX, url

    app = server.build_app(bot=None)
    # مسیرها با پیشوند ثبت می‌شوند؛ اینجا برای خوانایی برداشته می‌شود
    # تا فهرستِ پایین همانی بماند که در کد نوشته شده.
    # مینی‌اپ اصلاً زیر پنل نیست: هویتش را تلگرام با امضای initData
    # می‌دهد و هر مسیرش خودش می‌سنجدش. اینجا کنار گذاشته می‌شود تا
    # فهرستِ پایین فقط درباره‌ی پنل بماند.
    paths = set()
    for route in app.router.routes():
        if route.resource is None:
            continue
        canonical = route.resource.canonical
        if canonical.startswith(server.APP_PREFIX):
            continue
        assert canonical.startswith(PREFIX + "/") or canonical == PREFIX, canonical
        paths.add(canonical[len(PREFIX):] or "/")

    public = {path[len(PREFIX):] or "/" for path in server.PUBLIC_PATHS}
    assert server.PUBLIC_PATHS == {url(path) for path in public}

    # فهرست معافیت‌ها صریح است و اینجا مو‌به‌مو سنجیده می‌شود. اگر روزی
    # مسیری به آن اضافه شود، این تست می‌ایستد و کسی مجبور می‌شود
    # تصمیمش را توضیح بدهد — به‌جای اینکه پنل بی‌صدا عمومی شود.
    # «/theme» فقط رنگ صفحه‌ی همان مرورگر را عوض می‌کند و صفحه‌ی
    # ورود هم به آن نیاز دارد؛ هیچ داده‌ای پشتش نیست.
    assert public == {"/enter", "/healthz", "/login", "/theme", "/pay/zarinpal"}

    # فایل‌های ثابت (فونت) عمداً بازند: مرورگر پیش از ورود هم صفحه‌ی
    # لاگین را با همین فونت می‌کشد. چیزی جز فونت آنجا نیست.
    paths.discard("/static")

    # و هرچه معاف نیست باید پشت ورود بماند
    assert paths - public == {
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
        "/resellers",
        "/resellers/{id}",
        "/resellers/default",
        "/resellers/set",
        "/resellers/{id}/remove",
        "/tasks",
        "/tasks/{id}/toggle",
        "/finance",
        "/activity",
        "/account",
        "/account/password",
        "/account/logout-all",
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
        path = route.resource.canonical
        # ورود استثناست و باید هم باشد: هنوز نشستی وجود ندارد که
        # توکنی داشته باشد. محافظش چیز دیگری است — رمز، کد تلگرام، و
        # قفل شدن پس از چند تلاش.
        if path in server.PUBLIC_PATHS:
            continue
        # مینی‌اپ هم استثناست، و دلیلش ساختاری است نه سهل‌انگاری:
        # هویتش با سرصفحه‌ی X-Telegram-Init-Data می‌آید، نه با کوکی.
        # حمله‌ی CSRF روی این معنا ندارد چون مرورگر آن سرصفحه را
        # خودکار به درخواستی که سایتِ دیگری ساخته نمی‌چسباند — کاری
        # که با کوکی می‌کند. در عوض هر مسیرش امضا را می‌سنجد.
        if path.startswith(server.APP_PREFIX):
            source = inspect.getsource(route.handler)
            assert "_who(request)" in source, path
            continue
        source = inspect.getsource(route.handler)
        assert "_guard_post" in source, path


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


# ------------------------------------------------- پیشوند و نشانی بیرونی
def test_the_zarinpal_return_address_lands_on_the_panel(monkeypatch):
    """<b>نشانی بازگشتِ درگاه باید دقیقاً روی مسیرِ ثبت‌شده بیفتد.</b>

    این تنها نشانی‌ای است که ما نمی‌سازیمش و کاربر رویش هدایت می‌شود؛
    اگر یک پله بالاتر بیفتد، آدم بعد از پرداخت صفحه‌ی فروش را می‌بیند و
    خیال می‌کند پولش رفته. WEB_BASE_URL باید پیشوند پنل را داشته باشد و
    همین اینجا سنجیده می‌شود.
    """
    from types import SimpleNamespace

    from telkap.services import zarinpal
    from telkap.web import server
    from telkap.web.render import PREFIX

    host = "https://forwardbot.example"
    monkeypatch.setattr(
        zarinpal, "get_settings", lambda: SimpleNamespace(web_base_url=host + PREFIX)
    )

    app = server.build_app(bot=None)
    routes = {
        route.resource.canonical
        for route in app.router.routes()
        if route.resource is not None
    }

    assert zarinpal.callback_url() == f"{host}{PREFIX}/pay/zarinpal"
    assert zarinpal.callback_url().removeprefix(host) in routes


def test_the_example_env_shows_a_base_url_with_the_prefix():
    """<b>کسی که .env را پر می‌کند، از همین نمونه کپی می‌کند.</b>

    و WEB_BASE_URL بدون پیشوند خطایی نمی‌دهد؛ فقط لینک ورود و بازگشتِ
    درگاه را یک پله بالاتر می‌اندازد.
    """
    from pathlib import Path

    from telkap.web.render import PREFIX

    text = (Path(__file__).parent.parent / ".env.example").read_text(encoding="utf-8")
    samples = [
        line.split("=", 1)[1].strip()
        for line in text.splitlines()
        if line.lstrip("# ").startswith("WEB_BASE_URL=")
        and line.lstrip("# ").split("=", 1)[1].strip()
    ]

    assert samples, "نمونه‌ای برای WEB_BASE_URL در .env.example نیست"
    for sample in samples:
        assert sample.endswith(PREFIX), sample
