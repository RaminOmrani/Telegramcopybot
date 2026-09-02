"""وب‌سرور پنل مدیریت.

روی aiohttp که از قبل وابستگیِ پروژه است، و داخل همان حلقه‌ی asyncio ربات.
پس نه پروسه‌ی تازه‌ای بالا می‌آید، نه نصب تازه‌ای لازم است، نه دو نفر سر
دیتابیس SQLite به هم می‌خورند.
"""
from __future__ import annotations

import logging
from datetime import UTC, timedelta, timezone
from urllib.parse import urlencode

from aiohttp import web
from sqlalchemy import func, or_, select

from telkap import i18n
from telkap.config import get_settings
from telkap.db import get_session
from telkap.models import PaymentRequest, Subscription, Task, User, utcnow
from telkap.plans import purchasable
from telkap.services import (
    analytics,
    cardinfo,
    coins,
    crypto,
    moderation,
    payments,
    roles,
    subscription,
    usdtrate,
    zarinpal,
)
from telkap.web import auth, render
from telkap.web.render import card, esc, form, money, page, panel, pill, table

log = logging.getLogger(__name__)

BOT = web.AppKey("bot")

_runner: web.AppRunner | None = None

# رسیدها از تلگرام گرفته و همین‌جا نگه داشته می‌شوند تا هر بار باز کردن
# صفحه یک رفت‌وبرگشت تازه به تلگرام نباشد. رسید عوض نمی‌شود، پس کهنه
# شدنش معنا ندارد؛ فقط اندازه‌اش را محدود نگه می‌داریم.
_receipts: dict[str, tuple[str, bytes]] = {}
MAX_CACHED_RECEIPTS = 40


# ------------------------------------------------------------- کمکی‌ها
def _local(value):
    """زمان UTC را به وقت محلیِ تنظیم‌شده می‌برد."""
    if value is None:
        return None
    offset = get_settings().timezone_offset
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(timezone(timedelta(hours=offset)))


def _when(value) -> str:
    local = _local(value)
    return local.strftime("%Y-%m-%d %H:%M") if local else "—"


def _who(user: User | None) -> str:
    if user is None:
        return "—"
    name = (user.first_name or "").strip()
    handle = f"@{user.username}" if user.username else str(user.id)
    return f"{name} ({handle})" if name else handle


def _over_https(request: web.Request) -> bool:
    """آیا کاربر واقعاً از https آمده؟

    پشت nginx یا IIS، خودِ درخواست به ربات از http می‌رسد؛ آنچه کاربر دیده
    را فقط سرصفحه‌ی X-Forwarded-Proto می‌گوید.
    """
    forwarded = request.headers.get("X-Forwarded-Proto", "").split(",")[0].strip()
    return request.scheme == "https" or forwarded.lower() == "https"


async def _shell(
    request: web.Request, title: str, body: str, *, active: str = "", status: int = 200
) -> web.Response:
    """صفحه‌ی کامل با شمارِ رسیدهای منتظر روی تب.

    <b>چرا شمارنده در همه‌ی صفحه‌ها.</b> کارِ روزمره‌ی این پنل رسیدهاست.
    اگر برای دیدنِ اینکه چیزی منتظر است باید تب را باز کرد، گاهی باز
    نمی‌شود — و رسیدِ دیده‌نشده یعنی مشتریِ منتظر.
    """
    session = request.get("session")
    who = str(session.user_id) if session else ""
    waiting = 0
    if session is not None and await roles.can(session.user_id, roles.CAP_MONEY):
        waiting = await payments.pending_count()
    return web.Response(
        text=page(title, body, active=active, who=who, waiting=waiting),
        content_type="text/html",
        status=status,
    )


def _flash(request: web.Request) -> str:
    if request.query.get("ok"):
        return f"<div class='flash ok'>{esc(request.query['ok'])}</div>"
    if request.query.get("err"):
        return f"<div class='flash bad'>{esc(request.query['err'])}</div>"
    return ""


def _back(path: str, *, ok: str = "", err: str = "") -> web.HTTPFound:
    """هدایت به یک صفحه، همراه پیام.

    <b>چرا خودمان کدگذاری می‌کنیم.</b> پیام‌ها «#» دارند — «رسید #12
    تأیید شد». در نشانی، «#» شروعِ قطعه است و مرورگر هرچه بعدش بیاید
    را دور می‌ریزد و به سرور هم نمی‌فرستد. نتیجه‌اش پیامِ بریده‌ی
    «رسید » بود. همین برای «&» هم صادق است، که پیام را دو تکه می‌کند.
    """
    query = urlencode({"ok": ok} if ok else {"err": err})
    return web.HTTPFound(f"{path}?{query}")


async def _deny(request: web.Request, cap: str) -> web.Response | None:
    """اگر این ادمین دسترسیِ لازم را نداشته باشد، صفحه‌ی رد برمی‌گرداند."""
    session = request["session"]
    if await roles.can(session.user_id, cap):
        return None
    return web.Response(
        text=page(
            "دسترسی نیست",
            "<h1>دسترسی ندارید</h1>"
            "<p class='sub'>نقش شما این بخش را شامل نمی‌شود. "
            "اگر فکر می‌کنید اشتباهی شده، با مالک ربات صحبت کنید.</p>",
            who=str(session.user_id),
        ),
        content_type="text/html",
        status=403,
    )


# ------------------------------------------------------------ میدل‌ور
# مسیرهایی که بدون ورود باز می‌شوند. صریح نوشته شده‌اند و تست می‌سنجدشان،
# چون مسیری که بی‌صدا به این فهرست اضافه شود، پنل را عمومی می‌کند.
#
#   /enter    خودِ ورود است
#   /healthz  فقط «زنده‌ام» می‌گوید
#   بازگشت درگاه — مرورگر کاربر به آن هدایت می‌شود و کاربر ادمین نیست،
#   پس نمی‌تواند پشت ورود بماند. چیزی جز نتیجه‌ی همان پرداخت نشان
#   نمی‌دهد و به پارامترهای نشانی هم اعتماد نمی‌کند.
PUBLIC_PATHS = frozenset({"/enter", "/healthz", "/login", zarinpal.CALLBACK_PATH})


