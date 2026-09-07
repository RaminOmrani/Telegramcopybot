"""ورود به پنل وب: نام کاربری، رمز، و کد دومرحله‌ای در تلگرام.

<b>چرا عوض شد.</b> پیش از این ورود فقط با یک لینک یک‌بارمصرف از داخل
ربات ممکن بود. برای پنلی که دو-سه نفر گاهی بازش می‌کنند کافی بود، ولی
یعنی برای هر نگاه کردن باید تلگرام باز می‌شد — و نشست‌ها هم با هر
ری‌استارت می‌مردند، یعنی عملاً بعد از هر به‌روزرسانی.

<b>سه لایه، و هیچ‌کدام اضافی نیست.</b>

۱. <b>رمز.</b> چیزی که می‌دانید. تنها لایه‌ای که بدون تلگرام کار
   می‌کند، و برای همین به‌تنهایی کافی نیست: رمز لو می‌رود.

۲. <b>کد دومرحله‌ای در تلگرام.</b> چیزی که دارید. این پنل به پول و
   به اکانت مشتری‌ها دسترسی دارد؛ کسی که رمز را حدس بزند یا از جایی
   بردارد، بدون دسترسی به تلگرامِ صاحب حساب هنوز داخل نمی‌شود.

۳. <b>قفل پس از چند تلاش.</b> حدس زدن رمز را از «کند» به «بی‌فایده»
   می‌رساند.

<b>رمز هرگز خام ذخیره نمی‌شود</b> — فقط هش PBKDF2 با نمکِ مخصوص همان
حساب. و شناسه‌ی نشست هم هش‌شده ذخیره می‌شود: دیتابیسِ لو‌رفته نباید
کلیدِ ورودِ آماده بدهد.
"""
from __future__ import annotations

import hashlib
import logging
import secrets
import time
from dataclasses import dataclass
from datetime import timedelta

from sqlalchemy import delete, select

from telkap.db import get_session, log_activity
from telkap.models import WebAccount, WebSession, utcnow

log = logging.getLogger(__name__)

COOKIE_NAME = "telkap_panel"

# نشست سی روز می‌ماند. کوتاه‌تر کردنش وقتی کد دومرحله‌ای هست امنیت
# بیشتری نمی‌دهد، فقط آزاردهنده است.
SESSION_TTL_SECONDS = 30 * 24 * 60 * 60

# کد دومرحله‌ای پنج دقیقه اعتبار دارد — به اندازه‌ی رفتن به تلگرام و
# برگشتن، نه بیشتر.
CODE_TTL_SECONDS = 5 * 60
CODE_LENGTH = 6

# پس از این تعداد رمزِ اشتباه، حساب موقتاً قفل می‌شود.
MAX_FAILED = 5
LOCK_MINUTES = 15

# PBKDF2 با این تعداد تکرار روی سخت‌افزار امروز حدود صد میلی‌ثانیه
# طول می‌کشد: برای یک ورود ناچیز است، برای حدس زدن میلیونی گران.
PBKDF2_ROUNDS = 240_000

MIN_PASSWORD = 8
MIN_USERNAME = 3


# ── رمز ──────────────────────────────────────────────────────────────


