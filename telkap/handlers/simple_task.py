"""ساختِ کار در حالت ساده — بدون وصل کردن اکانت.

<b>چرا جریانِ جداگانه و نه یک گزینه در همان جریانِ موجود.</b>

سه قدمِ ظاهراً یکسان، اینجا سه چیزِ کاملاً متفاوت‌اند:

- <b>مبدأ</b> با اکانتِ <i>ما</i> بررسی می‌شود، نه اکانتِ مشتری — که
  اصلاً وجود ندارد. و باید عمومی باشد؛ خصوصی از این راه ممکن نیست.
- <b>مقصد</b> با <i>خودِ ربات</i> بررسی می‌شود: آیا ادمین هست و اجازه‌ی
  ارسال دارد.
- <b>گلوگاه اول</b> در آن جریان «اکانت وصل است؟» است و اینجا دقیقاً
  همان چیزی است که نباید پرسیده شود.

یک جریان با سه شرطِ «اگر ساده بود…» در هر قدم، همان جایی است که دیر یا
زود یکی از شاخه‌ها بی‌صدا اشتباه می‌شود.
"""
from __future__ import annotations

import logging

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import (
    CallbackQuery,
    ChatAdministratorRights,
    InlineKeyboardButton,
    KeyboardButton,
    KeyboardButtonRequestChat,
    Message,
    ReplyKeyboardMarkup,
)
from aiogram.utils.keyboard import InlineKeyboardBuilder
from sqlalchemy import func, select

from telkap.db import get_session, log_activity
from telkap.handlers.common import Flow
from telkap.handlers.tasks import show_task
from telkap.keyboards import CALM, GO, main_menu
from telkap.models import Task, User
from telkap.services import alerts, botsend, cache, pool
from telkap.services.subscription import active_plan_for
from telkap.texts import NO_SUBSCRIPTION, fa_num

log = logging.getLogger(__name__)
router = Router(name="simple-task")

ASK_SOURCE = (
    "🌐 <b>کانال مبدأ را انتخاب کنید.</b>\n\n"
    "دکمه‌ی زیر فهرست کانال‌های عمومی‌تان را باز می‌کند — یا اگر عضوش "
    "نیستید، آدرسش را تایپ کنید (مثل <code>@varzesh3</code>).\n\n"
    "<i>در این حالت فقط کانال‌های عمومی ممکن‌اند.</i>"
)

ASK_DEST = (
    "📥 <b>حالا کانال خودتان را انتخاب کنید.</b>\n\n"
    "با دکمه‌ی زیر، تلگرام فهرست کانال‌هایتان را نشان می‌دهد و "
    "<b>خودش ربات را با دسترسی «ارسال پیام» ادمین می‌کند</b> — لازم "
    "نیست جایی بروید و دستی اضافه‌اش کنید."
)

# شناسه‌ی درخواست: تلگرام همین عدد را با جواب برمی‌گرداند، پس از روی
# آن می‌فهمیم کاربر مبدأ را انتخاب کرده یا مقصد را — بدون تکیه به
# حالتِ گفتگو، که ممکن است در این فاصله عوض شده باشد.
PICK_SOURCE = 1
PICK_DEST = 2

# <b>کمترین دسترسیِ ممکن.</b> فقط «ارسال پیام» می‌خواهیم. خواستنِ حذف
# یا مسدودسازی یا ارتقای اعضا، همان‌جا سرِ انتخاب، کاربر را می‌ترساند —
# و حق هم دارد.
BOT_RIGHTS = ChatAdministratorRights(
    is_anonymous=False,
    can_manage_chat=False,
    can_delete_messages=False,
    can_manage_video_chats=False,
    can_restrict_members=False,
    can_promote_members=False,
    can_change_info=False,
    can_invite_users=False,
    can_post_stories=False,
    can_edit_stories=False,
    can_delete_stories=False,
    can_send_welcome_messages=False,
    can_post_messages=True,
)