@web.middleware
async def auth_middleware(request: web.Request, handler):
    if request.path in PUBLIC_PATHS:
        return await handler(request)

    session = await auth.get_session_for(request.cookies.get(auth.COOKIE_NAME))
    if session is None:
        # به صفحه‌ی ورود می‌رود، نه یک پیام بن‌بست. کسی که نشستش تمام
        # شده باید بتواند همان‌جا دوباره وارد شود.
        raise web.HTTPFound("/login")

    # نقش ممکن است بعد از ورود گرفته شده باشد؛ هر درخواست دوباره سنجیده
    # می‌شود تا کسی با نشستِ باز، بعدِ عزل هم داخل نماند
    if not await roles.is_staff(session.user_id):
        await auth.end_all(session.user_id)
        return web.Response(
            text=render.gate("دیگر دسترسی مدیریتی ندارید.", bad=True),
            content_type="text/html",
            status=403,
        )

    request["session"] = session
    return await handler(request)


# -------------------------------------------------------------- ورود
def _set_cookie(response, request: web.Request, token: str):
    """کوکی نشست، با پرچم Secure فقط وقتی واقعاً https هست."""
    secure = get_settings().web_base_url.startswith("https://")
    if secure and not _over_https(request):
        # ورود «کار می‌کند» ولی کاربر بی‌درنگ به صفحه‌ی ورود برمی‌گردد و
        # هیچ خطایی هم نمی‌بیند. بدون این خط، پیدا کردنش ساعت‌ها وقت می‌برد.
        log.warning(
            "WEB_BASE_URL روی https است ولی این درخواست از http آمد. "
            "کوکی ورود فرستاده می‌شود اما مرورگر برش نمی‌گرداند و ورود در "
            "حلقه می‌افتد. یا پنل را پشت HTTPS بگذارید، یا WEB_BASE_URL را "
            "به همان آدرسی که واقعاً باز می‌کنید تغییر دهید."
        )
    response.set_cookie(
        auth.COOKIE_NAME,
        token,
        httponly=True,
        samesite="Lax",
        max_age=auth.SESSION_TTL_SECONDS,
        secure=secure,
    )
    return response


async def login_page(request: web.Request) -> web.Response:
    """صفحه‌ی ورود — نام کاربری و رمز، بعد کد تلگرام."""
    if await auth.get_session_for(request.cookies.get(auth.COOKIE_NAME)):
        raise web.HTTPFound("/")

    error = request.query.get("err", "")
    key = request.query.get("k", "")
    return web.Response(
        text=render.login(error=error, pending_key=key),
        content_type="text/html",
    )


async def login_submit(request: web.Request) -> web.Response:
    """مرحله‌ی یک یا دو، بسته به اینکه کلید موقت آمده باشد یا نه."""
    posted = await request.post()
    key = str(posted.get("key", "")).strip()

    if key:
        token, problem = await auth.finish_login(
            key,
            str(posted.get("code", "")),
            user_agent=request.headers.get("User-Agent", ""),
        )
        if problem:
            return web.HTTPFound(f"/login?{urlencode({'k': key, 'err': problem})}")
        return _set_cookie(web.HTTPFound("/"), request, token)

    username = str(posted.get("username", ""))
    password = str(posted.get("password", ""))
    key, problem, user_id = await auth.start_login(username, password)
    if problem:
        return web.HTTPFound(f"/login?{urlencode({'err': problem})}")

    if not await roles.is_staff(user_id):
        return web.HTTPFound("/login?" + urlencode({"err": "دسترسی مدیریتی ندارید."}))

    # کد از راه تلگرام می‌رود، نه ایمیل یا پیامک: همان‌جایی که ربات
    # قبلاً هست و رایگان است، و صاحب حساب همیشه بازش دارد.
    bot = request.app[BOT]
    try:
        await bot.send_message(
            user_id,
            "🔐 <b>کد ورود به پنل وب</b>\n\n"
            f"<code>{auth.code_for(key)}</code>\n\n"
            "<i>پنج دقیقه اعتبار دارد. اگر شما وارد نمی‌شوید، این کد را به "
            "هیچ‌کس ندهید و رمز پنل را عوض کنید.</i>",
        )
    except Exception:
        log.warning("فرستادن کد ورود به %s ناموفق بود", user_id, exc_info=True)
        return web.HTTPFound(
            "/login?"
            + urlencode(
                {"err": "کد به تلگرام نرسید. مطمئن شوید ربات را استارت کرده‌اید."}
            )
        )

    return web.HTTPFound(f"/login?{urlencode({'k': key})}")


async def enter(request: web.Request) -> web.Response:
    user_id = auth.consume_login_token(request.query.get("t", ""))
    if user_id is None:
        return web.Response(
            text=render.gate(
                "این لینک منقضی شده یا قبلاً استفاده شده است. "
                "در ربات دوباره «🖥 پنل وب» را بزنید.",
                bad=True,
            ),
            content_type="text/html",
            status=401,
        )
    if not await roles.is_staff(user_id):
        return web.Response(
            text=render.gate("دسترسی مدیریتی ندارید.", bad=True),
            content_type="text/html",
            status=403,
        )

    sid = await auth.start_session(
        user_id, user_agent=request.headers.get("User-Agent", "")
    )
    return _set_cookie(web.HTTPFound("/"), request, sid)


async def logout(request: web.Request) -> web.Response:
    await auth.end_session(request.cookies.get(auth.COOKIE_NAME))
    response = web.Response(
        text=render.gate("از پنل خارج شدید."),
        content_type="text/html",
    )
    response.del_cookie(auth.COOKIE_NAME)
    return response


async def healthz(request: web.Request) -> web.Response:
    return web.Response(text="ok")


