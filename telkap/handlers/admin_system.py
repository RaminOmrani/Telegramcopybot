"""سیستم: نقش ادمین‌ها و حالت «در دست تعمیر»."""
from __future__ import annotations

import logging
from datetime import UTC

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder

from telkap.config import get_settings
from telkap.db import log_activity
from telkap.handlers.admin_reports import guard
from telkap.handlers.common import Flow, parse_int
from telkap.models import utcnow
from telkap.plans import toman
from telkap.services import (
    ai,
    backup,
    cardinfo,
    coins,
    crypto,
    maintenance,
    roles,
    usdtrate,
    zarinpal,
)
from telkap.texts import fa_num

log = logging.getLogger(__name__)
router = Router(name="admin-system")


def _menu_kb() -> InlineKeyboardBuilder:
    kb = InlineKeyboardBuilder()
    kb.row(
        InlineKeyboardButton(text="👮 نقش ادمین‌ها", callback_data="sys:roles"),
        InlineKeyboardButton(text="🛠 حالت تعمیر", callback_data="sys:maint"),
    )
    kb.row(InlineKeyboardButton(text="🆔 گرفتن شناسه‌ی کانال", callback_data="sys:chatid"))
    if get_settings().web_enabled:
        kb.row(InlineKeyboardButton(text="🖥 پنل وب", callback_data="sys:web"))
    kb.row(InlineKeyboardButton(text="🤖 هوش مصنوعی", callback_data="sys:ai"))
    kb.row(InlineKeyboardButton(text="💳 راه‌های پرداخت", callback_data="sys:usdt"))
    kb.row(InlineKeyboardButton(text="🔙 بازگشت", callback_data="adm:home"))
    return kb


async def _show(event: CallbackQuery | Message, text: str, kb) -> None:
    if isinstance(event, CallbackQuery):
        await event.answer()
        await event.message.edit_text(text, reply_markup=kb.as_markup())
    else:
        await event.answer(text, reply_markup=kb.as_markup())


@router.callback_query(F.data == "adm:sys")
async def cb_system(call: CallbackQuery) -> None:
    if await guard(call, roles.CAP_SYSTEM):
        return
    on, _note = await maintenance.mode()
    channel = await backup.chat_id()
    await _show(
        call,
        "⚙️ <b>سیستم</b>\n\n"
        f"حالت تعمیر: <b>{'🔴 روشن' if on else '🟢 خاموش'}</b>\n"
        + (
            f"کانال پشتیبان: <code>{channel}</code>"
            if channel
            else "کانال پشتیبان: <b>⚠️ تنظیم نشده</b> — نسخه‌ها فقط روی همین "
            "سرورند. «🆔 گرفتن شناسه‌ی کانال» را بزنید."
        ),
        _menu_kb(),
    )


@router.callback_query(F.data == "sys:ai")
async def cb_ai_status(call: CallbackQuery) -> None:
    """هر چهار مدل را واقعاً صدا می‌زند و می‌گوید کدام جواب داد.

    نام مدل‌ها باید مو‌به‌مو با فهرست سرویس بخوانند. به‌جای اینکه کسی
    حدس بزند و بعد در لاگ دنبال ۴۰۴ بگردد، همین‌جا معلوم می‌شود.
    """
    if await guard(call, roles.CAP_SYSTEM):
        return
    await call.answer("در حال تست…")
    _ok, report = await ai.health()
    kb = InlineKeyboardBuilder()
    kb.row(InlineKeyboardButton(text="🔄 تست دوباره", callback_data="sys:ai"))
    kb.row(InlineKeyboardButton(text="🔙 بازگشت", callback_data="adm:sys"))
    try:
        await call.message.edit_text(report, reply_markup=kb.as_markup())
    except Exception:
        await call.message.answer(report, reply_markup=kb.as_markup())


# ─────────────────────────────────────────────────── راه‌های پرداخت
# نرخ خودکار هر ربع ساعت تازه می‌شود. یک ساعت یعنی چهار دور پشت سر هم
# رد شده — دیگر «کمی دیر» نیست، یعنی چیزی خراب است.
STALE_AFTER_SECONDS = 3600