def _pick_source() -> ReplyKeyboardMarkup:
    """فهرست کانال‌های عمومی‌ای که کاربر عضوشان است."""
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(
            text="📋 انتخاب از کانال‌های من",
            request_chat=KeyboardButtonRequestChat(
                request_id=PICK_SOURCE,
                chat_is_channel=True,
                # فقط عمومی — خصوصی در این حالت اصلاً ممکن نیست، پس
                # نگذاریم کاربر انتخابش کند و بعد «نه» بشنود.
                chat_has_username=True,
                request_title=True,
                request_username=True,
            ),
        )]],
        resize_keyboard=True,
        one_time_keyboard=True,
        input_field_placeholder="یا آدرس کانال را تایپ کنید…",
    )


def _pick_dest() -> ReplyKeyboardMarkup:
    """<b>شکننده‌ترین قدمِ محصول، در یک ضربه.</b>

    تا امروز باید به کاربر می‌گفتیم برود در تنظیمات کانال، بخش مدیران،
    ربات را اضافه کند، دسترسی درست را روشن بگذارد و برگردد — شش قدم،
    و همان‌جایی که آدم‌ها رها می‌کنند.

    با این، تلگرام خودش ربات را با همان دسترسی ادمین می‌کند.
    """
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(
            text="📋 انتخاب کانال و افزودن ربات",
            request_chat=KeyboardButtonRequestChat(
                request_id=PICK_DEST,
                chat_is_channel=True,
                # فقط کانالی که خودش می‌تواند ادمین اضافه کند
                user_administrator_rights=ChatAdministratorRights(
                    is_anonymous=False,
                    can_manage_chat=True,
                    can_delete_messages=False,
                    can_manage_video_chats=False,
                    can_restrict_members=False,
                    can_promote_members=True,
                    can_change_info=False,
                    can_invite_users=False,
                    can_post_stories=False,
                    can_edit_stories=False,
                    can_delete_stories=False,
                    can_send_welcome_messages=False,
                ),
                bot_administrator_rights=BOT_RIGHTS,
                request_title=True,
                request_username=True,
            ),
        )]],
        resize_keyboard=True,
        one_time_keyboard=True,
        input_field_placeholder="یا آدرس کانال را تایپ کنید…",
    )

NO_POOL = (
    "⚠️ <b>حالت ساده همین حالا در دسترس نیست.</b>\n\n"
    "سرویس موقتاً خواننده‌ای برای کانال‌های عمومی ندارد. چند دقیقه بعد "
    "دوباره امتحان کنید، یا کار را با وصل کردن اکانت خودتان بسازید."
)


def entry_button() -> InlineKeyboardButton:
    return InlineKeyboardButton(
        text="⚡️ بدون وصل کردن اکانت", callback_data="simple:new", style=GO
    )


async def _allowed(user_id: int, message: Message) -> bool:
    """همان گلوگاه‌های همیشگی — منهای «اکانت وصل است؟»."""
    plan = await active_plan_for(user_id)
    if plan is None:
        await message.answer(NO_SUBSCRIPTION)
        return False

    async with get_session() as db:
        count = await db.scalar(
            select(func.count(Task.id)).where(Task.user_id == user_id)
        )
    if (count or 0) >= plan.max_tasks:
        await message.answer(
            f"⚠️ در پلن «{plan.title}» حداکثر {fa_num(plan.max_tasks)} کار "
            "می‌توانید داشته باشید."
        )
        return False
    return True


@router.callback_query(F.data == "simple:new")
async def cb_new(call: CallbackQuery, state: FSMContext) -> None:
    await call.answer()
    if not await _allowed(call.from_user.id, call.message):
        return
    await state.set_state(Flow.simple_source)
    await call.message.answer(ASK_SOURCE, reply_markup=_pick_source())


@router.message(Flow.simple_source, F.chat_shared)
async def picked_source(message: Message, state: FSMContext) -> None:
    """کاربر مبدأ را از فهرست کانال‌هایش انتخاب کرد."""
    shared = message.chat_shared
    ref = f"@{shared.username}" if shared.username else str(shared.chat_id)
    await got_source(message, state, picked=ref)


@router.message(Flow.simple_dest, F.chat_shared)
async def picked_dest(message: Message, state: FSMContext) -> None:
    """<b>کاربر مقصد را انتخاب کرد و تلگرام ربات را ادمین کرد.</b>

    ولی هنوز وارسی می‌کنیم. «تلگرام باید ادمینش کرده باشد» فرض است، و
    فرضِ وارسی‌نشده همان چیزی است که ساعت‌ها بعد به‌شکل «هیچ پستی
    نیامد» برمی‌گردد.
    """
    shared = message.chat_shared
    await got_dest(message, state, picked=str(shared.chat_id))


