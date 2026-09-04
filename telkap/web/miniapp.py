"""مینی‌اپ تلگرام: احراز هویت و رابط JSON.

<b>تفاوت بنیادی با پنل.</b> پنل برای ماست و پشت نام کاربری و رمز و کد
دومرحله‌ای می‌نشیند. مینی‌اپ برای مشتری است و هیچ‌کدام از این‌ها را
ندارد — خودِ تلگرام می‌گوید این چه کسی است.

<b>و همین‌جا خطرناک‌ترین جای کار است.</b> تلگرام هویت را در یک رشته‌ی
<code>initData</code> می‌دهد که مرورگر آن را در اختیار دارد؛ یعنی هرکس
می‌تواند بنویسد «من کاربر شماره‌ی فلانم». تنها چیزی که این را از یک
ادعای ساده جدا می‌کند، امضای HMAC است که با توکن ربات ساخته شده و فقط
تلگرام و ما می‌توانیم بسازیمش. اگر این بررسی جا بیفتد یا سرسری انجام
شود، هرکسی می‌تواند به داده‌ی هر مشتری‌ای برسد.

پس اینجا هیچ مسیری بدون <code>_who()</code> نیست، و
<code>_who()</code> بدون امضای درست چیزی برنمی‌گرداند.

روش امضا همان چیزی است که تلگرام مستند کرده:

    secret       = HMAC_SHA256(key="WebAppData", message=BOT_TOKEN)
    check_string = "\\n".join(f"{k}={v}" for k, v in sorted(pairs) if k != "hash")
    hash         = HMAC_SHA256(key=secret, message=check_string).hex()
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import time
from urllib.parse import parse_qsl

from aiohttp import web
from sqlalchemy import func, select

from telkap.config import get_settings
from telkap.db import get_session, log_activity
from telkap.models import Destination, Task, User
from telkap.plans import all_purchasable, get_plan
from telkap.services import chats, subscription, wallet

log = logging.getLogger(__name__)

# مسیر مینی‌اپ. فایل‌های ثابتش را nginx می‌دهد؛ این پیشوند فقط برای
# رابط JSON است.
API_PREFIX = "/app/api"

# initData کهنه پذیرفته نمی‌شود. اگر روزی یکی از کاربران‌مان جایی لو
# برود، نباید تا ابد کلیدِ ورود بماند. یک روز آن‌قدر هست که کسی وسط کار
# بیرون نیفتد و آن‌قدر کوتاه که دزدیدنش ارزش نداشته باشد.
MAX_AGE_SECONDS = 24 * 3600


def public_url() -> str:
    """نشانی عمومی مینی‌اپ، یا خالی اگر هنوز آماده نیست.

    از WEB_BASE_URL ساخته می‌شود که به «/panel» ختم می‌شود؛ مینی‌اپ
    همسایه‌ی آن است، نه زیرمجموعه‌اش.

    تلگرام فقط https را برای مینی‌اپ می‌پذیرد. اگر نشانی http باشد،
    دکمه اصلاً ساخته نمی‌شود — دکمه‌ای که با خطای تلگرام باز نشود از
    نبودنش بدتر است.
    """
    from telkap.web.render import PREFIX

    base = (get_settings().web_base_url or "").rstrip("/")
    if not base.startswith("https://"):
        return ""
    if PREFIX and base.endswith(PREFIX):
        base = base[: -len(PREFIX)]
    return f"{base.rstrip('/')}/app"


def check(init_data: str, token: str, *, now: float | None = None) -> dict | None:
    """اگر امضا درست و تازه بود، داده‌های تلگرام را برمی‌گرداند.

    در هر حالت دیگری <code>None</code> — بدون تفکیک اینکه کدام بررسی
    شکست خورده، چون آن تفکیک فقط به کسی که دارد امتحان می‌کند کمک
    می‌کند.
    """
    if not init_data or not token:
        return None

    # strict_parsing تا رشته‌ی خراب بی‌صدا به دیکشنری نصفه تبدیل نشود
    try:
        pairs = dict(parse_qsl(init_data, strict_parsing=True))
    except ValueError:
        return None

    given = pairs.pop("hash", "")
    if not given:
        return None

    # <b>«signature» هم مثل «hash» بیرون می‌ماند.</b>
    #
    # تلگرام بعداً فیلد signature را اضافه کرد — یک امضای Ed25519 برای
    # اینکه سرویس‌های ثالث بتوانند بدون داشتنِ توکنِ ربات هم داده را
    # بسنجند. آن فیلد جزو رشته‌ی امضای HMAC نیست.
    #
    # تا وقتی اینجا نبود، هر initDataیی که تلگرام می‌فرستاد رد می‌شد و
    # اپ می‌گفت «شناسایی نشدید» — بی‌آنکه چیزی در لاگ بیفتد، چون از
    # نظر کد این فقط یک امضای غلط بود.
    pairs.pop("signature", None)

    check_string = "\n".join(f"{key}={pairs[key]}" for key in sorted(pairs))
    secret = hmac.new(b"WebAppData", token.encode(), hashlib.sha256).digest()
    mine = hmac.new(secret, check_string.encode(), hashlib.sha256).hexdigest()

    # compare_digest نه ==، تا مقایسه به‌ازای هر کاراکترِ درست کندتر
    # نشود و امضا را نشود حرف‌به‌حرف حدس زد
    if not hmac.compare_digest(mine, given):
        return None

    try:
        issued = int(pairs.get("auth_date", "0"))
    except ValueError:
        return None
    if issued <= 0 or (now or time.time()) - issued > MAX_AGE_SECONDS:
        return None

    try:
        pairs["user"] = json.loads(pairs.get("user", "null"))
    except (TypeError, ValueError):
        pairs["user"] = None
    return pairs


def user_id_from(init_data: str, token: str) -> int | None:
    data = check(init_data, token)
    if not data:
        return None
    person = data.get("user") or {}
    try:
        return int(person.get("id", 0)) or None
    except (TypeError, ValueError):
        return None


# ------------------------------------------------------------ درخواست
def _init_data(request: web.Request) -> str:
    """initData از سرصفحه می‌آید، نه از نشانی.

    نشانی در لاگ سرور و در تاریخچه‌ی مرورگر می‌نشیند؛ چیزی که هویت را
    اثبات می‌کند نباید آنجا باشد.
    """
    return request.headers.get("X-Telegram-Init-Data", "")


async def _who(request: web.Request) -> int | None:
    return user_id_from(_init_data(request), get_settings().bot_token)


def _no(message: str = "شناسایی نشدید", status: int = 401) -> web.Response:
    return web.json_response({"error": message}, status=status)


def _yes(payload: dict) -> web.Response:
    # ensure_ascii=False تا فارسی در پاسخ فارسی بماند، نه \\uXXXX
    return web.json_response(payload, dumps=lambda data: json.dumps(data, ensure_ascii=False))


# ------------------------------------------------------------- مسیرها
async def me(request: web.Request) -> web.Response:
    """کیستم، چه اشتراکی دارم، چقدر پول دارم."""
    user_id = await _who(request)
    if user_id is None:
        return _no()

    async with get_session() as db:
        person = await db.get(User, user_id)

    if person is None:
        # هنوز ربات را استارت نکرده. این خطا نیست، یک حالت است — و
        # اپ باید بتواند بگوید «اول ربات را باز کنید».
        return _yes({"known": False})

    plan = await subscription.active_plan_for(user_id)
    days = await subscription.remaining_days(user_id)
    return _yes({
        "known": True,
        "id": person.id,
        "name": (person.first_name or "").strip(),
        "banned": bool(person.is_banned),
        "connected": bool(person.session_enc),
        "wallet": await wallet.balance(user_id),
        "plan": (
            {"code": plan.code, "title": plan.title, "days_left": days}
            if plan is not None
            else None
        ),
    })


async def tasks(request: web.Request) -> web.Response:
    user_id = await _who(request)
    if user_id is None:
        return _no()

    async with get_session() as db:
        rows = await db.execute(
            select(Task).where(Task.user_id == user_id).order_by(Task.id.desc())
        )
        found = list(rows.scalars())

    return _yes({
        "tasks": [
            {
                "id": task.id,
                "title": task.title or task.source_title or f"کار #{task.id}",
                "enabled": bool(task.enabled),
                "copied": int(task.copied_count or 0),
            }
            for task in found
        ]
    })


async def toggle(request: web.Request) -> web.Response:
    """روشن/خاموش کردن یک کار.

    <b>کار باید مالِ خودش باشد.</b> شناسه‌ی کار عددی و حدس‌زدنی است؛
    بدون این شرط، هرکس می‌توانست کارِ هر مشتریِ دیگری را خاموش کند.
    """
    user_id = await _who(request)
    if user_id is None:
        return _no()

    task_id = int(request.match_info["id"])
    async with get_session() as db:
        task = await db.get(Task, task_id)
        if task is None or task.user_id != user_id:
            return _no("این کار پیدا نشد", status=404)
        task.enabled = not task.enabled
        now_on = task.enabled
        await db.commit()

    # بدون این، ردیف در اپ عوض می‌شود ولی کپی همچنان ادامه دارد —
    # بدترین حالت، چون به نظر می‌رسد کار انجام شده.
    from telkap.services.userbot import manager

    await manager.reload_user(user_id)
    return _yes({"id": task_id, "enabled": now_on})


async def plans(request: web.Request) -> web.Response:
    """طرح‌ها. تنها مسیری که هویت نمی‌خواهد — قیمت‌ها عمومی‌اند."""
    from telkap.plans import POPULAR_CODE

    return _yes({
        "popular": POPULAR_CODE,
        "plans": [
            {
                "code": plan.code,
                "title": plan.title,
                "tagline": plan.tagline,
                "days": plan.days,
                "price": plan.price_toman,
                "messages": plan.messages_label,
                "tasks": plan.max_tasks,
                "destinations": plan.max_destinations,
            }
            for plan in all_purchasable()
        ],
    })


async def quote(request: web.Request) -> web.Response:
    """قیمت یک طرح برای همین شخص — با تخفیف نمایندگی اگر داشته باشد."""
    user_id = await _who(request)
    if user_id is None:
        return _no()

    plan = get_plan(request.match_info["code"])
    if plan is None or plan.price_toman <= 0:
        return _no("این طرح خریدنی نیست", status=404)

    from telkap.services import reseller

    is_reseller, discount = await reseller.profile(user_id)
    price = (
        reseller.discounted(plan.price_toman, discount)
        if is_reseller
        else plan.price_toman
    )
    return _yes({
        "code": plan.code,
        "title": plan.title,
        "list_price": plan.price_toman,
        "price": price,
        "discount": discount if is_reseller else 0,
        "wallet": await wallet.balance(user_id),
    })


# ------------------------------------------------------ کارها: ساخت و ویرایش
#
# <b>چه تنظیماتی از اپ قابل تغییرند.</b> فهرستِ زیر بسته است و عمداً.
# ورودی از بیرون می‌آید؛ اگر هر کلیدی را می‌پذیرفتیم، کسی می‌توانست
# کلیدهایی بنویسد که ما هرگز اعتبارسنجی‌شان نکرده‌ایم — یا کلیدهای
# داخلیِ آینده را از بیرون بنشاند.
BOOL_SETTINGS = (
    "remove_links", "remove_hashtags", "remove_mentions", "remove_emails",
    "remove_emoji", "remove_source_signature", "strip_empty_lines",
    "block_ads", "block_forwarded", "block_with_links", "block_with_buttons",
    "skip_duplicates", "skip_bots", "skip_replies",
    "sync_edits", "sync_deletes", "copy_buttons", "caption_only",
)
TEXT_SETTINGS = {"header": 1024, "footer": 1024, "signature": 256}
INT_SETTINGS = {
    "delay_seconds": (0, 86_400),
    "max_per_hour": (0, 10_000),
    "min_length": (0, 4096),
    "max_length": (0, 4096),
    "order_grace_seconds": (5, 86_400),
    "skip_media_over_mb": (0, 4096),
}
CHOICE_SETTINGS = {
    "mode": ("copy", "forward"),
    "order_mode": ("strict", "fast", "grace"),
    "ad_sensitivity": ("low", "medium", "high"),
}


def _clean_settings(posted: dict, cfg: dict) -> tuple[dict, list[str]]:
    """فقط کلیدهای شناخته‌شده، و هرکدام در دامنه‌ی خودش."""
    problems: list[str] = []
    for key, value in posted.items():
        if key in BOOL_SETTINGS:
            cfg[key] = bool(value)
        elif key in CHOICE_SETTINGS:
            if value in CHOICE_SETTINGS[key]:
                cfg[key] = value
            else:
                problems.append(key)
        elif key in TEXT_SETTINGS:
            cfg[key] = str(value or "")[: TEXT_SETTINGS[key]]
        elif key in INT_SETTINGS:
            low, high = INT_SETTINGS[key]
            try:
                cfg[key] = max(low, min(int(value), high))
            except (TypeError, ValueError):
                problems.append(key)
        else:
            problems.append(key)
    return cfg, problems


async def _own_task(user_id: int, task_id: int) -> Task | None:
    async with get_session() as db:
        task = await db.get(Task, task_id)
    return task if task is not None and task.user_id == user_id else None


async def task_detail(request: web.Request) -> web.Response:
    user_id = await _who(request)
    if user_id is None:
        return _no()

    task = await _own_task(user_id, int(request.match_info["id"]))
    if task is None:
        return _no("این کار پیدا نشد", status=404)

    from telkap.services.defaults import merged_settings

    cfg = merged_settings(task.settings)
    async with get_session() as db:
        extra = list(
            (
                await db.execute(
                    select(Destination).where(Destination.task_id == task.id)
                )
            ).scalars()
        )

    return _yes({
        "id": task.id,
        "title": task.title or task.source_title or f"کار #{task.id}",
        "enabled": bool(task.enabled),
        "copied": int(task.copied_count or 0),
        "skipped": int(task.skipped_count or 0),
        "source": task.source_title or task.source_ref,
        "dest": task.dest_title or task.dest_ref,
        "extra_dests": [
            {"id": row.id, "ref": row.ref, "enabled": bool(row.enabled)}
            for row in extra
        ],
        "settings": {
            key: cfg.get(key)
            for key in (
                *BOOL_SETTINGS, *TEXT_SETTINGS, *INT_SETTINGS, *CHOICE_SETTINGS
            )
        },
    })


async def task_settings(request: web.Request) -> web.Response:
    """تنظیمات یک کار را عوض می‌کند و کلاینت را دوباره بار می‌زند."""
    user_id = await _who(request)
    if user_id is None:
        return _no()

    task = await _own_task(user_id, int(request.match_info["id"]))
    if task is None:
        return _no("این کار پیدا نشد", status=404)

    try:
        posted = await request.json()
    except Exception:
        return _no("ورودی درست نبود", status=400)
    if not isinstance(posted, dict):
        return _no("ورودی درست نبود", status=400)

    from telkap.services.defaults import merged_settings

    cfg, problems = _clean_settings(posted, merged_settings(task.settings))
    if problems:
        return _no("این تنظیم‌ها پذیرفته نشدند: " + "، ".join(problems), status=400)

    async with get_session() as db:
        row = await db.get(Task, task.id)
        row.settings = cfg
        await db.commit()

    from telkap.services import cache
    from telkap.services.userbot import manager

    cache.invalidate_task(task.id)
    await manager.reload_user(user_id)
    return _yes({"ok": True})


async def task_create(request: web.Request) -> web.Response:
    """کار تازه — با همان سقفی که ربات هم رعایتش می‌کند."""
    user_id = await _who(request)
    if user_id is None:
        return _no()

    try:
        posted = await request.json()
    except Exception:
        return _no("ورودی درست نبود", status=400)

    async with get_session() as db:
        person = await db.get(User, user_id)
    if person is None or not person.is_logged_in:
        return _no("اول باید اکانت کاربری‌تان را در ربات وصل کنید", status=409)

    plan = await subscription.active_plan_for(user_id)
    if plan is None:
        return _no("اشتراک فعالی ندارید", status=402)

    async with get_session() as db:
        count = await db.scalar(
            select(func.count(Task.id)).where(Task.user_id == user_id)
        )
    if (count or 0) >= plan.max_tasks:
        return _no(
            f"در طرح «{plan.title}» حداکثر {plan.max_tasks} کار می‌توانید داشته باشید",
            status=402,
        )

    source = str(posted.get("source") or "").strip()
    dest = str(posted.get("dest") or "").strip()
    if not source or not dest:
        return _no("مبدا و مقصد لازم‌اند", status=400)
    if source == dest:
        return _no("مبدا و مقصد نمی‌توانند یکی باشند", status=400)

    async with get_session() as db:
        task = Task(
            user_id=user_id,
            title=str(posted.get("title") or "")[:128]
            or str(posted.get("source_title") or "")[:128],
            source_ref=source,
            source_title=str(posted.get("source_title") or "")[:160],
            dest_ref=dest,
            dest_title=str(posted.get("dest_title") or "")[:160],
            settings={},
        )
        db.add(task)
        await db.commit()
        await db.refresh(task)
        task_id = task.id

    # آیدی عددی مقصد یک‌بار حل و ذخیره می‌شود تا ارسال‌ها سریع‌تر باشند
    from telkap.services.userbot import manager

    client = await manager.ensure_client(user_id)
    if client is not None:
        dest_id = await manager.resolve_chat_id(client, dest)
        source_id = await manager.resolve_chat_id(client, source)
        async with get_session() as db:
            row = await db.get(Task, task_id)
            if row is not None:
                if dest_id is not None:
                    row.dest_id = dest_id
                if source_id is not None:
                    row.source_id = source_id
                await db.commit()

    await manager.reload_user(user_id)
    await log_activity(
        user_id=user_id,
        task_id=task_id,
        event="task_create",
        detail=f"{source} ← {dest} (مینی‌اپ)",
    )
    return _yes({"id": task_id})


async def task_delete(request: web.Request) -> web.Response:
    user_id = await _who(request)
    if user_id is None:
        return _no()

    task_id = int(request.match_info["id"])
    task = await _own_task(user_id, task_id)
    if task is None:
        return _no("این کار پیدا نشد", status=404)

    async with get_session() as db:
        row = await db.get(Task, task_id)
        if row is not None:
            await db.delete(row)
            await db.commit()

    from telkap.services.userbot import manager

    await manager.reload_user(user_id)
    await log_activity(
        user_id=user_id, task_id=task_id, event="task_delete", detail="از مینی‌اپ"
    )
    return _yes({"ok": True})


# ------------------------------------------------------------ چت‌ها و آمار
async def my_chats(request: web.Request) -> web.Response:
    """کانال‌ها و گروه‌های اکانتِ متصل — برای ساختن کار."""
    user_id = await _who(request)
    if user_id is None:
        return _no()

    writable = request.query.get("writable") == "1"
    found = await chats.load(user_id, writable_only=writable)
    if found is None:
        return _no("اکانت کاربری وصل نیست", status=409)
    return _yes({"chats": found})


async def wallet_page(request: web.Request) -> web.Response:
    user_id = await _who(request)
    if user_id is None:
        return _no()

    entries = await wallet.history(user_id, limit=25)
    return _yes({
        "balance": await wallet.balance(user_id),
        "entries": [
            {
                "amount": int(entry.amount_toman),
                "after": int(entry.balance_after or 0),
                "reason": wallet.reason_label(entry.reason),
                "note": entry.note or "",
                "at": entry.created_at.strftime("%Y/%m/%d %H:%M"),
            }
            for entry in entries
        ],
    })


async def stats(request: web.Request) -> web.Response:
    """آمار خودِ کاربر: چقدر کپی شده و چقدر طول کشیده."""
    user_id = await _who(request)
    if user_id is None:
        return _no()

    from telkap.services import timings

    data = await timings.report(days=7, user_id=user_id)
    async with get_session() as db:
        rows = list(
            (
                await db.execute(select(Task).where(Task.user_id == user_id))
            ).scalars()
        )

    return _yes({
        "tasks": len(rows),
        "active": sum(1 for row in rows if row.enabled),
        "copied": sum(int(row.copied_count or 0) for row in rows),
        "skipped": sum(int(row.skipped_count or 0) for row in rows),
        "speed": {
            "count": data.overall.count,
            "median": data.overall.median,
            "p90": data.overall.p90,
            "worst": data.overall.worst,
            "slow_percent": data.overall.over_minute_percent,
        },
        "daily": await timings.daily(14, user_id=user_id),
    })


def routes() -> list:
    return [
        web.get(f"{API_PREFIX}/plans", plans),
        web.get(f"{API_PREFIX}/me", me),
        web.get(f"{API_PREFIX}/stats", stats),
        web.get(f"{API_PREFIX}/wallet", wallet_page),
        web.get(f"{API_PREFIX}/chats", my_chats),
        web.get(f"{API_PREFIX}/tasks", tasks),
        web.post(f"{API_PREFIX}/tasks", task_create),
        web.get(f"{API_PREFIX}/tasks/{{id}}", task_detail),
        web.post(f"{API_PREFIX}/tasks/{{id}}/toggle", toggle),
        web.post(f"{API_PREFIX}/tasks/{{id}}/settings", task_settings),
        web.post(f"{API_PREFIX}/tasks/{{id}}/delete", task_delete),
        web.get(f"{API_PREFIX}/quote/{{code}}", quote),
    ]
