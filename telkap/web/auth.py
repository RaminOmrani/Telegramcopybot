"""ورود به پنل، بدون رمز عبور.

رمز عبور برای پنلی که دو-سه نفر می‌بینند بیشتر بار است تا محافظت: باید جایی
ذخیره شود، فراموش می‌شود، و دیر یا زود در یک چت رد و بدل می‌گردد.

به‌جایش ادمین در خودِ ربات دکمه‌ای می‌زند و یک لینک یک‌بارمصرف می‌گیرد. هویت
او را تلگرام تأیید کرده، پس چیزی برای ساختن یا به‌خاطر سپردن نیست. لینک پنج
دقیقه اعتبار دارد و با اولین باز شدن می‌سوزد؛ اگر جایی لو برود، تا وقتی
استفاده نشده باشد و پنج دقیقه نگذشته باشد ارزش دارد — و چون در همان چت خصوصی
ادمین با ربات می‌ماند، این پنجره عملاً بسته است.

نشست‌ها در حافظه‌اند: با ری‌استارت ربات همه بیرون می‌افتند و باید لینک تازه
بگیرند. برای پنلی با این اندازه، سادگی‌اش می‌ارزد به عمر بیشتر نشست.
"""
from __future__ import annotations

import secrets
import time
from dataclasses import dataclass

COOKIE_NAME = "telkap_panel"

# لینک ورود کوتاه عمر است تا اگر جایی جا ماند، مدت کمی خطرناک بماند
LOGIN_TTL_SECONDS = 5 * 60
# نشست یک روز کاری می‌ماند
SESSION_TTL_SECONDS = 8 * 60 * 60


@dataclass(slots=True)
class Session:
    user_id: int
    csrf: str
    expires_at: float


# توکن ورود → (آیدی کاربر، زمان انقضا)
_pending: dict[str, tuple[int, float]] = {}
# شناسه‌ی نشست → نشست
_sessions: dict[str, Session] = {}


def _sweep(now: float) -> None:
    """منقضی‌ها را دور می‌ریزد.

    هر بار صدا زدنش ارزان است و پنل ترافیک سنگین ندارد، پس همین‌جا انجام
    می‌شود و نیازی به کار زمان‌بندی‌شده نیست.
    """
    for token, (_, expires) in list(_pending.items()):
        if expires <= now:
            del _pending[token]
    for sid, session in list(_sessions.items()):
        if session.expires_at <= now:
            del _sessions[sid]


def issue_login_token(user_id: int) -> str:
    """یک توکن ورودِ یک‌بارمصرف برای این ادمین می‌سازد."""
    now = time.time()
    _sweep(now)
    token = secrets.token_urlsafe(32)
    _pending[token] = (user_id, now + LOGIN_TTL_SECONDS)
    return token


def consume_login_token(token: str) -> int | None:
    """توکن را مصرف می‌کند و آیدی صاحبش را برمی‌گرداند.

    مصرف یعنی حذف: لینکی که یک بار باز شد، بار دوم کار نمی‌کند.
    """
    now = time.time()
    _sweep(now)
    entry = _pending.pop(token, None)
    if entry is None:
        return None
    user_id, expires = entry
    return user_id if expires > now else None


def start_session(user_id: int) -> str:
    now = time.time()
    _sweep(now)
    sid = secrets.token_urlsafe(32)
    _sessions[sid] = Session(
        user_id=user_id,
        csrf=secrets.token_urlsafe(24),
        expires_at=now + SESSION_TTL_SECONDS,
    )
    return sid


def get_session(sid: str | None) -> Session | None:
    if not sid:
        return None
    session = _sessions.get(sid)
    if session is None:
        return None
    if session.expires_at <= time.time():
        _sessions.pop(sid, None)
        return None
    return session


def end_session(sid: str | None) -> None:
    if sid:
        _sessions.pop(sid, None)


def check_csrf(session: Session, sent: str) -> bool:
    """مقایسه‌ی زمان-ثابت تا از روی سرعتِ رد شدن نشود توکن را حدس زد.

    بایت مقایسه می‌شود نه رشته: `compare_digest` روی رشته‌ی غیر-ASCII خطا
    می‌دهد، و مقدارِ فرستاده‌شده از فرم می‌آید — یعنی هر چیزی می‌تواند باشد.
    با رشته، یک مقدار فارسی به‌جای «رد شد» می‌شد خطای ۵۰۰.
    """
    if not sent:
        return False
    return secrets.compare_digest(session.csrf.encode("utf-8"), sent.encode("utf-8"))


def forget_user(user_id: int) -> None:
    """همه‌ی نشست‌های یک نفر را می‌بندد — وقتی نقشش گرفته شده باشد."""
    for sid, session in list(_sessions.items()):
        if session.user_id == user_id:
            del _sessions[sid]


def reset() -> None:
    """فقط برای تست‌ها."""
    _pending.clear()
    _sessions.clear()