def _rate_age(when) -> tuple[str, bool]:
    """چقدر از آخرین به‌روزرسانی گذشته، و آیا زیادی گذشته."""
    if when is None:
        return "", False
    if when.tzinfo is None:
        when = when.replace(tzinfo=UTC)
    seconds = int((utcnow() - when).total_seconds())
    if seconds < 0:
        return "", False

    if seconds < 90:
        text = "همین حالا"
    elif seconds < 3600:
        text = f"{fa_num(seconds // 60)} دقیقه پیش"
    elif seconds < 86400:
        text = f"{fa_num(seconds // 3600)} ساعت پیش"
    else:
        text = f"{fa_num(seconds // 86400)} روز پیش"

    old = seconds >= STALE_AFTER_SECONDS
    return (f"⚠️ {text}" if old else text), old


def _gateway_state(merchant: str, ready: bool) -> str:
    """چرا درگاه فعال نیست — دقیقاً کدام قطعه کم است.

    دو شرط دارد و پیام باید بگوید کدام‌یک نیست، وگرنه ادمین کد پذیرنده
    را درست وارد می‌کند و درگاه همچنان خاموش می‌ماند بی‌آنکه بفهمد
    چیزِ دیگری لازم بوده.
    """
    if ready:
        # کد پذیرنده کامل نشان داده نمی‌شود؛ صفحه‌ی پنل جای دیدنی
        # نگه داشتنِ اعتبارنامه نیست.
        return f"فعال — کد پذیرنده: {merchant[:8]}…"
    if not merchant:
        return "کد پذیرنده ثبت نشده"
    return "کد پذیرنده هست، ولی WEB_BASE_URL خالی است — پنل وب لازم دارد"


async def _usdt_screen(event) -> None:
    """همه‌ی راه‌های پرداخت در یک صفحه.

    کارت تا امروز فقط در <code>.env</code> بود و از پنل دیده نمی‌شد —
    یعنی کسی که سرور را بلد نبود اصلاً نمی‌توانست فروش راه بیندازد.
    حالا هر سه راه یک‌جا و یک‌شکل‌اند.
    """
    card = await cardinfo.number()
    name = await cardinfo.holder()
    wallet = await crypto.address()
    auto = await usdtrate.is_auto()
    ready_coins = await crypto.ready_coins()
    gateway = await zarinpal.configured()
    merchant = await zarinpal.merchant()
    ready = bool(card) or bool(ready_coins) or gateway

    head = "💳 <b>راه‌های پرداخت</b>\n\n"
    if not ready:
        head += (
            "🚨 <b>هیچ راهی تنظیم نشده — یعنی کسی نمی‌تواند بخرد.</b>\n"
            "<i>مشتری به‌جای صفحه‌ی پرداخت، به پشتیبانی ارجاع داده "
            "می‌شود. دست‌کم یکی را کامل کنید.</i>\n\n"
        )

    lines = [
        f"{'✅' if card else '❌'} <b>کارت‌به‌کارت</b>",
        f"شماره: <code>{cardinfo.pretty(card) if card else 'تنظیم نشده'}</code>",
        f"به نام: {name or '—'}",
        "",
        f"🌐 نشانی ولت ترون: <code>{wallet or 'تنظیم نشده'}</code>",
        "<i>تتر و ترون هر دو به همین یک نشانی واریز می‌شوند.</i>",
        "",
    ]
    stale = False
    for code in coins.all_codes():
        spec = coins.get(code)
        rate = await crypto.rate(code)
        percent = await usdtrate.margin(code)
        lines.append(
            f"{'✅' if wallet and rate else '❌'} <b>{spec.label}</b> — "
            f"{toman(rate) if rate else 'نرخ تنظیم نشده'}"
            + (f"  <i>(—{fa_num(percent)}٪)</i>" if auto and rate else "")
        )
        # سن نرخ فقط وقتی معنا دارد که قرار باشد خودکار تازه شود.
        # نرخ دستی طبیعتاً قدیمی است و هشدارِ بی‌مورد فقط نویز است.
        if auto and rate:
            text, old = _rate_age(await crypto.rate_updated_at(code))
            if text:
                lines.append(f"<i>آخرین به‌روزرسانی: {text}</i>")
                stale = stale or old
    lines += [
        "",
        f"نرخ خودکار: {'✅ روشن' if auto else '❌ خاموش'}",
        "",
        f"{'✅' if gateway else '❌'} <b>درگاه زرین‌پال</b>",
        f"<i>{_gateway_state(merchant, gateway)}</i>",
    ]
    body = "\n".join(lines)

    if auto and stale:
        # نرخ کهنه از نرخ نداشته بدتر است: فروش ادامه دارد و همه‌چیز
        # سالم به نظر می‌رسد، فقط با قیمتِ هفته‌ی پیش.
        note = (
            "\n\n⚠️ <b>نرخ بیش از یک ساعت است تازه نشده.</b>\n"
            "<i>یعنی خواندن از صرافی می‌خوابد و فروش با قیمت قدیمی "
            "ادامه دارد. «⏬ گرفتن نرخ‌ها الان» را بزنید تا علتش را "
            "بگوید.</i>"
        )
    elif auto:
        note = (
            "\n\n<i>نرخ هر ربع ساعت از نوبیتکس گرفته می‌شود — بازار "
            "USDTIRT برای تتر و TRXIRT برای ترون. حاشیه کمی <b>زیر</b> "
            "بازار است تا ارزی که می‌گیرید دست‌کم به اندازه‌ی قیمت تومانی "
            "بیرزد. ترون حاشیه‌ی بیشتری دارد چون نوسانش بیشتر است.</i>"
        )
    else:
        note = (
            "\n\n<i>نرخ دستی است. با هر تکان بازار یا ضرر می‌کنید یا "
            "مشتری گران می‌خرد — «نرخ خودکار» این را حل می‌کند.</i>"
        )

    kb = InlineKeyboardBuilder()
    kb.row(InlineKeyboardButton(text="💳 شماره کارت", callback_data="sys:card"))
    kb.row(InlineKeyboardButton(text="👤 نام صاحب حساب", callback_data="sys:cardname"))
    kb.row(InlineKeyboardButton(text="🏦 نشانی ولت تتر", callback_data="sys:usdtaddr"))
    kb.row(InlineKeyboardButton(text="🏧 کد پذیرنده‌ی زرین‌پال", callback_data="sys:zarin"))
    kb.row(
        InlineKeyboardButton(
            text=f"🔄 نرخ خودکار: {'روشن' if auto else 'خاموش'}",
            callback_data="sys:autorate",
        )
    )
    if auto:
        kb.row(
            InlineKeyboardButton(text="📉 حاشیه‌ی تتر", callback_data="sys:margin:usdt"),
            InlineKeyboardButton(text="📉 حاشیه‌ی ترون", callback_data="sys:margin:trx"),
        )
        kb.row(InlineKeyboardButton(text="⏬ گرفتن نرخ‌ها الان", callback_data="sys:ratenow"))
    else:
        kb.row(
            InlineKeyboardButton(text="💱 نرخ تتر", callback_data="sys:usdtrate:usdt"),
            InlineKeyboardButton(text="💱 نرخ ترون", callback_data="sys:usdtrate:trx"),
        )
    kb.row(InlineKeyboardButton(text="🔙 بازگشت", callback_data="adm:sys"))
    await _show(event, head + body + note, kb)


