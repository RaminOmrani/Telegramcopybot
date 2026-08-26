"""فوروارد پیشرفته: کاربر پیام را به ربات می‌فرستد، ربات با تنظیمات دلخواه
آن را در کانال مقصد منتشر می‌کند."""
from __future__ import annotations

import logging
from pathlib import Path

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy import select

from telkap.config import get_settings
from telkap.db import get_session, log_activity
from telkap.handlers.common import Flow
from telkap.keyboards import (
    forward_menu,
    fwd_clean_menu,
    fwd_text_menu,
    main_menu,
    menu_texts,
)
from telkap.models import ForwardProfile
from telkap.services.defaults import merged_settings
from telkap.services.subscription import active_plan_for
from telkap.services.transform import apply_transforms
from telkap.services.userbot import manager
from telkap.texts import (
    ASK_FOOTER,
    ASK_HEADER,
    ASK_SIGNATURE,
    FORWARD_INTRO,
    NO_LOGIN,
    NO_SUBSCRIPTION,
)

log = logging.getLogger(__name__)
router = Router(name="forward")

FWD_ASKS = {"header": ASK_HEADER, "footer": ASK_FOOTER, "signature": ASK_SIGNATURE}


async def _profile(user_id: int) -> ForwardProfile | None:
    async with get_session() as db:
        rows = await db.execute(
            select(ForwardProfile).where(ForwardProfile.user_id == user_id).limit(1)
        )
        return rows.scalar_one_or_none()


async def _save_profile_settings(profile_id: int, cfg: dict) -> None:
    async with get_session() as db:
        profile = await db.get(ForwardProfile, profile_id)
        if profile:
            profile.settings = dict(cfg)
            await db.commit()


def _profile_text(profile: ForwardProfile | None) -> str:
    if profile is None:
        return FORWARD_INTRO + "\n\n❗️ هنوز کانال مقصدی تعیین نکرده‌اید."
    cfg = merged_settings(profile.settings)
    active = [
        label
        for label, key in (
            ("حذف لینک", "remove_links"),
            ("حذف هشتگ", "remove_hashtags"),
            ("حذف آیدی", "remove_mentions"),
            ("حذف امضای مبدا", "remove_source_signature"),
        )
        if cfg.get(key)
    ]
    if cfg.get("footer"):
        active.append("فوتر")
    if cfg.get("header"):
        active.append("هدر")

    return (
        FORWARD_INTRO
        + "\n\n"
        + f"کانال مقصد: <code>{profile.dest_ref}</code>\n"
        + f"وضعیت: {'🟢 فعال' if profile.enabled else '🔴 غیرفعال'}\n"
        + f"تنظیمات فعال: {'، '.join(active) if active else 'هیچ‌کدام'}\n\n"
        + "حالا هر پیامی را به ربات فوروارد کنید."
    )


@router.message(Command("forward"))
@router.message(F.text.in_(menu_texts("menu.forward")))
async def show_forward(message: Message) -> None:
    profile = await _profile(message.from_user.id)
    await message.answer(_profile_text(profile), reply_markup=forward_menu(profile))


@router.callback_query(F.data == "fwd:open")
async def cb_open(call: CallbackQuery) -> None:
    profile = await _profile(call.from_user.id)
    await call.answer()
    try:
        await call.message.edit_text(_profile_text(profile), reply_markup=forward_menu(profile))
    except Exception:
        await call.message.answer(_profile_text(profile), reply_markup=forward_menu(profile))


@router.callback_query(F.data == "fwd:new")
async def cb_new(call: CallbackQuery, state: FSMContext) -> None:
    await call.answer()
    await state.set_state(Flow.fwd_dest)
    await call.message.answer(
        "📤 آیدی یا لینک کانال مقصد فوروارد پیشرفته را بفرستید.\n\nانصراف: /cancel"
    )


@router.message(Flow.fwd_dest)
async def got_dest(message: Message, state: FSMContext) -> None:
    ref = (message.text or "").strip()
    client = await manager.ensure_client(message.from_user.id)
    if client is None:
        await state.clear()
        await message.answer(NO_LOGIN)
        return

    notice = await message.answer("⏳ در حال بررسی کانال…")
    entity = await manager.resolve_entity(client, ref)
    if entity is None:
        await notice.edit_text("⚠️ کانال پیدا نشد یا دسترسی ندارید.")
        return
    dest_id = await manager.resolve_chat_id(client, ref)
    title = getattr(entity, "title", None) or ref

    async with get_session() as db:
        profile = (
            await db.execute(
                select(ForwardProfile).where(ForwardProfile.user_id == message.from_user.id).limit(1)
            )
        ).scalar_one_or_none()
        if profile is None:
            profile = ForwardProfile(
                user_id=message.from_user.id,
                title=title[:128],
                dest_ref=ref,
                dest_id=dest_id,
                dest_title=title[:160],
                settings={},
            )
            db.add(profile)
        else:
            profile.dest_ref = ref
            profile.dest_id = dest_id
            profile.dest_title = title[:160]
            profile.enabled = True
        await db.commit()

    await state.clear()
    await notice.edit_text(f"✅ کانال مقصد فوروارد پیشرفته: <b>{title}</b>")
    profile = await _profile(message.from_user.id)
    await message.answer(_profile_text(profile), reply_markup=forward_menu(profile))


@router.callback_query(F.data == "fwd:toggle")
async def cb_toggle(call: CallbackQuery) -> None:
    async with get_session() as db:
        profile = (
            await db.execute(
                select(ForwardProfile).where(ForwardProfile.user_id == call.from_user.id).limit(1)
            )
        ).scalar_one_or_none()
        if profile is None:
            await call.answer("ابتدا کانال مقصد را تعیین کنید.", show_alert=True)
            return
        profile.enabled = not profile.enabled
        await db.commit()
    await call.answer()
    await cb_open(call)


