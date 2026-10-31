"""تست ورود به پنل وب: رمز، کد دومرحله‌ای، و نشست بادوام.

<b>چرا این فایل سخت‌گیر است.</b> این پنل به پول و به اکانت متصلِ
مشتری‌ها دسترسی دارد. هر سوراخی اینجا یعنی کسی می‌تواند رسید تأیید
کند، نرخ عوض کند، یا کاربری را مسدود کند.
"""
from __future__ import annotations

import pytest

from telkap.models import User, WebAccount
from telkap.services import roles
from telkap.web import auth
from tests.test_copier import _setup


async def _account(db_module, username="ramin", password="رمزِ-خیلی-قوی", user_id=7):
    async with db_module.get_session() as db:
        if await db.get(User, user_id) is None:
            db.add(User(id=user_id, first_name="ر"))
            await db.commit()
    account, problem = await auth.create_account(username, password, user_id)
    assert problem == "", problem
    return account


# ── رمز ──────────────────────────────────────────────────────────────


def test_a_password_is_never_stored_in_the_clear():
    """<b>اگر روزی دیتابیس بیرون برود، رمزها نباید قابل استفاده باشند.</b>"""
    stored = auth.hash_password("رمزِ من")

    assert "رمزِ من" not in stored
    assert stored.count("$") == 1          # salt$hash


def test_the_same_password_hashes_differently_each_time():
    """<b>نمکِ مخصوصِ هر حساب.</b>

    بدون آن، دو نفر با رمز یکسان هشِ یکسان می‌گرفتند — و یک جدولِ
    آماده می‌توانست هر دو را با هم باز کند.
    """
    assert auth.hash_password("یکی") != auth.hash_password("یکی")


def test_a_password_verifies_against_its_own_hash():
    stored = auth.hash_password("رمزِ درست")

    assert auth.verify_password("رمزِ درست", stored) is True
    assert auth.verify_password("رمزِ غلط", stored) is False
    assert auth.verify_password("", stored) is False


def test_a_broken_hash_never_verifies():
    """رکورد خراب نباید به «هرکسی وارد شود» ترجمه شود."""
    assert auth.verify_password("x", "") is False
    assert auth.verify_password("x", "بدون-دلار") is False


def test_weak_passwords_are_refused():
    assert auth.password_problem("کوتاه") != ""
    assert auth.password_problem("12345678") != ""      # فقط عدد
    assert auth.password_problem("یک رمز به‌درد‌بخور") == ""


def test_a_username_must_look_like_a_username():
    assert auth.clean_username("Ramin") == "ramin"
    assert auth.clean_username(" ramin_1 ") == "ramin_1"
    assert auth.clean_username("ab") == ""              # خیلی کوتاه
    assert auth.clean_username("1ramin") == ""          # با عدد شروع شده
    assert auth.clean_username("رامین") == ""           # غیرانگلیسی
    assert auth.clean_username("ram in") == ""          # فاصله


# ── ورود ─────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_correct_password_starts_the_second_step(tmp_path, monkeypatch):
    db_module, _ = await _setup(tmp_path, monkeypatch, settings={})
    await _account(db_module)

    key, problem, user_id = await auth.start_login("ramin", "رمزِ-خیلی-قوی")

    assert problem == ""
    assert user_id == 7
    assert len(auth.code_for(key)) == auth.CODE_LENGTH


@pytest.mark.asyncio
async def test_a_wrong_password_and_a_missing_user_say_the_same_thing(
    tmp_path, monkeypatch
):
    """<b>پیام‌های متفاوت می‌گویند کدام نام کاربری واقعی است.</b>

    کسی که دارد امتحان می‌کند، از «این کاربر نیست» و «رمز غلط است»
    نصف کار را رایگان می‌گیرد.
    """
    db_module, _ = await _setup(tmp_path, monkeypatch, settings={})
    await _account(db_module)

    _key, wrong_pass, _ = await auth.start_login("ramin", "غلط")
    _key2, no_user, _ = await auth.start_login("kasi", "غلط")

    assert wrong_pass == no_user != ""


@pytest.mark.asyncio
async def test_the_code_must_match(tmp_path, monkeypatch):
    db_module, _ = await _setup(tmp_path, monkeypatch, settings={})
    await _account(db_module)
    key, _problem, _uid = await auth.start_login("ramin", "رمزِ-خیلی-قوی")

    token, problem = await auth.finish_login(key, "000000")
    assert token == "" and problem != ""

    token, problem = await auth.finish_login(key, auth.code_for(key))
    assert token and problem == ""


