"""طرح‌ها و قیمت‌های قابل ویرایش از پنل ادمین.

مقادیر کارخانه‌ای در `plans.py` می‌مانند و کد به آن‌ها متکی است. هرچه ادمین
از پنل عوض کند در جدول `plan_overrides` ذخیره و روی همان پیش‌فرض‌ها سوار
می‌شود؛ پس «بازگردانی به پیش‌فرض» فقط پاک کردن یک ردیف است و هیچ مقداری
در دیتابیس تکرار نمی‌شود.

هنگام بالا آمدن ربات `load()` صدا زده می‌شود و `plans.PLANS` را با نسخه‌ی
مؤثر پر می‌کند، پس همه‌ی مصرف‌کننده‌ها بدون تغییر، مقدار تازه را می‌بینند.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, replace

from sqlalchemy import select

from telkap import plans
from telkap.db import get_session, log_activity
from telkap.models import AppSetting, PlanOverride, utcnow
from telkap.plans import (
    CREDIT_KINDS,
    FEAT_HISTORY,
    FEAT_PRIVATE,
    FEAT_PUBLIC,
    FEAT_VIP,
    FEAT_WATERMARK,
    UNLIMITED,
    Plan,
)

log = logging.getLogger(__name__)

_CREDIT_KEY = "credit_unit"      # کلید AppSetting برای قیمت هر واحد اعتبار


@dataclass(frozen=True, slots=True)
class FieldSpec:
    """یک مقدار قابل ویرایش، به‌همراه محدوده‌ی مجاز و نحوه‌ی نمایش."""

    key: str
    label: str
    kind: str                    # int | money | text | quota
    minimum: int = 0
    maximum: int = 10_000_000
    hint: str = ""
    per_user: bool = True        # آیا برای یک کاربر خاص هم قابل تغییر است؟

    @property
    def allows_unlimited(self) -> bool:
        return self.kind == "quota"

    def clean(self, value):
        """مقدار ورودی را به شکل معتبر درمی‌آورد یا None می‌دهد."""
        if self.kind == "text":
            text = str(value).strip()
            return text[:120] or None
        try:
            number = int(value)
        except (TypeError, ValueError):
            return None
        if number < 0:
            # فقط سهمیه‌ها می‌توانند «نامحدود» باشند
            return UNLIMITED if self.allows_unlimited else None
        return max(self.minimum, min(number, self.maximum))


# سقف‌های عددی. `quota` یعنی عدد منفی هم مجاز است و معنی‌اش «نامحدود» است.
NUMERIC_FIELDS: tuple[FieldSpec, ...] = (
    FieldSpec("period_messages", "📨 سقف پیام دوره", "quota", 0, 10_000_000,
              "کل پیام‌های قابل کپی در تمام دوره. منفی = نامحدود."),
    FieldSpec("watermark_quota", "💧 سهمیه‌ی واترمارک", "quota", 0, 1_000_000,
              "چند تصویر در کل دوره واترمارک بخورد. ۰ = این قابلیت را ندارد."),
    FieldSpec("history_quota", "🕓 سهمیه‌ی پیام گذشته", "quota", 0, 1_000_000,
              "چند پیام قدیمی در کل دوره کپی شود. ۰ = این قابلیت را ندارد."),
    FieldSpec("max_tasks", "📋 تعداد کار کپی", "int", 1, 10_000),
    FieldSpec("max_destinations", "📤 مقصد در هر کار", "int", 1, 1_000),
    FieldSpec("fair_use_daily", "⚖️ سقف منصفانه‌ی روزانه", "int", 0, 10_000_000,
              "فقط برای طرح نامحدود؛ ۰ یعنی بدون سقف روزانه."),
)

# مقادیر فروش که فقط سطح طرح‌اند و برای یک کاربر خاص معنی ندارند
PLAN_ONLY_FIELDS: tuple[FieldSpec, ...] = (
    FieldSpec("price_toman", "💰 قیمت", "money", 0, 1_000_000_000, per_user=False),
    FieldSpec("days", "📅 مدت (روز)", "int", 1, 3_650, per_user=False),
    FieldSpec("title", "🏷 عنوان", "text", per_user=False),
    FieldSpec("tagline", "💬 توضیح کوتاه", "text", per_user=False),
)

# قابلیت‌های روشن/خاموش‌شدنی
FEATURE_FIELDS: tuple[tuple[str, str], ...] = (
    (FEAT_PUBLIC, "🌐 کپی از کانال عمومی"),
    (FEAT_PRIVATE, "🔒 کپی از کانال خصوصی"),
    (FEAT_WATERMARK, "💧 واترمارک"),
    (FEAT_HISTORY, "🕓 کپی پیام‌های گذشته"),
    (FEAT_VIP, "⭐️ پشتیبانی ویژه"),
)

ALL_FIELDS: tuple[FieldSpec, ...] = NUMERIC_FIELDS + PLAN_ONLY_FIELDS
_BY_KEY = {spec.key: spec for spec in ALL_FIELDS}
_FEATURE_LABELS = dict(FEATURE_FIELDS)


def field_spec(key: str) -> FieldSpec | None:
    return _BY_KEY.get(key)


def feature_label(code: str) -> str:
    return _FEATURE_LABELS.get(code, code)


# ------------------------------------------------------- ساخت طرح مؤثر
def apply_overrides(base: Plan, data: dict) -> Plan:
    """تغییرهای ذخیره‌شده را روی یک طرح پایه سوار می‌کند."""
    if not data:
        return base
    changes: dict = {}
    for key, raw in data.items():
        if key == "features":
            if isinstance(raw, list | tuple | set):
                changes["features"] = frozenset(str(item) for item in raw)
            continue
        spec = _BY_KEY.get(key)
        if spec is None:
            continue                      # کلید ناشناخته نادیده گرفته می‌شود
        value = spec.clean(raw)
        if value is not None:
            changes[key] = value
    if not changes:
        return base
    return replace(base, **changes)


def _rebuild(overrides: dict[str, dict]) -> None:
    """`plans.PLANS` را از پیش‌فرض‌ها + تغییرها می‌سازد."""
    plans.PLANS.clear()
    for code, base in plans.DEFAULT_PLANS.items():
        plans.PLANS[code] = apply_overrides(base, overrides.get(code, {}))


def _apply_credit_units(stored: dict | None) -> None:
    plans.CREDIT_UNITS.clear()
    for kind, info in CREDIT_KINDS.items():
        value = (stored or {}).get(kind)
        try:
            price = int(value)
        except (TypeError, ValueError):
            price = info[2]
        plans.CREDIT_UNITS[kind] = max(0, price)


# ------------------------------------------------------------ خواندن
async def _read_overrides() -> dict[str, dict]:
    async with get_session() as db:
        rows = await db.execute(select(PlanOverride))
        return {row.code: dict(row.data or {}) for row in rows.scalars()}


async def _read_setting(key: str):
    async with get_session() as db:
        row = await db.get(AppSetting, key)
        return row.value if row is not None else None


async def load() -> None:
    """طرح‌ها و قیمت‌ها را از دیتابیس می‌خواند و در حافظه می‌نشاند."""
    try:
        _rebuild(await _read_overrides())
        _apply_credit_units(await _read_setting(_CREDIT_KEY))
    except Exception:
        # اگر خواندن شکست بخورد، پیش‌فرض‌های کد سر جایشان می‌مانند
        log.exception("خواندن تنظیمات طرح‌ها ناموفق بود؛ مقادیر پیش‌فرض به کار می‌روند")


async def customized_codes() -> set[str]:
    """کد طرح‌هایی که از پیش‌فرض فاصله گرفته‌اند."""
    return {code for code, data in (await _read_overrides()).items() if data}


# ------------------------------------------------------------ نوشتن
async def _store(code: str, data: dict, admin_id: int | None) -> None:
    async with get_session() as db:
        row = await db.get(PlanOverride, code)
        if row is None:
            row = PlanOverride(code=code)
            db.add(row)
        row.data = data
        row.updated_by = admin_id
        row.updated_at = utcnow()
        await db.commit()


def _invalidate() -> None:
    """کش اشتراک‌ها شیء Plan را نگه می‌دارد، پس باید دور ریخته شود."""
    from telkap.services import cache

    cache.clear()


async def set_field(
    code: str, key: str, value, *, admin_id: int | None = None
) -> Plan | None:
    """یک مقدار طرح را تغییر می‌دهد و طرح تازه را برمی‌گرداند."""
    base = plans.DEFAULT_PLANS.get(code)
    spec = _BY_KEY.get(key)
    if base is None or spec is None:
        return None
    cleaned = spec.clean(value)
    if cleaned is None:
        return None

    overrides = await _read_overrides()
    data = overrides.get(code, {})
    if cleaned == getattr(base, key):
        data.pop(key, None)          # برابر پیش‌فرض شد، دیگر override لازم نیست
    else:
        data[key] = cleaned
    await _store(code, data, admin_id)
    overrides[code] = data
    _rebuild(overrides)
    _invalidate()
    await log_activity(
        user_id=admin_id,
        event="plan_edit",
        detail=f"{code}.{key} = {cleaned}",
    )
    return plans.PLANS.get(code)


async def toggle_feature(
    code: str, feature: str, *, admin_id: int | None = None
) -> Plan | None:
    """یک قابلیت طرح را روشن یا خاموش می‌کند."""
    base = plans.DEFAULT_PLANS.get(code)
    if base is None or feature not in _FEATURE_LABELS:
        return None

    current = plans.PLANS.get(code) or base
    features = set(current.features)
    features.symmetric_difference_update({feature})

    overrides = await _read_overrides()
    data = overrides.get(code, {})
    if features == set(base.features):
        data.pop("features", None)
    else:
        data["features"] = sorted(features)
    await _store(code, data, admin_id)
    overrides[code] = data
    _rebuild(overrides)
    _invalidate()
    await log_activity(
        user_id=admin_id,
        event="plan_edit",
        detail=f"{code}.{feature} = {'روشن' if feature in features else 'خاموش'}",
    )
    return plans.PLANS.get(code)


async def reset(code: str, *, admin_id: int | None = None) -> Plan | None:
    """طرح را به مقادیر کارخانه برمی‌گرداند."""
    if code not in plans.DEFAULT_PLANS:
        return None
    async with get_session() as db:
        row = await db.get(PlanOverride, code)
        if row is not None:
            await db.delete(row)
            await db.commit()
    overrides = await _read_overrides()
    _rebuild(overrides)
    _invalidate()
    await log_activity(user_id=admin_id, event="plan_reset", detail=code)
    return plans.PLANS.get(code)


async def set_credit_unit(kind: str, value, *, admin_id: int | None = None) -> int | None:
    """قیمت هر واحد از یک نوع اعتبار را تغییر می‌دهد."""
    if kind not in CREDIT_KINDS:
        return None
    try:
        price = int(value)
    except (TypeError, ValueError):
        return None
    price = max(0, min(price, 100_000_000))

    stored = dict(await _read_setting(_CREDIT_KEY) or {})
    stored[kind] = price
    async with get_session() as db:
        row = await db.get(AppSetting, _CREDIT_KEY)
        if row is None:
            row = AppSetting(key=_CREDIT_KEY)
            db.add(row)
        row.value = stored
        row.updated_by = admin_id
        row.updated_at = utcnow()
        await db.commit()
    _apply_credit_units(stored)
    await log_activity(
        user_id=admin_id, event="credit_price", detail=f"{kind} = {price}"
    )
    return price
