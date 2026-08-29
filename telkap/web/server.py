"""وب‌سرور پنل مدیریت.

روی aiohttp که از قبل وابستگیِ پروژه است، و داخل همان حلقه‌ی asyncio ربات.
پس نه پروسه‌ی تازه‌ای بالا می‌آید، نه نصب تازه‌ای لازم است، نه دو نفر سر
دیتابیس SQLite به هم می‌خورند.
"""
from __future__ import annotations

import logging
from datetime import UTC, timedelta, timezone

from aiohttp import web
from sqlalchemy import func, or_, select

from telkap import i18n
from telkap.config import get_settings
from telkap.db import get_session
from telkap.models import PaymentRequest, Subscription, Task, User, utcnow
from telkap.services import analytics, payments, roles
from telkap.web import auth, render
from telkap.web.render import card, esc, money, page, table

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
@web.middleware
async def auth_middleware(request: web.Request, handler):
    if request.path in {"/enter", "/healthz"}:
        return await handler(request)

    session = auth.get_session(request.cookies.get(auth.COOKIE_NAME))
    if session is None:
        return web.Response(
            text=render.gate(
                "برای ورود، در ربات دکمه‌ی «🖥 پنل وب» را بزنید و لینکی که "
                "می‌دهد را باز کنید. لینک پنج دقیقه اعتبار دارد."
            ),
            content_type="text/html",
            status=401,
        )

    # نقش ممکن است بعد از ورود گرفته شده باشد؛ هر درخواست دوباره سنجیده
    # می‌شود تا کسی با نشستِ باز، بعدِ عزل هم داخل نماند
    if not await roles.is_staff(session.user_id):
        auth.forget_user(session.user_id)
        return web.Response(
            text=render.gate("دیگر دسترسی مدیریتی ندارید.", bad=True),
            content_type="text/html",
            status=403,
        )

    request["session"] = session
    return await handler(request)


# -------------------------------------------------------------- ورود
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

    sid = auth.start_session(user_id)
    # روی http کوکیِ Secure اصلاً برنمی‌گردد؛ پس فقط وقتی که آدرس پنل
    # https است این را می‌گذاریم
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

    response = web.HTTPFound("/")
    response.set_cookie(
        auth.COOKIE_NAME,
        sid,
        httponly=True,
        samesite="Lax",
        max_age=auth.SESSION_TTL_SECONDS,
        secure=secure,
    )
    return response


async def logout(request: web.Request) -> web.Response:
    auth.end_session(request.cookies.get(auth.COOKIE_NAME))
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
    return web.Response(
        text=page("نمای کلی", body, active="/", who=str(request["session"].user_id)),
        content_type="text/html",
    )


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
            "<td>"
            f"<form class='inline' method='post' action='/payments/{req.id}/approve'>"
            f"<input type='hidden' name='csrf' value='{esc(session.csrf)}'>"
            "<button class='btn ok'>تأیید</button></form> "
            f"<form class='inline' method='post' action='/payments/{req.id}/reject'"
            " onsubmit=\"return confirm('این رسید رد شود؟')\">"
            f"<input type='hidden' name='csrf' value='{esc(session.csrf)}'>"
            "<button class='btn bad'>رد</button></form>"
            "</td></tr>"
        )

    flash = ""
    if request.query.get("ok"):
        flash = f"<div class='flash ok'>{esc(request.query['ok'])}</div>"
    elif request.query.get("err"):
        flash = f"<div class='flash bad'>{esc(request.query['err'])}</div>"

    body = (
        f"{flash}<h1>رسیدهای منتظر بررسی</h1>"
        "<p class='sub'>تأیید همان کاری را می‌کند که دکمه‌ی داخل ربات — "
        "اشتراک فعال می‌شود و به کاربر خبر می‌رسد.</p>"
        + table(
            ["کد", "کاربر", "خرید", "مبلغ", "رسید", "زمان", ""],
            rows_html,
            empty="رسیدی منتظر بررسی نیست.",
        )
    )
    return web.Response(
        text=page("رسیدها", body, active="/payments", who=str(session.user_id)),
        content_type="text/html",
    )


