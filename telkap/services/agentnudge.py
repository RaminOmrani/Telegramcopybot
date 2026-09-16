"""یادآوری انقضای مشتری‌ها به نماینده.

<b>چرا این مهم‌ترین کارِ نگه‌داشتنِ نماینده است.</b> نگرانی هر نماینده
یک جمله است: «مشتریِ من مستقیم از شما بخرد و دیگر سراغ من نیاید».
هرچقدر هم سهمش را تضمین کنیم، آن اتفاق وقتی می‌افتد که مشتری اشتراکش
تمام شود و نماینده خبر نداشته باشد — مشتری خودش می‌آید سراغ ربات.

پس قاعده ساده است: <b>نماینده باید پیش از ما بداند</b>. یک هفته پیش
از انقضا خبردار می‌شود تا خودش تماس بگیرد و خودش تمدید کند.

<b>سه تصمیم که این را از یک ربات مزاحم جدا می‌کند:</b>

۱. یک پیام برای همه‌ی مشتری‌ها، نه یک پیام به‌ازای هر مشتری. نماینده‌ای
   با بیست مشتری نباید بیست اعلان بگیرد؛ بعد از بار دوم دیگر بازشان
   نمی‌کند.
۲. هر اشتراک فقط یک بار. جدولِ ReminderState همان کار را برای مشتری‌ها
   می‌کند و اینجا هم از همان استفاده می‌شود — با نامِ دیگر، چون
   گیرنده فرق دارد.
۳. مشتریِ تازه‌منقضی هم گفته می‌شود، ولی فقط تا دو روز. بعد از آن،
   یادآوری دیگر یادآوری نیست؛ سرزنش است.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import UTC, timedelta

from sqlalchemy import select

from telkap.db import get_session
from telkap.models import ReminderState, Subscription, User, utcnow
from telkap.plans import get_plan
from telkap.services.reseller import EXPIRY_WARNING_DAYS
from telkap.texts import fa_num

log = logging.getLogger(__name__)

CHECK_INTERVAL = 6 * 3600        # چهار بار در روز کافی است
KIND_SOON = "agent_expiry_soon"
KIND_OVER = "agent_expired"
# مشتریِ منقضی تا این‌قدر روز گفته می‌شود؛ بعد از آن دیگر خبر نیست
EXPIRED_GRACE_DAYS = 2


def _aware(value):
    """SQLite تاریخ را بدون منطقه‌ی زمانی برمی‌گرداند؛ UTC فرض می‌شود."""
    return value if value.tzinfo else value.replace(tzinfo=UTC)


async def _pending(kind: str, rows) -> dict[int, list[tuple]]:
    """آنچه هنوز به نماینده‌اش گفته نشده، دسته‌بندی‌شده بر اساس نماینده."""
    if not rows:
        return {}

    async with get_session() as db:
        sent = set(
            (
                await db.execute(
                    select(ReminderState.sub_id).where(
                        ReminderState.kind == kind,
                        ReminderState.sub_id.in_([sub.id for _owner, _person, sub in rows]),
                    )
                )
            ).scalars()
        )

    grouped: dict[int, list[tuple]] = {}
    for owner, person, sub in rows:
        if sub.id in sent:
            continue
        grouped.setdefault(owner, []).append((person, sub))
    return grouped


async def _rows(kind: str) -> list[tuple]:
    """(نماینده، مشتری، اشتراک) برای مشتری‌هایی که وضعیتشان خبر دارد."""
    now = utcnow()
    async with get_session() as db:
        people = list(
            (
                await db.execute(select(User).where(User.owned_by.is_not(None)))
            ).scalars()
        )
        if not people:
            return []

        by_id = {person.id: person for person in people}
        # <b>همه‌ی اشتراک‌ها خوانده می‌شوند، نه فقط آن‌هایی که در بازه‌اند.</b>
        #
        # نسخه‌ی اول بازه را در همین کوئری می‌گذاشت و یک باگ داشت که
        # تستش پیدایش کرد: مشتری‌ای که تمدید کرده بود، ردیفِ قدیمیِ رو
        # به اتمامش هنوز در بازه می‌افتاد و ردیفِ تازه‌اش — که شصت روز
        # اعتبار داشت — اصلاً خوانده نمی‌شد. نتیجه‌اش این بود که
        # نماینده سراغ کسی می‌رفت که همین دیروز تمدید کرده.
        #
        # پس اول تازه‌ترین اشتراکِ هر مشتری برداشته می‌شود، بعد بازه
        # روی همان یکی اعمال می‌شود.
        subs = list(
            (
                await db.execute(
                    select(Subscription).where(Subscription.user_id.in_(list(by_id)))
                )
            ).scalars()
        )

    latest: dict[int, Subscription] = {}
    for sub in subs:
        current = latest.get(sub.user_id)
        if current is None or _aware(sub.expires_at) > _aware(current.expires_at):
            latest[sub.user_id] = sub

    if kind == KIND_SOON:
        low, high = now, now + timedelta(days=EXPIRY_WARNING_DAYS)
    else:
        low, high = now - timedelta(days=EXPIRED_GRACE_DAYS), now

    out = []
    for user_id, sub in latest.items():
        expires = _aware(sub.expires_at)
        if not (low < expires <= high):
            continue
        person = by_id[user_id]
        if person.owned_by:
            out.append((int(person.owned_by), person, sub))
    return out


def _line(person: User, sub: Subscription, *, soon: bool) -> str:
    name = (person.first_name or "").strip() or str(person.id)
    plan = get_plan(sub.plan_code)
    tail = f" · {plan.title}" if plan else ""
    if not soon:
        return f"• {name} — <code>{person.id}</code>{tail} — <b>تمام شد</b>"

    delta = _aware(sub.expires_at) - utcnow()
    days = max(0, delta.days + (1 if delta.seconds else 0))
    when = "امروز" if days <= 0 else f"{fa_num(days)} روز دیگر"
    return f"• {name} — <code>{person.id}</code>{tail} — {when}"


async def run_once(notify=None) -> int:
    """یک دور. تعداد نماینده‌هایی که خبر گرفتند را برمی‌گرداند."""
    if notify is None:
        return 0

    told = 0
    for kind in (KIND_SOON, KIND_OVER):
        soon = kind == KIND_SOON
        grouped = await _pending(kind, await _rows(kind))

        for owner_id, items in grouped.items():
            head = (
                f"⏳ <b>{fa_num(len(items))} مشتری شما تا "
                f"{fa_num(EXPIRY_WARNING_DAYS)} روز دیگر تمام می‌شود</b>"
                if soon
                else f"🔴 <b>اشتراک {fa_num(len(items))} مشتری شما تمام شد</b>"
            )
            body = "\n".join(_line(person, sub, soon=soon) for person, sub in items[:20])
            more = (
                f"\n<i>و {fa_num(len(items) - 20)} مورد دیگر…</i>"
                if len(items) > 20
                else ""
            )
            foot = (
                "\n\nبرای تمدید، در «🤝 نمایندگی» طرح را بزنید و شناسه‌ی مشتری "
                "را بدهید — همان لحظه فعال می‌شود."
            )

            try:
                await notify(owner_id, f"{head}\n\n{body}{more}{foot}")
            except Exception:
                # نرسیدنِ پیام نباید مانع علامت‌گذاری بقیه شود، ولی این
                # چند مورد باید دفعه‌ی بعد دوباره امتحان شوند
                log.debug("یادآوری به نماینده %s نرسید", owner_id, exc_info=True)
                continue

            told += 1
            async with get_session() as db:
                for _person, sub in items:
                    db.add(
                        ReminderState(user_id=owner_id, kind=kind, sub_id=sub.id)
                    )
                try:
                    await db.commit()
                except Exception:
                    # قید یکتایی ممکن است در اجرای هم‌زمان بخورد؛ بدترین
                    # حالتش یک یادآوری تکراری است، نه از دست رفتن داده
                    await db.rollback()
    return told


async def run_forever(notify=None) -> None:
    while True:
        try:
            await asyncio.sleep(CHECK_INTERVAL)
            await run_once(notify)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("چرخه‌ی یادآوری به نماینده‌ها با خطا مواجه شد")
