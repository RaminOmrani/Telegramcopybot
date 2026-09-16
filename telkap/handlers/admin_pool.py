"""اکانت‌های سرویس: خواننده‌های مبدأ عمومی در حالت ساده.

<b>چرا این صفحه لازم است.</b> در حالت ساده مشتری هیچ اکانتی وصل
نمی‌کند؛ مبدأ عمومی را اکانت‌های <b>ما</b> می‌خوانند. یعنی این اکانت‌ها
زیرساخت‌اند، نه یک تنظیم: اگر همه‌شان بخوابند، هر کارِ ساده‌ای ساکت
می‌شود — و چون هیچ خطایی هم سمت مشتری دیده نمی‌شود، از بیرون شبیه
«سرویس کار نمی‌کند» است.

پس باید یک جا باشد که <b>بی هیچ حدسی</b> بگوید چند خواننده داریم، هرکدام
چه حالی دارند، و چقدر ظرفیت مانده.
"""
from __future__ import annotations

import asyncio
import logging
import time

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import (
    BufferedInputFile,
    CallbackQuery,
    InlineKeyboardButton,
    Message,
)
from aiogram.utils.keyboard import InlineKeyboardBuilder
from sqlalchemy import select

from telkap.db import get_session, log_activity
from telkap.handlers.account import _qr_png
from telkap.handlers.admin_reports import guard
from telkap.handlers.common import Flow
from telkap.keyboards import DANGER, GO
from telkap.models import ServiceAccount
from telkap.services import pool, roles, svclogin
from telkap.texts import fa_num

log = logging.getLogger(__name__)
router = Router(name="admin-pool")

QR_STEP_SECONDS = 25
QR_TOTAL_SECONDS = 180

STATE_LABELS = {
    ServiceAccount.STATE_OK: "✅ سالم",
    ServiceAccount.STATE_FLOOD: "⏳ محدودیت نرخ",
    ServiceAccount.STATE_BANNED: "⛔️ بسته شده",
    ServiceAccount.STATE_OFF: "⏸ خاموش",
}


async def _overview() -> tuple[str, InlineKeyboardBuilder]:
    async with get_session() as db:
        accounts = list(
            (
                await db.execute(select(ServiceAccount).order_by(ServiceAccount.id))
            ).scalars()
        )
    used, total = await pool.capacity()

    lines = ["👥 <b>اکانت‌های سرویس</b>", ""]
    if not accounts:
        lines.append(
            "هنوز هیچ اکانتی اضافه نشده.\n\n"
            "بدون این اکانت‌ها، <b>حالت ساده کار نمی‌کند</b> — چون کسی "
            "نیست که کانال‌های عمومی را بخواند. حالت کامل (با اکانت خودِ "
            "مشتری) مثل همیشه کار می‌کند."
        )
    else:
        for account in accounts:
            mark = STATE_LABELS.get(account.state, account.state)
            if not account.enabled and account.state != ServiceAccount.STATE_BANNED:
                mark = STATE_LABELS[ServiceAccount.STATE_OFF]
            name = account.label or account.account_name or f"#{account.id}"
            lines.append(
                f"<code>{account.id}</code> · {name} — {mark}\n"
                f"    مبدأها: {fa_num(account.sources)}"
                + (f"\n    <i>{account.note}</i>" if account.note else "")
            )
        lines.append("")
        lines.append(
            f"ظرفیت: {fa_num(used)} از {fa_num(total)} مبدأ"
        )
        if total and used >= total * 0.8:
            lines.append(
                "⚠️ ظرفیت رو به پر شدن است. یک اکانت دیگر اضافه کنید "
                "پیش از اینکه کارِ تازه‌ای رد شود."
            )

    kb = InlineKeyboardBuilder()
    kb.row(InlineKeyboardButton(text="➕ افزودن اکانت", callback_data="pool:add", style=GO))
    for account in accounts:
        name = account.label or f"#{account.id}"
        toggle = "⏸ خاموش" if account.enabled else "▶️ روشن"
        kb.row(
            InlineKeyboardButton(
                text=f"{toggle} — {name}", callback_data=f"pool:toggle:{account.id}"
            ),
            InlineKeyboardButton(
                text="🗑", callback_data=f"pool:drop:{account.id}", style=DANGER
            ),
        )
    kb.row(InlineKeyboardButton(text="🔄 تازه‌سازی", callback_data="pool:home"))
    return "\n".join(lines), kb