# --------------------------------------------------------- نمای کلی
async def dashboard(request: web.Request) -> web.Response:
    denied = await _deny(request, roles.CAP_REPORTS)
    if denied is not None:
        return denied

    data = await analytics.dashboard()
    waiting = await payments.pending_count()

    async with get_session() as db:
        users = await db.scalar(select(func.count()).select_from(User)) or 0
        running = (
            await db.scalar(
                select(func.count()).select_from(Task).where(Task.enabled.is_(True))
            )
            or 0
        )

    growth = data.growth
    top = "".join(
        (
            card("درآمد این ماه", money(data.this_month.total)),
            card("نسبت به ماه قبل", f"{growth:+d}%", "ok" if growth >= 0 else "warn"),
            card("رسید منتظر بررسی", i18n.num(waiting, "fa"), "warn" if waiting else ""),
            card("اشتراک فعال", i18n.num(data.retention.active_subs, "fa")),
            card("کاربر", i18n.num(users, "fa")),
            card("کار در حال اجرا", i18n.num(running, "fa")),
        )
    )

    steps = "".join(
        f"<tr><td>{esc(title)}</td>"
        f"<td class='money'>{esc(i18n.num(count, 'fa'))}</td>"
        f"<td class='money'>{percent}%</td></tr>"
        for title, count, percent in data.funnel.steps
    )
    drop_where, drop_count = data.funnel.biggest_drop

    body = (
        "<h1>نمای کلی</h1>"
        "<p class='sub'>همان اعدادی که در ربات هم هست، روی صفحه‌ی بزرگ‌تر.</p>"
        f"<div class='cards'>{top}</div>"
        "<section><h2>قیف تبدیل</h2>"
        f"{table(['پله', 'تعداد', 'از کل'], [steps] if steps else [])}"
        f"<p class='mini' style='margin-top:10px'>بزرگ‌ترین افت: "
        f"<b>{esc(drop_where)}</b> — {esc(i18n.num(drop_count, 'fa'))} نفر</p>"
        "</section>"
        "<section><h2>درآمد</h2>"
        "<div class='cards'>"
        + card("این ماه", money(data.this_month.total))
        + card("ماه قبل", money(data.last_month.total))
        + card("از ابتدا", money(data.all_time.total))
        + card("میانگین هر خریدار", money(data.all_time.per_payer))
        + "</div></section>"
        "<section><h2>ماندگاری</h2>"
        "<div class='cards'>"
        + card("یک‌بارخرید", i18n.num(data.retention.once, "fa"))
        + card("خرید دوباره", i18n.num(data.retention.repeat, "fa"))
        + card("نرخ خرید دوباره", f"{data.retention.repeat_rate}%")
        + card("منقضی‌شده", i18n.num(data.retention.expired_users, "fa"))
        + "</div></section>"
    )
    return await _shell(request, "نمای کلی", body, active="/")


# ----------------------------------------------------------- رسیدها
async def payment_list(request: web.Request) -> web.Response:
    denied = await _deny(request, roles.CAP_MONEY)
    if denied is not None:
        return denied

    session = request["session"]
    pending = await payments.pending_requests(limit=100)

    people: dict[int, User] = {}
    if pending:
        async with get_session() as db:
            rows = await db.execute(
                select(User).where(User.id.in_({r.user_id for r in pending}))
            )
            people = {user.id: user for user in rows.scalars()}

    rows_html = []
    for req in pending:
        receipt = (
            f"<a href='/receipt/{req.id}' target='_blank'>"
            f"<img class='receipt' src='/receipt/{req.id}' alt='رسید'></a>"
            if req.receipt_kind == "photo"
            else f"<a href='/receipt/{req.id}' target='_blank'>دانلود فایل</a>"
            if req.receipt_file_id
            else "<span class='mini'>متنی</span>"
        )
        breakdown = []
        if req.discount_toman:
            breakdown.append(f"تخفیف {money(req.discount_toman)}")
        if req.credit_toman:
            breakdown.append(f"کسر ارتقا {money(req.credit_toman)}")
        detail = (
            f"<span class='mini'>{esc(' · '.join(breakdown))}</span>"
            if breakdown
            else ""
        )
        note = (
            f"<div class='mini'>{esc(req.note)}</div>" if req.note else ""
        )
        rows_html.append(
            f"<tr><td>#{req.id}</td>"
            f"<td>{esc(_who(people.get(req.user_id)))}"
            f"<div class='mini'>{esc(req.user_id)}</div></td>"
            f"<td>{esc(payments.describe(req))}{note}</td>"
            f"<td class='money'>{esc(money(req.amount_toman))}<br>{detail}</td>"
            f"<td>{receipt}</td>"
            f"<td>{esc(_when(req.created_at))}</td>"
            "<td class='actions'>"
            + form(
                f"/payments/{req.id}/approve",
                session.csrf,
                "<button class='btn ok'>تأیید</button>",
            )
            + " "
            + form(
                f"/payments/{req.id}/reject",
                session.csrf,
                "<button class='btn bad'>رد</button>",
                confirm=f"رسید #{req.id} رد شود؟ به کاربر خبر داده می‌شود.",
            )
            + "</td></tr>"
        )

    body = (
        _flash(request)
        + "<h1>رسیدهای منتظر بررسی</h1>"
        + "<p class='sub'>تأیید همان کاری را می‌کند که دکمه‌ی داخل ربات — "
        "اشتراک فعال می‌شود و به کاربر خبر می‌رسد.</p>"
        + table(
            ["کد", "کاربر", "خرید", "مبلغ", "رسید", "زمان", ""],
            rows_html,
            empty="رسیدی منتظر بررسی نیست — همه‌چیز رسیدگی شده.",
            icon="✅",
        )
    )
    return await _shell(request, "رسیدها", body, active="/payments")


async def _guard_post(
    request: web.Request, cap: str = roles.CAP_MONEY, back: str = "/payments"
) -> web.Response | None:
    """هر POST دو سد دارد و هیچ‌کدام اختیاری نیست.

    <b>دسترسی</b>، چون نقش پشتیبانی نباید رسید تأیید کند. و <b>توکن
    CSRF</b>، چون بدون آن سایتی دیگر می‌تواند از مرورگرِ ادمینِ
    واردشده همین درخواست را بفرستد — یک تصویر یا فرم پنهان کافی است
    تا کاربری مسدود شود یا رسیدی تأیید.
    """
    denied = await _deny(request, cap)
    if denied is not None:
        return denied
    form = await request.post()
    if not auth.check_csrf(request["session"], str(form.get("csrf", ""))):
        return _back(back, err="درخواست معتبر نبود. دوباره تلاش کنید.")
    return None


