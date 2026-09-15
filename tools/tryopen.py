"""ازمایش: کانال عمومی را بدون عضو شدن می‌شود خواند؟

<b>تنها فرضی که کلِ «حالت ساده» رویش بنا می‌شود.</b>

ایده این است: برای مبدأ عمومی لازم نباشد مشتری اکانت خودش را وصل کند.
یک اکانت سرویسِ ما کانال را می‌خواند و ربات — که مشتری در کانال مقصدش
ادمینش کرده — پست را می‌گذارد.

کلِ این طرح روی یک فرض ایستاده: اکانت سرویس بتواند کانال عمومی را
<b>بدون عضو شدن</b> بخواند. اگر بشود، بقیه‌اش مهندسیِ معمولی است. اگر
نشود، باید عضو شویم و آن‌وقت سقفِ ۵۰۰ کانال برای هر اکانت می‌آید وسط و
شکلِ کسب‌وکار عوض می‌شود.

پس پیش از نوشتن هر کدی، همین یک چیز سنجیده می‌شود.

<b>این اسکریپت هیچ‌چیز را عوض نمی‌کند.</b> عضو هیچ کانالی نمی‌شود،
چیزی نمی‌فرستد، و در دیتابیس چیزی نمی‌نویسد. فقط می‌خواند و گزارش
می‌دهد.

اجرا:
    sudo -u telkap /opt/telkap/.venv/bin/python tools/tryopen.py

و با کانال‌های دلخواه خودتان (هرچه بیشتر، نتیجه مطمئن‌تر):
    sudo -u telkap /opt/telkap/.venv/bin/python tools/tryopen.py @ch1 @ch2

گزارش را در تلگرام بفرستد:
    sudo -u telkap /opt/telkap/.venv/bin/python tools/tryopen.py --send

خروجی عمداً لاتینِ خالص است: کنسول VNC حرف فارسی را نشان نمی‌دهد.
"""
from __future__ import annotations

import asyncio
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# این ابزار باید از هر پوشه‌ای کار کند؛ مسیر دیتابیس در تنظیمات نسبی است.
os.chdir(Path(__file__).resolve().parent.parent)

# کانال‌های پیش‌فرض: رسمی، قطعاً عمومی، و تقریباً مطمئن که اکانت سرویس
# عضوشان نیست. هدف این است که تست بدون هیچ ورودی هم معنا بدهد.
DEFAULTS = ["@telegram", "@durov"]


def _mmss(seconds: float) -> str:
    return f"{int(seconds // 60)}:{int(seconds % 60):02d}"


async def _membership(client, entity) -> str:
    """«عضوم یا نه» — چون اگر عضو باشیم، تست چیزی را ثابت نمی‌کند."""
    from telethon.errors import UserNotParticipantError
    from telethon.tl.functions.channels import GetParticipantRequest
    from telethon.tl.types import InputPeerSelf

    try:
        await client(GetParticipantRequest(entity, InputPeerSelf()))
        return "MEMBER"
    except UserNotParticipantError:
        return "not-member"
    except Exception as exc:
        return f"unknown ({type(exc).__name__})"


