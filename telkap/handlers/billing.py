"""خرید اشتراک: انتخاب پلن، پرداخت کارت‌به‌کارت، ارسال رسید.

کل جریان داخل تلگرام است. ادمین رسید را با یک دکمه تأیید می‌کند و
اشتراک خودکار فعال می‌شود.
"""
from __future__ import annotations

import logging

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)
from aiogram.utils.keyboard import InlineKeyboardBuilder

from telkap.config import get_settings
from telkap.handlers.common import Flow, get_or_create_user, parse_int
from telkap.keyboards import (
    PLAN_ICONS,
    credit_packs_menu,
    credits_menu,
    long_term_menu,
    menu_texts,
    plans_menu,
)
from telkap.models import PaymentRequest
from telkap.plans import (
    CREDIT_KINDS,
    CREDIT_WATERMARK,
    FEAT_PRIVATE,
    FEAT_VIP,
    POPULAR_CODE,
    credit_price,
    credit_unit,
    get_plan,
    long_term,
    purchasable,
    toman,
)
from telkap.services import (
    cardinfo,
    coins,
    credits,
    crypto,
    cryptocheck,
    payments,
    roles,
    zarinpal,
)
from telkap.services.subscription import active_subscription, remaining_days
from telkap.texts import fa_num

MAX_CREDIT_UNITS = 20_000

log = logging.getLogger(__name__)
router = Router(name="billing")


RULE = "━━━━━━━━━━━━━━━━━━"


def _plans_text() -> str:
    lines = ["💎 <b>طرح‌های اشتراک</b>", RULE]
    for plan in purchasable():
        icon = PLAN_ICONS.get(plan.code, "▫️")
        star = "  ⭐️ <i>محبوب‌ترین</i>" if plan.code == POPULAR_CODE else ""
        lines.append(f"\n{icon} <b>{plan.title}</b> — {plan.price_label}{star}")
        lines.append(f"<i>{plan.tagline}</i>")
        for perk in plan.perks:
            lines.append(f"   ✓ {perk}")
    lines.append(f"\n{RULE}")
    lines.append(
        "🎫 <b>اعتبار جداگانه</b> — سهمیه‌ی واترمارک و پیام‌های گذشته برای کل "
        "دوره است. اگر وسط دوره کم آوردید، به‌جای طرح بالاتر می‌توانید واحدی "
        f"بخرید (هر واحد {toman(credit_unit(CREDIT_WATERMARK))}، بدون انقضا)."
    )
    lines.append("\nبرای خرید، یکی از گزینه‌های زیر را بزنید 👇")
    return "\n".join(lines)


def _compare_text() -> str:
    """جدول مقایسه‌ی همه‌ی طرح‌ها در یک نگاه."""
    def mark(flag: bool) -> str:
        return "✅" if flag else "➖"

    rows = [
        ("💰 قیمت", lambda p: p.price_label.replace(" تومان", "")),
        ("📅 مدت", lambda p: f"{fa_num(p.days)} روز"),
        ("📨 پیام کل دوره", lambda p: p.messages_label),
        ("📋 کار کپی", lambda p: fa_num(p.max_tasks)),
        ("📤 مقصد هر کار", lambda p: fa_num(p.max_destinations)),
        ("💧 واترمارک", lambda p: p.watermark_label),
        ("🕓 پیام گذشته", lambda p: p.history_label),
        ("🔒 کانال خصوصی", lambda p: mark(p.has(FEAT_PRIVATE))),
        ("👑 پشتیبانی ویژه", lambda p: mark(p.has(FEAT_VIP))),
    ]

    blocks = ["📊 <b>مقایسه‌ی طرح‌ها</b>", RULE]
    for plan in purchasable():
        icon = PLAN_ICONS.get(plan.code, "▫️")
        blocks.append(f"\n{icon} <b>{plan.title}</b>")
        for label, getter in rows:
            blocks.append(f"   {label}: {getter(plan)}")
    blocks.append(f"\n{RULE}")
    blocks.append(
        "<i>همه‌ی سهمیه‌ها برای کل دوره‌ی اشتراک‌اند، نه روزانه. با تمدید یا "
        "خرید طرح تازه از نو پر می‌شوند. اگر وسط دوره سهمیه‌ی واترمارک یا "
        "پیام گذشته کم آوردید، اعتبار واحدی بخرید — اعتبار انقضا ندارد.</i>"
    )
    return "\n".join(blocks)