@router.callback_query(F.data == "fwd:del")
async def cb_del(call: CallbackQuery) -> None:
    async with get_session() as db:
        profile = (
            await db.execute(
                select(ForwardProfile).where(ForwardProfile.user_id == call.from_user.id).limit(1)
            )
        ).scalar_one_or_none()
        if profile:
            await db.delete(profile)
            await db.commit()
    await call.answer("حذف شد")
    await call.message.answer("پروفایل فوروارد پیشرفته حذف شد.", reply_markup=main_menu())


@router.callback_query(F.data.startswith("fwdset:"))
async def cb_panel(call: CallbackQuery) -> None:
    panel = call.data.split(":")[1]
    profile = await _profile(call.from_user.id)
    if profile is None:
        await call.answer("ابتدا کانال مقصد را تعیین کنید.", show_alert=True)
        return
    cfg = merged_settings(profile.settings)
    await call.answer()
    if panel == "clean":
        await call.message.edit_text(
            "🧹 <b>پاک‌سازی متن فوروارد</b>", reply_markup=fwd_clean_menu(cfg)
        )
    else:
        await call.message.edit_text("✍️ <b>هدر، فوتر و امضا</b>", reply_markup=fwd_text_menu())


@router.callback_query(F.data.startswith("fwdflag:"))
async def cb_flag(call: CallbackQuery) -> None:
    key = call.data.split(":")[1]
    profile = await _profile(call.from_user.id)
    if profile is None:
        await call.answer("ابتدا کانال مقصد را تعیین کنید.", show_alert=True)
        return
    cfg = merged_settings(profile.settings)
    cfg[key] = not bool(cfg.get(key))
    await _save_profile_settings(profile.id, cfg)
    await call.answer()
    await call.message.edit_reply_markup(reply_markup=fwd_clean_menu(cfg))


@router.callback_query(F.data.startswith("fwdask:"))
async def cb_ask(call: CallbackQuery, state: FSMContext) -> None:
    key = call.data.split(":")[1]
    if key not in FWD_ASKS:
        await call.answer()
        return
    await call.answer()
    await state.set_state(Flow.fwd_value)
    await state.update_data(fwd_key=key)
    await call.message.answer(FWD_ASKS[key] + "\n\nانصراف: /cancel")


@router.message(Flow.fwd_value)
async def got_value(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    key = data.get("fwd_key")
    await state.clear()
    profile = await _profile(message.from_user.id)
    if profile is None or key not in FWD_ASKS:
        await message.answer("پروفایل فوروارد پیدا نشد.")
        return
    raw = (message.text or "").strip()
    cfg = merged_settings(profile.settings)
    cfg[key] = "" if raw == "." else raw[:1000]
    await _save_profile_settings(profile.id, cfg)
    await message.answer("✅ ذخیره شد.")
    await message.answer(_profile_text(await _profile(message.from_user.id)),
                         reply_markup=forward_menu(await _profile(message.from_user.id)))


# ------------------------------------------------- دریافت پیام فورواردشده
@router.message(F.forward_origin | F.forward_from | F.forward_from_chat)
async def handle_forwarded(message: Message, state: FSMContext) -> None:
    """پیام فورواردشده را با تنظیمات پروفایل در کانال مقصد منتشر می‌کند."""
    if await state.get_state() is not None:
        return  # وسط یک گفتگوی دیگر هستیم

    profile = await _profile(message.from_user.id)
    if profile is None or not profile.enabled:
        return

    plan = await active_plan_for(message.from_user.id)
    if plan is None:
        await message.answer(NO_SUBSCRIPTION)
        return

    client = await manager.ensure_client(message.from_user.id)
    if client is None:
        await message.answer(NO_LOGIN)
        return

    cfg = merged_settings(profile.settings)
    source_text = message.text or message.caption or ""
    text = apply_transforms(source_text, cfg)
    target = profile.dest_id or profile.dest_ref

    notice = await message.answer("⏳ در حال ارسال…")
    file_path: Path | None = None
    try:
        if message.content_type == "text":
            if not text.strip():
                await notice.edit_text("⚠️ بعد از اعمال تنظیمات، متنی برای ارسال باقی نماند.")
                return
            await client.send_message(target, text, link_preview=False)
        else:
            # فایل از طریق Bot API دانلود و با اکانت کاربری آپلود می‌شود
            out_dir = get_settings().download_dir
            out_dir.mkdir(parents=True, exist_ok=True)
            file_id = _file_id(message)
            if file_id is None:
                await notice.edit_text("⚠️ این نوع پیام پشتیبانی نمی‌شود.")
                return
            tg_file = await message.bot.get_file(file_id)
            file_path = out_dir / f"fwd_{message.message_id}_{Path(tg_file.file_path).name}"
            await message.bot.download_file(tg_file.file_path, destination=file_path)
            await client.send_file(target, str(file_path), caption=text or None)

        await notice.edit_text(f"✅ در «{profile.dest_title or profile.dest_ref}» منتشر شد.")
        await log_activity(
            user_id=message.from_user.id, event="forward", detail=profile.dest_ref
        )
    except Exception as exc:
        log.exception("فوروارد پیشرفته ناموفق بود")
        await notice.edit_text(f"⚠️ ارسال ناموفق بود: {exc}")
    finally:
        if file_path is not None:
            file_path.unlink(missing_ok=True)


def _file_id(message: Message) -> str | None:
    if message.photo:
        return message.photo[-1].file_id
    for attr in ("video", "animation", "audio", "voice", "document", "video_note", "sticker"):
        media = getattr(message, attr, None)
        if media is not None:
            return media.file_id
    return None