async def payment_approve(request: web.Request) -> web.Response:
    blocked = await _guard_post(request)
    if blocked is not None:
        return blocked

    admin_id = request["session"].user_id
    request_id = int(request.match_info["id"])
    req, sub = await payments.approve(request_id, admin_id)
    if req is None:
        return _back("/payments", err="این درخواست قبلاً بررسی شده بود.")

    bot = request.app[BOT]
    try:
        await bot.send_message(req.user_id, await payments.approval_notice(req, sub))
    except Exception:
        # تأیید انجام شده و نباید به‌خاطر نرسیدن پیام برگردانده شود؛
        # فقط ادمین باید بداند که کاربر خبردار نشد
        log.warning("اطلاع تأیید به کاربر %s نرسید", req.user_id, exc_info=True)
        return _back(
            "/payments", ok=f"رسید #{request_id} تأیید شد، ولی پیام به کاربر نرسید."
        )
    return _back("/payments", ok=f"رسید #{request_id} تأیید شد.")


async def payment_reject(request: web.Request) -> web.Response:
    blocked = await _guard_post(request)
    if blocked is not None:
        return blocked

    admin_id = request["session"].user_id
    request_id = int(request.match_info["id"])
    req = await payments.reject(request_id, admin_id)
    if req is None:
        return _back("/payments", err="این درخواست قبلاً بررسی شده بود.")

    bot = request.app[BOT]
    try:
        await bot.send_message(req.user_id, payments.rejection_notice(req))
    except Exception:
        log.warning("اطلاع رد به کاربر %s نرسید", req.user_id, exc_info=True)
        return _back(
            "/payments", ok=f"رسید #{request_id} رد شد، ولی پیام به کاربر نرسید."
        )
    return _back("/payments", ok=f"رسید #{request_id} رد شد.")


async def receipt(request: web.Request) -> web.Response:
    """تصویر رسید را از تلگرام می‌گیرد و نشان می‌دهد.

    فایل مستقیم از تلگرام سرو نمی‌شود چون آدرسش شامل توکن ربات است؛ هرکس
    آن آدرس را ببیند توکن را دارد.
    """
    denied = await _deny(request, roles.CAP_MONEY)
    if denied is not None:
        return denied

    async with get_session() as db:
        req = await db.get(PaymentRequest, int(request.match_info["id"]))
    if req is None or not req.receipt_file_id:
        raise web.HTTPNotFound(text="رسیدی نیست")

    cached = _receipts.get(req.receipt_file_id)
    if cached is None:
        bot = request.app[BOT]
        try:
            info = await bot.get_file(req.receipt_file_id)
            buffer = await bot.download_file(info.file_path)
        except Exception:
            log.warning("گرفتن رسید %s ناموفق بود", req.id, exc_info=True)
            raise web.HTTPBadGateway(text="رسید از تلگرام گرفته نشد") from None
        payload = buffer.read()
        kind = "image/jpeg" if req.receipt_kind == "photo" else "application/octet-stream"
        if len(_receipts) >= MAX_CACHED_RECEIPTS:
            _receipts.pop(next(iter(_receipts)), None)
        cached = (kind, payload)
        _receipts[req.receipt_file_id] = cached

    kind, payload = cached
    return web.Response(body=payload, content_type=kind)


# ---------------------------------------------------------- کاربران
async def user_list(request: web.Request) -> web.Response:
    denied = await _deny(request, roles.CAP_USERS)
    if denied is not None:
        return denied

    query = request.query.get("q", "").strip()
    async with get_session() as db:
        statement = select(User).order_by(User.created_at.desc()).limit(100)
        if query:
            like = f"%{query}%"
            terms = [User.first_name.ilike(like), User.username.ilike(like)]
            if query.lstrip("-").isdigit():
                terms.append(User.id == int(query))
            statement = statement.where(or_(*terms))
        people = list((await db.execute(statement)).scalars())

        subs: dict[int, Subscription] = {}
        tasks: dict[int, int] = {}
        if people:
            ids = [user.id for user in people]
            # `is_active` متد است نه ستون، پس شرط باید روی تاریخ باشد
            rows = await db.execute(
                select(Subscription)
                .where(
                    Subscription.user_id.in_(ids),
                    Subscription.expires_at > utcnow(),
                )
                .order_by(Subscription.expires_at.desc())
            )
            for sub in rows.scalars():
                subs.setdefault(sub.user_id, sub)
            counts = await db.execute(
                select(Task.user_id, func.count())
                .where(Task.user_id.in_(ids))
                .group_by(Task.user_id)
            )
            tasks = dict(counts.all())

    rows_html = []
    for user in people:
        sub = subs.get(user.id)
        plan = (
            pill(f"{sub.plan_code} تا {_when(sub.expires_at)[:10]}", "ok")
            if sub
            else pill("ندارد")
        )
        state = (
            pill("مسدود", "bad")
            if user.is_banned
            else pill("وصل", "ok")
            if user.session_enc
            else pill("بدون اکانت")
        )
        rows_html.append(
            f"<tr><td><a href='/users/{user.id}'>{esc(_who(user))}</a>"
            f"<div class='mini'>{esc(user.id)}</div></td>"
            f"<td>{plan}</td><td>{state}</td>"
            f"<td class='money'>{esc(i18n.num(tasks.get(user.id, 0), 'fa'))}</td>"
            f"<td class='money'>{esc(money(user.wallet_toman))}</td>"
            f"<td>{esc(_when(user.created_at))}</td>"
            f"<td class='actions'><a class='btn small' href='/users/{user.id}'>مدیریت</a></td>"
            "</tr>"
        )

    body = (
        "<h1>کاربران</h1>"
        "<p class='sub'>۱۰۰ کاربر آخر. برای پیدا کردن کسی، نام یا آیدی عددی‌اش "
        "را بنویسید.</p>"
        "<form method='get' style='margin-bottom:16px'>"
        f"<input type='search' name='q' value='{esc(query)}' "
        "placeholder='نام، یوزرنیم یا آیدی عددی'> "
        "<button class='btn'>جستجو</button></form>"
        + table(
            ["کاربر", "اشتراک", "وضعیت", "کارها", "کیف پول", "عضویت", ""],
            rows_html,
            empty="کاربری پیدا نشد.",
            icon="🔍",
        )
    )
    return await _shell(request, "کاربران", body, active="/users")