async def _probe(client, ref: str, out) -> dict:
    """یک کانال را وارسی می‌کند: شناسایی، عضویت، و خواندن."""
    result = {"ref": ref, "resolved": False, "member": "?", "read": 0}

    out()
    out(f"--- {ref}")

    started = time.monotonic()
    try:
        entity = await client.get_entity(ref)
    except Exception as exc:
        out(f"    resolve FAILED: {type(exc).__name__}: {exc}")
        return result
    result["resolved"] = True
    result["title"] = getattr(entity, "title", "") or ""
    result["id"] = getattr(entity, "id", 0)
    out(f"    resolved in {time.monotonic() - started:.2f}s  id={result['id']}")

    result["member"] = await _membership(client, entity)
    out(f"    membership: {result['member']}")

    started = time.monotonic()
    newest = None
    count = 0
    media = 0
    try:
        async for message in client.iter_messages(entity, limit=5):
            count += 1
            if newest is None:
                newest = message
            if getattr(message, "media", None) is not None:
                media += 1
    except Exception as exc:
        out(f"    READ FAILED: {type(exc).__name__}: {exc}")
        return result

    took = time.monotonic() - started
    result["read"] = count
    result["took"] = took
    out(f"    read {count} message(s) in {took:.2f}s   with-media={media}")
    if newest is not None:
        age = ""
        try:
            from datetime import UTC, datetime

            posted = newest.date
            if posted.tzinfo is None:
                posted = posted.replace(tzinfo=UTC)
            age = f"  age={_mmss((datetime.now(UTC) - posted).total_seconds())}"
        except Exception:
            pass
        out(f"    newest: id={newest.id}{age}")

    # خواندنِ دوباره، چند ثانیه بعد. «یک بار جواب داد» کافی نیست: جارو
    # قرار است هر سه دقیقه، تا ابد، همین کار را بکند.
    await asyncio.sleep(3)
    started = time.monotonic()
    try:
        again = 0
        async for _ in client.iter_messages(entity, limit=5):
            again += 1
        out(f"    re-read ok: {again} message(s) in {time.monotonic() - started:.2f}s")
        result["reread"] = again
    except Exception as exc:
        out(f"    RE-READ FAILED: {type(exc).__name__}: {exc}")

    return result


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
    form.add_field("caption", "tryopen.py report")
    form.add_field(
        "document", report.encode("utf-8"),
        filename="tryopen.txt", content_type="text/plain",
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
    print("sent to telegram" if body.get("ok") else f"telegram refused: {body.get('description')}")


async def main() -> int:
    from sqlalchemy import select

    from telkap.db import get_session, init_db
    from telkap.models import User
    from telkap.services.userbot import manager

    refs = [a for a in sys.argv[1:] if not a.startswith("--")] or DEFAULTS
    send = "--send" in sys.argv

    lines: list[str] = []

    def out(text: str = "") -> None:
        lines.append(text)
        print(text)

    await init_db()

    # هر اکانتِ متصلی کافی است؛ اینجا فقط می‌خواهیم بدانیم تلگرام چه
    # اجازه‌ای می‌دهد، نه اینکه کارِ کسی را انجام دهیم.
    async with get_session() as db:
        person = (
            await db.execute(select(User).where(User.session_enc != ""))
        ).scalars().first()

    if person is None:
        out("no connected account in the database; cannot test.")
        return 1

    out("=" * 68)
    out("does a public channel open WITHOUT joining it?")
    out("=" * 68)
    out(f"testing with account of user {person.id}")
    out("this script joins nothing, sends nothing, writes nothing.")

    client = await manager.ensure_client(person.id)
    if client is None:
        out("could not start the account client.")
        return 1

    results = []
    for ref in refs:
        try:
            results.append(await _probe(client, ref, out))
        except Exception as exc:
            out(f"    UNEXPECTED: {type(exc).__name__}: {exc}")
        await asyncio.sleep(1)

    # جمع‌بندی: فقط کانال‌هایی که عضوشان نیستیم چیزی ثابت می‌کنند
    proof = [r for r in results if r.get("member") == "not-member" and r.get("read")]
    joined = [r for r in results if r.get("member") == "MEMBER"]
    failed = [r for r in results if r.get("member") == "not-member" and not r.get("read")]

    out()
    out("=" * 68)
    out("VERDICT")
    out("=" * 68)
    out(f"  read while NOT a member : {len(proof)}")
    out(f"  failed while NOT a member: {len(failed)}")
    out(f"  skipped (already member) : {len(joined)}")
    out()
    if proof and not failed:
        out("  YES - a public channel can be read without joining.")
        out("  the simple mode (no user account) is possible for public sources.")
    elif proof and failed:
        out("  MIXED - some opened, some did not. see the failures above.")
    elif failed:
        out("  NO - reading without joining was refused.")
        out("  the simple mode would have to join each source, which caps")
        out("  every service account at ~500 channels.")
    else:
        out("  INCONCLUSIVE - we are a member of every channel tested.")
        out("  re-run with a public channel this account has NOT joined:")
        out("      tools/tryopen.py @somechannel")

    if send:
        await deliver("\n".join(lines))
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
