"""مرورِ کاملِ عملکرد — ربات تا امروز چه کرده و کجا لنگیده.

<b>چرا یک ابزارِ جدا و نه جمعِ ابزارهای موجود.</b>

`why.py` می‌گوید یک کار چرا نزد. `simplecheck.py` می‌گوید حالت ساده
کجا ایستاده. هر دو برای <b>یک سؤالِ مشخص</b> ساخته شده‌اند و جوابِ
خوبی هم می‌دهند.

ولی سؤالِ «کلاً چطور کار کرده» جنسِ دیگری است: جوابش از مقایسه درمی‌آید،
نه از یک عدد. ۵۰۰ کپی زیاد است یا کم؟ بسته به اینکه چند تا شکست خورده.
تأخیر ۸۰ ثانیه بد است؟ بسته به اینکه از چه راهی آمده. این ابزار
<b>نسبت‌ها</b> را می‌سازد تا عددها معنا پیدا کنند.

<b>و چرا فقط از دیتابیس.</b> به تلگرام دست نمی‌زند و به بالا بودنِ ربات
نیاز ندارد؛ پس حتی وقتی چیزی خراب است هم اجرا می‌شود — که دقیقاً همان
وقتی است که بیشترین نیاز را به آن داریم.

اجرا:
    sudo -u telkap /opt/telkap/.venv/bin/python tools/review.py

و چون بخش‌هایی از آن فارسی است و کنسول VNC فارسی را نشان نمی‌دهد،
گزارش را در تلگرام هم می‌فرستد:
    sudo -u telkap /opt/telkap/.venv/bin/python tools/review.py --send

بازه‌ی پیش‌فرض ۳۰ روز است؛ با --days عوض می‌شود:
    sudo -u telkap /opt/telkap/.venv/bin/python tools/review.py --days 7 --send
"""
from __future__ import annotations

import asyncio
import os
import sys
from collections import Counter
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# این ابزار باید از هر پوشه‌ای کار کند؛ مسیر دیتابیس نسبی است.
os.chdir(Path(__file__).resolve().parent.parent)

# ایران UTC+3:30 است. ساعتِ خام UTC در گزارشی که آدم می‌خواند فقط
# اشتباه‌انداز است — «اوج ساعت ۵ صبح» یعنی ۸:۳۰، و آن یکی معنا دارد.
IRAN = timezone(timedelta(hours=3, minutes=30))


def _bar(part: int, whole: int, width: int = 24) -> str:
    """نوارِ متنی. <b>درصدِ تنها دروغ نمی‌گوید ولی دیده نمی‌شود.</b>"""
    if whole <= 0:
        return " " * width
    filled = min(width, max(0, round(width * part / whole)))
    return "#" * filled + "." * (width - filled)


def _pct(part: int, whole: int) -> str:
    return f"{(100.0 * part / whole):.1f}%" if whole else "--"


