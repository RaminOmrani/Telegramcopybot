"""چرا کارها کپی نمی‌کنند — گزارشِ خام از روی دیتابیس.

<b>چرا جدا از «بررسی سلامت» داخل ربات.</b> آن یکی از تلگرام می‌پرسد و
برای مشتری نوشته شده. این یکی برای وقتی است که می‌خواهیم <b>خودمان</b>
از روی سرور ببینیم چه خبر است — بدون اینکه به تلگرام دست بزنیم، و
بدون اینکه به بالا آمدن ربات نیاز باشد.

خروجی عمداً لاتینِ خالص است: کنسول VNC هیچ حرف فارسی‌ای را نشان
نمی‌دهد و همه را ♦ می‌کند.

اجرا:
    sudo -u telkap /opt/telkap/.venv/bin/python tools/why.py

و اگر صف تلاش مجدد پر شده باشد و بخواهیم از صفر شروع کنیم:
    sudo -u telkap /opt/telkap/.venv/bin/python tools/why.py --clear-retries
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


async def main() -> int:
    from sqlalchemy import delete, func, select

    from telkap.db import get_session, init_db
    from telkap.models import DailyStat, RetryItem, Task, User

    await init_db()
    clear = "--clear-retries" in sys.argv

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

    print("=" * 68)
    print(f"tasks={len(tasks)}  retry_queue_total={total_queue}")
    print("=" * 68)

    for task in tasks:
        person = people.get(task.user_id)
        state = getattr(person, "account_state", "") or "ok"
        linked = "yes" if person is not None and person.session_enc else "NO"
        waiting = int(queued.get(task.id, 0) or 0)
        lost = int(failed.get(task.id, 0) or 0)

        flag = "ON " if task.enabled else "OFF"
        print()
        print(f"[{flag}] task={task.id} user={task.user_id} account={state} session={linked}")
        print(f"       src={task.source_ref!r} src_id={task.source_id}")
        print(f"       dst={task.dest_ref!r} dst_id={task.dest_id}")
        print(f"       copied={task.copied_count} skipped={task.skipped_count} "
              f"failed_total={lost} retry_queue={waiting}")
        print(f"       last_copy={task.last_copy_at}")
        if task.last_error:
            # خطا ممکن است فارسی باشد و کنسول نشانش ندهد؛ ولی همین که
            # طولش دیده شود یعنی خطایی هست
            print(f"       last_error({len(task.last_error)} chars): {task.last_error}")

    print()
    print("-" * 68)
    print("how to read this:")
    print("  session=NO        -> user account not connected; nothing can run")
    print("  account=banned    -> telegram limited the account; sending is stopped")
    print("  OFF + last_error  -> the task was paused, the reason is on that line")
    print("  retry_queue high  -> sends keep failing (usually: no admin in destination)")
    print("-" * 68)

    if clear and total_queue:
        async with get_session() as db:
            await db.execute(delete(RetryItem))
            await db.commit()
        print(f"cleared {total_queue} items from the retry queue.")
    elif clear:
        print("retry queue was already empty.")

    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