@router.callback_query(F.data == "sys:card")
async def cb_card(call: CallbackQuery, state: FSMContext) -> None:
    if await guard(call, roles.CAP_MONEY):
        return
    await call.answer()
    await state.set_state(Flow.card_number)
    await call.message.answer(
        "💳 شماره کارت را بفرستید — ۱۶ رقم.\n\n"
        "<i>با فاصله یا خط تیره هم اشکالی ندارد؛ ارقام فارسی هم "
        "پذیرفته می‌شود.</i>\n\n"
        "برای انصراف /cancel را بزنید."
    )


@router.message(Flow.card_number)
async def got_card(message: Message, state: FSMContext) -> None:
    saved = await cardinfo.set_number(message.text or "", admin_id=message.from_user.id)
    if saved is None:
        await message.answer(
            "⚠️ شماره کارت باید دقیقاً ۱۶ رقم باشد.\nبرای انصراف /cancel را بزنید."
        )
        return
    await state.clear()
    await log_activity(
        user_id=message.from_user.id, event="admin", detail="شماره کارت عوض شد"
    )
    await message.answer(f"✅ ثبت شد: <code>{cardinfo.pretty(saved)}</code>")
    await _usdt_screen(message)


@router.callback_query(F.data == "sys:zarin")
async def cb_zarinpal(call: CallbackQuery, state: FSMContext) -> None:
    if await guard(call, roles.CAP_MONEY):
        return
    await call.answer()
    await state.set_state(Flow.zarinpal_merchant)

    base = (get_settings().web_base_url or "").rstrip("/")
    where = (
        f"<code>{base}{zarinpal.CALLBACK_PATH}</code>"
        if base
        else "<i>(اول پنل وب را راه بیندازید)</i>"
    )
    await call.message.answer(
        "🏧 <b>کد پذیرنده‌ی زرین‌پال</b> را بفرستید.\n\n"
        "از پنل زرین‌پال ← درگاه‌های پرداخت ← مشاهده‌ی کد پذیرنده.\n"
        "شکلش این است: <code>xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx</code>\n\n"
        "<b>یک کار دیگر هم لازم است</b> که فراموش کردنش رایج‌ترین علت "
        "«پول کم شد ولی اشتراک فعال نشد» است: در پنل زرین‌پال، نشانی "
        f"بازگشت را همین ثبت کنید 👇\n{where}\n\n"
        "<i>پیام شما بلافاصله پس از ثبت پاک می‌شود.</i>\n\n"
        "برای انصراف /cancel را بزنید."
    )


