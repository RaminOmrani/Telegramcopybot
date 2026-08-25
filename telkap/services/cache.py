"""کش تنظیمات کارها و اشتراک‌ها.

مسیر پردازش هر پست به تنظیمات کار، قواعد و پلن کاربر نیاز دارد. بدون کش،
هر پست چندین پرس‌وجوی دیتابیس می‌زند و در ترافیک بالا این سنگین‌ترین بار
سیستم می‌شود. اینجا یک عکس فوری از هر کار نگه داشته می‌شود که هنگام تغییر
تنظیمات باطل می‌گردد.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select

from telkap.db import get_session
from telkap.models import Destination, Rule, Task
from telkap.plans import Plan
from telkap.services.defaults import merged_settings
from telkap.services.subscription import active_entitlement

log = logging.getLogger(__name__)

# اشتراک هر ۲ دقیقه دوباره خوانده می‌شود؛ انقضا حداکثر با همین تأخیر اثر می‌کند
PLAN_TTL_SECONDS = 120


@dataclass(slots=True)
class RuleSnapshot:
    """کپی سبک یک قاعده، مستقل از نشست دیتابیس."""

    kind: str
    pattern: str
    replacement: str
    enabled: bool


@dataclass(slots=True)
class TargetSpec:
    """یک مقصد به‌همراه تنظیمات اختصاصی‌اش."""

    target: str | int
    overrides: dict[str, Any] = field(default_factory=dict)

    @property
    def key(self) -> str:
        return str(self.target)


@dataclass(slots=True)
class TaskSnapshot:
    """همه‌ی چیزی که موتور کپی برای پردازش یک پست لازم دارد."""

    id: int
    user_id: int
    title: str
    enabled: bool
    source_id: int | None
    source_ref: str
    targets: list[TargetSpec]
    cfg: dict[str, Any]
    rules: list[RuleSnapshot] = field(default_factory=list)


@dataclass(slots=True)
class Entitlement:
    """طرح فعال کاربر به‌همراه شناسه‌ی اشتراکی که سهمیه‌ها به آن گره خورده‌اند."""

    plan: Plan | None
    subscription_id: int | None = None


_tasks: dict[int, TaskSnapshot] = {}
_plans: dict[int, tuple[float, Entitlement]] = {}


async def get_task(task_id: int) -> TaskSnapshot | None:
    """عکس فوری کار را برمی‌گرداند؛ در صورت نبود، از دیتابیس می‌سازد."""
    snapshot = _tasks.get(task_id)
    if snapshot is not None:
        return snapshot

    async with get_session() as db:
        task = await db.get(Task, task_id)
        if task is None:
            return None
        rules = list((await db.execute(select(Rule).where(Rule.task_id == task_id))).scalars())
        extra = await db.execute(
            select(Destination).where(
                Destination.task_id == task_id, Destination.enabled.is_(True)
            )
        )
        # مقصد اصلی تنظیمات اختصاصی ندارد؛ همان تنظیمات کار را می‌گیرد
        targets: list[TargetSpec] = [TargetSpec(task.dest_id or task.dest_ref)]
        targets.extend(
            TargetSpec(dest.chat_id or dest.ref, dict(dest.overrides or {}))
            for dest in extra.scalars()
        )

        snapshot = TaskSnapshot(
            id=task.id,
            user_id=task.user_id,
            title=task.title or str(task.id),
            enabled=task.enabled,
            source_id=task.source_id,
            source_ref=task.source_ref,
            targets=targets,
            cfg=merged_settings(task.settings),
            rules=[
                RuleSnapshot(
                    kind=r.kind,
                    pattern=r.pattern,
                    replacement=r.replacement,
                    enabled=r.enabled,
                )
                for r in rules
            ],
        )

    _tasks[task_id] = snapshot
    return snapshot


def invalidate_task(task_id: int) -> None:
    """پس از هر تغییر در تنظیمات، قواعد یا مقصدهای یک کار صدا زده می‌شود."""
    _tasks.pop(task_id, None)


def invalidate_user(user_id: int) -> None:
    """کش پلن کاربر و همه‌ی کارهایش را پاک می‌کند."""
    _plans.pop(user_id, None)
    for task_id, snapshot in list(_tasks.items()):
        if snapshot.user_id == user_id:
            _tasks.pop(task_id, None)


def clear() -> None:
    _tasks.clear()
    _plans.clear()


async def get_entitlement(user_id: int) -> Entitlement:
    """طرح فعال و شناسه‌ی اشتراک کاربر، با کش کوتاه‌مدت."""
    cached = _plans.get(user_id)
    now = time.monotonic()
    if cached is not None and now - cached[0] < PLAN_TTL_SECONDS:
        return cached[1]
    plan, sub_id = await active_entitlement(user_id)
    entry = Entitlement(plan, sub_id)
    _plans[user_id] = (now, entry)
    return entry


async def get_plan(user_id: int) -> Plan | None:
    """فقط طرح فعال کاربر (برای جاهایی که شناسه‌ی اشتراک لازم نیست)."""
    return (await get_entitlement(user_id)).plan


def stats() -> dict[str, int]:
    return {"tasks": len(_tasks), "plans": len(_plans)}