async def _guard_post(request: web.Request) -> web.Response | None:
    denied = await _deny(request, roles.CAP_MONEY)
    if denied is not None:
        return denied
    form = await request.post()
    if not auth.check_csrf(request["session"], str(form.get("csrf", ""))):
        return web.HTTPFound("/payments?err=درخواست معتبر نبود. دوباره تلاش کنید.")
    return None


async def payment_approve(request: web.Request) -> web.Response:
    blocked = await _guard_post(request)
    if blocked is not None:
        return blocked

    admin_id = request["session"].user_id
    request_id = int(request.match_info["id"])
    req, sub = await payments.approve(request_id, admin_id)
    if req is None:
        return web.HTTPFound("/payments?err=این درخواست قبلاً بررسی شده بود.")

    bot = request.app[BOT]
    try:
        await bot.send_message(req.user_id, await payments.approval_notice(req, sub))
    except Exception:
        # تأیید انجام شده و نباید به‌خاطر نرسیدن پیام برگردانده شود؛
        # فقط ادمین باید بداند که کاربر خبردار نشد
        log.warning("اطلاع تأیید به کاربر %s نرسید", req.user_id, exc_info=True)
        return web.HTTPFound(
            f"/payments?ok=رسید #{request_id} تأیید شد، ولی پیام به کاربر نرسید."
        )
    return web.HTTPFound(f"/payments?ok=رسید #{request_id} تأیید شد.")


async def payment_reject(request: web.Request) -> web.Response:
    blocked = await _guard_post(request)
    if blocked is not None:
        return blocked

    admin_id = request["session"].user_id
    request_id = int(request.match_info["id"])
    req = await payments.reject(request_id, admin_id)
    if req is None:
        return web.HTTPFound("/payments?err=این درخواست قبلاً بررسی شده بود.")

    bot = request.app[BOT]
    try:
        await bot.send_message(req.user_id, payments.rejection_notice(req))
    except Exception:
        log.warning("اطلاع رد به کاربر %s نرسید", req.user_id, exc_info=True)
        return web.HTTPFound(
            f"/payments?ok=رسید #{request_id} رد شد، ولی پیام به کاربر نرسید."
        )
    return web.HTTPFound(f"/payments?ok=رسید #{request_id} رد شد.")


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
            f"<span class='pill ok'>{esc(sub.plan_code)} تا {esc(_when(sub.expires_at)[:10])}</span>"
            if sub
            else "<span class='pill'>ندارد</span>"
        )
        state = (
            "<span class='pill bad'>مسدود</span>"
            if user.is_banned
            else "<span class='pill ok'>وصل</span>"
            if user.session_enc
            else "<span class='pill'>بدون اکانت</span>"
        )
        rows_html.append(
            f"<tr><td>{esc(_who(user))}<div class='mini'>{esc(user.id)}</div></td>"
            f"<td>{plan}</td><td>{state}</td>"
            f"<td class='money'>{esc(i18n.num(tasks.get(user.id, 0), 'fa'))}</td>"
            f"<td class='money'>{esc(money(user.wallet_toman))}</td>"
            f"<td>{esc(_when(user.created_at))}</td></tr>"
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
            ["کاربر", "اشتراک", "وضعیت", "کارها", "کیف پول", "عضویت"],
            rows_html,
            empty="کاربری پیدا نشد.",
        )
    )
    return web.Response(
        text=page("کاربران", body, active="/users", who=str(request["session"].user_id)),
        content_type="text/html",
    )


# ------------------------------------------------------- بالا آوردن
def build_app(bot) -> web.Application:
    app = web.Application(middlewares=[auth_middleware])
    app[BOT] = bot
    app.add_routes(
        [
            web.get("/healthz", healthz),
            web.get("/enter", enter),
            web.get("/logout", logout),
            web.get("/", dashboard),
            web.get("/payments", payment_list),
            web.post("/payments/{id}/approve", payment_approve),
            web.post("/payments/{id}/reject", payment_reject),
            web.get("/receipt/{id}", receipt),
            web.get("/users", user_list),
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
