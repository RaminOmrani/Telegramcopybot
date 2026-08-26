"""ابزارهای مشترک هندلرها: کاربر جاری، گاردها و حالت‌های FSM."""
from __future__ import annotations

import hashlib
import hmac

from aiogram.fsm.state import State, StatesGroup
from aiogram.types import Message, TelegramObject
from aiogram.types import User as TgUser

from telkap.config import get_settings
from telkap.db import get_session
from telkap.models import User
from telkap.services.subscription import active_plan_for, grant_trial_if_new
from telkap.texts import NO_LOGIN, NO_SUBSCRIPTION


class Flow(StatesGroup):
    """همه‌ی جریان‌های گفتگویی ربات."""

    phone = State()
    code = State()
    password = State()

    task_source = State()
    task_dest = State()
    task_title = State()

    rule_from = State()
    rule_to = State()
    rule_word = State()

    ask_value = State()      # مقدار متنی/عددی یک تنظیم
    history_count = State()

    fwd_dest = State()
    fwd_value = State()

    receipt = State()
    credit_amount = State()
    coupon_code = State()
    gift_code = State()
    admin_gift = State()
    dest_add = State()
    dest_override = State()
    dest_route = State()
    task_route = State()
    import_settings = State()

    pin_set = State()
    pin_verify = State()

    admin_grant = State()
    admin_broadcast = State()
    admin_user_find = State()
    admin_days = State()
    admin_dm = State()
    admin_join_add = State()
    admin_credit = State()
    admin_plan_value = State()
    admin_credit_price = State()
    admin_limit_value = State()
    admin_referral_value = State()
    admin_wallet = State()
    admin_reseller = State()
    admin_chatid = State()
    admin_coupon = State()
    admin_role = State()
    admin_maint_note = State()

    reseller_customer = State()

    churn_note = State()

    support_message = State()
    support_reply = State()

    watermark_logo = State()
    watermark_input = State()


async def get_or_create_user(tg_user: TgUser) -> User:
    async with get_session() as db:
        user = await db.get(User, tg_user.id)
        created = False
        if user is None:
            user = User(
                id=tg_user.id,
                first_name=tg_user.first_name or "",
                username=tg_user.username,
            )
            db.add(user)
            created = True
        else:
            user.first_name = tg_user.first_name or user.first_name
            user.username = tg_user.username
        await db.commit()
        await db.refresh(user)
    if created:
        await grant_trial_if_new(user)
    return user


async def require_login(message: Message) -> User | None:
    """کاربر را برمی‌گرداند اگر اکانت کاربری‌اش متصل باشد، وگرنه هشدار می‌دهد."""
    user = await get_or_create_user(message.from_user)
    if not user.is_logged_in:
        await message.answer(NO_LOGIN)
        return None
    return user


async def require_subscription(message: Message):
    plan = await active_plan_for(message.from_user.id)
    if plan is None:
        await message.answer(NO_SUBSCRIPTION)
    return plan


def hash_pin(user_id: int, pin: str) -> str:
    """پین با کلید Fernet و آیدی کاربر نمک‌زده و هش می‌شود."""
    secret = get_settings().fernet_key.encode()
    return hmac.new(secret, f"{user_id}:{pin}".encode(), hashlib.sha256).hexdigest()


def check_pin(user: User, pin: str) -> bool:
    if not user.pin_hash:
        return True
    return hmac.compare_digest(user.pin_hash, hash_pin(user.id, pin))


def parse_int(raw: str) -> int | None:
    """عدد فارسی یا انگلیسی را می‌خواند."""
    table = str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789")
    cleaned = (raw or "").translate(table).strip().replace(",", "").replace("،", "")
    if not cleaned.lstrip("-").isdigit():
        return None
    return int(cleaned)


def clean_code(raw: str) -> str:
    """ارقام کد ورود را از میان فاصله و خط تیره بیرون می‌کشد."""
    table = str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789")
    return "".join(ch for ch in (raw or "").translate(table) if ch.isdigit())


def owner_id(obj: TelegramObject) -> int:
    return obj.from_user.id