def hash_password(password: str, *, salt: str = "") -> str:
    """هش رمز، به شکل <code>salt$hash</code>."""
    salt = salt or secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), salt.encode("utf-8"), PBKDF2_ROUNDS
    )
    return f"{salt}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    """مقایسه‌ی زمان-ثابت، تا از روی سرعتِ رد شدن چیزی لو نرود."""
    if not stored or "$" not in stored:
        return False
    salt, _, expected = stored.partition("$")
    candidate = hash_password(password, salt=salt).partition("$")[2]
    return secrets.compare_digest(candidate, expected)


def password_problem(password: str) -> str:
    """چرا این رمز پذیرفته نیست. رشته‌ی خالی یعنی مشکلی ندارد."""
    if len(password or "") < MIN_PASSWORD:
        return f"رمز باید دست‌کم {MIN_PASSWORD} نویسه باشد."
    if password.isdigit():
        return "رمزِ فقط عددی خیلی زود حدس زده می‌شود."
    return ""


def clean_username(raw: str) -> str:
    """نام کاربری یکدست: حروف کوچک انگلیسی، عدد، خط زیر."""
    cleaned = (raw or "").strip().lower()
    if not cleaned or len(cleaned) < MIN_USERNAME or len(cleaned) > 32:
        return ""
    # <b>فقط ASCII.</b> isalnum در پایتون برای حروف فارسی هم درست
    # است، پس بدون این بررسی «رامین» یک نام کاربری معتبر می‌شد. دو
    # اشکال داشت: در فیلد ورودِ لاتین تایپ‌کردنی نیست، و حروفی که
    # شبیه هم دیده می‌شوند راه را برای نام کاربریِ بدلی باز می‌کنند.
    if not all(("a" <= ch <= "z") or ch.isdigit() or ch == "_" for ch in cleaned):
        return ""
    if not ("a" <= cleaned[0] <= "z"):
        return ""       # شروع با عدد، با آیدی عددی اشتباه می‌شود
    return cleaned


# ── حساب ─────────────────────────────────────────────────────────────


async def create_account(
    username: str, password: str, user_id: int
) -> tuple[WebAccount | None, str]:
    """حساب تازه. خروجی دوم دلیل رد شدن است."""
    name = clean_username(username)
    if not name:
        return None, "نام کاربری باید ۳ تا ۳۲ نویسه‌ی انگلیسی باشد و با حرف شروع شود."
    problem = password_problem(password)
    if problem:
        return None, problem

    async with get_session() as db:
        taken = await db.scalar(
            select(WebAccount.id).where(WebAccount.username == name)
        )
        if taken:
            return None, "این نام کاربری قبلاً گرفته شده."
        account = WebAccount(
            username=name,
            password_hash=hash_password(password),
            user_id=user_id,
        )
        db.add(account)
        await db.commit()
        await db.refresh(account)

    await log_activity(
        user_id=user_id, event="admin", detail=f"حساب پنل وب ساخته شد: {name}"
    )
    return account, ""


async def set_password(user_id: int, password: str) -> str:
    """رمز تازه برای حساب این کاربر. خروجی دلیل رد شدن است."""
    problem = password_problem(password)
    if problem:
        return problem
    async with get_session() as db:
        account = await db.scalar(
            select(WebAccount).where(WebAccount.user_id == user_id)
        )
        if account is None:
            return "برای شما حساب پنل ساخته نشده."
        account.password_hash = hash_password(password)
        account.failed_logins = 0
        account.locked_until = None
        await db.commit()

    # رمز که عوض شد، هر نشستِ بازی باید بسته شود — وگرنه کسی که با
    # رمز قدیمی وارد شده بود همچنان داخل می‌ماند، و عوض کردن رمز
    # دقیقاً همان کاری است که آدم وقتی نگران است می‌کند.
    await end_all(user_id)
    return ""


async def account_of(user_id: int) -> WebAccount | None:
    async with get_session() as db:
        return await db.scalar(select(WebAccount).where(WebAccount.user_id == user_id))


async def by_username(username: str) -> WebAccount | None:
    name = clean_username(username)
    if not name:
        return None
    async with get_session() as db:
        return await db.scalar(select(WebAccount).where(WebAccount.username == name))


# ── مرحله‌ی یک: رمز ─────────────────────────────────────────────────


@dataclass(slots=True)
class Pending:
    """کسی که رمزش درست بود و منتظر کد دومرحله‌ای است."""

    account_id: int
    user_id: int
    code: str
    expires_at: float
    tries: int = 0


# کلیدِ موقت → منتظر. در حافظه است و باید هم باشد: عمرش پنج دقیقه
# است و ری‌استارت‌شدنش فقط یعنی کاربر دوباره رمز می‌زند.
_pending: dict[str, Pending] = {}

MAX_CODE_TRIES = 5


def _sweep() -> None:
    now = time.time()
    for key, item in list(_pending.items()):
        if item.expires_at <= now:
            del _pending[key]


async def start_login(username: str, password: str) -> tuple[str, str, int]:
    """مرحله‌ی اول. خروجی: (کلید موقت، خطا، آیدی تلگرام).

    <b>پیام خطا عمداً یکسان است</b> برای «نام کاربری نیست» و «رمز
    غلط است». پیام‌های متفاوت به کسی که دارد امتحان می‌کند می‌گویند
    کدام نام کاربری واقعی است.
    """
    _sweep()
    generic = "نام کاربری یا رمز درست نیست."
    account = await by_username(username)
    if account is None or not account.enabled:
        # کارِ هش را به هر حال انجام می‌دهیم تا از روی زمانِ پاسخ نشود
        # فهمید که نام کاربری وجود دارد یا نه
        hash_password(password or "x")
        return "", generic, 0

    if account.locked_until and account.locked_until.replace(tzinfo=None) > utcnow().replace(tzinfo=None):
        return "", "این حساب موقتاً قفل است. چند دقیقه بعد دوباره تلاش کنید.", 0

    if not verify_password(password or "", account.password_hash):
        await _note_failure(account.id)
        return "", generic, 0

    await _clear_failures(account.id)
    key = secrets.token_urlsafe(24)
    code = f"{secrets.randbelow(10 ** CODE_LENGTH):0{CODE_LENGTH}d}"
    _pending[key] = Pending(
        account_id=account.id,
        user_id=account.user_id,
        code=code,
        expires_at=time.time() + CODE_TTL_SECONDS,
    )
    return key, "", account.user_id


def code_for(key: str) -> str:
    """کدی که باید به تلگرام فرستاده شود. فقط برای لایه‌ی ارسال."""
    item = _pending.get(key)
    return item.code if item else ""


async def _note_failure(account_id: int) -> None:
    async with get_session() as db:
        account = await db.get(WebAccount, account_id)
        if account is None:
            return
        account.failed_logins = int(account.failed_logins or 0) + 1
        if account.failed_logins >= MAX_FAILED:
            account.locked_until = utcnow() + timedelta(minutes=LOCK_MINUTES)
            account.failed_logins = 0
            log.warning("حساب پنل %s پس از تلاش‌های ناموفق قفل شد", account.username)
        await db.commit()


async def _clear_failures(account_id: int) -> None:
    async with get_session() as db:
        account = await db.get(WebAccount, account_id)
        if account is not None and (account.failed_logins or account.locked_until):
            account.failed_logins = 0
            account.locked_until = None
            await db.commit()


# ── مرحله‌ی دو: کد تلگرام ───────────────────────────────────────────


async def finish_login(key: str, code: str, *, user_agent: str = "") -> tuple[str, str]:
    """مرحله‌ی دوم. خروجی: (شناسه‌ی نشست، خطا)."""
    _sweep()
    item = _pending.get(key)
    if item is None:
        return "", "این تلاش منقضی شده. دوباره وارد شوید."

    item.tries += 1
    if item.tries > MAX_CODE_TRIES:
        _pending.pop(key, None)
        return "", "تعداد تلاش‌ها زیاد شد. دوباره وارد شوید."

    cleaned = "".join(ch for ch in (code or "") if ch.isdigit())
    if not secrets.compare_digest(cleaned, item.code):
        return "", "کد درست نیست."

    _pending.pop(key, None)
    token = await _open_session(item.account_id, item.user_id, user_agent)
    return token, ""


async def _open_session(account_id: int, user_id: int, user_agent: str) -> str:
    token = secrets.token_urlsafe(32)
    async with get_session() as db:
        db.add(
            WebSession(
                token_hash=_digest(token),
                account_id=account_id,
                user_id=user_id,
                csrf=secrets.token_urlsafe(24),
                expires_at=utcnow() + timedelta(seconds=SESSION_TTL_SECONDS),
                user_agent=(user_agent or "")[:200],
            )
        )
        account = await db.get(WebAccount, account_id)
        if account is not None:
            account.last_login_at = utcnow()
        await db.commit()
    return token


def _digest(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


# ── نشست ─────────────────────────────────────────────────────────────


@dataclass(slots=True)
class Session:
    user_id: int
    csrf: str
    account_id: int


async def get_session_for(token: str | None) -> Session | None:
    """نشست زنده‌ی این کوکی، یا None."""
    if not token:
        return None
    async with get_session() as db:
        row = await db.scalar(
            select(WebSession).where(WebSession.token_hash == _digest(token))
        )
        if row is None:
            return None
        expires = row.expires_at
        if expires.tzinfo is None:
            from datetime import UTC

            expires = expires.replace(tzinfo=UTC)
        if expires <= utcnow():
            await db.delete(row)
            await db.commit()
            return None
        return Session(user_id=row.user_id, csrf=row.csrf, account_id=row.account_id)


async def end_session(token: str | None) -> None:
    if not token:
        return
    async with get_session() as db:
        await db.execute(
            delete(WebSession).where(WebSession.token_hash == _digest(token))
        )
        await db.commit()


async def end_all(user_id: int) -> int:
    """همه‌ی نشست‌های یک نفر — برای عزل، یا عوض شدن رمز."""
    async with get_session() as db:
        result = await db.execute(
            delete(WebSession).where(WebSession.user_id == user_id)
        )
        await db.commit()
    return int(result.rowcount or 0)


async def sessions_of(user_id: int) -> list[WebSession]:
    async with get_session() as db:
        rows = await db.execute(
            select(WebSession)
            .where(WebSession.user_id == user_id)
            .order_by(WebSession.created_at.desc())
        )
        return list(rows.scalars())


async def prune() -> int:
    """نشست‌های منقضی را پاک می‌کند."""
    async with get_session() as db:
        result = await db.execute(
            delete(WebSession).where(WebSession.expires_at <= utcnow())
        )
        await db.commit()
    return int(result.rowcount or 0)


def check_csrf(session: Session, sent: str) -> bool:
    """مقایسه‌ی زمان-ثابت تا از روی سرعتِ رد شدن نشود توکن را حدس زد.

    بایت مقایسه می‌شود نه رشته: <code>compare_digest</code> روی رشته‌ی
    غیر-ASCII خطا می‌دهد، و مقدارِ فرستاده‌شده از فرم می‌آید — یعنی هر
    چیزی می‌تواند باشد.
    """
    if not sent:
        return False
    return secrets.compare_digest(session.csrf.encode("utf-8"), sent.encode("utf-8"))


# ── مسیر سریع: لینک از داخل ربات ────────────────────────────────────
#
# <b>چرا هنوز هست.</b> ورود با رمز برای وقتی است که تلگرام دمِ دست
# نیست. ولی وقتی همین حالا داخل رباتید، گرفتن یک لینک یک کلیک است و
# رمز زدن سه مرحله. هویت را هم تلگرام تأیید کرده، پس این مسیر ضعیف‌تر
# از آن یکی نیست — فقط کوتاه‌تر است.
#
# نشستی که می‌سازد همان نشستِ بادوام است، پس یک بار زدنش یعنی سی روز
# باز ماندن.

LOGIN_TTL_SECONDS = 5 * 60

_links: dict[str, tuple[int, float]] = {}


def issue_login_token(user_id: int) -> str:
    """توکن ورودِ یک‌بارمصرف برای این ادمین."""
    now = time.time()
    for token, (_, expires) in list(_links.items()):
        if expires <= now:
            del _links[token]
    token = secrets.token_urlsafe(32)
    _links[token] = (user_id, now + LOGIN_TTL_SECONDS)
    return token


def consume_login_token(token: str) -> int | None:
    """توکن را مصرف می‌کند؛ لینکی که یک بار باز شد بار دوم کار نمی‌کند."""
    entry = _links.pop(token, None)
    if entry is None:
        return None
    user_id, expires = entry
    return user_id if expires > time.time() else None


async def start_session(user_id: int, *, user_agent: str = "") -> str:
    """نشست بادوام برای کسی که از راه لینک آمده."""
    account = await account_of(user_id)
    return await _open_session(account.id if account else 0, user_id, user_agent)


def reset() -> None:
    """فقط برای تست‌ها."""
    _pending.clear()
    _links.clear()
