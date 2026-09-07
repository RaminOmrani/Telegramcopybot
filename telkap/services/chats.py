"""فهرست کانال‌ها و گروه‌های اکانتِ متصلِ کاربر.

<b>چرا سرویس جدا و نه داخل هندلر.</b> هم ربات و هم مینی‌اپ به همین
فهرست نیاز دارند، با همان قواعد: فقط کانال و گروه، و برای مقصد فقط
جایی که واقعاً اجازه‌ی ارسال داریم. دو نسخه از این قواعد یعنی روزی
یکی‌شان عوض شود و دیگری نه — و کاربر در یکی کانالی را ببیند که در
دیگری نیست.
"""
from __future__ import annotations

import logging
import time

from telkap.services.userbot import manager

log = logging.getLogger(__name__)

MAX_DIALOGS = 200

# خواندن فهرست از تلگرام چند ثانیه طول می‌کشد. کش کوتاه است چون کانالِ
# تازه‌ساخته باید زود دیده شود؛ فقط جلوی خواندنِ دوباره در یک نشست را
# می‌گیرد.
CACHE_SECONDS = 120
_cache: dict[tuple[int, bool], tuple[float, list[dict]]] = {}


def _usable_as_destination(entity) -> bool:
    """آیا می‌شود در این چت پست گذاشت.

    کانالی که در آن ادمین نیستیم به درد مقصد نمی‌خورد و نشان دادنش
    فقط به بن‌بست می‌رسد: کاربر انتخابش می‌کند و بعد خطا می‌گیرد.
    """
    if getattr(entity, "left", False):
        return False
    if getattr(entity, "broadcast", False):
        return bool(
            getattr(entity, "creator", False) or getattr(entity, "admin_rights", None)
        )
    return True


async def load(user_id: int, *, writable_only: bool, fresh: bool = False):
    """فهرست چت‌ها، یا None اگر اکانتی متصل نیست."""
    key = (user_id, writable_only)
    if not fresh:
        cached = _cache.get(key)
        if cached and time.monotonic() - cached[0] < CACHE_SECONDS:
            return cached[1]

    client = await manager.ensure_client(user_id)
    if client is None:
        return None

    chats: list[dict] = []
    try:
        async for dialog in client.iter_dialogs(limit=MAX_DIALOGS):
            entity = dialog.entity
            # فقط کانال و گروه؛ چت خصوصی با افراد به درد نمی‌خورد
            if not (dialog.is_channel or dialog.is_group):
                continue
            if writable_only and not _usable_as_destination(entity):
                continue
            chats.append(
                {
                    "id": dialog.id,
                    "title": dialog.name or str(dialog.id),
                    "private": not getattr(entity, "username", None),
                    "channel": bool(getattr(entity, "broadcast", False)),
                    "username": getattr(entity, "username", "") or "",
                }
            )
    except Exception:
        log.exception("خواندن فهرست چت‌های کاربر %s ناموفق بود", user_id)
        return None

    _cache[key] = (time.monotonic(), chats)
    return chats


def forget(user_id: int) -> None:
    """کش این کاربر را دور می‌ریزد — بعد از اتصال یا قطع اکانت."""
    for key in [k for k in _cache if k[0] == user_id]:
        _cache.pop(key, None)
