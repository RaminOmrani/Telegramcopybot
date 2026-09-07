"""جادوگر واترمارک: کاربر هرچه بخواهد می‌فرستد، شش پیش‌نمایش می‌بیند و
هرکدام را پسندید انتخاب می‌کند.

ورودی می‌تواند متن، ایموجی، عکس یا استیکر باشد؛ نوع واترمارک خودکار از روی
همان چیزی که فرستاده تشخیص داده می‌شود. پیش‌نمایش‌ها روی یک عکس واقعی از
کانال مبدا ساخته می‌شوند تا کاربر دقیقاً همان چیزی را ببیند که منتشر خواهد
شد؛ اگر مبدا در دسترس نباشد، روی یک عکس نمونه.
"""
from __future__ import annotations

import logging
from pathlib import Path

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import (
    CallbackQuery,
    FSInputFile,
    InlineKeyboardButton,
    InputMediaPhoto,
    Message,
)
from aiogram.utils.keyboard import InlineKeyboardBuilder

from telkap.config import get_settings
from telkap.db import get_session
from telkap.handlers.common import Flow
from telkap.models import Task
from telkap.services import cache
from telkap.services.defaults import merged_settings
from telkap.services.userbot import manager
from telkap.services.watermark import (
    PRESET_BY_KEY,
    has_color_emoji_font,
    preview_variants,
    sample_photo,
    watermark_ready,
)

log = logging.getLogger(__name__)
router = Router(name="watermark_wizard")

SOURCE_SCAN_LIMIT = 15      # چند پست آخر مبدا برای پیدا کردن یک عکس بررسی شود


async def _owned_task(user_id: int, task_id: int) -> Task | None:
    async with get_session() as db:
        task = await db.get(Task, task_id)
    return task if task and task.user_id == user_id else None


async def _save(task_id: int, cfg: dict) -> None:
    async with get_session() as db:
        task = await db.get(Task, task_id)
        if task is None:
            return
        task.settings = dict(cfg)
        await db.commit()
    cache.invalidate_task(task_id)


def _work_dir(task_id: int) -> Path:
    path = get_settings().download_dir / "preview" / str(task_id)
    path.mkdir(parents=True, exist_ok=True)
    return path


# ------------------------------------------------- تصویر پایه‌ی پیش‌نمایش
async def _base_photo(user_id: int, task_id: int, work: Path) -> tuple[Path, bool]:
    """یک عکس واقعی از کانال مبدا، وگرنه عکس نمونه.

    خروجی دوم می‌گوید که آیا عکس واقعی به دست آمد یا نه.
    """
    target = work / "base.jpg"
    try:
        snapshot = await cache.get_task(task_id)
        client = await manager.ensure_client(user_id)
        if snapshot is not None and client is not None:
            entity = await manager.resolve_entity(
                client, str(snapshot.source_id or snapshot.source_ref)
            )
            if entity is not None:
                async for message in client.iter_messages(
                    entity, limit=SOURCE_SCAN_LIMIT
                ):
                    if getattr(message, "photo", None) is None:
                        continue
                    got = await client.download_media(message, file=str(target))
                    if got:
                        return Path(got), True
    except Exception:
        log.debug("گرفتن عکس نمونه از کانال مبدا ناموفق بود", exc_info=True)
    return sample_photo(target), False


# ------------------------------------------------------------- شروع
def _ask_text() -> str:
    lines = [
        "🎨 <b>واترمارک را بسازیم</b>",
        "━━━━━━━━━━━━━━━━━━",
        "",
        "هرچه می‌خواهید روی عکس‌ها بنشیند همین‌جا بفرستید:",
        "",
        "• ✏️ <b>متن</b> — مثلاً <code>@MyChannel</code>",
        "• 😀 <b>ایموجی</b> — چه تنها، چه کنار متن",
        "• 🖼 <b>عکس</b> یا <b>استیکر</b> — مثلاً لوگوی کانالتان",
        "• 📎 فایل <b>PNG</b> شفاف — تمیزترین نتیجه",
        "",
        "بعد از فرستادن، <b>۶ حالت مختلف</b> رویش می‌زنم و شما هرکدام را "
        "پسندیدید انتخاب می‌کنید.",
    ]
    if not has_color_emoji_font():
        lines.append(
            "\n⚠️ روی این سرور فونت ایموجی رنگی نصب نیست؛ ایموجیِ داخل متن "
            "بی‌رنگ می‌افتد. به‌جایش همان ایموجی را به‌صورت استیکر بفرستید."
        )
    lines.append("\nانصراف: /cancel")
    return "\n".join(lines)


