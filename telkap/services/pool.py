"""استخرِ اکانت‌های سرویس — خواننده‌های مبدأهای عمومی.

<b>چرا این هست.</b> در حالت ساده، مشتری هیچ اکانتی وصل نمی‌کند. مبدأ
عمومی را <b>اکانت خودمان</b> می‌خواند و خودِ ربات در مقصد پست می‌گذارد.
سنجیدیم و جواب داد: کانال عمومی بدون عضو شدن خوانده می‌شود.

<b>و چرا استخر، نه یک اکانت.</b> با یک اکانت، بن شدنش یعنی توقفِ همزمانِ
همه‌ی مشتری‌هایی که در حالت ساده‌اند. سرنوشتِ مشترک از بین نمی‌رود — این
هزینه‌ی ذاتیِ «اکانت نمی‌خواهیم» است — ولی با پخش کردن مبدأها روی چند
اکانت، دامنه‌اش محدود می‌شود. ساختن این انتزاع از همین اول تقریباً
مجانی است و با یک اکانت هم درست کار می‌کند؛ اضافه کردنش بعداً یعنی
بازنویسی.

<b>اجاره‌ی مبدأ ثابت می‌ماند.</b> هر کانال عمومی به یک اکانت سپرده
می‌شود و همان‌جا می‌ماند. دو دلیل: شمارشِ بار فقط این‌طور معنا دارد، و
مهم‌تر — چند اکانتِ مختلف که پشت سر هم سراغ یک کانال می‌روند الگویی
می‌سازند که از یک خواننده‌ی ثابت مشکوک‌تر است.
"""
from __future__ import annotations

import logging
from datetime import timedelta

from sqlalchemy import func, select

from telkap.crypto import decrypt
from telkap.db import get_session
from telkap.models import ServiceAccount, SourceLease, utcnow

log = logging.getLogger(__name__)

# بعد از FloodWait، اکانت این‌قدر کنار گذاشته می‌شود. عدد از خودِ خطا
# می‌آید؛ این فقط کفِ آن است تا یک FloodWait کوتاه هم بی‌اثر نماند.
MIN_QUIET = 60

# بیش از این تعداد مبدأ روی یک اکانت نمی‌نشیند. سقف عضویتِ تلگرام
# (حدود ۵۰۰) اینجا اعمال نمی‌شود چون عضو نمی‌شویم — این عدد برای
# پخش کردنِ نرخِ درخواست است، نه محدودیتِ تلگرام.
SOURCES_PER_ACCOUNT = 120


class NoAccount(Exception):
    """هیچ اکانت سرویسِ سالمی در دسترس نیست.

    <b>این خطا باید بالا برود، نه بلعیده شود.</b> اگر بی‌صدا رد شود،
    کارهای حالت ساده هیچ‌وقت اجرا نمی‌شوند و از بیرون شبیه «سرویس کند
    است» دیده می‌شود — نه شبیه «هیچ خواننده‌ای نداریم».
    """


async def _usable() -> list[ServiceAccount]:
    """اکانت‌هایی که همین حالا می‌شود رویشان حساب کرد."""
    now = utcnow()
    async with get_session() as db:
        rows = list(
            (
                await db.execute(
                    select(ServiceAccount).where(
                        ServiceAccount.enabled.is_(True),
                        ServiceAccount.state == ServiceAccount.STATE_OK,
                        ServiceAccount.session_enc.is_not(None),
                    )
                )
            ).scalars()
        )
    ready = []
    for account in rows:
        quiet = account.quiet_until
        if quiet is not None:
            if quiet.tzinfo is None:
                from datetime import UTC

                quiet = quiet.replace(tzinfo=UTC)
            if quiet > now:
                continue
        ready.append(account)
    return ready


async def capacity() -> tuple[int, int]:
    """(مبدأهای سپرده‌شده، ظرفیت کل). برای پنل و برای هشدارِ پیش از پر شدن."""
    async with get_session() as db:
        leased = int(await db.scalar(select(func.count(SourceLease.id))) or 0)
        healthy = int(
            await db.scalar(
                select(func.count(ServiceAccount.id)).where(
                    ServiceAccount.enabled.is_(True),
                    ServiceAccount.session_enc.is_not(None),
                )
            )
            or 0
        )
    return leased, healthy * SOURCES_PER_ACCOUNT