@router.message(Flow.zarinpal_merchant)
async def got_zarinpal(message: Message, state: FSMContext) -> None:
    """کد پذیرنده را ثبت می‌کند و پیامِ حاوی آن را پاک می‌کند.

    <b>چرا پاک می‌شود.</b> کد پذیرنده اعتبارنامه است و در تاریخچه‌ی چت
    ماندنش یعنی هرکس روزی به این گوشی یا این اکانت برسد آن را دارد.
    پاک کردن ممکن است شکست بخورد (پیام‌های قدیمی‌تر از ۴۸ ساعت)، و آن
    شکست نباید جلوی ثبت را بگیرد.
    """
    saved = await zarinpal.set_merchant(
        message.text or "", admin_id=message.from_user.id
    )
    if saved is None:
        await message.answer(
            "⚠️ کد پذیرنده باید دقیقاً همان شکل UUID باشد:\n"
            "<code>xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx</code>\n\n"
            "برای انصراف /cancel را بزنید."
        )
        return

    try:
        await message.delete()
    except Exception:                      # noqa: BLE001
        log.info("پاک کردن پیام کد پذیرنده ممکن نشد", exc_info=True)

    await state.clear()
    await log_activity(
        user_id=message.from_user.id, event="admin", detail="کد پذیرنده‌ی زرین‌پال عوض شد"
    )
    await message.answer(
        f"✅ ثبت شد: <code>{saved[:8]}…</code>\n\n"
        "<i>پیام شما پاک شد. اگر هنوز آنجاست، خودتان حذفش کنید.</i>"
    )
    await _usdt_screen(message)


@router.callback_query(F.data == "sys:cardname")
async def cb_card_name(call: CallbackQuery, state: FSMContext) -> None:
    if await guard(call, roles.CAP_MONEY):
        return
    await call.answer()
    await state.set_state(Flow.card_holder)
    await call.message.answer(
        "👤 نام صاحب حساب را بفرستید.\n\n"
        "<i>مشتری این را کنار شماره کارت می‌بیند. بودنش اعتماد "
        "می‌سازد، چون معلوم است پول به کجا می‌رود.</i>\n\n"
        "برای انصراف /cancel را بزنید."
    )


@router.message(Flow.card_holder)
async def got_card_name(message: Message, state: FSMContext) -> None:
    saved = await cardinfo.set_holder(message.text or "", admin_id=message.from_user.id)
    if saved is None:
        await message.answer("⚠️ نام خالی است.\nبرای انصراف /cancel را بزنید.")
        return
    await state.clear()
    await message.answer(f"✅ ثبت شد: <b>{saved}</b>")
    await _usdt_screen(message)


@router.callback_query(F.data == "sys:autorate")
async def cb_auto_rate(call: CallbackQuery) -> None:
    if await guard(call, roles.CAP_MONEY):
        return
    turning_on = not await usdtrate.is_auto()
    await usdtrate.set_auto(turning_on, admin_id=call.from_user.id)

    if turning_on:
        # همان لحظه یک بار گرفته می‌شود، وگرنه تا ربع ساعت بعد نرخ
        # قدیمی می‌ماند و کاربر فکر می‌کند کار نکرده
        fresh = await usdtrate.refresh(force=True)
        await call.answer(
            f"روشن شد — نرخ {fresh:,} تومان" if fresh else "روشن شد",
            show_alert=bool(fresh),
        )
    else:
        await call.answer("خاموش شد")
    await _usdt_screen(call)