@router.message(Flow.simple_source)
async def got_source(message: Message, state: FSMContext, picked: str = "") -> None:
    ref = picked or (message.text or "").strip()
    if not ref:
        await message.answer("⚠️ کانال را انتخاب کنید یا آدرسش را بفرستید.")
        return

    notice = await message.answer("⏳ در حال بررسی مبدأ…")
    try:
        client = await pool.any_client()
    except pool.NoAccount:
        await notice.edit_text(NO_POOL)
        await state.clear()
        # ادمین باید بداند؛ مشتری همین الان یک کار را نساخت
        await alerts.send(
            "👥 <b>یک مشتری نتوانست کار حالت ساده بسازد</b> — خواننده‌ای "
            "نداریم.\n\nدر ربات: <code>/pool</code> ← افزودن اکانت.",
            key="pool-empty-signup",
        )
        return

    if client is None:
        await notice.edit_text(NO_POOL)
        await state.clear()
        return

    try:
        entity = await client.get_entity(ref)
    except Exception as exc:
        log.info("مبدأ عمومی %s شناسایی نشد: %s", ref, exc)
        await notice.edit_text(
            "⚠️ این کانال پیدا نشد.\n\n"
            "مطمئن شوید آدرس درست است و کانال <b>عمومی</b> است — یعنی با "
            "همین آدرس در تلگرام باز می‌شود."
        )
        return

    # <b>عمومی بودن، شرطِ کلِ این حالت است.</b> کانالی که نام کاربری
    # ندارد را اکانت سرویس نمی‌تواند بدون عضو شدن بخواند، و عضو شدن
    # لینک دعوت و تأیید ادمینِ مبدأ می‌خواهد — یعنی کاری که مشتری
    # نمی‌تواند بکند.
    if not getattr(entity, "username", None):
        await notice.edit_text(
            "⚠️ این کانال <b>خصوصی</b> است.\n\n"
            "در حالت ساده فقط کانال‌های عمومی ممکن‌اند. برای کانال خصوصی "
            "باید اکانت خودتان را وصل کنید — چون فقط اکانتی که عضو آن "
            "است می‌تواند بخواندش."
        )
        return

    source_id = int(getattr(entity, "id", 0) or 0)
    if source_id > 0:
        source_id = int(f"-100{source_id}")

    title = getattr(entity, "title", None) or ref
    await state.update_data(
        source_ref=ref, source_title=title, source_id=source_id
    )
    await notice.edit_text(f"✅ مبدأ: <b>{title}</b> (🌐 عمومی)")
    await state.set_state(Flow.simple_dest)
    await message.answer(ASK_DEST, reply_markup=_pick_dest())


@router.message(Flow.simple_dest)
async def got_dest(message: Message, state: FSMContext, picked: str = "") -> None:
    ref = picked or (message.text or "").strip()
    if not ref:
        await message.answer("⚠️ کانال را انتخاب کنید یا آدرسش را بفرستید.")
        return

    notice = await message.answer("⏳ در حال بررسی مقصد…")
    bot = alerts.bot() or message.bot

    try:
        chat = await bot.get_chat(ref)
    except Exception as exc:
        log.info("مقصد %s برای ربات پیدا نشد: %s", ref, exc)
        await notice.edit_text(
            "⚠️ ربات این کانال را نمی‌بیند.\n\n" + botsend.ADD_BOT
        )
        return

    verdict = await botsend.can_post(bot, chat.id)
    if not verdict:
        # <b>اینجا رها کردن آسان است.</b> پس به‌جای «خطا»، همان قدمِ
        # جامانده گفته می‌شود و کاربر در همین حالت می‌ماند تا دوباره
        # بفرستد — نه اینکه از اول شروع کند.
        await notice.edit_text(f"⚠️ {verdict.message}\n\nبعد دوباره آدرس را بفرستید.")
        return

    title = getattr(chat, "title", None) or ref
    await state.update_data(dest_ref=ref, dest_title=title, dest_id=int(chat.id))
    await notice.edit_text(f"✅ مقصد: <b>{title}</b> — ربات اجازه‌ی ارسال دارد.")
    await state.set_state(Flow.simple_title)
    # صفحه‌کلیدِ انتخاب کارش تمام شد؛ منوی اصلی برمی‌گردد
    await message.answer(
        "یک نام برای این کار بنویسید (یا «-» بفرستید).",
        reply_markup=main_menu(),
    )


