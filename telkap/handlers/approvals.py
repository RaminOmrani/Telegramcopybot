"""صف تأیید: پست‌هایی که منتظر «آری» یا «نه» کاربر مانده‌اند.

هدف این است که تصمیم گرفتن سریع باشد — فهرست، یک نگاه، یک کلیک. برای
همین متن هر پست همان‌جا در فهرست هست و برای دیدن بیشترش لازم نیست جایی
برود.
"""
from __future__ import annotations

import logging

from aiogram import F, Router
from aiogram.types import CallbackQuery, InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder

from telkap.db import get_session, log_activity
from telkap.models import PendingPost, Task
from telkap.services import pending
from telkap.texts import fa_num

log = logging.getLogger(__name__)
router = Router(name="approvals")

# چند پست در هر صفحه؛ بیشتر از این، پیام تلگرام شلوغ می‌شود
PAGE = 6

_release_worker = None


def bind(worker) -> None:
    """کارگر انتشار را وصل می‌کند تا تأیید بتواند فوراً منتشر کند."""
    global _release_worker
    _release_worker = worker


async def _owned_task(user_id: int, task_id: int) -> Task | None:
    async with get_session() as db:
        task = await db.get(Task, task_id)
    return task if task is not None and task.user_id == user_id else None


def _entry(item: PendingPost, index: int) -> str:
    return f"<b>{fa_num(index)}.</b> {item.preview}"


async def _screen(user_id: int, task_id: int):
    items = await pending.listing(
        user_id,
        reason=PendingPost.REASON_APPROVAL,
        task_id=task_id,
        limit=PAGE,
    )
    total = await pending.waiting_count(
        user_id, reason=PendingPost.REASON_APPROVAL, task_id=task_id
    )

    kb = InlineKeyboardBuilder()
    if not items:
        kb.row(
            InlineKeyboardButton(text="🔙 بازگشت", callback_data=f"task:open:{task_id}")
        )
        return (
            "✅ <b>چیزی در انتظار تأیید نیست</b>\n\n"
            "هر پست تازه‌ای که برسد اینجا می‌آید و تا تأییدش نکنید منتشر "
            "نمی‌شود.",
            kb,
        )

    lines = [f"⏳ <b>{fa_num(total)} پست در انتظار تأیید</b>\n"]
    for number, item in enumerate(items, start=1):
        lines.append(_entry(item, number))
        kb.row(
            InlineKeyboardButton(
                text=f"✅ {fa_num(number)}", callback_data=f"pend:ok:{item.id}"
            ),
            InlineKeyboardButton(
                text=f"❌ {fa_num(number)}", callback_data=f"pend:no:{item.id}"
            ),
        )

    if total > 1:
        kb.row(
            InlineKeyboardButton(
                text="✅ تأیید همه", callback_data=f"pend:allok:{task_id}"
            ),
            InlineKeyboardButton(
                text="🗑 رد همه", callback_data=f"pend:allno:{task_id}"
            ),
        )
    kb.row(InlineKeyboardButton(text="🔙 بازگشت", callback_data=f"task:open:{task_id}"))
    if total > PAGE:
        lines.append(
            f"\n<i>{fa_num(total - PAGE)} پست دیگر هم در صف است؛ بعد از "
            "رسیدگی به این‌ها نمایش داده می‌شوند.</i>"
        )
    return "\n\n".join(lines), kb


async def _refresh(call: CallbackQuery, task_id: int) -> None:
    text, kb = await _screen(call.from_user.id, task_id)
    try:
        await call.message.edit_text(text, reply_markup=kb.as_markup())
    except Exception:
        await call.message.answer(text, reply_markup=kb.as_markup())


@router.callback_query(F.data.startswith("pend:list:"))
async def cb_list(call: CallbackQuery) -> None:
    task_id = int(call.data.split(":")[2])
    if await _owned_task(call.from_user.id, task_id) is None:
        await call.answer("دسترسی ندارید", show_alert=True)
        return
    await call.answer()
    await _refresh(call, task_id)


@router.callback_query(F.data.startswith("pend:ok:"))
async def cb_approve(call: CallbackQuery) -> None:
    item = await pending.get(int(call.data.split(":")[2]), call.from_user.id)
    if item is None:
        await call.answer("این پست دیگر در صف نیست", show_alert=True)
        return

    task_id = item.task_id
    if _release_worker is None:
        await call.answer("موتور انتشار آماده نیست؛ کمی بعد دوباره امتحان کنید", show_alert=True)
        return

    await call.answer("در حال انتشار…")
    published = await _release_worker.release(item)
    await log_activity(
        user_id=call.from_user.id,
        task_id=task_id,
        event="approved",
        detail=item.preview[:120],
    )
    if not published:
        await call.message.answer(
            "⚠️ این پست منتشر نشد.\n\n"
            "معمولاً یعنی در کانال مبدا حذف شده، یا فیلترهای همین کار "
            "جلویش را گرفته‌اند. در «🧾 گزارش فعالیت» دلیلش هست."
        )
    await _refresh(call, task_id)


@router.callback_query(F.data.startswith("pend:no:"))
async def cb_reject(call: CallbackQuery) -> None:
    item = await pending.get(int(call.data.split(":")[2]), call.from_user.id)
    if item is None:
        await call.answer("این پست دیگر در صف نیست", show_alert=True)
        return
    task_id = item.task_id
    await pending.drop(item.id)
    await log_activity(
        user_id=call.from_user.id,
        task_id=task_id,
        event="rejected",
        detail=item.preview[:120],
    )
    await call.answer("رد شد")
    await _refresh(call, task_id)


@router.callback_query(F.data.startswith("pend:allok:"))
async def cb_approve_all(call: CallbackQuery) -> None:
    task_id = int(call.data.split(":")[2])
    if await _owned_task(call.from_user.id, task_id) is None:
        await call.answer("دسترسی ندارید", show_alert=True)
        return
    if _release_worker is None:
        await call.answer("موتور انتشار آماده نیست", show_alert=True)
        return

    await call.answer("در حال انتشار همه…")
    items = await pending.listing(
        call.from_user.id,
        reason=PendingPost.REASON_APPROVAL,
        task_id=task_id,
        limit=50,
    )
    published = 0
    for item in items:
        try:
            if await _release_worker.release(item):
                published += 1
        except Exception:
            log.exception("انتشار پست %s در تأیید گروهی ناموفق بود", item.id)

    await log_activity(
        user_id=call.from_user.id,
        task_id=task_id,
        event="approved_bulk",
        detail=f"{published} از {len(items)} پست منتشر شد",
    )
    await call.message.answer(
        f"✅ {fa_num(published)} پست از {fa_num(len(items))} پست صف منتشر شد."
        + (
            "\n\n<i>بقیه منتشر نشدند — یا در مبدا حذف شده بودند یا فیلترها "
            "جلویشان را گرفت.</i>"
            if published < len(items)
            else ""
        )
    )
    await _refresh(call, task_id)


@router.callback_query(F.data.startswith("pend:allno:"))
async def cb_reject_all(call: CallbackQuery) -> None:
    task_id = int(call.data.split(":")[2])
    if await _owned_task(call.from_user.id, task_id) is None:
        await call.answer("دسترسی ندارید", show_alert=True)
        return
    cleared = await pending.drop_task(
        task_id, reason=PendingPost.REASON_APPROVAL
    )
    await log_activity(
        user_id=call.from_user.id,
        task_id=task_id,
        event="rejected_bulk",
        detail=f"{cleared} پست رد شد",
    )
    await call.answer(f"{cleared} پست رد شد")
    await _refresh(call, task_id)