async def user_detail(request: web.Request) -> web.Response:
    """صفحه‌ی یک کاربر — با کارهایی که واقعاً انجام می‌شوند.

    <b>چرا این صفحه اضافه شد.</b> فهرست کاربران فقط نشان می‌داد. برای
    هر کارِ ساده — دادن اشتراک، مسدود کردن — باید سراغ ربات می‌رفتید،
    یعنی پنل برای کارِ روزمره بی‌فایده بود.
    """
    denied = await _deny(request, roles.CAP_USERS)
    if denied is not None:
        return denied

    user_id = int(request.match_info["id"])
    session = request["session"]

    async with get_session() as db:
        user = await db.get(User, user_id)
        if user is None:
            raise web.HTTPNotFound(text="کاربری با این شناسه نیست")
        rows = await db.execute(
            select(Task).where(Task.user_id == user_id).order_by(Task.id.desc())
        )
        tasks = list(rows.scalars())
        paid = await db.scalar(
            select(func.coalesce(func.sum(PaymentRequest.amount_toman), 0)).where(
                PaymentRequest.user_id == user_id,
                PaymentRequest.status == PaymentRequest.STATUS_APPROVED,
            )
        )

    sub = await subscription.active_subscription(user_id)
    days = await subscription.remaining_days(user_id)

    state = (
        pill("مسدود", "bad")
        if user.is_banned
        else pill("اکانت وصل", "ok")
        if user.session_enc
        else pill("بدون اکانت")
    )
    plan_text = (
        f"{esc(sub.plan_code)} — {i18n.num(days, 'fa')} روز مانده"
        if sub
        else "<span class='mini'>اشتراک فعالی ندارد</span>"
    )

    facts = (
        "<dl class='facts'>"
        f"<dt>شناسه</dt><dd><code>{esc(user.id)}</code></dd>"
        f"<dt>وضعیت</dt><dd>{state}</dd>"
        f"<dt>اشتراک</dt><dd>{plan_text}</dd>"
        f"<dt>کیف پول</dt><dd class='money'>{esc(money(user.wallet_toman))}</dd>"
        f"<dt>مجموع خرید</dt><dd class='money'>{esc(money(int(paid or 0)))}</dd>"
        f"<dt>کارها</dt><dd>{esc(i18n.num(len(tasks), 'fa'))}</dd>"
        f"<dt>عضویت</dt><dd>{esc(_when(user.created_at))}</dd>"
        "</dl>"
    )

    options = "".join(
        f"<option value='{esc(plan.code)}'>{esc(plan.title)} — "
        f"{esc(i18n.num(plan.days, 'fa'))} روز</option>"
        for plan in purchasable()
    )
    give = form(
        f"/users/{user_id}/grant",
        session.csrf,
        "<label class='field'><span class='cap'>طرح</span>"
        f"<select name='plan'>{options}</select></label>"
        "<button class='btn primary'>دادن اشتراک</button>",
    )

    adjust = form(
        f"/users/{user_id}/days",
        session.csrf,
        "<label class='field'><span class='cap'>روز (منفی یعنی کم کردن)</span>"
        "<input type='number' name='days' value='7' min='-3650' max='3650'>"
        "<span class='hint'>فقط وقتی کار می‌کند که اشتراک فعالی داشته باشد.</span>"
        "</label><button class='btn'>اعمال</button>",
    )

    ban_label = "آزاد کردن" if user.is_banned else "مسدود کردن"
    danger = (
        form(
            f"/users/{user_id}/ban",
            session.csrf,
            f"<button class='btn {'ok' if user.is_banned else 'bad'}'>{ban_label}</button>",
            confirm=(
                f"کاربر {user_id} آزاد شود؟"
                if user.is_banned
                else f"کاربر {user_id} مسدود شود؟ همه‌ی کارهایش خاموش می‌شود."
            ),
        )
        + " "
        + form(
            f"/users/{user_id}/revoke",
            session.csrf,
            "<button class='btn bad'>لغو اشتراک</button>",
            confirm=f"اشتراک کاربر {user_id} همین حالا تمام شود؟",
        )
    )

    task_rows = [
        f"<tr><td>#{task.id}</td>"
        f"<td>{esc(task.title or task.source_title or task.source_ref)}</td>"
        f"<td>{esc(task.dest_title or task.dest_ref)}</td>"
        f"<td>{pill('روشن', 'ok') if task.enabled else pill('خاموش')}</td></tr>"
        for task in tasks
    ]

    body = (
        _flash(request)
        + f"<h1>{esc(_who(user))}</h1>"
        + "<p class='sub'><a href='/users'>← بازگشت به فهرست</a></p>"
        + "<div class='split'>"
        + panel("خلاصه", facts)
        + panel("دادن اشتراک", give, sub="از انتهای اشتراک فعلی تمدید می‌شود.")
        + panel("تغییر روزهای اشتراک", adjust)
        + panel(
            "کارهای پرخطر",
            danger,
            sub="هر دو همین حالا اثر می‌کنند و به کاربر خبر داده می‌شود.",
        )
        + "</div>"
        + "<section><h2>کارهای کپی</h2>"
        + table(
            ["کد", "مبدا", "مقصد", "وضعیت"],
            task_rows,
            empty="این کاربر هیچ کاری نساخته.",
            icon="📋",
        )
        + "</section>"
    )
    return await _shell(request, _who(user), body, active="/users")


async def user_grant(request: web.Request) -> web.Response:
    back = f"/users/{request.match_info['id']}"
    blocked = await _guard_post(request, roles.CAP_MONEY, back)
    if blocked is not None:
        return blocked

    user_id = int(request.match_info["id"])
    plan_code = str((await request.post()).get("plan", ""))
    sub = await subscription.grant(
        user_id, plan_code, granted_by=request["session"].user_id, note="پنل وب"
    )
    if sub is None:
        return _back(back, err="این طرح وجود ندارد.")

    bot = request.app[BOT]
    try:
        await bot.send_message(
            user_id,
            f"🎁 اشتراک <b>{plan_code}</b> برای شما فعال شد.\n"
            f"تا <b>{sub.expires_at:%Y/%m/%d}</b> اعتبار دارد.",
        )
    except Exception:
        log.warning("اطلاع اشتراک به کاربر %s نرسید", user_id, exc_info=True)
        return _back(back, ok="اشتراک داده شد، ولی پیام به کاربر نرسید.")
    return _back(back, ok="اشتراک داده شد.")


async def user_days(request: web.Request) -> web.Response:
    back = f"/users/{request.match_info['id']}"
    blocked = await _guard_post(request, roles.CAP_MONEY, back)
    if blocked is not None:
        return blocked

    user_id = int(request.match_info["id"])
    days = _int_or_zero((await request.post()).get("days"))
    if not days:
        return _back(back, err="عدد روز معتبر نیست.")

    row = await subscription.adjust_days(
        user_id, days, admin_id=request["session"].user_id
    )
    if row is None:
        return _back(back, err="اشتراک فعالی ندارد. اول یک طرح بدهید.")
    return _back(back, ok=f"اشتراک {days:+d} روز شد.")