@router.message(F.text == "/pool")
async def cmd_pool(message: Message) -> None:
    if not await roles.can(message.from_user.id, roles.CAP_SYSTEM):
        return
    text, kb = await _overview()
    await message.answer(text, reply_markup=kb.as_markup())


@router.callback_query(F.data == "pool:home")
async def cb_home(call: CallbackQuery) -> None:
    if await guard(call, roles.CAP_SYSTEM):
        return
    await call.answer()
    text, kb = await _overview()
    try:
        await call.message.edit_text(text, reply_markup=kb.as_markup())
    except Exception:
        await call.message.answer(text, reply_markup=kb.as_markup())


@router.callback_query(F.data == "pool:add")
async def cb_add(call: CallbackQuery, state: FSMContext) -> None:
    if await guard(call, roles.CAP_SYSTEM):
        return
    await call.answer()
    await state.set_state(Flow.pool_label)
    await call.message.answer(
        "یک نام کوتاه برای این اکانت بنویسید — فقط برای اینکه در فهرست "
        "بشناسیدش (مثلاً «خواننده ۱»).\n\n"
        "⚠️ <b>از شماره‌ی شخصی خودتان استفاده نکنید.</b> بارِ خواندنِ "
        "مبدأهای همه‌ی مشتری‌ها روی این اکانت می‌نشیند و اگر تلگرام "
        "محدودش کند، اکانت شخصی‌تان است که محدود شده."
    )


@router.message(Flow.pool_label)
async def on_label(message: Message, state: FSMContext) -> None:
    if not await roles.can(message.from_user.id, roles.CAP_SYSTEM):
        await state.clear()
        return
    await state.clear()
    label = (message.text or "").strip()[:64]

    try:
        url = await svclogin.start(message.from_user.id, label)
    except svclogin.LoginError as exc:
        await message.answer(f"⚠️ {exc}")
        return

    photo = BufferedInputFile(_qr_png(url), filename="service-qr.png")
    sent = await message.answer_photo(
        photo,
        caption=(
            "📷 <b>با اکانتی که می‌خواهید خواننده شود این را اسکن کنید.</b>\n\n"
            "در آن گوشی: تنظیمات ← دستگاه‌ها ← «اتصال دستگاه».\n\n"
            "کد هر چند ثانیه تازه می‌شود؛ همین تصویر را نگاه کنید."
        ),
    )
    asyncio.create_task(
        _watch(message.bot, message.from_user.id, sent.chat.id, sent.message_id),
        name=f"pool-qr-{message.from_user.id}",
    )


async def _watch(bot, admin_id: int, chat_id: int, message_id: int) -> None:
    """کد را تا اسکن شدن زنده نگه می‌دارد.

    بدون تازه‌سازی، کسی که برود گوشیِ اکانت سرویس را بردارد برمی‌گردد
    و کدی را اسکن می‌کند که دیگر کار نمی‌کند — و هیچ خطایی هم نمی‌بیند.
    """
    from aiogram.types import InputMediaPhoto

    deadline = time.monotonic() + QR_TOTAL_SECONDS
    while time.monotonic() < deadline:
        outcome = await svclogin.result(admin_id, QR_STEP_SECONDS)

        if outcome == "done":
            text, kb = await _overview()
            await bot.send_message(
                chat_id, "✅ اکانت سرویس اضافه شد.", reply_markup=None
            )
            await bot.send_message(chat_id, text, reply_markup=kb.as_markup())
            await log_activity(
                user_id=admin_id, event="pool", detail="اکانت سرویس اضافه شد"
            )
            return
        if outcome == "password":
            kb = InlineKeyboardBuilder()
            kb.row(
                InlineKeyboardButton(
                    text="🔑 وارد کردن رمز دو مرحله‌ای", callback_data="pool:pass"
                )
            )
            await bot.send_message(
                chat_id,
                "این اکانت رمز دو مرحله‌ای دارد. برای ادامه دکمه‌ی زیر را بزنید.",
                reply_markup=kb.as_markup(),
            )
            return
        if outcome == "gone":
            return

        fresh = await svclogin.refresh(admin_id)
        if fresh is None:
            return
        try:
            await bot.edit_message_media(
                chat_id=chat_id,
                message_id=message_id,
                media=InputMediaPhoto(
                    media=BufferedInputFile(_qr_png(fresh), filename="service-qr.png"),
                    caption=(
                        "📷 <b>با اکانتی که می‌خواهید خواننده شود این را اسکن کنید.</b>\n\n"
                        "در آن گوشی: تنظیمات ← دستگاه‌ها ← «اتصال دستگاه»."
                    ),
                ),
            )
        except Exception:
            log.debug("تازه‌سازی تصویر QR اکانت سرویس نشد", exc_info=True)

    await svclogin.cancel(admin_id)
    await bot.send_message(
        chat_id, "کد QR منقضی شد. اگر هنوز می‌خواهید، دوباره «افزودن اکانت» را بزنید."
    )