def _percentiles(values: list[float]) -> dict[str, float]:
    """میانه و دنباله.

    <b>میانگین در اینجا تقریباً همیشه دروغ می‌گوید.</b> یک ویدیوی ۲
    گیگی که ۲۰ دقیقه طول کشیده، میانگینِ هزار پستِ یک‌ثانیه‌ای را
    بالا می‌برد و گزارش می‌شود «سرویس کند است». میانه آن را نشان
    نمی‌دهد و p95 خودِ دنباله را جدا می‌گوید.
    """
    if not values:
        return {}
    ordered = sorted(values)

    def at(q: float) -> float:
        index = min(len(ordered) - 1, max(0, int(round(q * (len(ordered) - 1)))))
        return ordered[index]

    return {"p50": at(0.50), "p90": at(0.90), "p95": at(0.95), "max": ordered[-1]}


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
    form.add_field("caption", "review.py -- full performance review")
    form.add_field(
        "document", report.encode("utf-8"),
        filename="review.txt", content_type="text/plain",
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


def _days_arg() -> int:
    if "--days" in sys.argv:
        try:
            return max(1, int(sys.argv[sys.argv.index("--days") + 1]))
        except (IndexError, ValueError):
            pass
    return 30


async def main() -> int:
    from sqlalchemy import func, select

    from telkap.db import get_session, init_db
    from telkap.models import (
        ActivityLog,
        DailyStat,
        DeliveryTiming,
        MessageMap,
        PaymentRequest,
        RetryItem,
        ServiceAccount,
        Subscription,
        Task,
        User,
    )

    await init_db()
    days = _days_arg()
    since = datetime.now(UTC) - timedelta(days=days)
    lines: list[str] = []

    def out(text: str = "") -> None:
        lines.append(text)
        print(text)

    def head(title: str) -> None:
        out()
        out("=" * 70)
        out(title)
        out("=" * 70)

    head(f"TELKAP -- performance review (last {days} days)")
    out(f"generated: {datetime.now(IRAN):%Y-%m-%d %H:%M} Iran time")

    # ---------------------------------------------------------- ۱. کلیات
    async with get_session() as db:
        users = int(await db.scalar(select(func.count(User.id))) or 0)
        linked = int(
            await db.scalar(
                select(func.count(User.id)).where(User.session_enc.is_not(None))
            ) or 0
        )
        banned = int(
            await db.scalar(
                select(func.count(User.id)).where(User.is_banned.is_(True))
            ) or 0
        )
        new_users = int(
            await db.scalar(
                select(func.count(User.id)).where(User.created_at >= since)
            ) or 0
        )
        tasks = list((await db.execute(select(Task))).scalars())
        first_copy = await db.scalar(select(func.min(DeliveryTiming.created_at)))
        total_copies = int(
            await db.scalar(select(func.count(DeliveryTiming.id))) or 0
        )

    head("1. THE SHAPE OF IT")
    out(f"  users registered      : {users}  (+{new_users} in window)")
    out(f"  accounts connected    : {linked}  ({_pct(linked, users)} of users)")
    if banned:
        out(f"  banned                : {banned}")
    out(f"  tasks                 : {len(tasks)}"
        f"  ({sum(1 for t in tasks if t.enabled)} on,"
        f" {sum(1 for t in tasks if not t.enabled)} off)")
    simple = sum(1 for t in tasks if (t.mode or "full") == Task.MODE_SIMPLE)
    out(f"       simple / full    : {simple} / {len(tasks) - simple}")
    out(f"  deliveries recorded   : {total_copies}")
    if first_copy is not None:
        age = (datetime.now(UTC) - first_copy.replace(tzinfo=UTC)).days
        out(f"  measuring since       : {first_copy:%Y-%m-%d}  ({age} days)")

    # <b>قیفِ واقعی.</b> عددِ «کاربر ثبت‌نام‌شده» به‌تنهایی خودفریبی
    # است: کسی که کار نساخته هیچ‌وقت ارزش محصول را ندیده و تمدید هم
    # نمی‌کند. فاصله‌ی این دو خط می‌گوید کجا آدم‌ها را گم می‌کنیم.
    with_task = len({t.user_id for t in tasks})
    async with get_session() as db:
        with_copy = len(
            {
                row
                for row in (
                    await db.execute(select(DeliveryTiming.user_id).distinct())
                ).scalars()
            }
        )
    out()
    out("  funnel:")
    out(f"    registered          {users:>6}  {_bar(users, users)}")
    out(f"    made a task         {with_task:>6}  {_bar(with_task, users)}  {_pct(with_task, users)}")
    out(f"    got a post copied   {with_copy:>6}  {_bar(with_copy, users)}  {_pct(with_copy, users)}")

    # -------------------------------------------------- ۲. کپی: حجم و کیفیت
    # <b>`day` رشته است، نه تاریخ.</b> ستونش `String(10)` با قالب
    # YYYY-MM-DD است. مقایسه با یک شیءِ date تصادفاً کار می‌کند — چون
    # repr آن دقیقاً همین قالب است و مقایسه‌ی حرف‌به‌حرف درست درمی‌آید —
    # ولی تکیه به آن تصادف، همان جایی است که روزی بی‌صدا می‌شکند.
    cutoff = f"{since.date():%Y-%m-%d}"
    async with get_session() as db:
        stats = list(
            (
                await db.execute(
                    select(
                        func.sum(DailyStat.copied),
                        func.sum(DailyStat.skipped),
                        func.sum(DailyStat.failed),
                    ).where(DailyStat.day >= cutoff)
                )
            ).all()
        )
    copied, skipped, failed = (int(v or 0) for v in (stats[0] if stats else (0, 0, 0)))
    attempted = copied + failed

    head("2. DID THE POSTS ARRIVE")
    out(f"  copied   : {copied}")
    out(f"  failed   : {failed}   {_bar(failed, attempted)}  {_pct(failed, attempted)} of attempts")
    out(f"  skipped  : {skipped}  (filters, duplicates, quiet hours -- not errors)")
    out()
    if attempted:
        # <b>این تنها عددی است که مشتری حس می‌کند.</b> بقیه برای ماست.
        out(f"  SUCCESS RATE: {_pct(copied, attempted)}")
        if failed and copied / attempted < 0.98:
            out("    -> below 98%. see section 5 for what is failing.")
    else:
        out("  no delivery attempts recorded in this window.")

    # ------------------------------------------------ ۳. سرعت و مسیرِ رسیدن
    async with get_session() as db:
        timings = list(
            (
                await db.execute(
                    select(
                        DeliveryTiming.seconds,
                        DeliveryTiming.via,
                        DeliveryTiming.path,
                        DeliveryTiming.media_kind,
                        DeliveryTiming.created_at,
                    ).where(DeliveryTiming.created_at >= since)
                )
            ).all()
        )

    head("3. HOW FAST, AND BY WHICH ROUTE")
    if not timings:
        out("  nothing measured in this window.")
    else:
        # تأخیرِ «کپی آرشیو» بی‌معناست — پستِ پارسال را امروز کپی
        # کرده‌ایم و عددش ساعت‌هاست. در آمارِ سرعت نمی‌آید.
        live = [
            t for t in timings
            if t.via in DeliveryTiming.VIA_LIVE or t.via == DeliveryTiming.VIA_SIMPLE
        ]
        pct = _percentiles([float(t.seconds) for t in live if t.seconds is not None])
        out(f"  measured deliveries   : {len(timings)}  (live: {len(live)})")
        if pct:
            out(f"  median (p50)          : {pct['p50']:.0f}s")
            out(f"  p90                   : {pct['p90']:.0f}s")
            out(f"  p95                   : {pct['p95']:.0f}s")
            out(f"  slowest               : {pct['max']:.0f}s")

        out()
        out("  route (how we learned the post existed):")
        by_via = Counter(t.via or "unknown" for t in timings)
        for name, count in by_via.most_common():
            out(f"    {name:<10} {count:>6}  {_bar(count, len(timings))}  {_pct(count, len(timings))}")

        # <b>عددی که باید نگاهش کرد.</b> جارو تورِ ایمنی است؛ اگر سهمش
        # بالا باشد یعنی آپدیت‌های لحظه‌ای نمی‌رسند و تور دارد کلِ بار
        # را می‌برد.
        sweep = by_via.get(DeliveryTiming.VIA_SWEEP, 0)
        update = by_via.get(DeliveryTiming.VIA_UPDATE, 0)
        if sweep + update:
            share = 100.0 * sweep / (sweep + update)
            out()
            out(f"  sweep share of live account traffic: {share:.0f}%")
            out("    (telethon does not fire updates for big public channels it")
            out("     does not own -- issue #4345. the sweeper is the workaround,")
            out("     not a symptom of our bug. but it costs latency.)")

        out()
        out("  send path (what it cost us to deliver):")
        by_path = Counter(t.path or "unknown" for t in timings)
        for name, count in by_path.most_common():
            out(f"    {name:<10} {count:>6}  {_bar(count, len(timings))}  {_pct(count, len(timings))}")

        out()
        out("  content type:")
        by_kind = Counter(t.media_kind or "unknown" for t in timings)
        for name, count in by_kind.most_common(12):
            out(f"    {name:<10} {count:>6}  {_bar(count, len(timings))}  {_pct(count, len(timings))}")

        # ساعتِ اوج — برای تصمیم درباره‌ی ظرفیت و زمانِ به‌روزرسانی
        out()
        out("  busiest hours (Iran time):")
        hours = Counter(
            t.created_at.replace(tzinfo=UTC).astimezone(IRAN).hour
            for t in timings if t.created_at is not None
        )
        busiest = hours.most_common(5)
        for hour, count in busiest:
            out(f"    {hour:02d}:00     {count:>6}  {_bar(count, len(timings))}")
        if busiest:
            quiet = min(hours, key=lambda h: hours[h]) if hours else None
            out(f"    -> quietest hour is {quiet:02d}:00; best time to restart/update.")

    # ------------------------------------------------- ۴. روزبه‌روز
    async with get_session() as db:
        daily = list(
            (
                await db.execute(
                    select(
                        DailyStat.day,
                        func.sum(DailyStat.copied),
                        func.sum(DailyStat.failed),
                    )
                    .where(DailyStat.day >= cutoff)
                    .group_by(DailyStat.day)
                    .order_by(DailyStat.day)
                )
            ).all()
        )

    head("4. DAY BY DAY")
    if not daily:
        out("  no daily stats in this window.")
    else:
        peak = max(int(c or 0) for _, c, _ in daily) or 1
        for day, c, f in daily:
            c, f = int(c or 0), int(f or 0)
            flag = f"  <-- {f} failed" if f else ""
            out(f"  {day}  {c:>5}  {_bar(c, peak, 30)}{flag}")
        # <b>روزِ خالی یعنی قطعی.</b> و قطعی‌ای که کسی ندیده، بدترین
        # نوعش است: نه شکایتی آمده، نه ما خبر داشته‌ایم.
        #
        # رشته‌ها اینجا به تاریخ تبدیل می‌شوند، چون «روزِ بعد» روی رشته
        # معنا ندارد. هر چیزی که قالبش نخورد کنار گذاشته می‌شود — یک
        # ردیفِ خراب نباید کلِ گزارش را بیندازد.
        seen = set()
        for day, _, _ in daily:
            try:
                seen.add(datetime.strptime(str(day), "%Y-%m-%d").date())
            except ValueError:
                continue
        if seen:
            start, end = min(seen), max(seen)
            gaps = [
                start + timedelta(days=i)
                for i in range((end - start).days + 1)
                if start + timedelta(days=i) not in seen
            ]
            if gaps:
                out()
                out(f"  !! {len(gaps)} day(s) with NO activity at all:")
                for gap in gaps[:10]:
                    out(f"     {gap}")
                out("     (either every task was off, or the bot was down)")

    # ------------------------------------------------- ۵. خرابی‌ها
    async with get_session() as db:
        errors = list(
            (
                await db.execute(
                    select(Task.id, Task.title, Task.last_error, Task.enabled)
                    .where(Task.last_error.is_not(None))
                )
            ).all()
        )
        queued = int(await db.scalar(select(func.count(RetryItem.id))) or 0)
        worst = list(
            (
                await db.execute(
                    select(RetryItem.task_id, func.count(RetryItem.id))
                    .group_by(RetryItem.task_id)
                    .order_by(func.count(RetryItem.id).desc())
                    .limit(5)
                )
            ).all()
        )
        skips = list(
            (
                await db.execute(
                    select(ActivityLog.detail, func.count(ActivityLog.id))
                    .where(
                        ActivityLog.created_at >= since,
                        ActivityLog.event.in_(["skip", "error", "pause"]),
                    )
                    .group_by(ActivityLog.detail)
                    .order_by(func.count(ActivityLog.id).desc())
                    .limit(12)
                )
            ).all()
        )

    head("5. WHAT WENT WRONG")
    out(f"  retry queue right now : {queued}")
    if worst:
        for task_id, count in worst:
            if count:
                out(f"      task {task_id}: {count} waiting")
    out(f"  tasks carrying an error: {len(errors)}")
    for task_id, title, error, enabled in errors[:10]:
        state = "ON " if enabled else "OFF"
        out(f"    [{state}] task {task_id} ({title}): {error}")

    out()
    out("  most common reasons a post did not go (from the activity log):")
    if not skips:
        out("    none recorded.")
    for detail, count in skips:
        out(f"    {count:>6}x  {(detail or '(no reason recorded)')[:60]}")

    # ------------------------------------------------- ۶. پول و اشتراک
    async with get_session() as db:
        active = int(
            await db.scalar(
                select(func.count(Subscription.id)).where(
                    Subscription.expires_at > datetime.now(UTC)
                )
            ) or 0
        )
        by_plan = list(
            (
                await db.execute(
                    select(Subscription.plan_code, func.count(Subscription.id))
                    .where(Subscription.expires_at > datetime.now(UTC))
                    .group_by(Subscription.plan_code)
                )
            ).all()
        )
        expiring = int(
            await db.scalar(
                select(func.count(Subscription.id)).where(
                    Subscription.expires_at > datetime.now(UTC),
                    Subscription.expires_at < datetime.now(UTC) + timedelta(days=7),
                )
            ) or 0
        )
        paid = list(
            (
                await db.execute(
                    select(PaymentRequest.status, func.count(PaymentRequest.id))
                    .group_by(PaymentRequest.status)
                )
            ).all()
        )

    head("6. SUBSCRIPTIONS AND MONEY")
    out(f"  active subscriptions  : {active}")
    for code, count in by_plan:
        out(f"    {code:<14} {count}")
    out(f"  expiring within 7 days: {expiring}")
    out()
    out("  payment requests by status:")
    if not paid:
        out("    none yet.")
    for status, count in paid:
        out(f"    {status:<14} {count}")

    # ------------------------------------------- ۷. زیرساختِ حالت ساده
    async with get_session() as db:
        accounts = list((await db.execute(select(ServiceAccount))).scalars())
        mapped = int(await db.scalar(select(func.count(MessageMap.id))) or 0)

    head("7. SERVICE ACCOUNTS (simple mode)")
    out(f"  accounts: {len(accounts)}")
    for account in accounts:
        flag = "ok " if (account.enabled and account.state == "ok") else "OFF"
        out(f"    [{flag}] id={account.id} state={account.state} "
            f"sources={account.sources} last_used={account.last_used_at}")
        if account.quiet_until:
            out(f"           asleep until {account.quiet_until} (UTC)")
        if account.note:
            out(f"           note: {account.note}")
    if not accounts and simple:
        out("  !! there are simple tasks but NO service account -- they are all silent.")
    out(f"  message_map rows (copies remembered): {mapped}")

    # ------------------------------------------------------ ۸. سرنخ‌ها
    head("8. WHAT I WOULD LOOK AT")
    notes: list[str] = []
    if attempted and copied / attempted < 0.98:
        notes.append(
            f"success rate is {_pct(copied, attempted)} -- {failed} posts never arrived."
        )
    if queued > 20:
        notes.append(f"retry queue is {queued} deep; something is refusing to accept posts.")
    if errors:
        notes.append(f"{len(errors)} task(s) carry an error message; see section 5.")
    if simple and not accounts:
        notes.append("simple tasks exist with no service account -- nothing runs for them.")
    if users and with_copy / max(users, 1) < 0.5:
        notes.append(
            f"only {_pct(with_copy, users)} of registered users ever got a post copied."
        )
    if expiring:
        notes.append(f"{expiring} subscription(s) expire within a week.")
    if not notes:
        out("  nothing stands out. the numbers above are the evidence.")
    for note in notes:
        out(f"  - {note}")

    out()
    out("-" * 70)
    out("how to read this:")
    out("  success rate   -> the only number the customer feels")
    out("  sweep share    -> high means live updates are not arriving")
    out("  funnel         -> where people stop before seeing the value")
    out("  empty days     -> an outage nobody reported")
    out("-" * 70)

    if "--send" in sys.argv:
        await deliver("\n".join(lines))
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