@router.message(Flow.simple_title)
async def got_title(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    raw = (message.text or "").strip()
    title = ("" if raw == "-" else raw)[:128] or data.get("source_title", "کار جدید")
    await state.clear()

    async with get_session() as db:
        task = Task(
            user_id=message.from_user.id,
            title=title,
            mode=Task.MODE_SIMPLE,
            source_kind=Task.SOURCE_TELEGRAM,
            source_ref=data["source_ref"],
            source_id=data["source_id"],
            source_title=data.get("source_title", "")[:160],
            dest_ref=data["dest_ref"],
            dest_id=data["dest_id"],
            dest_title=data.get("dest_title", "")[:160],
            settings={},
        )
        db.add(task)
        await db.commit()
        await db.refresh(task)
        task_id = task.id

    cache.invalidate_task(task_id)
    await log_activity(
        user_id=message.from_user.id,
        task_id=task_id,
        event="task_create",
        detail=f"ساده: {data['source_ref']} ← {data['dest_ref']}",
    )
    await message.answer(
        f"✅ کار «{title}» ساخته شد.\n\n"
        "پست‌های <b>تازه</b>ی کانال مبدأ از این پس در کانال شما منتشر "
        "می‌شوند — متن، عکس، ویدیو و آلبوم. پست‌های قبلی منتشر نمی‌شوند.",
        reply_markup=main_menu(),
    )
    await show_task(message, task_id)


@router.callback_query(F.data == "simple:why")
async def cb_why(call: CallbackQuery) -> None:
    await call.answer()
    await call.message.answer(
        "⚡️ <b>حالت ساده چیست</b>\n\n"
        "کانال مبدأ را <b>اکانت‌های خودِ سرویس</b> می‌خوانند و پست را "
        "<b>خودِ ربات</b> در کانال شما می‌گذارد. پس لازم نیست اکانت "
        "تلگرامتان را وصل کنید — فقط ربات را در کانال خودتان ادمین "
        "می‌کنید، مثل هر ربات دیگری.\n\n"
        "<b>محدودیتش:</b> فقط کانال مبدأ <b>عمومی</b>. برای کانال خصوصی "
        "یا کپیِ پست‌های قدیمی، باید اکانت خودتان وصل باشد."
    )


def new_task_choice() -> InlineKeyboardBuilder:
    """دو راهِ ساختنِ کار، کنار هم."""
    kb = InlineKeyboardBuilder()
    kb.row(entry_button())
    kb.row(
        InlineKeyboardButton(
            text="🔗 با اکانت خودم", callback_data="task:new", style=CALM
        )
    )
    kb.row(InlineKeyboardButton(text="❓ فرقشان چیست", callback_data="simple:why"))
    return kb


async def offer(message: Message, user_id: int) -> bool:
    """اگر کاربر اکانت وصل نکرده، حالت ساده را پیشنهاد می‌کند.

    True یعنی پیشنهاد داده شد و صداکننده نباید ادامه دهد.
    """
    async with get_session() as db:
        user = await db.get(User, user_id)
    if user is not None and user.is_logged_in:
        return False

    try:
        used, total = await pool.capacity()
    except Exception:
        return False
    if used >= total:
        return False        # ظرفیتی نیست؛ پیشنهادِ نشدنی ندهیم

    await message.answer(
        "دو راه دارید:\n\n"
        "⚡️ <b>بدون وصل کردن اکانت</b> — برای کانال مبدأ عمومی. فقط "
        "ربات را در کانال خودتان ادمین می‌کنید.\n\n"
        "🔗 <b>با اکانت خودتان</b> — برای کانال خصوصی، پست‌های قدیمی و "
        "همه‌ی امکانات.",
        reply_markup=new_task_choice().as_markup(),
    )
    return True
