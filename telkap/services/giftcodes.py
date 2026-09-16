"""کد هدیه و پیش‌فروش.

برخلاف کد تخفیف که روی قیمت اثر می‌گذارد، این کد خودش اشتراک است: کاربر
واردش می‌کند و طرح بدون هیچ پرداختی فعال می‌شود. برای مسابقه‌های کانالی،
همکاری با اینفلوئنسر، و فروش کارت‌های پیش‌پرداخت به کار می‌آید.

هر کد فقط یک بار مصرف می‌شود و مصرفش با یک `UPDATE … WHERE used_by IS
NULL` قفل می‌گردد، نه با خواندن و بعد نوشتن — وگرنه دو نفر که همزمان یک
کد را می‌فرستند هر دو اشتراک می‌گرفتند.
"""
from __future__ import annotations

import logging
import secrets

from sqlalchemy import func, select, update

from telkap.db import get_session, log_activity
from telkap.models import GiftCode, User, utcnow
from telkap.plans import get_plan
from telkap.services import subscription

log = logging.getLogger(__name__)

# نویسه‌های مبهم (O/0 و I/1/L) عمداً حذف شده‌اند تا کاربر اشتباه تایپ نکند
ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"
CODE_LENGTH = 10
MAX_BATCH = 200


class GiftError(Exception):
    """پیامی که مستقیم به کاربر نشان داده می‌شود."""


def normalize(code: str) -> str:
    return (code or "").strip().upper().replace(" ", "").replace("-", "")[:32]


def _random_code(prefix: str = "") -> str:
    body = "".join(secrets.choice(ALPHABET) for _ in range(CODE_LENGTH))
    return f"{normalize(prefix)}{body}"[:32]


async def generate(
    plan_code: str,
    count: int,
    *,
    prefix: str = "",
    batch: str = "",
    note: str = "",
    admin_id: int | None = None,
) -> list[GiftCode] | str:
    """چند کد یکبارمصرف برای یک طرح می‌سازد."""
    if get_plan(plan_code) is None:
        return "این طرح وجود ندارد."
    count = int(count)
    if not 1 <= count <= MAX_BATCH:
        return f"تعداد باید بین ۱ و {MAX_BATCH} باشد."

    label = normalize(batch) or utcnow().strftime("%Y%m%d%H%M")
    made: list[GiftCode] = []
    async with get_session() as db:
        for _ in range(count):
            # احتمال تکرار ناچیز است ولی رایگان هم پوشش داده می‌شود
            for _attempt in range(5):
                code = _random_code(prefix)
                exists = await db.scalar(
                    select(GiftCode.id).where(GiftCode.code == code)
                )
                if exists is None:
                    break
            else:
                continue
            gift = GiftCode(
                code=code,
                plan_code=plan_code,
                batch=label,
                note=note[:160],
                created_by=admin_id,
            )
            db.add(gift)
            made.append(gift)
        await db.commit()
        for gift in made:
            await db.refresh(gift)

    await log_activity(
        user_id=admin_id,
        event="gift_generate",
        detail=f"{len(made)} کد {plan_code} (دسته {label})",
    )
    return made


async def redeem(user_id: int, code: str):
    """کد را مصرف و اشتراک را فعال می‌کند."""
    cleaned = normalize(code)
    if not cleaned:
        raise GiftError("کد خالی است.")

    async with get_session() as db:
        gift = (
            await db.execute(select(GiftCode).where(GiftCode.code == cleaned))
        ).scalar_one_or_none()
        if gift is None:
            raise GiftError("این کد وجود ندارد. از درست بودن حروف مطمئن شوید.")
        if gift.used_by is not None:
            raise GiftError("این کد قبلاً استفاده شده است.")
        gift_id, plan_code = gift.id, gift.plan_code

    plan = get_plan(plan_code)
    if plan is None:
        raise GiftError("طرح این کد دیگر موجود نیست. با پشتیبانی تماس بگیرید.")

    async with get_session() as db:
        user = await db.get(User, user_id)
        if user is None:
            raise GiftError("ابتدا ربات را استارت کنید.")
        # شرط روی خود UPDATE است تا دو نفرِ همزمان یک کد را دو بار نگیرند
        result = await db.execute(
            update(GiftCode)
            .where(GiftCode.id == gift_id, GiftCode.used_by.is_(None))
            .values(used_by=user_id, used_at=utcnow())
        )
        if result.rowcount == 0:
            await db.rollback()
            raise GiftError("این کد همین حالا توسط شخص دیگری استفاده شد.")
        await db.commit()

    sub = await subscription.grant(
        user_id, plan_code, note=f"کد هدیه #{gift_id}"
    )
    if sub is None:
        # کد را آزاد می‌کنیم تا از بین نرود
        async with get_session() as db:
            await db.execute(
                update(GiftCode)
                .where(GiftCode.id == gift_id)
                .values(used_by=None, used_at=None)
            )
            await db.commit()
        raise GiftError("فعال‌سازی ناموفق بود. دوباره تلاش کنید.")

    await log_activity(
        user_id=user_id, event="gift_redeem", detail=f"{cleaned} — {plan.title}"
    )
    return plan, sub


async def batches(limit: int = 20) -> list[tuple[str, str, int, int]]:
    """(دسته، طرح، تعداد کل، تعداد استفاده‌شده) برای گزارش ادمین."""
    async with get_session() as db:
        rows = await db.execute(
            select(
                GiftCode.batch,
                GiftCode.plan_code,
                func.count(GiftCode.id),
                func.count(GiftCode.used_by),
            )
            .group_by(GiftCode.batch, GiftCode.plan_code)
            .order_by(func.max(GiftCode.id).desc())
            .limit(limit)
        )
        return [(b or "—", p, int(t or 0), int(u or 0)) for b, p, t, u in rows.all()]


async def unused_codes(batch: str, limit: int = 100) -> list[str]:
    async with get_session() as db:
        rows = await db.execute(
            select(GiftCode.code)
            .where(GiftCode.batch == batch, GiftCode.used_by.is_(None))
            .limit(limit)
        )
        return [row[0] for row in rows.all()]
