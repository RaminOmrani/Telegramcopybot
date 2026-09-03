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
from sqlalchemy import select

from telkap.config import get_settings
from telkap.db import get_session
from telkap.models import Task, User
from telkap.plans import all_purchasable, get_plan
from telkap.services import subscription, wallet

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


def routes() -> list:
    return [
        web.get(f"{API_PREFIX}/plans", plans),
        web.get(f"{API_PREFIX}/me", me),
        web.get(f"{API_PREFIX}/tasks", tasks),
        web.post(f"{API_PREFIX}/tasks/{{id}}/toggle", toggle),
        web.get(f"{API_PREFIX}/quote/{{code}}", quote),
    ]