@router.callback_query(F.data == "pool:pass")
async def cb_password(call: CallbackQuery, state: FSMContext) -> None:
    if await guard(call, roles.CAP_SYSTEM):
        return
    await call.answer()
    if svclogin.pending(call.from_user.id) is None:
        await call.message.answer("جریان ورود منقضی شده. دوباره «افزودن اکانت» را بزنید.")
        return
    await state.set_state(Flow.pool_password)
    await call.message.answer("رمز دو مرحله‌ای این اکانت را بفرستید.")


@router.message(Flow.pool_password)
async def on_password(message: Message, state: FSMContext) -> None:
    if not await roles.can(message.from_user.id, roles.CAP_SYSTEM):
        await state.clear()
        return
    await state.clear()
    try:
        await svclogin.submit_password(message.from_user.id, (message.text or "").strip())
    except svclogin.LoginError as exc:
        await message.answer(f"⚠️ {exc}")
        return

    # <b>رمز را از گفتگو پاک می‌کنیم.</b> در تاریخچه‌ی چت ماندنش یعنی
    # هرکسی که بعداً به این گفتگو دسترسی پیدا کند، رمزِ دو مرحله‌ایِ یک
    # اکانتِ زنده را می‌بیند.
    try:
        await message.delete()
    except Exception:
        log.debug("پاک کردن پیام رمز نشد", exc_info=True)

    text, kb = await _overview()
    await message.answer("✅ اکانت سرویس اضافه شد.")
    await message.answer(text, reply_markup=kb.as_markup())


@router.callback_query(F.data.startswith("pool:toggle:"))
async def cb_toggle(call: CallbackQuery) -> None:
    if await guard(call, roles.CAP_SYSTEM):
        return
    account_id = int(call.data.rsplit(":", 1)[1])
    async with get_session() as db:
        account = await db.get(ServiceAccount, account_id)
        if account is None:
            await call.answer("پیدا نشد", show_alert=True)
            return
        account.enabled = not account.enabled
        if account.enabled and account.state == ServiceAccount.STATE_BANNED:
            # روشن کردنِ اکانتِ بسته‌شده کاری نمی‌کند جز اینکه هر دور
            # دوباره به همان دیوار بخوریم
            await call.answer(
                "این اکانت را تلگرام بسته است؛ روشن کردنش کمکی نمی‌کند.",
                show_alert=True,
            )
            account.enabled = False
            await db.commit()
            return
        await db.commit()
        on = account.enabled
    await call.answer("روشن شد" if on else "خاموش شد")
    text, kb = await _overview()
    try:
        await call.message.edit_text(text, reply_markup=kb.as_markup())
    except Exception:
        await call.message.answer(text, reply_markup=kb.as_markup())


@router.callback_query(F.data.startswith("pool:drop:"))
async def cb_drop(call: CallbackQuery) -> None:
    """<b>حذف، با آزاد کردنِ مبدأهایش.</b>

    اگر ردیف پاک شود ولی اجاره‌ها بمانند، هر مبدأیی که رویش بود به
    اکانتی اشاره می‌کند که دیگر نیست — و کارهایش برای همیشه ساکت
    می‌مانند بی‌آنکه جایی نوشته شود چرا.
    """
    if await guard(call, roles.CAP_SYSTEM):
        return
    account_id = int(call.data.rsplit(":", 1)[1])
    await pool.mark_banned(account_id, "دستی حذف شد")
    async with get_session() as db:
        account = await db.get(ServiceAccount, account_id)
        if account is not None:
            await db.delete(account)
            await db.commit()
    await call.answer("حذف شد")
    text, kb = await _overview()
    try:
        await call.message.edit_text(text, reply_markup=kb.as_markup())
    except Exception:
        await call.message.answer(text, reply_markup=kb.as_markup())