async def lease(source_id: int, source_ref: str = "") -> ServiceAccount:
    """اکانتی که این مبدأ را می‌خواند — همیشه همان یکی.

    اگر قبلاً سپرده شده باشد همان برمی‌گردد. وگرنه کم‌بارترین اکانتِ
    سالم انتخاب و اجاره ثبت می‌شود.
    """
    async with get_session() as db:
        found = await db.scalar(
            select(SourceLease).where(SourceLease.source_id == source_id)
        )
        if found is not None:
            account = await db.get(ServiceAccount, found.account_id)
            # اکانتِ اجاره ممکن است بن یا خاموش شده باشد؛ آن‌وقت اجاره
            # باید جابه‌جا شود، وگرنه این مبدأ برای همیشه می‌خوابد.
            if (
                account is not None
                and account.enabled
                and account.state == ServiceAccount.STATE_OK
                and account.session_enc
            ):
                return account
            log.warning(
                "اکانت سرویس %s دیگر سالم نیست؛ مبدأ %s جابه‌جا می‌شود",
                found.account_id, source_id,
            )
            # شمارشِ اکانتِ قبلی هم باید کم شود. اگر نشود، اکانتی که
            # ادمین موقتاً خاموشش کرده با شمارشِ بادکرده برمی‌گردد و
            # دیگر هیچ مبدأ تازه‌ای به آن نمی‌رسد — بی‌صدا، چون از
            # بیرون فقط «بار پخش نمی‌شود» دیده می‌شود.
            if account is not None:
                account.sources = max(0, int(account.sources or 0) - 1)
            await db.delete(found)
            await db.commit()

    ready = await _usable()
    if not ready:
        raise NoAccount("هیچ اکانت سرویسِ سالمی در دسترس نیست")

    # کم‌بارترین. با تساوی، قدیمی‌ترین — تا انتخاب قابل پیش‌بینی بماند.
    ready.sort(key=lambda a: (a.sources, a.id))
    chosen = ready[0]

    async with get_session() as db:
        db.add(
            SourceLease(
                source_id=source_id,
                source_ref=source_ref[:128],
                account_id=chosen.id,
            )
        )
        account = await db.get(ServiceAccount, chosen.id)
        if account is not None:
            account.sources = int(account.sources or 0) + 1
        await db.commit()

    log.info("مبدأ %s به اکانت سرویس %s سپرده شد", source_id, chosen.id)
    return chosen


async def any_client():
    """کلاینتِ هر اکانتِ سالمی — برای کارهایی که به مبدأ خاصی بند نیستند.

    <b>چرا جدا از `lease`.</b> اجاره بر اساس آیدیِ مبدأ داده می‌شود، ولی
    برای <b>پیدا کردنِ</b> همان آیدی هم به یک کلاینت نیاز داریم. بدون
    این، ساختنِ هر کارِ تازه به مرغ و تخم‌مرغ می‌خورد.

    عمداً اجاره‌ای ثبت نمی‌کند: یک resolve، خواننده‌ی دائمیِ آن کانال
    نمی‌سازد.
    """
    ready = await _usable()
    if not ready:
        raise NoAccount("هیچ اکانت سرویسِ سالمی در دسترس نیست")
    ready.sort(key=lambda a: (a.sources, a.id))
    return await client_for(ready[0])


async def release(source_id: int) -> None:
    """اجاره را پس می‌گیرد — وقتی آخرین کارِ این مبدأ حذف شد."""
    async with get_session() as db:
        found = await db.scalar(
            select(SourceLease).where(SourceLease.source_id == source_id)
        )
        if found is None:
            return
        account = await db.get(ServiceAccount, found.account_id)
        if account is not None:
            account.sources = max(0, int(account.sources or 0) - 1)
        await db.delete(found)
        await db.commit()