@router.message(Command("plans"))
@router.message(F.text.in_(menu_texts("menu.plans")))
async def show_plans(message: Message) -> None:
    sub = await active_subscription(message.from_user.id)
    header = ""
    if sub:
        plan = get_plan(sub.plan_code)
        days = await remaining_days(message.from_user.id)
        header = (
            f"اشتراک فعلی: <b>{plan.title if plan else sub.plan_code}</b> "
            f"({fa_num(days)} روز باقی‌مانده)\n"
            "با خرید پلن جدید، از انتهای اشتراک فعلی تمدید می‌شود.\n\n"
        )
    await message.answer(header + _plans_text(), reply_markup=plans_menu())


async def _quote_screen(target: Message, user_id: int, plan_code: str, coupon: str):
    """صفحه‌ی «قبل از پرداخت»: ریز قیمت با همه‌ی کسری‌ها."""
    priced = await payments.quote(user_id, plan_code, coupon)
    if priced is None:
        await target.answer("این پلن وجود ندارد.")
        return

    plan = priced["plan"]
    lines = [
        f"🧾 <b>{plan.title}</b>",
        f"مدت: {fa_num(plan.days)} روز",
        "",
        f"قیمت طرح: {toman(priced['list_toman'])}",
    ]
    if priced["credit_toman"]:
        lines.append(
            f"اعتبار اشتراک فعلی شما: <b>−{toman(priced['credit_toman'])}</b>"
        )
    if priced["discount_toman"]:
        lines.append(
            f"کد <code>{priced['coupon_code']}</code>: "
            f"<b>−{toman(priced['discount_toman'])}</b>"
        )
    lines += ["━━━━━━━━━━", f"<b>قابل پرداخت: {toman(priced['payable'])}</b>"]

    if priced["coupon_error"]:
        lines += ["", f"⚠️ {priced['coupon_error']}"]
    if priced["is_upgrade"]:
        lines += [
            "",
            "<i>این یک ارتقا است: ارزش روزهای باقی‌مانده‌ی طرح فعلی‌تان کسر "
            "شد و طرح تازه از همین حالا شروع می‌شود.</i>",
        ]

    kb = InlineKeyboardBuilder()
    kb.row(
        InlineKeyboardButton(
            text="💳 پرداخت", callback_data=f"pay:go:{plan.code}"
        )
    )
    kb.row(
        InlineKeyboardButton(
            text="🎟 کد تخفیف دارم" if not priced["coupon_code"] else "🎟 تغییر کد",
            callback_data=f"pay:cpn:{plan.code}",
        )
    )
    kb.row(InlineKeyboardButton(text="🔙 طرح‌ها", callback_data="credit:plans"))
    await target.answer("\n".join(lines), reply_markup=kb.as_markup())


@router.callback_query(F.data == "plan:long")
async def cb_long_term(call: CallbackQuery) -> None:
    """طرح‌های بلندمدت.

    <b>چرا صفحه‌ی جدا.</b> سیزده طرح در یک فهرست یعنی هیچ‌کدام دیده
    نمی‌شوند. اینجا فقط بلندمدت‌هاست، با صرفه‌جویی هرکدام کنارش — که
    همان چیزی است که خرید بلندمدت را توجیه می‌کند.
    """
    await call.answer()
    lines = [
        "⏳ <b>اشتراک بلندمدت</b>",
        RULE,
        "<i>هرچه دوره بلندتر، ماهانه ارزان‌تر. سهمیه‌ها هم به همان نسبت "
        "بزرگ‌تر می‌شوند — یعنی طرح یک‌ساله واقعاً دوازده برابر طرح "
        "ماهانه ظرفیت دارد، نه فقط دوازده برابر مدت.</i>",
        "",
    ]
    for plan in long_term():
        monthly = plan.price_toman // max(1, round(plan.days / 30))
        star = "  ⭐️ <i>پیشنهاد ما</i>" if plan.code == POPULAR_CODE else ""
        lines.append(
            f"🗓 <b>{plan.title}</b> — {plan.price_label}{star}\n"
            f"   ماهی {toman(monthly)} · <i>{plan.tagline}</i>"
        )
    lines += [
        RULE,
        "<i>اگر اشتراک فعالی دارید، طرح تازه از انتهای آن تمدید می‌شود.</i>",
    ]
    await call.message.edit_text("\n".join(lines), reply_markup=long_term_menu())


@router.callback_query(F.data.startswith("plan:"))
async def cb_plan(call: CallbackQuery, state: FSMContext) -> None:
    plan = get_plan(call.data.split(":")[1])
    if plan is None:
        await call.answer("این پلن وجود ندارد.", show_alert=True)
        return
    await call.answer()
    await get_or_create_user(call.from_user)
    await state.update_data(coupon="")
    await _quote_screen(call.message, call.from_user.id, plan.code, "")


