"""وضعیتِ «حالت ساده» — کجا ایستاده و چه چیزی جا مانده.

<b>چرا این ابزار.</b> حالت ساده چند تکه دارد که هرکدام جدا می‌توانند
نباشند: اکانت سرویس، اجاره‌ی مبدأ، ادمین بودنِ ربات در مقصد، و خودِ
پویشگر. اگر پستی نیاید، از بیرون همه‌ی این‌ها یک‌شکل‌اند — «کار
نمی‌کند» — و تشخیص می‌شود حدس زدن.

<b>و این دقیقاً همان تله‌ای است که یک بار افتادیم:</b> شش تستِ سبز
داشتیم و مسیر واقعی سرِ اولین پست می‌شکست. پس این یکی از <b>دیتابیس
و خودِ تلگرام</b> می‌پرسد، نه از فرض‌ها.

اجرا:
    sudo -u telkap /opt/telkap/.venv/bin/python tools/simplecheck.py

و برای فرستادنِ گزارش در تلگرام:
    sudo -u telkap /opt/telkap/.venv/bin/python tools/simplecheck.py --send

خروجی عمداً لاتینِ خالص است: کنسول VNC حرف فارسی را نشان نمی‌دهد.
"""
from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# این ابزار باید از هر پوشه‌ای کار کند؛ مسیر دیتابیس نسبی است.
os.chdir(Path(__file__).resolve().parent.parent)


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
    form.add_field("caption", "simplecheck.py report")
    form.add_field(
        "document", report.encode("utf-8"),
        filename="simplecheck.txt", content_type="text/plain",
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
    print("sent to telegram" if body.get("ok") else f"refused: {body.get('description')}")


async def main() -> int:
    from sqlalchemy import select

    from telkap.db import get_session, init_db
    from telkap.models import MessageMap, ServiceAccount, SourceLease, Task

    await init_db()
    lines: list[str] = []

    def out(text: str = "") -> None:
        lines.append(text)
        print(text)

    out("=" * 68)
    out("simple mode: where it stands")
    out("=" * 68)

    # ---------------------------------------------------- اکانت‌های سرویس
    async with get_session() as db:
        accounts = list(
            (await db.execute(select(ServiceAccount).order_by(ServiceAccount.id)))
            .scalars()
        )
        tasks = list(
            (await db.execute(select(Task).where(Task.mode == Task.MODE_SIMPLE)))
            .scalars()
        )
        leases = {
            lease.source_id: lease
            for lease in (await db.execute(select(SourceLease))).scalars()
        }

    out()
    out(f"service accounts: {len(accounts)}")
    if not accounts:
        out("  NONE. simple mode cannot run at all.")
        out("  fix: in the bot, /pool -> add account (scan the QR).")
    for account in accounts:
        flag = "ok " if (account.enabled and account.state == "ok") else "OFF"
        linked = "yes" if account.session_enc else "NO"
        out(f"  [{flag}] id={account.id} label={account.label!r} "
            f"state={account.state} session={linked} sources={account.sources}")
        if account.note:
            out(f"        note: {account.note}")

    # -------------------------------------------------- کارهای حالت ساده
    out()
    out(f"simple tasks: {len(tasks)}")
    if not tasks and accounts:
        out("  none yet. make one in the bot: new task -> without connecting")

    for task in tasks:
        flag = "ON " if task.enabled else "OFF"
        lease = leases.get(task.source_id or 0)
        out()
        out(f"  [{flag}] task={task.id} user={task.user_id}")
        out(f"        src={task.source_ref!r} src_id={task.source_id}")
        out(f"        dst={task.dest_ref!r} dst_id={task.dest_id}")
        out(f"        copied={task.copied_count} last_copy={task.last_copy_at}")
        if task.last_error:
            out(f"        last_error: {task.last_error}")

        if lease is None:
            out("        lease: NONE -> the poller has not claimed this source yet")
            out("               (normal for the first minute; otherwise no account)")
        else:
            out(f"        lease: account={lease.account_id} seen_msg_id={lease.seen_msg_id}")
            if not lease.seen_msg_id:
                out("               seen=0 -> first sweep not finished yet")

        async with get_session() as db:
            copies = len(list(
                (await db.execute(
                    select(MessageMap.id).where(MessageMap.task_id == task.id).limit(5)
                )).scalars()
            ))
        out(f"        message_map rows: {copies}")

        # <b>«post arrived truncated» comes from here.</b> the source
        # channel publishes a headline and finishes the post seconds
        # later. we look once a minute, so sometimes we grab the half
        # version -- and only edit sync brings it back up to date.
        from telkap.services.defaults import merged_settings

        cfg = merged_settings(task.settings)
        on = "yes" if cfg.get("sync_edits") else "NO -> half-written posts stay half"
        out(f"        edit sync: {on}")

    # ------------------------------ آیا ربات واقعاً در مقصد اجازه دارد
    if tasks:
        out()
        out("asking telegram whether the bot can post in each destination:")
        await _check_destinations(tasks, out)

    out()
    out("-" * 68)
    out("how to read this:")
    out("  no service account   -> nothing runs; add one with /pool")
    out("  lease NONE           -> poller never reached this source")
    out("  seen_msg_id 0        -> first visit done, nothing sent (by design)")
    out("  bot cannot post      -> the customer must re-add the bot as admin")
    out("  edit sync NO         -> a post edited after we copied it stays stale")
    out("-" * 68)

    if "--send" in sys.argv:
        await deliver("\n".join(lines))
    return 0


async def _check_destinations(tasks, out) -> None:
    """از خودِ تلگرام می‌پرسد، نه از دیتابیس.

    ادمین بودنِ ربات چیزی است که مشتری هر لحظه می‌تواند پسش بگیرد —
    و آن‌وقت کار بی‌صدا می‌ایستد. تنها مرجعِ درست، خودِ تلگرام است.
    """
    import aiohttp
    from dotenv import load_dotenv

    load_dotenv(Path(__file__).resolve().parent.parent / ".env")
    token = (os.getenv("BOT_TOKEN") or "").strip()
    if not token:
        out("  cannot check: BOT_TOKEN missing")
        return

    async with aiohttp.ClientSession() as session:
        async with session.get(
            f"https://api.telegram.org/bot{token}/getMe", timeout=20
        ) as response:
            me = await response.json()
        if not me.get("ok"):
            out(f"  cannot check: telegram refused the token ({me.get('description')})")
            return
        bot_id = me["result"]["id"]

        seen: set = set()
        for task in tasks:
            target = task.dest_id or task.dest_ref
            if not target or target in seen:
                continue
            seen.add(target)
            try:
                async with session.get(
                    f"https://api.telegram.org/bot{token}/getChatMember",
                    params={"chat_id": str(target), "user_id": str(bot_id)},
                    timeout=20,
                ) as response:
                    body = await response.json()
            except Exception as exc:
                out(f"  {target}: could not ask ({exc})")
                continue

            if not body.get("ok"):
                out(f"  {target}: NOT REACHABLE -- {body.get('description')}")
                out("      the bot is not an admin there (or was removed)")
                continue

            member = body["result"]
            status = member.get("status")
            can_post = member.get("can_post_messages")
            if status == "creator" or (status == "administrator" and can_post is not False):
                out(f"  {target}: ok (status={status})")
            else:
                out(f"  {target}: CANNOT POST (status={status}, can_post={can_post})")


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