@router.callback_query(F.data == "sys:ratenow")
async def cb_rate_now(call: CallbackQuery) -> None:
    """گرفتن فوریِ نرخ همه‌ی ارزها — بدون محافظِ جهش، چون ادمین خواسته.

    <b>پیام باید بگوید چه شد، نه اینکه «نشد».</b> پیام قبلی سه حالتِ
    کاملاً متفاوت را یکی می‌کرد و ادمین برای فهمیدن علت مجبور بود لاگ
    سرور را ببیند — یعنی عملاً هیچ‌وقت نمی‌فهمید.
    """
    if await guard(call, roles.CAP_MONEY):
        return

    await call.answer("در حال گرفتن نرخ‌ها…")
    results = await usdtrate.refresh_all(force=True)

    lines = []
    for code, outcome in results.items():
        symbol = coins.get(code).symbol if coins.get(code) else code
        if outcome.changed:
            lines.append(f"✅ {symbol}: {toman(outcome.rate)}")
        elif outcome.error:
            lines.append(f"❌ {symbol}: {outcome.error}")
        else:
            lines.append(f"➖ {symbol}: {outcome.note}")

    failed = [o for o in results.values() if o.error]
    hint = ""
    if failed:
        hint = (
            "\n\n<i>هر دو منبع نوبیتکس امتحان شد. اگر پیام درباره‌ی "
            "اتصال است، یعنی سرور به صرافی نمی‌رسد — تا رفع شدنش نرخ "
            "را دستی بگذارید تا فروش نخوابد.</i>"
        )

    await call.message.answer(
        "⏬ <b>نتیجه‌ی گرفتن نرخ</b>\n\n" + "\n".join(lines) + hint
    )
    await _usdt_screen(call)


@router.callback_query(F.data.startswith("sys:margin:"))
async def cb_margin(call: CallbackQuery, state: FSMContext) -> None:
    if await guard(call, roles.CAP_MONEY):
        return
    coin = call.data.split(":")[2]
    spec = coins.get(coin)
    if spec is None:
        await call.answer("ارز ناشناخته", show_alert=True)
        return
    await call.answer()
    await state.set_state(Flow.usdt_margin)
    await state.update_data(rate_coin=coin)
    await call.message.answer(
        f"📉 حاشیه‌ی اطمینان <b>{spec.symbol}</b> را به درصد بفرستید (۰ تا ۲۰).\n\n"
        "<i>نرخ فروش این‌قدر <b>پایین‌تر</b> از بازار گذاشته می‌شود. "
        "چرا پایین‌تر: مبلغ ارزی از تقسیم می‌آید، پس نرخ کمتر یعنی "
        "مشتری بیشتر می‌پردازد — و همین جلوی ضرر شما را می‌گیرد.</i>\n\n"
        f"پیشنهاد برای {spec.symbol}: {fa_num(spec.default_margin)}\n\n"
        "برای انصراف /cancel را بزنید."
    )


@router.message(Flow.usdt_margin)
async def got_margin(message: Message, state: FSMContext) -> None:
    coin = (await state.get_data()).get("rate_coin", coins.USDT)
    saved = await usdtrate.set_margin(
        message.text or "", coin=coin, admin_id=message.from_user.id
    )
    if saved is None:
        await message.answer(
            "⚠️ عددی بین ۰ تا ۲۰ بفرستید.\nبرای انصراف /cancel را بزنید."
        )
        return
    await state.clear()
    await usdtrate.refresh(coin, force=True)
    await message.answer(f"✅ حاشیه روی {fa_num(saved)}٪ تنظیم شد.")
    await _usdt_screen(message)


@router.callback_query(F.data == "sys:usdt")
async def cb_usdt(call: CallbackQuery) -> None:
    if await guard(call, roles.CAP_MONEY):
        return
    await _usdt_screen(call)


@router.callback_query(F.data == "sys:usdtaddr")
async def cb_usdt_address(call: CallbackQuery, state: FSMContext) -> None:
    if await guard(call, roles.CAP_MONEY):
        return
    await call.answer()
    await state.set_state(Flow.usdt_address)
    await call.message.answer(
        "نشانی ولت <b>TRC20</b> خود را بفرستید.\n\n"
        "<i>با T شروع می‌شود و ۳۴ نویسه است. اشتباه بودنش یعنی پولِ "
        "مشتری به جای دیگری می‌رود، پس دوبار چک کنید.</i>\n\n"
        "برای انصراف /cancel را بزنید."
    )


@router.message(Flow.usdt_address)
async def got_usdt_address(message: Message, state: FSMContext) -> None:
    saved = await crypto.set_address(message.text or "", admin_id=message.from_user.id)
    if saved is None:
        await message.answer(
            "⚠️ این نشانی معتبر نیست. نشانی ترون با T شروع می‌شود و ۳۴ "
            "نویسه دارد.\nبرای انصراف /cancel را بزنید."
        )
        return
    await state.clear()
    await log_activity(
        user_id=message.from_user.id, event="admin", detail="نشانی ولت تتر عوض شد"
    )
    await message.answer("✅ نشانی ولت ثبت شد.")
    await _usdt_screen(message)