@router.callback_query(F.data.startswith("pay:cpn:"))
async def cb_coupon_ask(call: CallbackQuery, state: FSMContext) -> None:
    plan_code = call.data.split(":")[2]
    await call.answer()
    await state.set_state(Flow.coupon_code)
    await state.update_data(plan_code=plan_code)
    await call.message.answer(
        "🎟 کد تخفیف را بفرستید.\n\n"
        "<i>اگر کد ندارید یا پشیمان شدید، /cancel بزنید.</i>"
    )


@router.message(Flow.coupon_code)
async def got_coupon(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    plan_code = data.get("plan_code", "")
    code = (message.text or "").strip()
    await state.set_state(None)
    await state.update_data(coupon=code)
    await _quote_screen(message, message.from_user.id, plan_code, code)


@router.callback_query(F.data.startswith("pay:go:"))
async def cb_pay(call: CallbackQuery, state: FSMContext) -> None:
    plan_code = call.data.split(":")[2]
    plan = get_plan(plan_code)
    if plan is None:
        await call.answer("این پلن وجود ندارد.", show_alert=True)
        return
    await call.answer()

    coupon = (await state.get_data()).get("coupon", "")
    request = await payments.create_request(call.from_user.id, plan_code, coupon)
    if request is None:
        await call.message.answer("⚠️ ثبت درخواست ناموفق بود. دوباره تلاش کنید.")
        return

    saved = int(request.discount_toman or 0) + int(request.credit_toman or 0)
    saving = f"\n<i>({toman(saved)} کسر شد)</i>" if saved else ""
    headline = (
        f"🧾 <b>{plan.title}</b>\n"
        f"مبلغ قابل پرداخت: <b>{toman(request.amount_toman)}</b>{saving}\n"
        f"مدت: {fa_num(plan.days)} روز"
    )
    await start_payment(call.message, request, headline=headline, state=state)


async def start_payment(
    target: Message,
    request,
    *,
    headline: str = "",
    state: FSMContext | None = None,
) -> None:
    """صفحه‌ی پرداخت یک درخواست — هر نوعی که باشد.

    <b>چرا مشترک است.</b> خرید طرح، خرید اعتبار و شارژ کیف پول هر سه
    به همین چهار راه پرداخت می‌رسند. مسیر دوم ساختن یعنی روزی یکی‌شان
    اصلاح می‌شود و دیگری نه — و آن روز، یکی از راه‌های فروش بی‌صدا
    می‌شکند.
    """
    cfg = get_settings()
    headline = headline or (
        f"🧾 <b>{payments.describe(request)}</b>\n"
        f"مبلغ قابل پرداخت: <b>{toman(request.amount_toman)}</b>"
    )

    # هیچ راه پرداختی تنظیم نشده باشد، تنها کار ممکن ارجاع به پشتیبانی
    # است. ولی این حالت یعنی <b>فروش از دست می‌رود</b>، پس به ادمین هم
    # خبر داده می‌شود — وگرنه ممکن است هفته‌ها ادامه پیدا کند بی‌آنکه
    # کسی بفهمد چرا کسی نمی‌خرد.
    if not await payments.any_method_ready():
        support = f"@{cfg.support_username}" if cfg.support_username else "پشتیبانی"
        await target.answer(
            f"{headline}\n\nبرای فعال‌سازی با {support} در تماس باشید.\n"
            f"شناسه‌ی شما: <code>{request.user_id}</code>"
        )
        await payments.warn_no_method()
        return

    # هر راه فقط وقتی پیشنهاد می‌شود که کامل تنظیم شده باشد؛ وگرنه
    # دکمه‌ای است که به بن‌بست می‌رسد. کارت هم استثنا نیست: تا امروز
    # همیشه نشان داده می‌شد، حتی وقتی شماره‌ای ثبت نشده بود.
    card_ready = await cardinfo.available()
    ready_coins = await crypto.ready_coins()
    gateway_ready = await zarinpal.configured()

    if state is not None:
        await state.update_data(request_id=request.id)

    total = int(card_ready) + int(gateway_ready) + len(ready_coins)
    if total > 1:
        await target.answer(
            f"{headline}\n\nاز کدام راه می‌خواهید بپردازید؟",
            reply_markup=_method_menu(
                request.id,
                card=card_ready,
                gateway=gateway_ready,
                crypto_coins=ready_coins,
            ),
        )
        return

    # فقط یک راه هست؛ پرسیدن «کدام؟» وقتی یک گزینه بیشتر نیست، یک
    # کلیک اضافه است بدون هیچ فایده‌ای
    if len(ready_coins) == 1 and not card_ready and not gateway_ready:
        await _crypto_screen(target, state, request, headline, ready_coins[0])
        return
    if gateway_ready and not card_ready:
        await target.answer(
            f"{headline}\n\nاز کدام راه می‌خواهید بپردازید؟",
            reply_markup=_method_menu(request.id, card=False, gateway=True),
        )
        return
    await _card_screen(target, state, request, headline)


def _method_menu(
    request_id: int,
    *,
    card: bool = True,
    gateway: bool = False,
    crypto_coins: tuple[str, ...] = (),
) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    # درگاه اول می‌آید چون تنها راهی است که همان لحظه فعال می‌شود؛
    # بقیه منتظر تأیید دستی می‌مانند.
    if gateway:
        kb.row(
            InlineKeyboardButton(
                text=payments.METHOD_LABELS[payments.METHOD_GATEWAY],
                callback_data=f"paym:gate:{request_id}",
            )
        )
    if card:
        kb.row(
            InlineKeyboardButton(
                text=payments.METHOD_LABELS[payments.METHOD_CARD],
                callback_data=f"paym:card:{request_id}",
            )
        )
    for code in crypto_coins:
        spec = coins.get(code)
        if spec is None:
            continue
        kb.row(
            InlineKeyboardButton(
                text=spec.label, callback_data=f"paym:{code}:{request_id}"
            )
        )
    kb.row(InlineKeyboardButton(text="❌ انصراف", callback_data="pay:cancel"))
    return kb.as_markup()


async def _card_screen(message, state: FSMContext, request, headline: str) -> None:
    """صفحه‌ی کارت‌به‌کارت — همان چیزی که پیش از تتر هم بود."""
    kb = InlineKeyboardBuilder()
    kb.row(InlineKeyboardButton(text="❌ انصراف", callback_data="pay:cancel"))
    # شماره از پنل خوانده می‌شود نه از .env؛ اگر پنل خالی باشد همان
    # مقدار .env برمی‌گردد، پس نصب‌های قدیمی چیزی از دست نمی‌دهند.
    name = await cardinfo.holder()
    holder = f"\nبه نام: <b>{name}</b>" if name else ""
    await state.set_state(Flow.receipt)
    await state.update_data(request_id=request.id)
    await message.answer(
        f"{headline}\n\n"
        # چهارتایی، چون شانزده رقمِ پیوسته را نمی‌شود با کارت مقایسه کرد
        f"💳 شماره کارت:\n<code>{cardinfo.pretty(await cardinfo.number())}</code>{holder}\n\n"
        "پس از واریز، <b>تصویر رسید</b> را همین‌جا بفرستید.\n"
        "اشتراک بلافاصله پس از تأیید فعال می‌شود.",
        reply_markup=kb.as_markup(),
    )


# ------------------------------------------------------- مقایسه و اعتبار
@router.callback_query(F.data == "cmp:plans")
async def cb_compare(call: CallbackQuery) -> None:
    await call.answer()
    await call.message.edit_text(_compare_text(), reply_markup=plans_menu())


@router.callback_query(F.data == "credit:plans")
async def cb_back_to_plans(call: CallbackQuery) -> None:
    await call.answer()
    await call.message.edit_text(_plans_text(), reply_markup=plans_menu())


@router.callback_query(F.data == "credit:menu")
async def cb_credits(call: CallbackQuery) -> None:
    await call.answer()
    await get_or_create_user(call.from_user)
    balances = await credits.balances(call.from_user.id)
    lines = ["🎫 <b>خرید اعتبار</b>", RULE, ""]
    for kind, (title, desc, _default) in CREDIT_KINDS.items():
        price = credit_unit(kind)
        lines.append(f"{title}")
        lines.append(f"   {desc}")
        lines.append(f"   قیمت هر واحد: <b>{toman(price)}</b>")
        lines.append(f"   مانده‌ی شما: <b>{fa_num(balances.get(kind, 0))}</b>\n")
    lines.append(
        "<i>اعتبار تاریخ انقضا ندارد و با تمام شدن اشتراک هم از بین نمی‌رود. "
        "همیشه اول سهمیه‌ی طرحتان مصرف می‌شود، بعد اعتبار.</i>"
    )
    await call.message.edit_text("\n".join(lines), reply_markup=credits_menu(balances))


@router.callback_query(F.data.startswith("credit:pick:"))
async def cb_credit_pick(call: CallbackQuery) -> None:
    kind = call.data.split(":")[2]
    info = CREDIT_KINDS.get(kind)
    if info is None:
        await call.answer("این بسته وجود ندارد.", show_alert=True)
        return
    title, desc, price = info
    await call.answer()
    have = await credits.balance(call.from_user.id, kind)
    text = (
        f"{title}\n{RULE}\n\n"
        f"{desc}\n"
        f"قیمت هر واحد: <b>{toman(price)}</b>\n"
        f"مانده‌ی فعلی شما: <b>{fa_num(have)}</b>\n\n"
        "چند واحد می‌خواهید؟"
    )
    markup = credit_packs_menu(kind)
    try:
        await call.message.edit_text(text, reply_markup=markup)
    except Exception:
        await call.message.answer(text, reply_markup=markup)


@router.callback_query(F.data.startswith("credit:ask:"))
async def cb_credit_ask(call: CallbackQuery, state: FSMContext) -> None:
    kind = call.data.split(":")[2]
    if kind not in CREDIT_KINDS:
        await call.answer("این بسته وجود ندارد.", show_alert=True)
        return
    await call.answer()
    await state.set_state(Flow.credit_amount)
    await state.update_data(credit_kind=kind)
    price = credit_unit(kind)
    await call.message.answer(
        f"🔢 چند واحد می‌خواهید؟ عدد را بفرستید.\n"
        f"هر واحد {toman(price)} — مثلاً <code>250</code>\n\n"
        "انصراف: /cancel"
    )


@router.message(Flow.credit_amount)
async def got_credit_amount(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    kind = str(data.get("credit_kind", ""))
    quantity = parse_int(message.text or "")
    if kind not in CREDIT_KINDS:
        await state.clear()
        await message.answer("این بسته وجود ندارد.")
        return
    if quantity is None or quantity <= 0:
        await message.answer("یک عدد مثبت بفرستید، مثلاً <code>250</code>.")
        return
    if quantity > MAX_CREDIT_UNITS:
        await message.answer(f"حداکثر {fa_num(MAX_CREDIT_UNITS)} واحد در هر خرید.")
        return
    await state.clear()
    await _start_credit_purchase(message, message.from_user.id, kind, quantity, state)


@router.callback_query(F.data.startswith("credit:buy:"))
async def cb_credit_buy(call: CallbackQuery, state: FSMContext) -> None:
    _, _, kind, raw = call.data.split(":")
    if kind not in CREDIT_KINDS:
        await call.answer("این بسته وجود ندارد.", show_alert=True)
        return
    await call.answer()
    await _start_credit_purchase(call.message, call.from_user.id, kind, int(raw), state)


async def _start_credit_purchase(
    target: Message, user_id: int, kind: str, quantity: int, state: FSMContext
) -> None:
    """رسید کارت‌به‌کارت برای خرید اعتبار را می‌خواهد."""
    title = CREDIT_KINDS[kind][0]
    amount = credit_price(kind, quantity)
    request = await payments.create_credit_request(user_id, kind, quantity, amount)
    if request is None:
        await target.answer("⚠️ ثبت درخواست ناموفق بود. دوباره تلاش کنید.")
        return

    cfg = get_settings()
    if not cfg.card_number:
        support = f"@{cfg.support_username}" if cfg.support_username else "پشتیبانی"
        await target.answer(
            f"🎫 <b>{title}</b> — {fa_num(quantity)} واحد\n"
            f"مبلغ: <b>{toman(amount)}</b>\n\n"
            f"برای پرداخت با {support} در تماس باشید.\n"
            f"شناسه‌ی شما: <code>{user_id}</code>"
        )
        return

    kb = InlineKeyboardBuilder()
    kb.row(InlineKeyboardButton(text="❌ انصراف", callback_data="pay:cancel"))
    holder = f"\nبه نام: <b>{cfg.card_holder}</b>" if cfg.card_holder else ""
    await state.set_state(Flow.receipt)
    await state.update_data(request_id=request.id)
    await target.answer(
        f"🎫 <b>{title}</b>\n"
        f"تعداد: <b>{fa_num(quantity)}</b> واحد\n"
        f"مبلغ قابل پرداخت: <b>{toman(amount)}</b>\n\n"
        f"💳 شماره کارت:\n<code>{cfg.card_number}</code>{holder}\n\n"
        "پس از واریز، <b>تصویر رسید</b> را همین‌جا بفرستید.",
        reply_markup=kb.as_markup(),
    )


@router.callback_query(F.data == "pay:cancel")
async def cb_cancel(call: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await call.answer("لغو شد")
    await call.message.answer("خرید لغو شد.")


@router.callback_query(F.data.startswith("paym:"))
async def cb_pay_method(call: CallbackQuery, state: FSMContext) -> None:
    """کاربر راه پرداخت را انتخاب کرد."""
    _, method, raw_id = call.data.split(":")
    request_id = int(raw_id)
    await call.answer()

    request = await payments.get_request(request_id)
    if request is None or request.user_id != call.from_user.id:
        await call.message.answer("این درخواست پیدا نشد.")
        return

    headline = (
        f"🧾 <b>{payments.describe(request)}</b>\n"
        f"مبلغ قابل پرداخت: <b>{toman(request.amount_toman)}</b>"
    )

    if method == payments.METHOD_CARD:
        await payments.set_method(request_id, payments.METHOD_CARD)
        await _card_screen(call.message, state, request, headline)
        return

    if method == payments.METHOD_GATEWAY:
        await _gateway_screen(call, state, request)
        return

    await _crypto_screen(call.message, state, request, headline, method)


async def _crypto_screen(
    message, state: FSMContext, request, headline: str, coin: str
) -> None:
    """صفحه‌ی پرداخت ارز دیجیتال — تتر یا ترون.

    جدا شد چون دو جا لازم است: وقتی کاربر بین چند راه یکی را انتخاب
    می‌کند، و وقتی همین یک راه تنظیم شده و پرسیدن «کدام راه؟» برای
    یک گزینه فقط یک کلیک اضافه است.
    """
    spec = coins.get(coin)
    if spec is None:
        spec = coins.get(coins.USDT)
        coin = coins.USDT

    priced = await crypto.quote(request.amount_toman, coin)
    if priced is None:
        # بین انتخاب پلن و اینجا، ادمین نرخ یا نشانی را برداشته است
        if await cardinfo.available():
            await message.answer(
                f"پرداخت با {spec.symbol} موقتاً در دسترس نیست. "
                "با کارت بانکی ادامه می‌دهیم."
            )
            await payments.set_method(request.id, payments.METHOD_CARD)
            await _card_screen(message, state, request, headline)
            return
        await message.answer(
            f"⚠️ پرداخت با {spec.symbol} موقتاً در دسترس نیست. "
            "کمی بعد دوباره تلاش کنید."
        )
        await payments.warn_no_method()
        return

    await payments.set_method(
        request.id,
        coin,
        usdt_amount=priced["amount_text"],
        usdt_rate=priced["rate"],
    )

    # ترون در فاصله‌ی ساخت درخواست تا پرداخت واقعاً تکان می‌خورد، پس
    # کاربر باید بداند که معطل کردن به ضررِ خودش است.
    hurry = (
        "\n⏱ <i>قیمت ترون نوسان دارد؛ همین حالا واریز کنید.</i>"
        if spec.is_native
        else ""
    )

    # ترون خودِ ارز شبکه است، نه توکن TRC20 روی آن. گفتنِ «TRC20» به
    # کسی که ترون می‌فرستد غلط است و در کیف پول دنبال چیزی می‌گردد
    # که وجود ندارد.
    if spec.is_native:
        network = (
            "🌐 شبکه: <b>Tron (TRX)</b>\n"
            f"نشانی ولت:\n<code>{priced['address']}</code>\n\n"
            "پس از واریز، <b>هش تراکنش</b> را همین‌جا بفرستید.\n\n"
            "⚠️ فقط <b>خودِ ترون روی شبکه‌ی ترون</b> را بفرستید. "
            "واریز ترونِ بسته‌بندی‌شده روی شبکه‌ی دیگر (مثل BEP20) به "
            "این نشانی <b>قابل بازگشت نیست</b>."
        )
    else:
        network = (
            "🌐 شبکه: <b>TRC20 (Tron)</b>\n"
            f"نشانی ولت:\n<code>{priced['address']}</code>\n\n"
            "پس از واریز، <b>هش تراکنش</b> را همین‌جا بفرستید.\n\n"
            "⚠️ فقط از شبکه‌ی TRC20 بفرستید. واریز از شبکه‌ی دیگر "
            "(ERC20، BEP20) به این نشانی <b>قابل بازگشت نیست</b>."
        )

    kb = InlineKeyboardBuilder()
    kb.row(InlineKeyboardButton(text="❌ انصراف", callback_data="pay:cancel"))
    await state.set_state(Flow.tx_hash)
    await state.update_data(request_id=request.id)
    await message.answer(
        f"{headline}\n"
        f"معادل: <b>{priced['amount_text']} {spec.symbol}</b>\n"
        f"<i>نرخ امروز: {toman(priced['rate'])} برای هر {spec.symbol}</i>{hurry}\n\n"
        f"{network}",
        reply_markup=kb.as_markup(),
    )


async def _gateway_screen(call: CallbackQuery, state: FSMContext, request) -> None:
    """کاربر را به درگاه زرین‌پال می‌فرستد.

    برخلاف دو راه دیگر، اینجا حالت گفتگویی لازم نیست: کاربر از مرورگر
    برمی‌گردد و مسیر بازگشت خودش اشتراک را فعال می‌کند.
    """
    authority = await zarinpal.start(
        request.amount_toman, payments.describe(request), request_id=request.id
    )
    if authority is None:
        await call.message.answer(
            "⚠️ اتصال به درگاه ممکن نشد. لطفاً راه دیگری انتخاب کنید.",
            reply_markup=_method_menu(
                request.id, gateway=False, crypto_coins=await crypto.ready_coins()
            ),
        )
        return

    await payments.set_method(request.id, payments.METHOD_GATEWAY)
    await state.clear()

    kb = InlineKeyboardBuilder()
    kb.row(
        InlineKeyboardButton(text="🏦 رفتن به درگاه پرداخت", url=zarinpal.pay_url(authority))
    )
    await call.message.answer(
        f"🧾 <b>{payments.describe(request)}</b>\n"
        f"مبلغ: <b>{toman(request.amount_toman)}</b>\n\n"
        "روی دکمه‌ی زیر بزنید و پرداخت را کامل کنید.\n"
        "پس از پرداخت، اشتراکتان <b>همان لحظه</b> فعال می‌شود و همین‌جا "
        "خبرش را می‌گیرید.\n\n"
        f"<i>کد پیگیری: {request.id}</i>",
        reply_markup=kb.as_markup(),
    )


@router.message(Flow.tx_hash)
async def got_tx_hash(message: Message, state: FSMContext) -> None:
    """هش تراکنش تتر — همان نقشی که تصویر رسید برای کارت دارد."""
    data = await state.get_data()
    request_id = int(data.get("request_id", 0))

    request = await payments.attach_tx(request_id, message.text or "")
    if request is None:
        await message.answer(
            "⚠️ این هش معتبر نیست.\n\n"
            "هش تراکنش ترون ۶۴ نویسه است و از صفحه‌ی تراکنش در کیف پول "
            "یا در tronscan.org کپی می‌شود.\n"
            "برای انصراف /cancel را بزنید."
        )
        return

    await state.clear()

    # <b>همین‌جا یک بار بلاک‌چین را می‌خوانیم، نه فقط در چرخه‌ی دو
    # دقیقه‌ای.</b> تراکنشی که کاربر هشش را می‌فرستد معمولاً چند ثانیه
    # قبل تأیید شده، پس اغلب همین‌جا فعال می‌شود و کاربر اصلاً منتظر
    # نمی‌ماند. اگر هنوز آماده نباشد، چرخه بعداً می‌گیردش.
    notice = await message.answer("⏳ در حال بررسی تراکنش روی بلاک‌چین…")
    activated = await cryptocheck.verify_now(request.id)
    if activated:
        await notice.edit_text(activated, disable_web_page_preview=True)
        return

    await notice.edit_text(
        "✅ هش تراکنش شما ثبت شد.\n\n"
        f"کد پیگیری: <code>{request.id}</code>\n\n"
        "<i>تراکنش هنوز روی شبکه تأیید نشده است. به‌محض تأیید، اشتراک "
        "خودکار فعال می‌شود — معمولاً چند دقیقه طول می‌کشد و لازم نیست "
        "کاری بکنید.</i>"
    )
    await _notify_admins(message, request)


@router.message(Flow.receipt)
async def got_receipt(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    request_id = int(data.get("request_id", 0))

    file_id, kind = None, "text"
    if message.photo:
        file_id, kind = message.photo[-1].file_id, "photo"
    elif message.document:
        file_id, kind = message.document.file_id, "document"

    if file_id is None:
        await message.answer(
            "⚠️ لطفاً <b>تصویر</b> رسید را بفرستید.\nبرای انصراف /cancel را بزنید."
        )
        return

    request = await payments.attach_receipt(
        request_id, file_id, kind, note=message.caption or ""
    )
    await state.clear()
    if request is None:
        await message.answer("⚠️ این درخواست دیگر معتبر نیست. دوباره از «💳 خرید اشتراک» شروع کنید.")
        return

    await message.answer(
        "✅ رسید شما ثبت شد.\n\n"
        f"خرید: <b>{payments.describe(request)}</b>\n"
        f"مبلغ: <b>{toman(request.amount_toman)}</b>\n"
        f"کد پیگیری: <code>{request.id}</code>\n\n"
        "پس از بررسی، نتیجه همین‌جا اعلام می‌شود."
    )
    await _notify_admins(message, request)


async def _notify_admins(message: Message, request: PaymentRequest) -> None:
    """رسید را با دکمه‌های تأیید/رد برای همه‌ی ادمین‌ها می‌فرستد."""
    cfg = get_settings()
    user = message.from_user
    username = f"@{user.username}" if user.username else "—"

    kb = InlineKeyboardBuilder()
    kb.row(
        InlineKeyboardButton(text="✅ تأیید", callback_data=f"pay:ok:{request.id}"),
        InlineKeyboardButton(text="❌ رد", callback_data=f"pay:no:{request.id}"),
    )
    method = payments.METHOD_LABELS.get(request.pay_method, request.pay_method)
    extra = ""
    if request.pay_method == payments.METHOD_USDT:
        # هش کامل می‌آید تا ادمین بتواند مستقیم در کاوشگر بلاک‌چین
        # بازش کند؛ کوتاه کردنش یعنی کپی‌کردن دستی از دو تکه.
        extra = (
            f"\nمبلغ تتری: <b>{request.usdt_amount} USDT</b>"
            f"\nنرخ آن روز: {toman(request.usdt_rate)}"
            f"\n\nهش تراکنش:\n<code>{request.tx_hash}</code>"
            f"\nhttps://tronscan.org/#/transaction/{request.tx_hash}"
            # ادمین باید بداند چرا این پرداخت به دستش رسیده. تأیید
            # خودکار پیش از این پیام امتحان شده و نگرفته — گفتنِ همین،
            # جلوی «چرا خودکار نشد؟» را می‌گیرد.
            "\n\n<i>⚠️ تأیید خودکار این تراکنش را روی بلاک‌چین پیدا "
            "نکرد. یا هنوز تأیید نشده، یا مبلغش کمتر است، یا به ولت "
            "دیگری رفته. پیش از تأیید دستی، لینک بالا را ببینید.</i>"
        )

    caption = (
        "🧾 <b>پرداخت جدید</b>\n\n"
        f"کاربر: {user.full_name} ({username})\n"
        f"شناسه: <code>{user.id}</code>\n"
        f"خرید: <b>{payments.describe(request)}</b>\n"
        f"مبلغ: <b>{toman(request.amount_toman)}</b>\n"
        f"روش: {method}{extra}\n"
        f"کد پیگیری: <code>{request.id}</code>"
    )

    for admin_id in cfg.admin_ids:
        try:
            if request.pay_method == payments.METHOD_USDT:
                await message.bot.send_message(
                    admin_id, caption, reply_markup=kb.as_markup(),
                    disable_web_page_preview=True,
                )
            elif request.receipt_kind == "photo":
                await message.bot.send_photo(
                    admin_id, request.receipt_file_id, caption=caption,
                    reply_markup=kb.as_markup(),
                )
            else:
                await message.bot.send_document(
                    admin_id, request.receipt_file_id, caption=caption,
                    reply_markup=kb.as_markup(),
                )
        except Exception:
            log.warning("ارسال رسید به ادمین %s ناموفق بود", admin_id, exc_info=True)


@router.callback_query(F.data.startswith("pay:ok:"))
async def cb_approve(call: CallbackQuery) -> None:
    if not await roles.can(call.from_user.id, roles.CAP_MONEY):
        await call.answer("دسترسی ندارید", show_alert=True)
        return
    request_id = int(call.data.split(":")[2])
    request, sub = await payments.approve(request_id, call.from_user.id)
    if request is None:
        await call.answer("این درخواست قبلاً بررسی شده است.", show_alert=True)
        return

    await call.answer("تأیید شد")
    await _mark_reviewed(call, f"✅ تأیید شد توسط {call.from_user.full_name}")

    try:
        await call.bot.send_message(
            request.user_id, await payments.approval_notice(request, sub)
        )
    except Exception:
        log.warning("اطلاع تأیید به کاربر %s نرسید", request.user_id, exc_info=True)


@router.callback_query(F.data.startswith("pay:no:"))
async def cb_reject(call: CallbackQuery) -> None:
    if not await roles.can(call.from_user.id, roles.CAP_MONEY):
        await call.answer("دسترسی ندارید", show_alert=True)
        return
    request_id = int(call.data.split(":")[2])
    request = await payments.reject(request_id, call.from_user.id)
    if request is None:
        await call.answer("این درخواست قبلاً بررسی شده است.", show_alert=True)
        return

    await call.answer("رد شد")
    await _mark_reviewed(call, f"❌ رد شد توسط {call.from_user.full_name}")

    try:
        await call.bot.send_message(
            request.user_id, payments.rejection_notice(request)
        )
    except Exception:
        log.warning("اطلاع رد به کاربر %s نرسید", request.user_id, exc_info=True)


async def _mark_reviewed(call: CallbackQuery, verdict: str) -> None:
    """دکمه‌ها را برمی‌دارد تا ادمین دیگری دوباره بررسی نکند."""
    try:
        base = call.message.caption or call.message.text or ""
        new_text = f"{base}\n\n{verdict}"
        if call.message.caption is not None:
            await call.message.edit_caption(caption=new_text, reply_markup=None)
        else:
            await call.message.edit_text(new_text, reply_markup=None)
    except Exception:
        log.debug("به‌روزرسانی پیام رسید ناموفق بود", exc_info=True)
