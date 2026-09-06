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
from telkap.models import Destination, Rule, Task, User
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

    # <b>keep_blank_values حیاتی است.</b> بدون آن، `parse_qsl` هر
    # فیلدی را که مقدارش خالی است بی‌صدا دور می‌ریزد — مثلاً
    # «start_param=». ولی تلگرام امضایش را روی <b>همه‌ی</b> فیلدهایی
    # که فرستاده حساب کرده، از جمله خالی‌ها. نتیجه‌اش رشته‌ای کوتاه‌تر
    # از چیزی است که تلگرام امضا کرده، و امضا هیچ‌وقت نمی‌خواند: اپ
    # برای همیشه می‌گفت «شناسایی نشدید».
    #
    # strict_parsing هم می‌ماند تا رشته‌ی خراب به دیکشنری نصفه تبدیل نشود.
    try:
        pairs = dict(parse_qsl(init_data, strict_parsing=True, keep_blank_values=True))
    except ValueError:
        return None

    given = pairs.pop("hash", "")
    if not given:
        return None

    secret = hmac.new(b"WebAppData", token.encode(), hashlib.sha256).digest()

    # <b>«signature» — و چرا هر دو حالت امتحان می‌شود.</b>
    #
    # تلگرام بعداً فیلد signature را اضافه کرد: یک امضای Ed25519 تا
    # سرویس‌های ثالث بتوانند بدون داشتنِ توکنِ ربات هم داده را بسنجند.
    # مستندات می‌گویند آن فیلد باید مثل hash از رشته‌ی امضا بیرون
    # بماند، و پیاده‌سازی‌های مرجع هم همین کار را می‌کنند.
    #
    # ولی این جزئیات یک بار عوض شده و ممکن است دوباره عوض شود، و
    # هزینه‌ی اشتباه بودنش این است که هیچ‌کس نمی‌تواند وارد شود. پس
    # هر دو رشته امتحان می‌شوند. <b>این ضعف امنیتی نیست</b>: هر دو
    # نامزد به همان HMAC با همان کلید نیاز دارند، و کسی که نمی‌تواند
    # یکی را جعل کند، دیگری را هم نمی‌تواند.
    without_signature = {key: value for key, value in pairs.items() if key != "signature"}
    for fields in (without_signature, pairs):
        check_string = "\n".join(f"{key}={fields[key]}" for key in sorted(fields))
        mine = hmac.new(secret, check_string.encode(), hashlib.sha256).hexdigest()
        # compare_digest نه ==، تا مقایسه به‌ازای هر کاراکترِ درست کندتر
        # نشود و امضا را نشود حرف‌به‌حرف حدس زد
        if hmac.compare_digest(mine, given):
            break
    else:
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


def diagnose(init_data: str, token: str, *, now: float | None = None) -> str:
    """چرا احراز هویت شکست خورد — به زبان آدمیزاد.

    <b>چرا این لازم شد.</b> «شناسایی نشدید» برای کاربر یعنی هیچ، و
    برای ما هم یعنی هیچ: سه علتِ کاملاً متفاوت یک پیام می‌دهند و
    پیدا کردنشان به حدس زدن می‌گذرد. یک بار حدس زدیم و اشتباه بود.

    <b>و چرا افشای اطلاعات نیست.</b> هرچه اینجا گفته می‌شود درباره‌ی
    داده‌ای است که خودِ درخواست‌دهنده فرستاده. توکن، کلید و امضای
    درست هیچ‌جا بیرون نمی‌رود؛ فقط می‌گوید سهمِ خودت کجایش ایراد
    دارد.
    """
    if not token:
        return "توکن ربات روی سرور تنظیم نشده است."
    if not init_data:
        return (
            "تلگرام هویتی نفرستاد. یعنی این صفحه از داخل تلگرام باز نشده،"
            " یا از راهی باز شده که مینی‌اپ نیست."
        )
    try:
        pairs = dict(parse_qsl(init_data, strict_parsing=True, keep_blank_values=True))
    except ValueError:
        return "داده‌ی هویت خراب بود."
    if "hash" not in pairs:
        return "داده‌ی هویت امضا نداشت."
    if check(init_data, token, now=now) is not None:
        return ""

    # امضا غلط است یا کهنه. این دو را از هم جدا می‌کنیم چون درمانشان
    # کاملاً فرق دارد: یکی توکنِ عوض‌شده است، دیگری فقط بازکردن دوباره.
    try:
        issued = int(pairs.get("auth_date", "0"))
    except ValueError:
        issued = 0
    age = (now or time.time()) - issued
    if issued > 0 and age > MAX_AGE_SECONDS:
        return "این صفحه از دیروز باز مانده؛ ببندید و دوباره بازش کنید."
    return (
        "امضای تلگرام با توکن این سرور نمی‌خواند."
        " معمولاً یعنی توکن ربات عوض شده و در .env به‌روز نشده،"
        " یا این صفحه با رباتِ دیگری باز شده است."
    )


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