@router.callback_query(F.data.startswith("sys:usdtrate"))
async def cb_usdt_rate(call: CallbackQuery, state: FSMContext) -> None:
    if await guard(call, roles.CAP_MONEY):
        return
    parts = call.data.split(":")
    coin = parts[2] if len(parts) > 2 else coins.USDT
    spec = coins.get(coin)
    if spec is None:
        await call.answer("ارز ناشناخته", show_alert=True)
        return
    await call.answer()
    await state.set_state(Flow.usdt_rate)
    await state.update_data(rate_coin=coin)
    await call.message.answer(
        f"نرخ هر <b>{spec.symbol}</b> را به تومان بفرستید.\n\n"
        "<i>بدون جداکننده، مثل 95000</i>\n\n"
        "برای انصراف /cancel را بزنید."
    )


@router.message(Flow.usdt_rate)
async def got_usdt_rate(message: Message, state: FSMContext) -> None:
    coin = (await state.get_data()).get("rate_coin", coins.USDT)
    spec = coins.get(coin) or coins.get(coins.USDT)
    saved = await crypto.set_rate(
        message.text or "", coin=coin, admin_id=message.from_user.id
    )
    if saved is None:
        await message.answer(
            "⚠️ عدد معتبر نیست.\nنرخ را به تومان و بدون جداکننده بنویسید، "
            "مثل 95000.\nبرای انصراف /cancel را بزنید."
        )
        return
    await state.clear()
    await log_activity(
        user_id=message.from_user.id,
        event="admin",
        detail=f"نرخ {spec.symbol} → {saved}",
    )
    await message.answer(f"✅ نرخ ثبت شد: {toman(saved)} برای هر {spec.symbol}.")
    await _usdt_screen(message)


@router.callback_query(F.data == "sys:web")
async def cb_web_panel(call: CallbackQuery) -> None:
    """لینک یک‌بارمصرف ورود به پنل وب.

    رمز عبوری در کار نیست: هویت ادمین را همین چت تلگرام تأیید کرده. لینک
    پنج دقیقه اعتبار دارد و با اولین باز شدن می‌سوزد.
    """
    if await guard(call, roles.CAP_SYSTEM):
        return
    cfg = get_settings()
    if not cfg.web_enabled:
        await call.answer("پنل وب روشن نیست.", show_alert=True)
        return
    if not cfg.web_base_url:
        await call.answer()
        await call.message.answer(
            "🖥 <b>پنل وب</b>\n\n"
            "پنل بالا آمده ولی <code>WEB_BASE_URL</code> در فایل <code>.env</code> "
            "خالی است، پس نمی‌دانم چه آدرسی به شما بدهم.\n\n"
            "آدرس عمومی پنل را آنجا بگذارید و ربات را ری‌استارت کنید، مثل:\n"
            "<code>WEB_BASE_URL=https://botpanel.softmiliac.com</code>"
        )
        return

    from telkap.web import auth as web_auth

    token = web_auth.issue_login_token(call.from_user.id)
    await call.answer()
    await call.message.answer(
        "🖥 <b>ورود به پنل وب</b>\n\n"
        f"{cfg.web_base_url}/enter?t={token}\n\n"
        "⏳ این لینک <b>۵ دقیقه</b> اعتبار دارد و با اولین باز شدن می‌سوزد.\n"
        "🔒 آن را برای کسی نفرستید — هرکس بازش کند با نام شما وارد می‌شود.",
        disable_web_page_preview=True,
    )
    await log_activity(
        user_id=call.from_user.id,
        event="web_login_link",
        detail="لینک ورود به پنل وب ساخته شد",
    )


@router.callback_query(F.data == "sys:chatid")
async def cb_chatid(call: CallbackQuery, state: FSMContext) -> None:
    if await guard(call, roles.CAP_SYSTEM):
        return
    await call.answer()
    await state.set_state(Flow.admin_chatid)
    await call.message.answer(
        "🆔 <b>گرفتن شناسه‌ی کانال</b>\n\n"
        "یک پیام از آن کانال را برای من <b>فوروارد</b> کنید تا شناسه‌اش را "
        "بگویم و بتوانید با یک دکمه کانال پشتیبانش کنید.\n\n"
        "<i>اگر کانال فوروارد را بسته، ربات را همان‌جا ادمین کنید، یک پیام "
        "بفرستید و همان را فوروارد کنید.</i>\n\n"
        "انصراف: /cancel"
    )


