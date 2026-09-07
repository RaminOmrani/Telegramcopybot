"""چرا کارها کپی نمی‌کنند — گزارشِ خام از روی دیتابیس.

<b>چرا جدا از «بررسی سلامت» داخل ربات.</b> آن یکی از تلگرام می‌پرسد و
برای مشتری نوشته شده. این یکی برای وقتی است که می‌خواهیم <b>خودمان</b>
از روی سرور ببینیم چه خبر است — بدون اینکه به تلگرام دست بزنیم، و
بدون اینکه به بالا آمدن ربات نیاز باشد.

خروجی عمداً لاتینِ خالص است: کنسول VNC هیچ حرف فارسی‌ای را نشان
نمی‌دهد و همه را ♦ می‌کند.

اجرا:
    sudo -u telkap /opt/telkap/.venv/bin/python tools/why.py

<b>و اگر خروجی بلند باشد و در کنسول بالا برود</b> — که با چند کار
همیشه می‌شود — همان گزارش را به‌صورت فایل در تلگرام برای ادمین
می‌فرستد. آنجا هم می‌ماند و هم می‌شود کپی‌اش کرد:
    sudo -u telkap /opt/telkap/.venv/bin/python tools/why.py --send

و اگر صف تلاش مجدد پر شده باشد و بخواهیم از صفر شروع کنیم:
    sudo -u telkap /opt/telkap/.venv/bin/python tools/why.py --clear-retries
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


async def deliver(report: str) -> None:
    """گزارش را در تلگرام برای ادمین می‌فرستد.

    <b>چرا فایل و نه پیام.</b> پیام تلگرام سقف ۴۰۹۶ نویسه دارد و
    گزارشِ ده کار از آن رد می‌شود — آن‌وقت یا بریده می‌رود یا اصلاً
    نمی‌رود. فایل هم بریده نمی‌شود و هم بعداً پیدا کردنش راحت‌تر است.
    """
    import os

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
    form.add_field("caption", "why.py report")
    form.add_field(
        "document", report.encode("utf-8"),
        filename="why.txt", content_type="text/plain",
    )
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                f"https://api.telegram.org/bot{token}/sendDocument",
                data=form, timeout=30,
            ) as response:
                body = await response.json()
    except Exception as exc:
        print(f"cannot send: {exc}")
        return

    if body.get("ok"):
        print(f"sent to telegram chat {raw[0]} as why.txt")
    else:
        print(f"telegram refused: {body.get('description')}")


async def main() -> int:
    from sqlalchemy import delete, func, select

    from telkap.db import get_session, init_db
    from telkap.models import DailyStat, RetryItem, Task, User

    await init_db()
    clear = "--clear-retries" in sys.argv
    send = "--send" in sys.argv

    lines: list[str] = []

    def out(text: str = "") -> None:
        lines.append(text)
        print(text)

    async with get_session() as db:
        tasks = list((await db.execute(select(Task).order_by(Task.id))).scalars())
        people = {
            person.id: person
            for person in (await db.execute(select(User))).scalars()
        }
        queued = dict(
            (
                await db.execute(
                    select(RetryItem.task_id, func.count(RetryItem.id)).group_by(
                        RetryItem.task_id
                    )
                )
            ).all()
        )
        failed = dict(
            (
                await db.execute(
                    select(DailyStat.task_id, func.sum(DailyStat.failed)).group_by(
                        DailyStat.task_id
                    )
                )
            ).all()
        )
        total_queue = int(
            await db.scalar(select(func.count(RetryItem.id))) or 0
        )

    out("=" * 68)
    out(f"tasks={len(tasks)}  retry_queue_total={total_queue}")
    out("=" * 68)

    for task in tasks:
        person = people.get(task.user_id)
        state = getattr(person, "account_state", "") or "ok"
        linked = "yes" if person is not None and person.session_enc else "NO"
        waiting = int(queued.get(task.id, 0) or 0)
        lost = int(failed.get(task.id, 0) or 0)

        flag = "ON " if task.enabled else "OFF"
        out()
        out(f"[{flag}] task={task.id} user={task.user_id} account={state} session={linked}")
        out(f"       src={task.source_ref!r} src_id={task.source_id}")
        out(f"       dst={task.dest_ref!r} dst_id={task.dest_id}")
        out(f"       copied={task.copied_count} skipped={task.skipped_count} "
            f"failed_total={lost} retry_queue={waiting}")
        out(f"       last_copy={task.last_copy_at}")
        if task.last_error:
            # خطا ممکن است فارسی باشد و کنسول نشانش ندهد؛ ولی در فایلی
            # که به تلگرام می‌رود کامل و خوانا می‌آید
            out(f"       last_error: {task.last_error}")

    out()
    out("-" * 68)
    out("how to read this:")
    out("  session=NO        -> user account not connected; nothing can run")
    out("  account=banned    -> telegram limited the account; sending is stopped")
    out("  OFF + last_error  -> the task was paused, the reason is on that line")
    out("  retry_queue high  -> sends keep failing (usually: no admin in destination)")
    out("-" * 68)

    if clear and total_queue:
        async with get_session() as db:
            await db.execute(delete(RetryItem))
            await db.commit()
        out(f"cleared {total_queue} items from the retry queue.")
    elif clear:
        out("retry queue was already empty.")

    if send:
        await deliver("\n".join(lines))

    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