async def user_ban(request: web.Request) -> web.Response:
    back = f"/users/{request.match_info['id']}"
    blocked = await _guard_post(request, roles.CAP_USERS, back)
    if blocked is not None:
        return blocked

    user_id = int(request.match_info["id"])
    banning = not await moderation.is_banned(user_id)
    if not await moderation.set_ban(
        user_id, banning, admin_id=request["session"].user_id
    ):
        return _back(back, err="کاربر پیدا نشد.")
    return _back(
        back, ok="مسدود شد و کارهایش خاموش شد." if banning else "آزاد شد."
    )


async def user_revoke(request: web.Request) -> web.Response:
    back = f"/users/{request.match_info['id']}"
    blocked = await _guard_post(request, roles.CAP_MONEY, back)
    if blocked is not None:
        return blocked

    user_id = int(request.match_info["id"])
    count = await subscription.revoke(user_id, admin_id=request["session"].user_id)
    if not count:
        return _back(back, err="اشتراک فعالی نداشت.")

    bot = request.app[BOT]
    try:
        await bot.send_message(user_id, "⛔️ اشتراک شما توسط مدیریت لغو شد.")
    except Exception:
        log.debug("اطلاع لغو اشتراک به کاربر نرسید", exc_info=True)
    return _back(back, ok="اشتراک لغو شد.")


# ------------------------------------------------------------- کارها
async def task_list(request: web.Request) -> web.Response:
    """کارهای در حال اجرا، در یک نگاه.

    مفیدترین ستون «آخرین فعالیت» است: کاری که روشن است ولی مدت‌هاست
    چیزی کپی نکرده، معمولاً یعنی اکانت کاربر از کار افتاده — و آن
    چیزی است که کاربر با یک تیکت گله‌آمیز خبر می‌دهد، نه زودتر.
    """
    denied = await _deny(request, roles.CAP_USERS)
    if denied is not None:
        return denied

    only = request.query.get("only", "")
    async with get_session() as db:
        statement = select(Task).order_by(Task.id.desc()).limit(150)
        if only == "on":
            statement = statement.where(Task.enabled.is_(True))
        elif only == "off":
            statement = statement.where(Task.enabled.is_(False))
        tasks = list((await db.execute(statement)).scalars())

        people: dict[int, User] = {}
        if tasks:
            rows = await db.execute(
                select(User).where(User.id.in_({t.user_id for t in tasks}))
            )
            people = {user.id: user for user in rows.scalars()}

        running = await db.scalar(
            select(func.count()).select_from(Task).where(Task.enabled.is_(True))
        )
        total = await db.scalar(select(func.count()).select_from(Task))

    session = request["session"]
    rows_html = []
    for task in tasks:
        kind = "🌐 فید" if task.source_kind == Task.SOURCE_RSS else "📢 کانال"
        toggle = form(
            f"/tasks/{task.id}/toggle",
            session.csrf,
            f"<button class='btn small {'bad' if task.enabled else 'ok'}'>"
            f"{'خاموش' if task.enabled else 'روشن'}</button>",
        )
        rows_html.append(
            f"<tr><td>#{task.id}</td>"
            f"<td><a href='/users/{task.user_id}'>{esc(_who(people.get(task.user_id)))}</a></td>"
            f"<td>{kind}<div class='mini'>{esc(task.source_title or task.source_ref)}</div></td>"
            f"<td>{esc(task.dest_title or task.dest_ref)}</td>"
            f"<td>{pill('روشن', 'ok') if task.enabled else pill('خاموش')}</td>"
            f"<td class='actions'>{toggle}</td></tr>"
        )

    tabs = "".join(
        f"<a class='btn small' href='/tasks{q}'>{label}</a> "
        for q, label in (("", "همه"), ("?only=on", "روشن"), ("?only=off", "خاموش"))
    )

    body = (
        _flash(request)
        + "<h1>کارهای کپی</h1>"
        + "<p class='sub'>۱۵۰ کار آخر. خاموش کردن از اینجا همان اثری را دارد "
        "که خاموش کردن از داخل ربات.</p>"
        + "<div class='cards'>"
        + card("کار روشن", i18n.num(int(running or 0), "fa"), "ok")
        + card("کل کارها", i18n.num(int(total or 0), "fa"))
        + "</div>"
        + f"<section><h2>فهرست</h2><div class='row' style='margin-bottom:12px'>{tabs}</div>"
        + table(
            ["کد", "کاربر", "مبدا", "مقصد", "وضعیت", ""],
            rows_html,
            empty="کاری نیست.",
            icon="📋",
        )
        + "</section>"
    )
    return await _shell(request, "کارها", body, active="/tasks")


async def task_toggle(request: web.Request) -> web.Response:
    blocked = await _guard_post(request, roles.CAP_USERS, "/tasks")
    if blocked is not None:
        return blocked

    task_id = int(request.match_info["id"])
    async with get_session() as db:
        task = await db.get(Task, task_id)
        if task is None:
            return _back("/tasks", err="این کار پیدا نشد.")
        task.enabled = not task.enabled
        user_id, now_on = task.user_id, task.enabled
        await db.commit()

    # بدون این، ردیف در پنل عوض می‌شود ولی کپی همچنان ادامه دارد —
    # بدترین حالت، چون به نظر می‌رسد کار انجام شده.
    from telkap.services.userbot import manager

    await manager.reload_user(user_id)
    return _back("/tasks", ok=f"کار #{task_id} {'روشن' if now_on else 'خاموش'} شد.")