# --------------------------------------------------------- نقش ادمین‌ها
async def _roles_screen() -> tuple[str, InlineKeyboardBuilder]:
    owners = get_settings().admin_ids
    rows = await roles.listing()

    text = ["👮 <b>نقش ادمین‌ها</b>\n"]
    text.append("<b>مالکان (از فایل .env)</b>")
    text.append(
        "\n".join(f"• <code>{uid}</code>" for uid in owners) if owners else "—"
    )
    text.append("\nاین‌ها از پنل قابل حذف نیستند؛ فقط با ویرایش <code>.env</code>.\n")

    if rows:
        text.append("<b>ادمین‌های افزوده‌شده</b>")
        for row in rows:
            label = roles.ROLE_LABELS.get(row.role, row.role)
            note = f" — {row.note}" if row.note else ""
            text.append(f"• <code>{row.user_id}</code>: {label}{note}")
    else:
        text.append("هنوز ادمین دیگری اضافه نشده.")

    text.append(
        "\n<b>هر نقش چه می‌بیند</b>\n"
        + "\n".join(
            f"{roles.ROLE_LABELS[role]}"
            for role in (roles.ROLE_OWNER, roles.ROLE_FINANCE, roles.ROLE_SUPPORT)
        )
    )

    kb = InlineKeyboardBuilder()
    kb.row(InlineKeyboardButton(text="➕ افزودن ادمین", callback_data="sys:radd"))
    for row in rows:
        kb.row(
            InlineKeyboardButton(
                text=f"🗑 حذف {row.user_id}", callback_data=f"sys:rdel:{row.user_id}"
            )
        )
    kb.row(InlineKeyboardButton(text="🔙 بازگشت", callback_data="adm:sys"))
    return "\n".join(text), kb


@router.callback_query(F.data == "sys:roles")
async def cb_roles(call: CallbackQuery) -> None:
    if await guard(call, roles.CAP_SYSTEM):
        return
    text, kb = await _roles_screen()
    await _show(call, text, kb)


@router.callback_query(F.data == "sys:radd")
async def cb_role_add(call: CallbackQuery, state: FSMContext) -> None:
    if await guard(call, roles.CAP_SYSTEM):
        return
    await call.answer()
    await state.set_state(Flow.admin_role)
    kb = InlineKeyboardBuilder()
    kb.row(InlineKeyboardButton(text="🔙 انصراف", callback_data="sys:roles"))
    await call.message.edit_text(
        "➕ <b>افزودن ادمین</b>\n\n"
        "شناسه‌ی عددی و نقش را در یک خط بفرستید:\n"
        "<code>123456789 support</code>\n\n"
        "نقش‌های مجاز:\n"
        "<code>owner</code> — همه‌چیز\n"
        "<code>finance</code> — پرداخت، طرح‌ها، کد تخفیف، گزارش‌ها\n"
        "<code>support</code> — تیکت‌ها و کاربران\n\n"
        "می‌توانید بعد از نقش، یک یادداشت هم بنویسید:\n"
        "<code>123456789 support علی — شیفت شب</code>",
        reply_markup=kb.as_markup(),
    )


@router.message(Flow.admin_role)
async def do_role_add(message: Message, state: FSMContext) -> None:
    if not await roles.can(message.from_user.id, roles.CAP_SYSTEM):
        await state.clear()
        return
    parts = (message.text or "").split(maxsplit=2)
    user_id = parse_int(parts[0]) if parts else None
    role = parts[1].strip().lower() if len(parts) > 1 else ""
    note = parts[2].strip() if len(parts) > 2 else ""

    if user_id is None or role not in roles.ROLE_CAPS:
        await message.answer(
            "قالب درست نیست. مثال:\n<code>123456789 support</code>"
        )
        return

    await state.clear()
    ok = await roles.set_role(
        user_id, role, note=note, added_by=message.from_user.id
    )
    if not ok:
        await message.answer(
            "این شناسه در <code>.env</code> ثبت شده و از قبل مالک است."
        )
        return

    await log_activity(
        user_id=user_id,
        actor_id=message.from_user.id,
        event="admin_role_set",
        detail=f"نقش {role}",
        level="warning",
    )
    text, kb = await _roles_screen()
    await message.answer(
        f"✅ نقش <b>{roles.ROLE_LABELS[role]}</b> به <code>{user_id}</code> داده شد."
    )
    await message.answer(text, reply_markup=kb.as_markup())