async def mark_flood(account_id: int, seconds: int) -> None:
    """اکانت به محدودیت نرخ خورد؛ تا پایانش سراغش نمی‌رویم."""
    async with get_session() as db:
        account = await db.get(ServiceAccount, account_id)
        if account is None:
            return
        account.state = ServiceAccount.STATE_FLOOD
        account.quiet_until = utcnow() + timedelta(seconds=max(MIN_QUIET, int(seconds)))
        account.note = f"محدودیت نرخ: {int(seconds)} ثانیه"
        await db.commit()
    log.warning("اکانت سرویس %s به مدت %ss کنار گذاشته شد", account_id, seconds)


async def mark_banned(account_id: int, why: str = "") -> None:
    """<b>اکانت از دست رفت.</b>

    مبدأهایش را آزاد می‌کنیم تا دورِ بعد به اکانت دیگری سپرده شوند؛
    وگرنه هر کاری که رویش بود برای همیشه ساکت می‌ماند.
    """
    async with get_session() as db:
        account = await db.get(ServiceAccount, account_id)
        if account is not None:
            account.state = ServiceAccount.STATE_BANNED
            account.enabled = False
            account.note = (why or "تلگرام اکانت را بست")[:200]
            account.sources = 0
        for found in (
            await db.execute(
                select(SourceLease).where(SourceLease.account_id == account_id)
            )
        ).scalars():
            await db.delete(found)
        await db.commit()
    log.error("اکانت سرویس %s بسته شد: %s", account_id, why)


async def revive(account_id: int) -> None:
    """بعد از پایان محدودیت، اکانت دوباره به چرخه برمی‌گردد."""
    async with get_session() as db:
        account = await db.get(ServiceAccount, account_id)
        if account is None or account.state == ServiceAccount.STATE_BANNED:
            return
        account.state = ServiceAccount.STATE_OK
        account.quiet_until = None
        account.note = ""
        await db.commit()


# ------------------------------------------------------- کلاینتِ هر اکانت

_clients: dict[int, object] = {}


async def client_for(account: ServiceAccount):
    """کلاینتِ متصلِ این اکانت سرویس؛ یکی برای هر اکانت، ماندگار.

    <b>چرا کش می‌شود.</b> هر بار وصل شدن یعنی یک دست‌دادنِ کامل با
    تلگرام؛ با چند ده مبدأ و جاروی هر سه دقیقه، همان دست‌دادن‌ها خودشان
    به محدودیت نرخ می‌خورند.
    """
    existing = _clients.get(account.id)
    if existing is not None and getattr(existing, "is_connected", lambda: False)():
        return existing

    session = decrypt(account.session_enc or "")
    if not session:
        log.error("سشن اکانت سرویس %s قابل بازگشایی نیست", account.id)
        return None

    from telethon import TelegramClient
    from telethon.sessions import StringSession

    from telkap.config import get_settings

    settings = get_settings()
    client = TelegramClient(
        StringSession(session),
        settings.api_id,
        settings.api_hash,
        # این اکانت‌ها فقط می‌خوانند. آپدیت‌های لحظه‌ای برای کانالی که
        # عضوش نیستیم نمی‌آید، پس نگه داشتنِ حلقه‌ی آپدیت فقط هزینه‌ی
        # اتصال و ترافیک است.
        receive_updates=False,
    )
    await client.connect()
    if not await client.is_user_authorized():
        log.error("اکانت سرویس %s دیگر معتبر نیست", account.id)
        await client.disconnect()
        await mark_banned(account.id, "سشن دیگر معتبر نیست")
        return None

    _clients[account.id] = client
    async with get_session() as db:
        row = await db.get(ServiceAccount, account.id)
        if row is not None:
            row.last_used_at = utcnow()
            await db.commit()
    return client


async def disconnect_all() -> None:
    """هنگام خاموش شدن سرویس."""
    for account_id, client in list(_clients.items()):
        try:
            await client.disconnect()
        except Exception:
            log.debug("قطع اکانت سرویس %s نشد", account_id, exc_info=True)
    _clients.clear()