# --------------------------------------------------------- تنظیم پرداخت
async def settings_page(request: web.Request) -> web.Response:
    """همان تنظیماتی که در ربات هست، روی صفحه‌ی بزرگ‌تر.

    <b>چرا تکرارِ ربات نیست.</b> در ربات هر مقدار یک دکمه و یک گفتگوی
    جداست؛ اینجا همه با هم دیده می‌شوند و همان‌جا عوض می‌شوند. برای
    راه‌اندازی اولیه — که همه‌ی این‌ها باید یک‌بار پر شوند — تفاوتش
    زیاد است.
    """
    denied = await _deny(request, roles.CAP_MONEY)
    if denied is not None:
        return denied

    session = request["session"]
    number = await cardinfo.number()
    holder = await cardinfo.holder()
    wallet = await crypto.address()
    auto = await usdtrate.is_auto()
    merchant = await zarinpal.merchant()
    gateway = await zarinpal.configured()

    card_form = form(
        "/settings/card",
        session.csrf,
        "<label class='field'><span class='cap'>شماره کارت (۱۶ رقم)</span>"
        f"<input type='text' name='number' value='{esc(number)}' inputmode='numeric'>"
        "</label>"
        "<label class='field'><span class='cap'>نام صاحب حساب</span>"
        f"<input type='text' name='holder' value='{esc(holder)}'></label>"
        "<button class='btn primary'>ذخیره</button>",
    )

    coin_rows = []
    for code in coins.all_codes():
        spec = coins.get(code)
        rate = await crypto.rate(code)
        percent = await usdtrate.margin(code)
        coin_rows.append(
            "<label class='field'>"
            f"<span class='cap'>{esc(spec.label)} — تومان به ازای هر واحد</span>"
            f"<input type='number' name='rate_{esc(code)}' value='{rate}' min='0'>"
            f"<span class='hint'>حاشیه‌ی فعلی: {esc(i18n.num(percent, 'fa'))}٪</span>"
            "</label>"
        )

    crypto_form = form(
        "/settings/crypto",
        session.csrf,
        "<label class='field'><span class='cap'>نشانی ولت ترون (تتر و ترون هر دو)</span>"
        f"<input type='text' name='wallet' value='{esc(wallet)}' dir='ltr'></label>"
        + "".join(coin_rows)
        + "<button class='btn primary'>ذخیره</button>",
    )

    auto_form = form(
        "/settings/autorate",
        session.csrf,
        f"<button class='btn {'bad' if auto else 'ok'}'>"
        f"{'خاموش کردن نرخ خودکار' if auto else 'روشن کردن نرخ خودکار'}</button>",
    ) + " " + form(
        "/settings/ratenow", session.csrf, "<button class='btn'>گرفتن نرخ‌ها الان</button>"
    )

    base = (get_settings().web_base_url or "").rstrip("/")
    gate_form = form(
        "/settings/zarinpal",
        session.csrf,
        "<label class='field'><span class='cap'>کد پذیرنده</span>"
        f"<input type='text' name='merchant' value='{esc(merchant)}' dir='ltr'>"
        "<span class='hint'>در پنل زرین‌پال، نشانی بازگشت را هم ثبت کنید:<br>"
        f"<code>{esc(base + zarinpal.CALLBACK_PATH)}</code></span></label>"
        "<button class='btn primary'>ذخیره</button>",
    )

    ready = bool(number) or bool(await crypto.ready_coins()) or gateway
    warning = (
        "<div class='note warn'>🚨 <b>هیچ راه پرداختی تنظیم نشده</b> — یعنی "
        "کسی نمی‌تواند بخرد. مشتری به‌جای صفحه‌ی پرداخت به پشتیبانی ارجاع "
        "داده می‌شود.</div>"
        if not ready
        else ""
    )

    body = (
        _flash(request)
        + "<h1>راه‌های پرداخت</h1>"
        + "<p class='sub'>همان تنظیماتی که در ربات هست — اینجا همه با هم.</p>"
        + warning
        + "<div class='split'>"
        + panel("کارت‌به‌کارت", card_form)
        + panel(
            "ارز دیجیتال",
            crypto_form,
            sub="نشانی ولت بین تتر و ترون مشترک است؛ فقط نرخ جداست.",
        )
        + panel(
            f"نرخ خودکار — {'روشن' if auto else 'خاموش'}",
            auto_form,
            sub="هر ربع ساعت از نوبیتکس، کمی زیر بازار تا در فروش ضرر نکنید.",
        )
        + panel(
            f"درگاه زرین‌پال — {'فعال' if gateway else 'غیرفعال'}",
            gate_form,
        )
        + "</div>"
    )
    return await _shell(request, "پرداخت", body, active="/settings")


async def settings_card(request: web.Request) -> web.Response:
    blocked = await _guard_post(request, roles.CAP_MONEY, "/settings")
    if blocked is not None:
        return blocked

    posted = await request.post()
    admin_id = request["session"].user_id
    problems = []
    if await cardinfo.set_number(str(posted.get("number", "")), admin_id=admin_id) is None:
        problems.append("شماره کارت باید ۱۶ رقم باشد")
    if await cardinfo.set_holder(str(posted.get("holder", "")), admin_id=admin_id) is None:
        problems.append("نام صاحب حساب خالی است")
    if problems:
        return _back("/settings", err=" · ".join(problems))
    return _back("/settings", ok="اطلاعات کارت ذخیره شد.")


async def settings_crypto(request: web.Request) -> web.Response:
    blocked = await _guard_post(request, roles.CAP_MONEY, "/settings")
    if blocked is not None:
        return blocked

    posted = await request.post()
    admin_id = request["session"].user_id
    problems = []

    wallet = str(posted.get("wallet", "")).strip()
    if wallet and await crypto.set_address(wallet, admin_id=admin_id) is None:
        problems.append("نشانی ولت معتبر نیست")

    for code in coins.all_codes():
        raw = str(posted.get(f"rate_{code}", "")).strip()
        if not raw or raw == "0":
            continue
        if await crypto.set_rate(raw, coin=code, admin_id=admin_id) is None:
            problems.append(f"نرخ {coins.get(code).symbol} پذیرفته نشد")

    if problems:
        return _back("/settings", err=" · ".join(problems))
    return _back("/settings", ok="تنظیمات ارز ذخیره شد.")


async def settings_autorate(request: web.Request) -> web.Response:
    blocked = await _guard_post(request, roles.CAP_MONEY, "/settings")
    if blocked is not None:
        return blocked

    now_on = not await usdtrate.is_auto()
    await usdtrate.set_auto(now_on, admin_id=request["session"].user_id)
    if now_on:
        await usdtrate.refresh_all(force=True)
    return _back("/settings", ok=f"نرخ خودکار {'روشن' if now_on else 'خاموش'} شد.")