@router.callback_query(F.data.startswith("wmpv:"))
async def cb_start(call: CallbackQuery, state: FSMContext) -> None:
    task_id = int(call.data.split(":")[1])
    if await _owned_task(call.from_user.id, task_id) is None:
        await call.answer("دسترسی ندارید", show_alert=True)
        return
    await call.answer()
    await state.set_state(Flow.watermark_input)
    await state.update_data(task_id=task_id)
    await call.message.answer(_ask_text())


@router.callback_query(F.data.startswith("wmpvnow:"))
async def cb_preview_current(call: CallbackQuery) -> None:
    """پیش‌نمایش با همان چیزی که از قبل تنظیم شده."""
    task_id = int(call.data.split(":")[1])
    task = await _owned_task(call.from_user.id, task_id)
    if task is None:
        await call.answer("دسترسی ندارید", show_alert=True)
        return
    cfg = merged_settings(task.settings)
    if not watermark_ready({**cfg, "watermark_enabled": True}):
        await call.answer(
            "اول متن یا لوگوی واترمارک را تعیین کنید.", show_alert=True
        )
        return
    await call.answer()
    await _send_previews(call.message, call.from_user.id, task_id, cfg)


# --------------------------------------------------- دریافت ورودی کاربر
async def _download(message: Message, file_id: str, task_id: int) -> Path | None:
    logo_dir = get_settings().download_dir / "logos"
    logo_dir.mkdir(parents=True, exist_ok=True)
    target = logo_dir / f"task-{task_id}.png"
    try:
        info = await message.bot.get_file(file_id)
        await message.bot.download_file(info.file_path, destination=str(target))
    except Exception:
        log.exception("دانلود ورودی واترمارک ناموفق بود")
        return None
    return target