@pytest.mark.asyncio
async def test_a_key_is_burned_after_use(tmp_path, monkeypatch):
    """کلیدی که یک بار وارد شد، بار دوم کار نمی‌کند."""
    db_module, _ = await _setup(tmp_path, monkeypatch, settings={})
    await _account(db_module)
    key, _p, _u = await auth.start_login("ramin", "رمزِ-خیلی-قوی")
    code = auth.code_for(key)

    await auth.finish_login(key, code)
    token, problem = await auth.finish_login(key, code)

    assert token == "" and problem != ""


@pytest.mark.asyncio
async def test_guessing_the_code_runs_out_of_tries(tmp_path, monkeypatch):
    """<b>کد شش‌رقمی بدون سقفِ تلاش، فقط یک میلیون حدس فاصله دارد.</b>"""
    db_module, _ = await _setup(tmp_path, monkeypatch, settings={})
    await _account(db_module)
    key, _p, _u = await auth.start_login("ramin", "رمزِ-خیلی-قوی")
    real = auth.code_for(key)

    for _ in range(auth.MAX_CODE_TRIES + 1):
        await auth.finish_login(key, "000000")

    token, _problem = await auth.finish_login(key, real)
    assert token == ""          # حتی کد درست هم دیگر کار نمی‌کند


@pytest.mark.asyncio
async def test_an_account_locks_after_repeated_wrong_passwords(tmp_path, monkeypatch):
    """<b>حدس زدن رمز باید از «کند» به «بی‌فایده» برسد.</b>"""
    db_module, _ = await _setup(tmp_path, monkeypatch, settings={})
    await _account(db_module)

    for _ in range(auth.MAX_FAILED):
        await auth.start_login("ramin", "غلط")

    _key, problem, _uid = await auth.start_login("ramin", "رمزِ-خیلی-قوی")
    assert "قفل" in problem


@pytest.mark.asyncio
async def test_a_successful_login_clears_the_failure_count(tmp_path, monkeypatch):
    """چند اشتباه و بعد ورود درست، نباید حساب را نزدیک قفل نگه دارد."""
    db_module, _ = await _setup(tmp_path, monkeypatch, settings={})
    account = await _account(db_module)

    await auth.start_login("ramin", "غلط")
    await auth.start_login("ramin", "رمزِ-خیلی-قوی")

    async with db_module.get_session() as db:
        fresh = await db.get(WebAccount, account.id)
    assert fresh.failed_logins == 0


# ── عوض کردن رمز ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_changing_the_password_closes_open_sessions(tmp_path, monkeypatch):
    """<b>عوض کردن رمز همان کاری است که آدمِ نگران می‌کند.</b>

    اگر نشست‌های باز بمانند، کسی که با رمز قدیمی وارد شده بود همچنان
    داخل است — یعنی دقیقاً همان کاری که کاربر می‌خواست انجام نشده.
    """
    db_module, _ = await _setup(tmp_path, monkeypatch, settings={})
    await _account(db_module)
    token = await auth.start_session(7)
    assert await auth.get_session_for(token) is not None

    problem = await auth.set_password(7, "رمزِ تازه و بلند")

    assert problem == ""
    assert await auth.get_session_for(token) is None


@pytest.mark.asyncio
async def test_the_old_password_stops_working(tmp_path, monkeypatch):
    db_module, _ = await _setup(tmp_path, monkeypatch, settings={})
    await _account(db_module)

    await auth.set_password(7, "رمزِ تازه و بلند")

    _k, problem, _u = await auth.start_login("ramin", "رمزِ-خیلی-قوی")
    assert problem != ""
    _k, problem, _u = await auth.start_login("ramin", "رمزِ تازه و بلند")
    assert problem == ""


@pytest.mark.asyncio
async def test_a_duplicate_username_is_refused(tmp_path, monkeypatch):
    db_module, _ = await _setup(tmp_path, monkeypatch, settings={})
    await _account(db_module)

    account, problem = await auth.create_account("ramin", "رمزِ دیگری هست", 9)

    assert account is None
    assert "گرفته شده" in problem


@pytest.mark.asyncio
async def test_a_disabled_account_cannot_log_in(tmp_path, monkeypatch):
    db_module, _ = await _setup(tmp_path, monkeypatch, settings={})
    account = await _account(db_module)

    async with db_module.get_session() as db:
        row = await db.get(WebAccount, account.id)
        row.enabled = False
        await db.commit()

    _key, problem, _uid = await auth.start_login("ramin", "رمزِ-خیلی-قوی")
    assert problem != ""


@pytest.mark.asyncio
async def test_losing_the_role_closes_the_session(tmp_path, monkeypatch):
    """نقش که گرفته شد، نشستِ باز هم باید بسته شود."""
    db_module, _ = await _setup(tmp_path, monkeypatch, settings={})
    await _account(db_module)
    await roles.set_role(7, roles.ROLE_SUPPORT)
    token = await auth.start_session(7)

    await auth.end_all(7)

    assert await auth.get_session_for(token) is None