async def settings_ratenow(request: web.Request) -> web.Response:
    """گرفتن فوریِ نرخ‌ها — و گفتنِ اینکه اگر نشد، چرا نشد."""
    blocked = await _guard_post(request, roles.CAP_MONEY, "/settings")
    if blocked is not None:
        return blocked

    results = await usdtrate.refresh_all(force=True)
    good, bad = [], []
    for code, outcome in results.items():
        spec = coins.get(code)
        name = spec.symbol if spec else code
        if outcome.changed:
            good.append(f"{name} {outcome.rate:,}")
        elif outcome.error:
            bad.append(f"{name}: {outcome.error}")

    if bad:
        return _back("/settings", err=" · ".join(bad))
    if good:
        return _back("/settings", ok=" · ".join(good))
    return _back("/settings", ok="نرخ‌ها همین حالا هم به‌روز بودند.")


async def settings_zarinpal(request: web.Request) -> web.Response:
    blocked = await _guard_post(request, roles.CAP_MONEY, "/settings")
    if blocked is not None:
        return blocked

    posted = await request.post()
    saved = await zarinpal.set_merchant(
        str(posted.get("merchant", "")), admin_id=request["session"].user_id
    )
    if saved is None:
        return _back("/settings", err="کد پذیرنده باید شکل UUID داشته باشد.")
    return _back("/settings", ok="کد پذیرنده ذخیره شد.")


# --------------------------------------------- بازگشت از درگاه زرین‌پال
async def zarinpal_return(request: web.Request) -> web.Response:
    """کاربر از درگاه برگشته است.

    <b>به هیچ‌کدام از پارامترهای این نشانی اعتماد نمی‌شود.</b> مرورگر
    کاربر `Status=OK` را می‌آورد و هرکسی می‌تواند همین نشانی را دستی
    باز کند. تنها چیزی که پرداخت را ثابت می‌کند، تماس سمت سرور با
    زرین‌پال است — و مبلغی که به آن تماس می‌دهیم از دیتابیس خودمان
    می‌آید، نه از این نشانی.
    """
    def done(message: str, *, bad: bool = False) -> web.Response:
        return web.Response(
            text=render.gate(message, bad=bad), content_type="text/html"
        )

    request_id = _int_or_zero(request.query.get("rid"))
    authority = (request.query.get("Authority") or "").strip()
    status = (request.query.get("Status") or "").strip().upper()

    payment = await payments.get_request(request_id) if request_id else None
    if payment is None:
        return done("این پرداخت پیدا نشد.", bad=True)

    if payment.status == PaymentRequest.STATUS_APPROVED:
        # کاربر صفحه را دوباره باز کرده؛ نباید دوباره اشتراک بدهیم
        return done("این پرداخت قبلاً تأیید شده و اشتراکتان فعال است.")

    if status != "OK" or not authority:
        return done("پرداخت انجام نشد یا لغو شد. می‌توانید دوباره تلاش کنید.")

    receipt_data = await zarinpal.verify(authority, payment.amount_toman)
    if receipt_data is None:
        return done(
            "تأیید پرداخت ناموفق بود. اگر مبلغ از حسابتان کم شده، با "
            "پشتیبانی تماس بگیرید و کد پیگیری را بدهید: " + str(payment.id),
            bad=True,
        )

    await payments.attach_reference(payment.id, receipt_data["ref_id"])
    approved, sub = await payments.approve(payment.id, admin_id=0)
    if approved is None:
        return done("فعال‌سازی ناموفق بود. با پشتیبانی تماس بگیرید.", bad=True)

    bot = request.app[BOT]
    try:
        await bot.send_message(
            approved.user_id, await payments.approval_notice(approved, sub)
        )
    except Exception:
        log.warning("اطلاع فعال‌سازی به کاربر %s نرسید", approved.user_id, exc_info=True)

    return done(
        f"پرداخت تأیید شد و اشتراکتان فعال است.\n"
        f"شماره پیگیری: {receipt_data['ref_id']}\n"
        "می‌توانید به تلگرام برگردید."
    )


def _int_or_zero(raw) -> int:
    try:
        return int(raw)
    except (TypeError, ValueError):
        return 0


# ------------------------------------------------------- بالا آوردن
def build_app(bot) -> web.Application:
    app = web.Application(middlewares=[auth_middleware])
    app[BOT] = bot
    app.add_routes(
        [
            web.get("/healthz", healthz),
            web.get(zarinpal.CALLBACK_PATH, zarinpal_return),
            web.get("/enter", enter),
            web.get("/login", login_page),
            web.post("/login", login_submit),
            web.get("/logout", logout),
            web.get("/", dashboard),
            web.get("/payments", payment_list),
            web.post("/payments/{id}/approve", payment_approve),
            web.post("/payments/{id}/reject", payment_reject),
            web.get("/receipt/{id}", receipt),
            web.get("/users", user_list),
            web.get("/users/{id}", user_detail),
            web.post("/users/{id}/grant", user_grant),
            web.post("/users/{id}/days", user_days),
            web.post("/users/{id}/ban", user_ban),
            web.post("/users/{id}/revoke", user_revoke),
            web.get("/tasks", task_list),
            web.post("/tasks/{id}/toggle", task_toggle),
            web.get("/settings", settings_page),
            web.post("/settings/card", settings_card),
            web.post("/settings/crypto", settings_crypto),
            web.post("/settings/autorate", settings_autorate),
            web.post("/settings/ratenow", settings_ratenow),
            web.post("/settings/zarinpal", settings_zarinpal),
        ]
    )
    return app


async def start_panel(bot) -> None:
    """اگر در .env روشن شده باشد، پنل را بالا می‌آورد."""
    global _runner
    cfg = get_settings()
    if not cfg.web_enabled:
        return
    if _runner is not None:
        return

    _runner = web.AppRunner(build_app(bot), access_log=None)
    await _runner.setup()
    site = web.TCPSite(_runner, cfg.web_host, cfg.web_port)
    await site.start()
    log.info("پنل وب روی http://%s:%s بالا آمد", cfg.web_host, cfg.web_port)
    if not cfg.web_base_url:
        log.warning(
            "WEB_BASE_URL خالی است؛ لینک ورود ساخته نمی‌شود. "
            "آدرس عمومی پنل را در .env بگذارید."
        )


async def stop_panel() -> None:
    global _runner
    if _runner is None:
        return
    await _runner.cleanup()
    _runner = None
    log.info("پنل وب خاموش شد")
