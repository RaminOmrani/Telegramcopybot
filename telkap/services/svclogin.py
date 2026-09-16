"""وارد کردنِ اکانت‌های سرویس — با همان کد QR.

<b>چرا جدا از ورودِ مشتری‌ها.</b> ظاهرِ هر دو یکی است (یک QR، یک اسکن)
ولی مقصدشان نه: آنجا سشن روی ردیفِ کاربر می‌نشیند و بعدش یک رانتایمِ
کامل با هندلرهای آپدیت ساخته می‌شود. اکانت سرویس هیچ‌کدام را نمی‌خواهد
— نه کاربری دارد، نه آپدیتی می‌گیرد (کانالی که عضوش نیستیم آپدیت
نمی‌فرستد). چپاندنِ هر دو در یک مسیر یعنی هر تغییرِ آینده باید هر بار
بپرسد «این کدامشان است»، و همان‌جاست که یکی‌شان بی‌صدا خراب می‌شود.

<b>و چرا اینجا هم QR.</b> اکانت سرویس مالِ خودمان است و کدِ ورودش هم
دستِ خودمان — ولی ورود با کد یعنی کدِ پیامکی باید جایی تایپ شود، و
همان تایپ کردن در کنسول VNC یا تلگرام، همان کد را در جایی می‌گذارد که
لازم نبود باشد.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

from telethon.errors import SessionPasswordNeededError

from telkap.crypto import encrypt
from telkap.db import get_session
from telkap.models import ServiceAccount

log = logging.getLogger(__name__)


class LoginError(Exception):
    pass


@dataclass
class Pending:
    client: object
    qr: object | None = None
    label: str = ""
    needs_password: bool = False


# کلید، آیدیِ ادمینی است که دارد اکانت را اضافه می‌کند — نه خودِ اکانت،
# چون هنوز اکانتی وجود ندارد.
_pending: dict[int, Pending] = {}


def _new_client():
    from telethon import TelegramClient
    from telethon.sessions import StringSession

    from telkap.config import get_settings

    settings = get_settings()
    return TelegramClient(
        StringSession(), settings.api_id, settings.api_hash, receive_updates=False
    )


def pending(admin_id: int) -> Pending | None:
    return _pending.get(admin_id)


async def cancel(admin_id: int) -> None:
    found = _pending.pop(admin_id, None)
    if found is not None:
        try:
            await found.client.disconnect()
        except Exception:
            log.debug("قطع کلاینتِ نیمه‌کاره نشد", exc_info=True)


async def start(admin_id: int, label: str = "") -> str:
    """ورود اکانت سرویس را شروع می‌کند و نشانیِ QR را برمی‌گرداند."""
    await cancel(admin_id)
    client = _new_client()
    await client.connect()
    try:
        qr = await client.qr_login()
    except Exception as exc:
        await client.disconnect()
        raise LoginError(f"ساخت کد QR ناموفق بود: {exc}") from exc
    _pending[admin_id] = Pending(client=client, qr=qr, label=label)
    return qr.url


async def refresh(admin_id: int) -> str | None:
    found = _pending.get(admin_id)
    if not found or found.qr is None:
        return None
    try:
        await found.qr.recreate()
    except Exception:
        log.debug("تازه‌سازی QR اکانت سرویس نشد", exc_info=True)
        return None
    return found.qr.url


async def result(admin_id: int, timeout: float) -> str:
    """«done» · «password» · «wait» · «gone»"""
    found = _pending.get(admin_id)
    if not found or found.qr is None:
        return "gone"
    try:
        await found.qr.wait(timeout)
    except SessionPasswordNeededError:
        found.needs_password = True
        return "password"
    except TimeoutError:
        return "wait"
    except Exception:
        log.debug("انتظار QR اکانت سرویس تمام شد", exc_info=True)
        return "wait"
    await _finish(admin_id, found)
    return "done"


async def submit_password(admin_id: int, password: str) -> None:
    found = _pending.get(admin_id)
    if not found:
        raise LoginError("جریان ورود منقضی شده است.")
    try:
        await found.client.sign_in(password=password)
    except Exception as exc:
        raise LoginError(f"رمز پذیرفته نشد: {exc}") from exc
    await _finish(admin_id, found)


async def _finish(admin_id: int, found: Pending) -> int:
    me = await found.client.get_me()
    session_string = found.client.session.save()

    async with get_session() as db:
        # <b>اکانتِ تکراری ردیفِ تازه نمی‌سازد.</b> دو ردیف برای یک
        # اکانت یعنی استخر فکر می‌کند دو خواننده دارد و بارِ دو برابر
        # روی همان یک اکانت می‌ریزد — دقیقاً راهِ رسیدن به محدودیتِ
        # نرخ، از دلِ کاری که قرار بود جلویش را بگیرد.
        from sqlalchemy import select

        account = await db.scalar(
            select(ServiceAccount).where(ServiceAccount.account_id == me.id)
        )
        if account is None:
            account = ServiceAccount()
            db.add(account)

        account.session_enc = encrypt(session_string)
        account.account_id = me.id
        account.account_name = (
            " ".join(filter(None, [me.first_name, me.last_name])) or me.username or ""
        )
        account.phone = getattr(me, "phone", "") or ""
        account.label = found.label or account.label or (me.username or str(me.id))
        account.enabled = True
        account.state = ServiceAccount.STATE_OK
        account.note = ""
        account.quiet_until = None
        await db.commit()
        await db.refresh(account)
        account_id = account.id

    _pending.pop(admin_id, None)
    # کلاینت را نگه نمی‌داریم: استخر خودش هر وقت لازم شد از روی سشن
    # وصل می‌شود، و دو کلاینتِ همزمان روی یک سشن دردسرِ بی‌دلیل است.
    try:
        await found.client.disconnect()
    except Exception:
        log.debug("قطع کلاینتِ ورود نشد", exc_info=True)

    log.info("اکانت سرویس %s (%s) اضافه شد", account_id, me.id)
    return account_id
