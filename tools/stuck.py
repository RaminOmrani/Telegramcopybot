"""پست‌هایی که نرفته‌اند، واقعاً چه هستند؟

<b>چرا این ابزار.</b> گزارش می‌گوید «Cannot use [] as file» — یعنی ربات
خواسته رسانه را دانلود کند و هیچ فایلی نگرفته. ولی نمی‌گوید <b>چه
نوع</b> پستی بوده: استوری؟ عکس پولی؟ قرعه‌کشی؟ نوعی که Telethonِ ما
هنوز نمی‌شناسد؟ بدون این، اصلاح فقط حدس است.

این ابزار آخرین پست‌های مبدأِ یک کار را از خودِ تلگرام می‌خواند و برای
هرکدام می‌گوید: نوعِ رسانه، و اینکه کپی شده یا نه. الگو همان‌جا پیدا
می‌شود — «همه‌ی نرفته‌ها X هستند».

<b>چرا ربات باید خاموش باشد.</b> این ابزار با <b>همان اکانتی</b> وصل
می‌شود که ربات وصل است. دو برنامه که هم‌زمان با یک کلید به تلگرام
وصل شوند، ممکن است تلگرام کلید را باطل کند — یعنی اکانت از ربات
بیرون بیفتد و باید دوباره وارد شوید. پس ابزار اگر ببیند ربات روشن است،
اجرا نمی‌شود. چند ثانیه خاموشی، همان چیزی است که هر آپدیت دارد.

اجرا (به‌جای 2 شماره‌ی کار را بگذارید):
    systemctl stop telkap
    sudo -u telkap /opt/telkap/.venv/bin/python /opt/telkap/tools/stuck.py 2 --send
    systemctl start telkap

خروجی لاتینِ خالص است تا در کنسول خوانا باشد. هیچ چیزی دانلود، ارسال یا
تغییر داده نمی‌شود؛ فقط خوانده می‌شود.
"""
from __future__ import annotations

import asyncio
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# این ابزار باید از هر پوشه‌ای کار کند؛ مسیر دیتابیس نسبی است.
os.chdir(Path(__file__).resolve().parent.parent)

# چند پست آخر. برای دیدنِ الگو کافی است و آن‌قدر کم که به محدودیت نرخ
# نخورد.
LOOKBACK = 40


def bot_is_running() -> bool:
    """<b>نگهبانِ اکانت.</b> اگر نتوانیم مطمئن شویم خاموش است، روشن فرض می‌کنیم.

    خطای این طرف ارزان است (کاربر یک بار دیگر stop می‌زند)؛ خطای آن طرف
    بیرون افتادنِ اکانت است.
    """
    try:
        state = subprocess.run(
            ["systemctl", "is-active", "telkap"],
            capture_output=True, text=True, timeout=10,
        ).stdout.strip()
    except Exception:
        return True
    return state not in ("inactive", "failed", "unknown")


def describe(media) -> str:
    """نوعِ رسانه، تا جایی که برای تشخیص لازم است — بدون دانلود."""
    if media is None:
        return "-"
    kind = type(media).__name__
    inner = []
    for attr in ("photo", "document", "webpage", "story", "poll", "extended_media"):
        value = getattr(media, attr, None)
        if value is None:
            continue
        if isinstance(value, list):
            names = ",".join(type(v).__name__ for v in value)
            inner.append(f"{attr}=[{names}]")
        else:
            inner.append(f"{attr}={type(value).__name__}")
    document = getattr(media, "document", None)
    mime = getattr(document, "mime_type", None)
    if mime:
        inner.append(f"mime={mime}")
    ttl = getattr(media, "ttl_seconds", None)
    if ttl:
        inner.append(f"ttl={ttl}")
    if getattr(media, "spoiler", False):
        inner.append("spoiler")
    return kind + (" (" + " ".join(inner) + ")" if inner else "")


async def deliver(report: str) -> None:
    import aiohttp
    from dotenv import load_dotenv

    load_dotenv(Path(__file__).resolve().parent.parent / ".env")
    token = (os.getenv("BOT_TOKEN") or "").strip()
    raw = (os.getenv("ADMIN_IDS") or "").replace(",", " ").split()
    if not token or not raw:
        print("cannot send: BOT_TOKEN or ADMIN_IDS missing in .env")
        return
    form = aiohttp.FormData()
    form.add_field("chat_id", raw[0])
    form.add_field("caption", "stuck.py -- what the failing posts are")
    form.add_field(
        "document", report.encode("utf-8"),
        filename="stuck.txt", content_type="text/plain",
    )
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                f"https://api.telegram.org/bot{token}/sendDocument",
                data=form, timeout=60,
            ) as response:
                body = await response.json()
    except Exception as exc:
        print(f"cannot send: {exc}")
        return
    print("sent to telegram" if body.get("ok") else f"refused: {body.get('description')}")


