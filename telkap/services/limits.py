"""سقف‌ها و قابلیت‌های اختصاصیِ هر کاربر.

ادمین می‌تواند برای یک کاربر خاص هر سقفی را کم و زیاد کند یا هر قابلیتی را
روشن و خاموش کند، بدون اینکه طرحش عوض شود یا بقیه‌ی کاربران آن طرح تأثیر
بگیرند. مقادیر در `users.limits` ذخیره می‌شوند و روی طرح فعال سوار می‌گردند.

قرارداد کلیدها:
  «period_messages» و مانند آن  → مقدار عددی جایگزین سقف طرح
  «feat:watermark» و مانند آن   → True یعنی روشن، False یعنی خاموش

کلیدی که وجود نداشته باشد یعنی «همان مقدار طرح».
"""
from __future__ import annotations

from dataclasses import replace

from telkap.db import get_session, log_activity
from telkap.models import User
from telkap.plans import Plan, quota_label
from telkap.services.planstore import (
    FEATURE_FIELDS,
    NUMERIC_FIELDS,
    feature_label,
    field_spec,
)

FEATURE_PREFIX = "feat:"

# فقط سقف‌هایی که برای یک کاربر خاص معنی دارند (قیمت و مدت، سطح طرح‌اند)
USER_FIELDS = tuple(spec for spec in NUMERIC_FIELDS if spec.per_user)


def feature_key(code: str) -> str:
    return f"{FEATURE_PREFIX}{code}"


def apply(plan: Plan | None, limits: dict | None) -> Plan | None:
    """سقف‌ها و قابلیت‌های اختصاصی را روی طرح کاربر سوار می‌کند."""
    if plan is None or not limits:
        return plan

    changes: dict = {}
    features = set(plan.features)
    touched_features = False

    for key, raw in limits.items():
        if key.startswith(FEATURE_PREFIX):
            code = key[len(FEATURE_PREFIX):]
            if not any(code == item for item, _ in FEATURE_FIELDS):
                continue
            touched_features = True
            if raw:
                features.add(code)
            else:
                features.discard(code)
            continue
        spec = field_spec(key)
        if spec is None or not spec.per_user:
            continue
        value = spec.clean(raw)
        if value is not None:
            changes[key] = value

    if touched_features:
        changes["features"] = frozenset(features)
    if not changes:
        return plan
    return replace(plan, **changes)


async def get(user_id: int) -> dict:
    async with get_session() as db:
        user = await db.get(User, user_id)
        return dict(user.limits or {}) if user is not None else {}


async def _write(user_id: int, limits: dict) -> bool:
    async with get_session() as db:
        user = await db.get(User, user_id)
        if user is None:
            return False
        # جایگزینی کامل دیکشنری لازم است تا SQLAlchemy تغییر JSON را ببیند
        user.limits = limits
        await db.commit()

    from telkap.services import cache

    cache.invalidate_user(user_id)
    return True


async def set_value(
    user_id: int, key: str, value, *, admin_id: int | None = None
) -> bool:
    """یک سقف عددی اختصاصی برای کاربر تعیین می‌کند."""
    spec = field_spec(key)
    if spec is None or not spec.per_user:
        return False
    cleaned = spec.clean(value)
    if cleaned is None:
        return False
    limits = await get(user_id)
    limits[key] = cleaned
    if not await _write(user_id, limits):
        return False
    await log_activity(
        user_id=user_id,
        event="limit_set",
        detail=f"{key} = {cleaned} توسط ادمین {admin_id or '—'}",
    )
    return True


async def set_feature(
    user_id: int, code: str, enabled: bool, *, admin_id: int | None = None
) -> bool:
    """یک قابلیت را برای کاربر روشن یا خاموش می‌کند."""
    if not any(code == item for item, _ in FEATURE_FIELDS):
        return False
    limits = await get(user_id)
    limits[feature_key(code)] = bool(enabled)
    if not await _write(user_id, limits):
        return False
    await log_activity(
        user_id=user_id,
        event="limit_feature",
        detail=f"{code} = {'روشن' if enabled else 'خاموش'} توسط ادمین {admin_id or '—'}",
    )
    return True


async def clear(user_id: int, key: str, *, admin_id: int | None = None) -> bool:
    """یک تغییر اختصاصی را برمی‌دارد تا مقدار طرح دوباره حاکم شود."""
    limits = await get(user_id)
    if key not in limits:
        return False
    limits.pop(key)
    if not await _write(user_id, limits):
        return False
    await log_activity(
        user_id=user_id,
        event="limit_clear",
        detail=f"{key} توسط ادمین {admin_id or '—'} به حالت طرح برگشت",
    )
    return True


async def clear_all(user_id: int, *, admin_id: int | None = None) -> bool:
    limits = await get(user_id)
    if not limits:
        return False
    if not await _write(user_id, {}):
        return False
    await log_activity(
        user_id=user_id,
        event="limit_clear",
        detail=f"همه‌ی تغییرهای اختصاصی توسط ادمین {admin_id or '—'} برداشته شد",
    )
    return True


def describe(plan: Plan | None, limits: dict | None) -> list[str]:
    """خلاصه‌ی خوانا از تفاوت وضعیت کاربر با طرحش."""
    if plan is None or not limits:
        return []
    lines: list[str] = []
    for spec in USER_FIELDS:
        if spec.key not in limits:
            continue
        base = getattr(plan, spec.key, None)
        value = spec.clean(limits[spec.key])
        if value is None:
            continue
        lines.append(f"{spec.label}: {quota_label(base)} ← <b>{quota_label(value)}</b>")
    for code, _label in FEATURE_FIELDS:
        key = feature_key(code)
        if key not in limits:
            continue
        state = "روشن ✅" if limits[key] else "خاموش ⛔️"
        lines.append(f"{feature_label(code)}: <b>{state}</b>")
    return lines