async def ping(request: web.Request) -> web.Response:
    """آیا شناسایی شدم، و اگر نه چرا.

    هویت نمی‌خواهد — چون دقیقاً برای وقتی است که هویت کار نمی‌کند.
    """
    why = diagnose(_init_data(request), get_settings().bot_token)
    if why:
        log.warning("مینی‌اپ شناسایی نکرد: %s", why)
    return _yes({"ok": not why, "why": why})


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
    "skip_duplicates", "skip_bots", "skip_replies", "skip_cross_duplicates",
    "sync_edits", "sync_deletes", "copy_buttons", "caption_only",
    "approval", "hold_outside_hours",
    "watermark_enabled", "rewrite_configs", "rewrite_files",
    "ai_summarize", "ai_rewrite", "ai_translate", "feed_preview",
)
TEXT_SETTINGS = {
    "header": 1024, "footer": 1024, "signature": 256,
    "watermark_text": 64, "config_tag": 64, "file_rename": 128,
    "feed_template": 1024,
}
INT_SETTINGS = {
    "delay_seconds": (0, 86_400),
    "max_per_hour": (0, 10_000),
    "min_length": (0, 4096),
    "max_length": (0, 4096),
    "order_grace_seconds": (5, 86_400),
    "skip_media_over_mb": (0, 4096),
    "active_from_hour": (0, 23),
    "active_to_hour": (0, 23),
    "min_gap_seconds": (0, 86_400),
    "engagement_wait_minutes": (0, 1_440),
    "min_views": (0, 100_000_000),
    "min_reactions": (0, 1_000_000),
    "min_forwards": (0, 1_000_000),
    "similarity_percent": (50, 100),
    "watermark_opacity": (0, 100),
    "watermark_size": (1, 20),
    "ai_sentences": (1, 10),
    "feed_summary_chars": (0, 4096),
}
# <b>چرا این چهارتا اینجا دوباره نوشته شده‌اند.</b> منبع اصلی‌شان
# جای دیگری است (watermark.POSITIONS و aiskills.STYLES/LANGUAGES)، ولی
# آوردنِ آن ماژول‌ها به لایه‌ی وب یعنی وارد کردن PIL و کل زنجیره‌ی هوش
# مصنوعی فقط برای خواندن چند کلید. پس اینجا نوشته شده‌اند و یک تست
# مراقب است که با منبع اصلی یکی بمانند.
CHOICE_SETTINGS = {
    "mode": ("copy", "forward"),
    "order_mode": ("strict", "fast", "grace"),
    "ad_sensitivity": ("low", "medium", "high"),
    "duplicate_mode": ("exact", "normalized", "fuzzy"),
    "watermark_kind": ("text", "logo"),
    "watermark_position": (
        "top-left", "top-right", "bottom-left", "bottom-right", "center",
    ),
    "ai_style": ("same", "formal", "friendly", "short", "marketing"),
    "ai_language": ("fa", "en", "ar", "tr", "ru"),
}
# فهرست‌ها. «allowed_media» از مجموعه‌ی بسته‌ی نوع رسانه‌ها می‌آید؛ دو
# تای دیگر کلمه‌های دلخواه کاربرند و فقط تعداد و طولشان مهار می‌شود.
LIST_SETTINGS = {"allowed_media", "route_words", "route_skip"}
MAX_LIST_ITEMS = 60
MAX_LIST_ITEM_CHARS = 64