@router.message(Flow.watermark_input)
async def got_input(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    task_id = int(data.get("task_id", 0))
    task = await _owned_task(message.from_user.id, task_id)
    if task is None:
        await state.clear()
        await message.answer("این کار پیدا نشد.")
        return

    cfg = merged_settings(task.settings)

    # تصویر (عکس، استیکر ساده یا فایل تصویری) → حالت لوگو
    file_id = None
    if message.photo:
        file_id = message.photo[-1].file_id
    elif message.sticker and not message.sticker.is_animated and not message.sticker.is_video:
        file_id = message.sticker.file_id
    elif message.document and (message.document.mime_type or "").startswith("image/"):
        file_id = message.document.file_id

    if file_id is not None:
        notice = await message.answer("⏳ در حال ذخیره…")
        path = await _download(message, file_id, task_id)
        if path is None:
            await notice.edit_text("⚠️ دریافت فایل ناموفق بود. دوباره بفرستید.")
            return
        cfg["watermark_kind"] = "logo"
        cfg["watermark_logo"] = str(path)
        await notice.delete()
    elif (message.sticker and (message.sticker.is_animated or message.sticker.is_video)):
        await message.answer(
            "⚠️ استیکر متحرک و ویدیویی پشتیبانی نمی‌شود.\n"
            "یک استیکر ساده، عکس یا متن بفرستید."
        )
        return
    else:
        text = (message.text or message.caption or "").strip()
        if not text:
            await message.answer(
                "⚠️ چیزی که فرستادید قابل استفاده نیست.\n"
                "متن، ایموجی، عکس یا استیکر ساده بفرستید."
            )
            return
        cfg["watermark_kind"] = "text"
        cfg["watermark_text"] = text[:100]

    cfg["watermark_enabled"] = True
    await _save(task_id, cfg)
    await state.clear()
    await _send_previews(message, message.from_user.id, task_id, cfg)


# ------------------------------------------------------------ پیش‌نمایش
async def _send_previews(target: Message, user_id: int, task_id: int, cfg: dict) -> None:
    notice = await target.answer("🎨 در حال ساخت پیش‌نمایش‌ها…")
    work = _work_dir(task_id)
    base, real = await _base_photo(user_id, task_id, work)
    variants = preview_variants(base, work, cfg)

    if not variants:
        await notice.edit_text(
            "⚠️ ساخت پیش‌نمایش ناموفق بود. مطمئن شوید متن یا لوگو تعیین شده است."
        )
        return

    kind = "لوگو" if cfg.get("watermark_kind") == "logo" else "متن"
    source_note = (
        "روی یک عکس واقعی از کانال مبدا" if real else "روی یک عکس نمونه"
    )
    # فقط اولین عکسِ آلبوم کپشن می‌گیرد؛ تلگرام کپشن آلبوم را از همان می‌خواند
    legend = "\n".join(
        f"{index}️⃣ {preset.label}"
        for index, (preset, _) in enumerate(variants, start=1)
    )
    caption = (
        f"🎨 <b>پیش‌نمایش واترمارک ({kind})</b>\n"
        f"<i>{source_note}</i>\n\n{legend}"
    )
    media = [
        InputMediaPhoto(
            media=FSInputFile(path),
            caption=caption if index == 0 else None,
        )
        for index, (_preset, path) in enumerate(variants)
    ]
    try:
        await target.answer_media_group(media)
    except Exception:
        log.exception("ارسال پیش‌نمایش‌ها ناموفق بود")
        await notice.edit_text("⚠️ ارسال پیش‌نمایش‌ها ناموفق بود.")
        return

    kb = InlineKeyboardBuilder()
    buttons = [
        InlineKeyboardButton(
            text=f"{index}️⃣", callback_data=f"wmpk:{task_id}:{preset.key}"
        )
        for index, (preset, _) in enumerate(variants, start=1)
    ]
    kb.row(*buttons[:3])
    kb.row(*buttons[3:])
    kb.row(
        InlineKeyboardButton(
            text="🔄 یک چیز دیگر می‌فرستم", callback_data=f"wmpv:{task_id}"
        )
    )
    kb.row(
        InlineKeyboardButton(
            text="🔙 منوی واترمارک", callback_data=f"set:wm:{task_id}"
        )
    )
    await notice.edit_text(
        "👆 کدام را می‌پسندید؟ شماره‌اش را بزنید تا همان روی همه‌ی عکس‌های "
        "این کار اعمال شود.\n\n"
        "<i>بعدش هم می‌توانید از منوی واترمارک، اندازه و شفافیت را ریز تنظیم "
        "کنید.</i>",
        reply_markup=kb.as_markup(),
    )


@router.callback_query(F.data.startswith("wmpk:"))
async def cb_pick(call: CallbackQuery) -> None:
    _, raw_id, key = call.data.split(":")
    task_id = int(raw_id)
    task = await _owned_task(call.from_user.id, task_id)
    preset = PRESET_BY_KEY.get(key)
    if task is None or preset is None:
        await call.answer("دسترسی ندارید", show_alert=True)
        return

    cfg = merged_settings(task.settings)
    kind = cfg.get("watermark_kind", "text")
    cfg["watermark_enabled"] = True
    cfg["watermark_position"] = preset.position
    cfg["watermark_opacity"] = preset.opacity
    cfg["watermark_size"] = preset.size_for(kind)
    await _save(task_id, cfg)

    await call.answer("اعمال شد ✅")
    kb = InlineKeyboardBuilder()
    kb.row(
        InlineKeyboardButton(
            text="🔧 تنظیم دقیق‌تر", callback_data=f"set:wm:{task_id}"
        )
    )
    kb.row(
        InlineKeyboardButton(text="👁 پیش‌نمایش دوباره", callback_data=f"wmpvnow:{task_id}")
    )
    await call.message.edit_text(
        f"✅ حالت <b>{preset.label}</b> اعمال شد.\n\n"
        "از این به بعد هر عکسی که این کار کپی کند همین واترمارک را می‌گیرد.\n\n"
        "<i>یادآوری: واترمارک فقط روی عکس اعمال می‌شود؛ ویدیو، فایل و پست "
        "متنی دست‌نخورده کپی می‌شوند.</i>",
        reply_markup=kb.as_markup(),
    )
