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

# <b>یک متن، چون یک واقعیت است.</b>
#
# این مقایسه در سه جا دیده می‌شود: پیش از انتخابِ حالت، روی خودِ کار، و
# بعد از وصل شدنِ اکانت. اگر سه نسخه می‌داشت، دیر یا زود یکی‌شان کهنه
# می‌ماند و همان یکی چیزی وعده می‌دهد که نداریم.
#
# و ترتیبش عمدی است: اول آنچه ساده <b>دارد</b>. فهرستی که با
# «نمی‌تواند» شروع شود، حالتِ بی‌دردسرمان را شبیه نسخه‌ی معیوب نشان
# می‌دهد، در حالی که برای بیشترِ مشتری‌ها همین کافی است.
MODE_COMPARISON = (
    "<b>در حالت ساده اینها می‌آیند:</b>\n"
    "متن، عکس، ویدیو، آلبوم، فایل — با بولد، ایتالیک، لینک، اسپویلر و "
    "نقل‌قول. ویرایش‌های مبدأ هم دنبال می‌شوند.\n\n"
    "<b>و اینها فقط در حالت کامل هستند:</b>\n"
    "• <b>ایموجی پریمیوم.</b> در حالت ساده به شکل ایموجی معمولیِ زیرش "
    "می‌رسد. خودِ تلگرام اجازه نمی‌دهد ربات‌ها ایموجی پریمیوم در کانال "
    "بفرستند — ربطی به اشتراک شما ندارد و با پول بیشتر هم درست "
    "نمی‌شود.\n"
    "• <b>کانال مبدأ خصوصی.</b> در حالت ساده فقط کانال عمومی.\n"
    "• <b>کپی پست‌های قدیمی</b> (آرشیو مبدأ).\n\n"
    "<b>حالت کامل چه می‌خواهد:</b> یک بار اکانت تلگرامتان را وصل "
    "می‌کنید — یا <b>کد QR را اسکن می‌کنید</b>، یا کد ورودی که تلگرام "
    "می‌فرستد را وارد می‌کنید. همین. بعدش همه‌چیز همان‌طور کار می‌کند."
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
            # تنها دکمه‌ی صفحه و تنها قدمِ ممکن — و رنگش تفاوتِ
            # «یک ضربه» با «آدرس را دستی تایپ کن» را دیدنی می‌کند.
            style=GO,
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
            style=GO,
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
        "می‌شوند — متن، عکس، ویدیو و آلبوم. پست‌های قبلی منتشر نمی‌شوند.\n\n"
        # <b>همین حالا گفته می‌شود، نه وقتی خودش ببیند.</b> اولین پستی
        # که برسد این را نشان می‌دهد؛ اگر از قبل نگفته باشیم، شبیه
        # خرابی به نظر می‌رسد نه یک قاعده‌ی تلگرام.
        "ℹ️ <b>ایموجی پریمیوم</b> به شکل ایموجی معمولیِ زیرش می‌رسد — "
        "تلگرام به ربات‌ها اجازه‌ی فرستادنش در کانال را نمی‌دهد. بقیه‌ی "
        "قالب‌بندی کامل می‌آید.",
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
        + MODE_COMPARISON
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


# ------------------------------------------------- جابه‌جایی بین دو حالت
#
# <b>چرا این مسیر از خودِ «ارتقا» مهم‌تر است.</b>
#
# حالت ساده برای این ساخته شد که مشتریِ بی‌اعتماد بتواند بدون دادنِ
# اکانتش امتحان کند. ولی اگر همان‌جا بماند، هرگز به امکاناتِ کامل
# نمی‌رسد — و ما هم هیچ‌وقت آن اعتماد را نمی‌گیریم.
#
# ترتیبِ درست این است: اول کار کند، بعد چیزی بخواهد. کسی که یک هفته
# پست‌هایش را درست گرفته و حالا ایموجی پریمیوم می‌خواهد، اکانتش را وصل
# می‌کند؛ همان آدم در قدم اول این کار را نمی‌کرد.


def _mode_keyboard(task) -> InlineKeyboardBuilder:
    kb = InlineKeyboardBuilder()
    if task.mode == Task.MODE_SIMPLE:
        kb.row(
            InlineKeyboardButton(
                text="⬆️ ارتقا به حالت کامل",
                callback_data=f"task:mode:up:{task.id}",
                style=GO,
            )
        )
    else:
        kb.row(
            InlineKeyboardButton(
                text="⚡️ بازگشت به حالت ساده",
                callback_data=f"task:mode:down:{task.id}",
            )
        )
    kb.row(
        InlineKeyboardButton(text="🔙 بازگشت", callback_data=f"task:open:{task.id}")
    )
    return kb


async def _owned(call: CallbackQuery, task_id: int):
    async with get_session() as db:
        task = await db.get(Task, task_id)
    if task is None or task.user_id != call.from_user.id:
        await call.answer("دسترسی ندارید", show_alert=True)
        return None
    return task


@router.callback_query(F.data.startswith("task:mode:up:"))
async def cb_upgrade(call: CallbackQuery) -> None:
    """ارتقا — ولی فقط وقتی واقعاً کار می‌کند.

    <b>نصفه ارتقا دادن بدتر از ارتقا ندادن است.</b> اگر حالت را عوض
    کنیم و اکانت نتواند مبدأ را بخواند، کار از هر دو طرف می‌افتد: نه
    پویشگرِ حالت ساده دیگر برش می‌دارد (چون دیگر ساده نیست) و نه
    اکانت چیزی می‌بیند. یعنی کاری که تا یک دقیقه پیش سالم بود، ساکت
    می‌ایستد.
    """
    task = await _owned(call, int(call.data.split(":")[3]))
    if task is None:
        return
    await call.answer()

    async with get_session() as db:
        user = await db.get(User, call.from_user.id)
    if user is None or not user.is_logged_in:
        await call.message.answer(
            "🔗 <b>برای حالت کامل باید یک بار اکانتتان را وصل کنید.</b>\n\n"
            + MODE_COMPARISON
            + "\n\nاز «👤 حساب کاربری ← 🔐 اتصال اکانت» شروع کنید. بعد از "
            "وصل شدن، همین‌جا برگردید و دوباره «ارتقا» را بزنید — "
            "تنظیمات و آمارِ این کار دست‌نخورده می‌ماند."
        )
        return

    # اکانت هست؛ حالا واقعاً می‌بیندش؟
    from telkap.handlers.tasks import manager

    client = await manager.ensure_client(call.from_user.id)
    if client is None:
        await call.message.answer(
            "اکانت وصل است ولی همین حالا در دسترس نیست. چند دقیقه بعد "
            "دوباره امتحان کنید."
        )
        return

    resolved = await manager.resolve_chat_id(client, task.source_ref)
    if resolved is None:
        await call.message.answer(
            f"❌ اکانت شما «{task.source_ref}» را نمی‌بیند.\n\n"
            "با همان اکانتی که وصل کرده‌اید عضو کانال مبدأ شوید و بعد "
            "دوباره «ارتقا» را بزنید.\n\n"
            "<i>کار فعلاً در حالت ساده و سالم ادامه دارد.</i>"
        )
        return

    async with get_session() as db:
        row = await db.get(Task, task.id)
        row.mode = Task.MODE_FULL
        row.source_id = resolved
        await db.commit()
    cache.invalidate_task(task.id)
    await manager.reload_user(call.from_user.id)
    await log_activity(
        user_id=call.from_user.id, task_id=task.id,
        event="mode_change", detail="ساده ← کامل",
    )
    await call.message.answer(
        "✅ <b>این کار حالا در حالت کامل است.</b>\n\n"
        "از این پس ایموجی پریمیوم هم منتقل می‌شود — به شرطی که اکانتی "
        "که وصل کرده‌اید خودش پریمیوم باشد. کپی پست‌های قدیمی هم از "
        "«🕓 کپی پیام‌های گذشته» در دسترس است.\n\n"
        "<i>اگر روزی از اکانتتان خارج شوید، این کار متوقف می‌شود — "
        "آن‌وقت می‌توانید به حالت ساده برش گردانید.</i>"
    )
    await show_task(call.message, task.id)


@router.callback_query(F.data.startswith("task:mode:down:"))
async def cb_downgrade(call: CallbackQuery) -> None:
    """بازگشت — چون یک‌طرفه کردنِ این در، یک تلهٔ پشتیبانی است.

    کسی که از اکانتش خارج شده یا سشنش باطل شده، کارِ کاملش می‌ایستد.
    بدون این دکمه تنها راهش ساختنِ کار از نو است — یعنی از دست دادنِ
    تنظیمات و آمار، برای مشکلی که یک کلیک راه دارد.
    """
    task = await _owned(call, int(call.data.split(":")[3]))
    if task is None:
        return
    await call.answer()

    # <b>شرطِ مثبت، نه فهرستی از حالت‌های بد.</b> مبدأ خصوصی می‌تواند
    # لینک دعوت باشد، آیدی عددی، یا عنوانِ کانال — شمردنشان یعنی دیر یا
    # زود یکی از قلم می‌افتد و کاری به حالت ساده می‌رود که هیچ‌وقت
    # نمی‌تواند بخواندش.
    if not (task.source_ref or "").startswith("@"):
        await call.message.answer(
            "این کار مبدأ خصوصی دارد و حالت ساده فقط کانال عمومی را "
            "می‌خواند. پس بازگشت ممکن نیست."
        )
        return

    # <b>وارسی، نه فرض.</b> در حالت ساده فرستنده خودِ ربات است. اگر
    # مشتری در این مدت ربات را از کانالش برداشته باشد، برگرداندنِ کار
    # یعنی کاری که سالم بود از این لحظه ساکت می‌ایستد — و دلیلش را
    # هیچ‌جا نمی‌بیند.
    bot = alerts.bot()
    if bot is not None:
        verdict = await botsend.can_post(bot, task.dest_id or task.dest_ref)
        if not verdict:
            await call.message.answer(
                "❌ ربات نمی‌تواند در کانال مقصد پست بگذارد، و در حالت "
                "ساده فرستنده خودِ ربات است.\n\n"
                f"{verdict.message}\n\n"
                "<i>کار فعلاً در حالت کامل و سالم ادامه دارد.</i>"
            )
            return

    try:
        used, total = await pool.capacity()
    except Exception:
        used, total = 1, 0
    if used >= total:
        await call.message.answer(
            "ظرفیت خواننده‌های حالت ساده همین حالا پر است. کمی بعد "
            "دوباره امتحان کنید.\n\n"
            "<i>کار در حالت کامل و سالم ادامه دارد.</i>"
        )
        return

    async with get_session() as db:
        row = await db.get(Task, task.id)
        row.mode = Task.MODE_SIMPLE
        await db.commit()
    cache.invalidate_task(task.id)
    from telkap.handlers.tasks import manager

    await manager.reload_user(call.from_user.id)
    await log_activity(
        user_id=call.from_user.id, task_id=task.id,
        event="mode_change", detail="کامل ← ساده",
    )
    await call.message.answer(
        "⚡️ <b>این کار به حالت ساده برگشت.</b>\n\n"
        "دیگر به اکانت شما وابسته نیست — از این پس خودِ ربات پست "
        "می‌گذارد (و بررسی کردیم که هنوز در کانالتان اجازه دارد).\n\n"
        "در عوض ایموجی پریمیوم به شکل معمولی می‌رسد و پست‌های قدیمی "
        "کپی نمی‌شوند."
    )
    await show_task(call.message, task.id)


@router.callback_query(F.data.startswith("task:mode:"))
async def cb_mode(call: CallbackQuery) -> None:
    """صفحه‌ی «این کار در کدام حالت است» — و راهِ عوض کردنش.

    این هندلر <b>بعد از</b> up/down ثبت می‌شود چون پیشوندش هر دو را
    هم می‌گیرد؛ aiogram اولین تطبیق را می‌برد.
    """
    task = await _owned(call, int(call.data.split(":")[2]))
    if task is None:
        return
    await call.answer()

    now = "⚡️ ساده (بدون اکانت)" if task.mode == Task.MODE_SIMPLE else "🔗 کامل (با اکانت شما)"
    await call.message.answer(
        f"<b>حالت این کار:</b> {now}\n\n" + MODE_COMPARISON,
        reply_markup=_mode_keyboard(task).as_markup(),
    )