@router.callback_query(F.data.startswith("sys:rdel:"))
async def cb_role_del(call: CallbackQuery) -> None:
    if await guard(call, roles.CAP_SYSTEM):
        return
    user_id = parse_int(call.data.split(":")[-1])
    if user_id is None:
        await call.answer("شناسه نامعتبر", show_alert=True)
        return
    removed = await roles.remove(user_id)
    await call.answer("دسترسی گرفته شد" if removed else "چیزی برای حذف نبود")
    if removed:
        await log_activity(
            user_id=user_id,
            actor_id=call.from_user.id,
            event="admin_role_removed",
            level="warning",
        )
    text, kb = await _roles_screen()
    await call.message.edit_text(text, reply_markup=kb.as_markup())


# ---------------------------------------------------------- حالت تعمیر
async def _maint_screen() -> tuple[str, InlineKeyboardBuilder]:
    on, note = await maintenance.mode()
    text = (
        "🛠 <b>حالت تعمیر</b>\n\n"
        f"وضعیت: <b>{'🔴 روشن' if on else '🟢 خاموش'}</b>\n\n"
        "وقتی روشن باشد، کاربران عادی به‌جای خطای مبهم این پیام را می‌بینند "
        "و ادمین‌ها بدون محدودیت کار می‌کنند:\n\n"
        f"<i>{note}</i>\n\n"
        "کارهای کپی در پس‌زمینه ادامه دارند؛ فقط کار با ربات متوقف می‌شود."
    )
    kb = InlineKeyboardBuilder()
    kb.row(
        InlineKeyboardButton(
            text="🟢 خاموش کن" if on else "🔴 روشن کن",
            callback_data="sys:maint:off" if on else "sys:maint:on",
        )
    )
    kb.row(InlineKeyboardButton(text="✍️ تغییر متن پیام", callback_data="sys:maint:note"))
    kb.row(InlineKeyboardButton(text="🔙 بازگشت", callback_data="adm:sys"))
    return text, kb


@router.callback_query(F.data == "sys:maint")
async def cb_maint(call: CallbackQuery) -> None:
    if await guard(call, roles.CAP_SYSTEM):
        return
    text, kb = await _maint_screen()
    await _show(call, text, kb)


@router.callback_query(F.data.in_({"sys:maint:on", "sys:maint:off"}))
async def cb_maint_toggle(call: CallbackQuery) -> None:
    if await guard(call, roles.CAP_SYSTEM):
        return
    turning_on = call.data.endswith(":on")
    _on, note = await maintenance.mode()
    await maintenance.set_mode(turning_on, note=note, by=call.from_user.id)
    await log_activity(
        actor_id=call.from_user.id,
        event="maintenance_mode",
        detail="روشن" if turning_on else "خاموش",
        level="warning",
    )
    await call.answer("حالت تعمیر روشن شد" if turning_on else "ربات دوباره باز است")
    text, kb = await _maint_screen()
    await call.message.edit_text(text, reply_markup=kb.as_markup())


@router.callback_query(F.data == "sys:maint:note")
async def cb_maint_note(call: CallbackQuery, state: FSMContext) -> None:
    if await guard(call, roles.CAP_SYSTEM):
        return
    await call.answer()
    await state.set_state(Flow.admin_maint_note)
    kb = InlineKeyboardBuilder()
    kb.row(InlineKeyboardButton(text="🔙 انصراف", callback_data="sys:maint"))
    await call.message.edit_text(
        "✍️ متنی که کاربران در حالت تعمیر می‌بینند را بفرستید.\n\n"
        "برای برگشت به متن پیش‌فرض، یک خط تیره <code>-</code> بفرستید.",
        reply_markup=kb.as_markup(),
    )


@router.message(Flow.admin_maint_note)
async def do_maint_note(message: Message, state: FSMContext) -> None:
    if not await roles.can(message.from_user.id, roles.CAP_SYSTEM):
        await state.clear()
        return
    raw = (message.text or "").strip()
    await state.clear()
    on, _note = await maintenance.mode()
    await maintenance.set_mode(
        on,
        note="" if raw in {"", "-"} else raw,
        by=message.from_user.id,
    )
    text, kb = await _maint_screen()
    await message.answer("✅ متن ذخیره شد.")
    await message.answer(text, reply_markup=kb.as_markup())