async def main() -> int:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    if not args or not args[0].isdigit():
        print("usage: stuck.py <task_id> [--send]")
        return 2
    task_id = int(args[0])

    if bot_is_running() and "--i-know-the-bot-is-running" not in sys.argv:
        print("STOP. the bot is running.")
        print("this tool logs in with the SAME account as the bot. two programs")
        print("on one account at once can make telegram revoke the session, and")
        print("then the account falls out of the bot. stop it for a few seconds:")
        print()
        print("    systemctl stop telkap")
        print(f"    sudo -u telkap /opt/telkap/.venv/bin/python /opt/telkap/tools/stuck.py {task_id} --send")
        print("    systemctl start telkap")
        return 3

    from sqlalchemy import select

    from telkap.crypto import decrypt
    from telkap.db import get_session, init_db
    from telkap.models import MessageMap, RetryItem, Task, User
    from telkap.services import reader
    from telkap.services.copier import classify_media
    from telkap.services.userbot import UserbotManager

    await init_db()
    lines: list[str] = []

    def out(text: str = "") -> None:
        lines.append(text)
        print(text)

    async with get_session() as db:
        task = await db.get(Task, task_id)
        if task is None:
            print(f"no task {task_id}")
            return 2
        owner = await db.get(User, task.user_id)
        copied = {
            int(row)
            for row in (
                await db.execute(
                    select(MessageMap.src_msg_id).where(MessageMap.task_id == task_id)
                )
            ).scalars()
        }
        stuck_ids: set[int] = set()
        for item in (
            await db.execute(select(RetryItem).where(RetryItem.task_id == task_id))
        ).scalars():
            for part in str(item.src_msg_ids or "").split(","):
                if part.strip().isdigit():
                    stuck_ids.add(int(part))

    session = decrypt(owner.session_enc) if owner and owner.session_enc else None
    if not session:
        print("the task owner has no connected account; nothing to read with")
        return 2

    client = UserbotManager()._new_client(session)
    await client.connect()
    try:
        if not await client.is_user_authorized():
            print("the session is no longer valid")
            return 2

        out("=" * 72)
        out(f"task {task_id}: last {LOOKBACK} posts of the source, newest first")
        out("=" * 72)
        source = await reader.entity_for(client, task.source_id, task.source_ref or "")
        try:
            chat = await client.get_entity(source)
            out(f"source         : id={task.source_id}")
            out(f"protected      : {bool(getattr(chat, 'noforwards', False))}"
                "   (True = forward/save disabled; media must be re-uploaded)")
        except Exception as exc:
            out(f"source         : could not read details ({type(exc).__name__})")
        out(f"stuck in retry : {sorted(stuck_ids) or 'none'}")
        out()
        out("  msg_id   when(UTC)         copied  our-kind   telegram media")
        out("  " + "-" * 68)

        not_copied: dict[str, int] = {}
        async for message in client.iter_messages(source, limit=LOOKBACK):
            mark = "yes" if message.id in copied else "NO "
            if message.id in stuck_ids:
                mark = "STK"
            kind = classify_media(message)
            media = describe(getattr(message, "media", None))
            when = f"{message.date:%m-%d %H:%M}" if message.date else "?"
            group = f" album={message.grouped_id}" if message.grouped_id else ""
            out(f"  {message.id:<8} {when:<17} {mark:<7} {kind:<10} {media}{group}")
            if mark != "yes" and message.media is not None:
                key = type(message.media).__name__
                not_copied[key] = not_copied.get(key, 0) + 1

        out()
        out("media kinds among posts that were NOT copied:")
        if not not_copied:
            out("  none -- every recent post with media was copied")
        for name, count in sorted(not_copied.items(), key=lambda kv: -kv[1]):
            out(f"  {count:>3}x  {name}")
        out()
        out("legend: copied yes = reached the destination; NO = did not")
        out("        (filters/duplicates also show NO); STK = stuck in retry queue")
    finally:
        await client.disconnect()

    if "--send" in sys.argv:
        await deliver("\n".join(lines))
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