def _clean_list(key: str, value: object) -> list[str] | None:
    """یک فهرست تمیز، یا None اگر ورودی اصلاً فهرست نبود."""
    if not isinstance(value, list):
        return None
    from telkap.services.defaults import MEDIA_KINDS

    items: list[str] = []
    for raw in value[:MAX_LIST_ITEMS]:
        item = str(raw or "").strip()[:MAX_LIST_ITEM_CHARS]
        if not item or item in items:
            continue
        if key == "allowed_media" and item not in MEDIA_KINDS:
            continue
        items.append(item)
    return items


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
        elif key in LIST_SETTINGS:
            items = _clean_list(key, value)
            if items is None:
                problems.append(key)
            else:
                cfg[key] = items
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
        "source_kind": task.source_kind,
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
                *BOOL_SETTINGS, *TEXT_SETTINGS, *INT_SETTINGS,
                *CHOICE_SETTINGS, *LIST_SETTINGS,
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

    kind = str(posted.get("source_kind") or Task.SOURCE_TELEGRAM)
    if kind not in (Task.SOURCE_TELEGRAM, Task.SOURCE_RSS):
        return _no("نوع مبدا درست نیست", status=400)

    source_title = str(posted.get("source_title") or "")[:160]
    if kind == Task.SOURCE_RSS:
        # <b>فید همین حالا خوانده می‌شود، نه بعداً.</b> اگر آدرس غلط
        # باشد کاربر باید همین‌جا بفهمد — نه ساعت‌ها بعد وقتی می‌بیند
        # هیچ چیزی نیامده و نمی‌داند چرا. خودِ fetch جلوی آدرسِ شبکه‌ی
        # داخلی را هم می‌گیرد.
        from telkap.services import feeds
        from telkap.services.feeds import FeedError

        try:
            items = await feeds.fetch(source)
        except FeedError as exc:
            return _no(str(exc), status=400)
        except Exception:
            log.exception("خواندن فید «%s» ناموفق بود", source)
            return _no("این فید خوانده نشد", status=400)
        source_title = source_title or feeds.clean_html(items[0].title)[:60]

    async with get_session() as db:
        task = Task(
            user_id=user_id,
            title=str(posted.get("title") or "")[:128] or source_title[:128],
            source_kind=kind,
            source_ref=source,
            source_title=source_title,
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
        # مبدأ فید یک نشانی اینترنتی است، نه چتِ تلگرام؛ حل کردنش
        # فقط یک تماس بی‌فایده با تلگرام است
        source_id = (
            await manager.resolve_chat_id(client, source)
            if kind == Task.SOURCE_TELEGRAM
            else None
        )
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


# ------------------------------------------------------------------ قواعد
#
# <b>چرا قواعد در اپ لازم بودند.</b> این‌ها همان چیزی هستند که کاربر
# هر روز عوض می‌کند — نام کانال رقیب را بردار، این کلمه را جایگزین
# کن، پستِ دارای این عبارت اصلاً نرود. تا امروز فقط در ربات بودند و
# هر تغییرشان یعنی چند صفحه دکمه.
RULE_KINDS = ("replace", "regex", "block", "allow")
MAX_RULES = 100


async def rules_list(request: web.Request) -> web.Response:
    user_id = await _who(request)
    if user_id is None:
        return _no()

    task = await _own_task(user_id, int(request.match_info["id"]))
    if task is None:
        return _no("این کار پیدا نشد", status=404)

    async with get_session() as db:
        rows = list(
            (
                await db.execute(
                    select(Rule).where(Rule.task_id == task.id).order_by(Rule.id)
                )
            ).scalars()
        )

    return _yes({
        "rules": [
            {
                "id": row.id,
                "kind": row.kind,
                "pattern": row.pattern,
                "replacement": row.replacement or "",
                "enabled": bool(row.enabled),
            }
            for row in rows
        ]
    })


async def rule_add(request: web.Request) -> web.Response:
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

    kind = str(posted.get("kind", ""))
    if kind not in RULE_KINDS:
        return _no("نوع قاعده درست نیست", status=400)

    pattern = str(posted.get("pattern") or "").strip()[:512]
    if not pattern:
        return _no("الگو خالی است", status=400)

    # <b>الگوی regex پیش از ذخیره اجرا می‌شود، نه سرِ اولین پست.</b>
    # یک الگوی خراب که ذخیره شود، هر بار روی هر پست خطا می‌دهد و
    # کار را بی‌صدا از کار می‌اندازد. اینجا همان لحظه گفته می‌شود.
    if kind == "regex":
        import re

        try:
            re.compile(pattern)
        except re.error as exc:
            return _no(f"الگوی regex درست نیست: {exc}", status=400)

    async with get_session() as db:
        count = await db.scalar(
            select(func.count(Rule.id)).where(Rule.task_id == task.id)
        )
        if (count or 0) >= MAX_RULES:
            return _no(f"بیشتر از {MAX_RULES} قاعده برای یک کار نمی‌شود", status=400)
        row = Rule(
            task_id=task.id,
            kind=kind,
            pattern=pattern,
            replacement=str(posted.get("replacement") or "")[:512],
        )
        db.add(row)
        await db.commit()
        await db.refresh(row)
        rule_id = row.id

    await _touch(user_id, task.id)
    return _yes({"ok": True, "id": rule_id})


async def rule_delete(request: web.Request) -> web.Response:
    user_id = await _who(request)
    if user_id is None:
        return _no()

    task = await _own_task(user_id, int(request.match_info["id"]))
    if task is None:
        return _no("این کار پیدا نشد", status=404)

    async with get_session() as db:
        row = await db.get(Rule, int(request.match_info["rule"]))
        # قاعده‌ای که مالِ این کار نیست، انگار وجود ندارد
        if row is None or row.task_id != task.id:
            return _no("این قاعده پیدا نشد", status=404)
        await db.delete(row)
        await db.commit()

    await _touch(user_id, task.id)
    return _yes({"ok": True})


# ------------------------------------------------------------------ مقصدها
async def dest_add(request: web.Request) -> web.Response:
    """مقصد دوم به بعد. سقفش همان سقفِ طرح است."""
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

    ref = str((posted or {}).get("ref") or "").strip()[:128]
    if not ref:
        return _no("کانالی انتخاب نشده", status=400)

    plan = await subscription.active_plan_for(user_id)
    if plan is None:
        return _no("اشتراک فعالی ندارید", status=400)

    async with get_session() as db:
        extras = list(
            (
                await db.execute(select(Destination).where(Destination.task_id == task.id))
            ).scalars()
        )

    # مقصد اصلی هم یکی از سقف است
    if len(extras) + 1 >= plan.max_destinations:
        return _no(
            f"در طرح «{plan.title}» هر کار تا {plan.max_destinations} مقصد می‌تواند داشته باشد",
            status=400,
        )

    from telkap.services.userbot import manager

    client = await manager.ensure_client(user_id)
    if client is None:
        return _no("اکانت کاربری وصل نیست", status=400)

    entity = await manager.resolve_entity(client, ref)
    if entity is None:
        return _no("کانال پیدا نشد یا اکانت شما به آن دسترسی ندارد", status=400)
    if not chats._usable_as_destination(entity):
        return _no("در این کانال اجازه‌ی ارسال ندارید", status=400)

    chat_id = await manager.resolve_chat_id(client, ref)
    title = (getattr(entity, "title", None) or ref)[:160]

    # همان بررسی‌های تکراری که ربات هم می‌کند — دو مقصدِ یکسان یعنی
    # هر پست دو بار در همان کانال
    if str(chat_id) == str(task.dest_id) or ref == task.dest_ref:
        return _no("این همان کانال مقصد اصلی است", status=400)
    for row in extras:
        if str(row.chat_id) == str(chat_id) or row.ref == ref:
            return _no("این کانال قبلاً اضافه شده است", status=400)

    async with get_session() as db:
        db.add(Destination(task_id=task.id, chat_id=chat_id, ref=ref, title=title))
        await db.commit()

    await _touch(user_id, task.id)
    await log_activity(user_id=user_id, task_id=task.id, event="dest_add", detail=ref)
    return _yes({"ok": True, "title": title})


async def dest_delete(request: web.Request) -> web.Response:
    user_id = await _who(request)
    if user_id is None:
        return _no()

    task = await _own_task(user_id, int(request.match_info["id"]))
    if task is None:
        return _no("این کار پیدا نشد", status=404)

    async with get_session() as db:
        row = await db.get(Destination, int(request.match_info["dest"]))
        if row is None or row.task_id != task.id:
            return _no("این مقصد پیدا نشد", status=404)
        await db.delete(row)
        await db.commit()

    await _touch(user_id, task.id)
    return _yes({"ok": True})


# -------------------------------------------------------- کپی تنظیمات
async def task_clone(request: web.Request) -> web.Response:
    """تنظیمات و قواعدِ یک کار روی کار دیگر می‌نشیند.

    مبدأ و مقصد دست نمی‌خورند — آن‌ها هویتِ کارند، نه تنظیماتش.
    """
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

    source_id = int((posted or {}).get("from") or 0)
    if source_id == task.id:
        return _no("مبدأ و مقصدِ کپی یکی است", status=400)
    other = await _own_task(user_id, source_id)
    if other is None:
        return _no("آن کار پیدا نشد", status=404)

    async with get_session() as db:
        rows = list(
            (
                await db.execute(select(Rule).where(Rule.task_id == other.id))
            ).scalars()
        )
        target = await db.get(Task, task.id)
        target.settings = dict(other.settings or {})
        # قواعدِ قبلی می‌روند، وگرنه «کپی» می‌شد «ادغام» و کسی
        # نمی‌فهمید کدام قاعده از کجا آمده
        for old in (
            await db.execute(select(Rule).where(Rule.task_id == task.id))
        ).scalars():
            await db.delete(old)
        for row in rows:
            db.add(
                Rule(
                    task_id=task.id,
                    kind=row.kind,
                    pattern=row.pattern,
                    replacement=row.replacement,
                    enabled=row.enabled,
                )
            )
        await db.commit()

    await _touch(user_id, task.id)
    await log_activity(
        user_id=user_id, task_id=task.id, event="clone", detail=f"از کار {other.id}"
    )
    return _yes({"ok": True, "rules": len(rows)})


# ------------------------------------------------------------ لوگوی واترمارک
#
# <b>تنها مسیری که فایل می‌گیرد — و تنها جایی که ورودی باینری است.</b>
# اینجا هیچ‌چیز از حرفِ فرستنده باور نمی‌شود: نه نامِ فایل، نه
# Content-Type، نه پسوند. مسیرِ ذخیره از آیدی کار ساخته می‌شود (نه از
# نام فرستاده‌شده، وگرنه «../../» می‌شد یک راهِ نوشتن روی هر فایلی) و
# محتوا باید واقعاً با Pillow باز شود.
MAX_LOGO_BYTES = 3 * 1024 * 1024
MAX_LOGO_SIDE = 2000


async def logo_upload(request: web.Request) -> web.Response:
    user_id = await _who(request)
    if user_id is None:
        return _no()

    task = await _own_task(user_id, int(request.match_info["id"]))
    if task is None:
        return _no("این کار پیدا نشد", status=404)

    if request.content_length and request.content_length > MAX_LOGO_BYTES + 4096:
        return _no("فایل بزرگ‌تر از ۳ مگابایت است", status=400)

    try:
        posted = await request.post()
    except Exception:
        return _no("فایل درست نرسید", status=400)

    field = posted.get("logo")
    blob = getattr(field, "file", None)
    if blob is None:
        return _no("فایلی انتخاب نشده", status=400)

    # سقف را حین خواندن هم نگه می‌داریم؛ Content-Length حرفِ فرستنده است
    raw = blob.read(MAX_LOGO_BYTES + 1)
    if len(raw) > MAX_LOGO_BYTES:
        return _no("فایل بزرگ‌تر از ۳ مگابایت است", status=400)
    if not raw:
        return _no("فایل خالی بود", status=400)

    import io

    from PIL import Image

    try:
        image = Image.open(io.BytesIO(raw))
        image.verify()                      # واقعاً تصویر است؟
        image = Image.open(io.BytesIO(raw))  # verify شیء را می‌بندد
        image = image.convert("RGBA")
    except Exception:
        return _no("این فایل تصویر نبود", status=400)

    image.thumbnail((MAX_LOGO_SIDE, MAX_LOGO_SIDE))

    logo_dir = get_settings().download_dir / "logos"
    logo_dir.mkdir(parents=True, exist_ok=True)
    # نام از آیدی کار می‌آید، نه از فرستنده — همان‌جایی که ربات هم
    # می‌گذاردش، تا هر دو راه یک فایل را ببینند
    target = logo_dir / f"task-{task.id}.png"
    try:
        image.save(target, "PNG")
    except Exception:
        log.exception("ذخیره‌ی لوگوی کار %s ناموفق بود", task.id)
        return _no("ذخیره‌ی فایل ناموفق بود", status=500)

    from telkap.services.defaults import merged_settings

    async with get_session() as db:
        row = await db.get(Task, task.id)
        cfg = merged_settings(row.settings)
        cfg["watermark_logo"] = str(target)
        cfg["watermark_kind"] = "logo"
        cfg["watermark_enabled"] = True
        row.settings = cfg
        await db.commit()

    await _touch(user_id, task.id)
    await log_activity(user_id=user_id, task_id=task.id, event="watermark", detail="لوگو از مینی‌اپ")
    return _yes({"ok": True, "width": image.width, "height": image.height})


async def _touch(user_id: int, task_id: int) -> None:
    """کش را دور بریز و هندلرها را دوباره بساز.

    بدون این، تغییر ذخیره می‌شود ولی تا ری‌استارت بعدی روی پست‌ها
    اثری ندارد — و کاربر فکر می‌کند تنظیمش کار نمی‌کند.
    """
    from telkap.services import cache
    from telkap.services.userbot import manager

    cache.invalidate_task(task_id)
    await manager.reload_user(user_id)


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


async def health(request: web.Request) -> web.Response:
    """کدام کار کار می‌کند، کدام نه، و چرا."""
    user_id = await _who(request)
    if user_id is None:
        return _no()

    from telkap.services import checkup

    report = await checkup.check_user(user_id)
    return _yes({
        "account": report.account,
        "fixes": report.fixes,
        "live": report.live,
        "healthy": report.healthy,
        "broken": report.broken,
        "tasks": [
            {
                "id": item.task_id,
                "title": item.title,
                "enabled": item.enabled,
                "state": item.state,
                "problems": item.problems,
                "fixes": item.fixes,
                "copied": item.copied,
                "last_copy": item.last_copy.isoformat() if item.last_copy else None,
            }
            for item in report.tasks
        ],
    })


def routes() -> list:
    return [
        web.get(f"{API_PREFIX}/ping", ping),
        web.get(f"{API_PREFIX}/health", health),
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
        web.get(f"{API_PREFIX}/tasks/{{id}}/rules", rules_list),
        web.post(f"{API_PREFIX}/tasks/{{id}}/rules", rule_add),
        web.post(f"{API_PREFIX}/tasks/{{id}}/rules/{{rule}}/delete", rule_delete),
        web.post(f"{API_PREFIX}/tasks/{{id}}/dests", dest_add),
        web.post(f"{API_PREFIX}/tasks/{{id}}/dests/{{dest}}/delete", dest_delete),
        web.post(f"{API_PREFIX}/tasks/{{id}}/clone", task_clone),
        web.post(f"{API_PREFIX}/tasks/{{id}}/logo", logo_upload),
        web.get(f"{API_PREFIX}/quote/{{code}}", quote),
    ]
