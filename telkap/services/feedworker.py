"""انتشار خودکار مطالب فید در کانال کاربر.

<b>جای این ماژول در معماری.</b> موتور کپی (<code>copier.py</code>) با
<b>پیام تلگرام</b> کار می‌کند: رسانه، آلبوم، نظرسنجی، دکمه، ویرایش و
حذف. آیتم فید هیچ‌کدام را ندارد — فقط عنوان و متن و لینک است. پس
به‌جای ساختن پیام تلگرامیِ قلابی تا آن مسیر سنگین راه بیفتد، اینجا
یک مسیر کوتاه هست که <b>همان</b> اجزای تصمیم‌گیری را صدا می‌زند:
<code>apply_transforms</code>، <code>should_copy</code>،
<code>routing.wants</code>، سهمیه‌ی طرح، و همان
<code>message_map</code> برای تکراری‌ها.

نتیجه این است که همه‌ی تنظیماتی که کاربر برای کار تلگرامی‌اش بلد
است — امضا، فوتر، کلمات ممنوعه، مسیریابی، ساعت فعال — روی فید هم
بدون یک خط کد اضافه کار می‌کند.

<b>مهم‌ترین قاعده: اولین خواندنِ هر فید چیزی منتشر نمی‌کند.</b> فید
معمولاً ۲۰ تا ۵۰ مطلب گذشته دارد. بدون این قاعده، لحظه‌ای که کاربر
کارش را می‌سازد پنجاه پست پشت سر هم در کانالش می‌افتد و اکانتش هم
محدود می‌شود. پس بار اول فقط <b>علامت خورده</b> می‌شوند و از آن به
بعد فقط مطلبِ تازه می‌رود.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime

import aiohttp
from sqlalchemy import select

from telkap.db import get_session, log_activity
from telkap.models import MessageMap, Task
from telkap.services import cache, feeds, health, routing
from telkap.services.copier import with_own_entities
from telkap.services.feeds import FeedError, FeedItem
from telkap.services.filters import MessageFacts, content_hash, should_copy
from telkap.services.transform import apply_transforms
from telkap.services.userbot import manager

log = logging.getLogger(__name__)

INTERVAL_SECONDS = 300          # هر پنج دقیقه یک دور
MIN_CHECK_SECONDS = 600         # هر فید حداکثر هر ده دقیقه یک بار خوانده شود
MAX_PER_CHECK = 5               # سقف انتشار در یک دور، تا کانال ناگهان پر نشود
GAP_SECONDS = 8                 # فاصله بین دو انتشار پشت سر هم

# زمان آخرین خواندن هر کار، در حافظه. ماندگار نیست و لازم هم نیست:
# با ری‌استارت، بدترین اتفاق یک خواندنِ زودهنگام است و تکراری‌ها را
# message_map می‌گیرد.
_last_check: dict[int, float] = {}


def render(item: FeedItem, cfg: dict) -> str:
    """آیتم فید را به متن پست تبدیل می‌کند.

    الگو دست کاربر است چون سلیقه‌ی کانال‌ها فرق دارد: یکی فقط تیتر
    می‌خواهد، یکی تیتر و خلاصه و لینک.
    """
    template = (cfg.get("feed_template") or "").strip() or DEFAULT_TEMPLATE

    # `or` اینجا جواب نمی‌دهد: صفر یعنی «کوتاه نکن» ولی falsy است و به
    # پیش‌فرض می‌افتد — یعنی گزینه‌ی کاربر بی‌صدا نادیده گرفته می‌شد.
    raw_limit = cfg.get("feed_summary_chars")
    limit = 400 if raw_limit is None else int(raw_limit)

    summary = item.summary
    if limit > 0 and len(summary) > limit:
        # بریدن وسط کلمه زشت است؛ تا آخرین فاصله عقب می‌رویم
        cut = summary[:limit].rsplit(" ", 1)[0] or summary[:limit]
        summary = f"{cut.rstrip()}…"

    text = (
        template.replace("{title}", item.title)
        .replace("{summary}", summary)
        .replace("{link}", item.link)
    )
    # جای خالیِ پرنشده خط خالی می‌سازد؛ سه خط خالی پشت سر هم یعنی
    # همان‌ها. جمعشان می‌کنیم تا پست تمیز بماند.
    lines = [line.rstrip() for line in text.split("\n")]
    cleaned: list[str] = []
    for line in lines:
        if not line and cleaned and not cleaned[-1]:
            continue
        cleaned.append(line)
    return "\n".join(cleaned).strip()


# <b>بدون HTML.</b> کلاینت با parse_mode=None کار می‌کند — عمداً، تا
# ستاره و زیرخطِ داخل متنِ کپی‌شده به‌عنوان قالب تفسیر نشود. یعنی
# «<b>» اینجا واقعاً همان پنج نویسه در پست کاربر دیده می‌شود. قالب‌بندی
# جای دیگری است: امضا و هدر که entity هایشان نگه داشته می‌شود.
DEFAULT_TEMPLATE = "{title}\n\n{summary}\n\n{link}"


def _facts(item: FeedItem, text: str) -> MessageFacts:
    """آیتم فید را در قالبی می‌گذارد که فیلترها می‌فهمند.

    فیلترها برای پیام تلگرام نوشته شده‌اند ولی چیزی که واقعاً
    می‌خوانند متن و چند پرچم است — و همان‌ها اینجا هم معنا دارند.
    """
    return MessageFacts(text=text, media_kind="text")


async def seen_keys(task_id: int, keys) -> set[int]:
    """کدام‌یک از این مطالب قبلاً دیده شده‌اند.

    یک پرس‌وجو برای همه، نه یکی برای هرکدام: فید تا شصت مطلب دارد و
    شصت رفت‌وبرگشت به دیتابیس در هر دور، برای هر کاربر، جمع می‌شود.
    """
    keys = list(keys)
    if not keys:
        return set()
    async with get_session() as db:
        rows = await db.execute(
            select(MessageMap.src_msg_id).where(
                MessageMap.task_id == task_id,
                MessageMap.src_msg_id.in_(keys),
            )
        )
    return {row[0] for row in rows.all()}


async def _remember(task_id: int, key: int, dest: str, sent_id: int, digest: str) -> None:
    async with get_session() as db:
        db.add(
            MessageMap(
                task_id=task_id,
                src_msg_id=key,
                dst_msg_id=sent_id,
                dest_chat=dest,
                content_hash=digest,
            )
        )
        await db.commit()


async def _mark_all_seen(task_id: int, items) -> int:
    """همه‌ی مطالب فعلی را «دیده‌شده» ثبت می‌کند، بدون انتشار.

    <code>dst_msg_id=0</code> یعنی «این هرگز منتشر نشد» — که برای
    همگام‌سازی ویرایش و حذف هم بی‌ضرر است، چون پیامی در مقصد وجود
    ندارد که بخواهد به‌روز شود.
    """
    async with get_session() as db:
        for item in items:
            db.add(
                MessageMap(
                    task_id=task_id,
                    src_msg_id=item.key,
                    dst_msg_id=0,
                    dest_chat="",
                    content_hash=None,
                )
            )
        await db.commit()
    return len(items)


async def _first_run(task_id: int) -> bool:
    """آیا این کار تا حالا هیچ مطلبی از فیدش ندیده است."""
    async with get_session() as db:
        found = await db.scalar(
            select(MessageMap.id).where(MessageMap.task_id == task_id).limit(1)
        )
    return found is None


async def publish(snapshot, item: FeedItem, *, session=None) -> bool:
    """یک مطلب را در همه‌ی مقصدهای کار منتشر می‌کند.

    True یعنی دست‌کم به یک مقصد رفت.
    """
    cfg = snapshot.cfg
    rules = snapshot.rules
    raw = render(item, cfg)
    if not raw.strip():
        return False

    text = apply_transforms(raw, cfg, rules)
    facts = _facts(item, text)

    decision = should_copy(facts, cfg, rules)
    if not decision.allowed:
        await log_activity(
            user_id=snapshot.user_id, task_id=snapshot.id,
            event="skip", detail=decision.reason,
        )
        return False

    # اشتراک تمام‌شده یعنی کار نباید ادامه پیدا کند — همان قاعده‌ای که
    # برای کارهای تلگرامی هست
    if await cache.get_plan(snapshot.user_id) is None:
        return False

    client = await manager.ensure_client(snapshot.user_id)
    if client is None:
        return False

    digest = content_hash(facts)
    any_sent = False

    for spec in snapshot.targets:
        dest_cfg = {**cfg, **spec.overrides} if spec.overrides else cfg
        if spec.overrides:
            dest_text = apply_transforms(raw, dest_cfg, rules)
        else:
            dest_text = text

        if not routing.wants(dest_text, dest_cfg):
            continue

        entities = with_own_entities(dest_text, dest_cfg, [])
        try:
            sent = await client.send_message(
                spec.target,
                dest_text,
                link_preview=bool(dest_cfg.get("feed_preview", True)),
                formatting_entities=entities or None,
            )
        except Exception as exc:
            # محدودیت اکانت با تلاش دوباره بدتر می‌شود؛ بالاتر رسیدگی
            # می‌شود، مثل مسیر تلگرامی
            if health.classify(exc).fatal:
                raise
            log.warning("انتشار مطلب فید در %s ناموفق بود: %s", spec.target, exc)
            continue

        any_sent = True
        await _remember(snapshot.id, item.key, str(spec.target), sent.id, digest)

    if any_sent:
        async with get_session() as db:
            task = await db.get(Task, snapshot.id)
            if task is not None:
                task.copied_count += 1
                task.last_copy_at = datetime.now(UTC)
                await db.commit()
    return any_sent


async def check_task(snapshot, *, session: aiohttp.ClientSession | None = None) -> int:
    """یک کارِ فید را می‌خواند و مطالب تازه‌اش را منتشر می‌کند.

    تعداد منتشرشده را برمی‌گرداند؛ صفر یعنی چیز تازه‌ای نبود.
    """
    try:
        items = await feeds.fetch(snapshot.source_ref, session=session)
    except FeedError as exc:
        await _note_error(snapshot.id, str(exc))
        return 0

    # بار اول پیش از هر کار دیگری سنجیده می‌شود: فیدِ پنجاه‌مطلبی نباید
    # لحظه‌ی ساختن کار، پنجاه پست در کانال بریزد
    if await _first_run(snapshot.id):
        marked = await _mark_all_seen(snapshot.id, items)
        log.info("فید کار %s برای اولین بار خوانده شد؛ %s مطلب علامت خورد",
                 snapshot.id, marked)
        return 0

    already = await seen_keys(snapshot.id, (item.key for item in items))
    fresh = [item for item in items if item.key not in already]
    if not fresh:
        return 0

    # فید تازه‌ترین را اول می‌دهد ولی انتشار باید از قدیمی به تازه باشد،
    # وگرنه ترتیب کانال برعکس می‌شود
    published = 0
    for item in reversed(fresh[:MAX_PER_CHECK]):
        if await publish(snapshot, item, session=session):
            published += 1
            await asyncio.sleep(GAP_SECONDS)
    return published


async def _note_error(task_id: int, message: str) -> None:
    async with get_session() as db:
        task = await db.get(Task, task_id)
        if task is not None:
            task.last_error = message[:400]
            await db.commit()


async def _due_tasks() -> list[int]:
    """کارهای فیدِ فعالی که وقت خواندنشان رسیده."""
    now = asyncio.get_running_loop().time()
    async with get_session() as db:
        rows = await db.execute(
            select(Task.id).where(
                Task.source_kind == Task.SOURCE_RSS,
                Task.enabled.is_(True),
            )
        )
    return [
        task_id
        for (task_id,) in rows.all()
        if now - _last_check.get(task_id, 0.0) >= MIN_CHECK_SECONDS
    ]


async def run_once() -> int:
    """یک دور کامل روی همه‌ی فیدها. تعداد پست‌های منتشرشده."""
    due = await _due_tasks()
    if not due:
        return 0

    total = 0
    loop = asyncio.get_running_loop()
    async with aiohttp.ClientSession() as session:
        for task_id in due:
            snapshot = await cache.get_task(task_id)
            if snapshot is None or not snapshot.enabled:
                continue
            _last_check[task_id] = loop.time()
            try:
                total += await check_task(snapshot, session=session)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                # یک فید خراب نباید جلوی بقیه را بگیرد
                log.exception("خواندن فید کار %s شکست خورد", task_id)
                await _note_error(task_id, str(exc))
    return total


async def run_forever() -> None:
    while True:
        try:
            await asyncio.sleep(INTERVAL_SECONDS)
            await run_once()
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("چرخه‌ی خواندن فیدها با خطا مواجه شد")
