"""تست شرایط استفاده و ثبت پذیرش.

<b>چرا این تست‌ها بیش از حد معمول‌اند.</b> این متن قرار است وقتی
شکایتی مطرح شود به کار بیاید. آن روز، «کاربر پذیرفته بود» باید یک
واقعیتِ ثبت‌شده باشد نه یک حدس — پس هم گلوگاه باید واقعاً بسته
باشد، هم پذیرش باید نسخه و زمان داشته باشد.
"""
from __future__ import annotations

import pytest

from telkap.services import terms
from tests.test_copier import _setup


def test_the_three_promises_that_matter_are_actually_in_the_text():
    """اگر روزی این جمله‌ها از متن بیفتند، متن دیگر کاری نمی‌کند."""
    body = terms.text()

    assert "پاسخگویش شمایید" in body           # مسئولیت محتوا
    assert "مسئولیتش با ما نیست" in body        # ریسک اکانت
    assert "بازگردانده" in body                 # عودت وجه


def test_the_text_never_promises_a_refund():
    """قاعده‌ی کسب‌وکار: عودت وجه نداریم.

    یک «در صورت نارضایتی وجه بازمی‌گردد» که ناخواسته وارد متن شود،
    تعهدی می‌سازد که نمی‌توانیم به آن عمل کنیم.
    """
    body = terms.text()

    assert "عودت وجه" not in body
    assert "بازگردانده نمی‌شود" in body
    assert "بدون بازگرداندن وجه" in body


def test_there_is_a_path_for_the_content_owner():
    """بدون مسیر رسیدگی، انتقال مسئولیت روی کاغذ می‌ماند."""
    body = terms.text()

    assert "شکایتی دارید" in body
    assert "متوقف" in body


def test_the_text_fits_in_one_telegram_message():
    """سقف تلگرام ۴۰۹۶ نویسه است؛ متنِ بلندتر اصلاً فرستاده نمی‌شود."""
    assert len(terms.text()) < 4096
    assert len(terms.short()) < 400


def test_the_html_tags_are_balanced():
    """تگ باز مانده یعنی تلگرام کل پیام را رد می‌کند."""
    body = terms.text()

    for tag in ("b", "i"):
        assert body.count(f"<{tag}>") == body.count(f"</{tag}>"), tag


def test_support_handle_falls_back_when_unset():
    """متن باید بدون SUPPORT_USERNAME هم ساخته شود، نه اینکه خطا بدهد."""
    assert "پشتیبانی" in terms.text()


# ── ثبت پذیرش ────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_new_user_has_not_accepted(tmp_path, monkeypatch):
    await _setup(tmp_path, monkeypatch, settings={})

    assert await terms.accepted(7) is False


@pytest.mark.asyncio
async def test_accepting_is_recorded_with_version_and_time(tmp_path, monkeypatch):
    """زمان لازم است: بدون آن نمی‌شود گفت «این تاریخ پذیرفت»."""
    db_module, _ = await _setup(tmp_path, monkeypatch, settings={})
    from telkap.models import User

    await terms.accept(7)

    assert await terms.accepted(7) is True
    async with db_module.get_session() as db:
        user = await db.get(User, 7)
        assert user.terms_version == terms.VERSION
        assert user.terms_accepted_at is not None


@pytest.mark.asyncio
async def test_accepting_an_old_version_is_not_accepting_the_new_one(
    tmp_path, monkeypatch
):
    """اگر متن عوض شود، پذیرشِ قبلی دیگر معتبر نیست.

    این تنها راهی است که بالا بردن VERSION واقعاً همه را دوباره
    بپرسد — بدون آن، تغییر متن بی‌صدا نادیده گرفته می‌شود.
    """
    await _setup(tmp_path, monkeypatch, settings={})
    await terms.accept(7)

    monkeypatch.setattr(terms, "VERSION", terms.VERSION + 1)

    assert await terms.accepted(7) is False


@pytest.mark.asyncio
async def test_an_unknown_user_is_not_accepted_and_does_not_crash(
    tmp_path, monkeypatch
):
    await _setup(tmp_path, monkeypatch, settings={})

    await terms.accept(999999)          # نباید خطا بدهد
    assert await terms.accepted(999999) is False


# ── گلوگاه ───────────────────────────────────────────────────────────


def test_login_is_gated_on_acceptance():
    """اتصال اکانت تنها راه کپی کردن است، پس تنها جایی است که باید بسته باشد.

    اگر روزی این بررسی از <code>_begin_login</code> برداشته شود،
    کاربران بدون پذیرش شروع به کپی می‌کنند و متن بی‌اثر می‌ماند.
    """
    import inspect

    from telkap.handlers import account

    source = inspect.getsource(account._begin_login)

    assert "terms.accepted" in source
    assert "return" in source


def test_accepting_continues_straight_into_login():
    """کاربری که پذیرفت نباید دوباره دنبال دکمه بگردد."""
    import inspect

    from telkap.handlers import account

    source = inspect.getsource(account.cb_accept_terms)

    assert "terms.accept" in source
    assert "Flow.phone" in source


def test_the_reading_view_has_no_accept_button():
    """خواندن از راهنما نباید سهواً پذیرش ثبت کند."""
    import inspect

    from telkap.handlers import account

    source = inspect.getsource(account.cb_show_terms)

    assert "terms.accept" not in source
    assert "terms_keyboard" not in source


# ------------------------------------------------------- صفحه‌ی فروش
def _landing() -> str:
    from pathlib import Path

    return (Path(__file__).parent.parent / "site" / "index.html").read_text(
        encoding="utf-8"
    )


def test_the_landing_prices_match_the_real_plans():
    """<b>قیمتِ کهنه روی صفحه‌ی فروش، بدترین نوع اشتباه است.</b>

    مشتری عددی را می‌بیند و وقتی وارد ربات می‌شود عدد دیگری است. یک
    بار همین اتفاق افتاد، چون صفحه نسخه‌ی دستیِ خودش را داشت. حالا از
    plans.py ساخته می‌شود و این تست می‌سنجد که کهنه نشده باشد.
    """
    import subprocess
    import sys
    from pathlib import Path

    root = Path(__file__).parent.parent
    result = subprocess.run(
        [sys.executable, str(root / "tools" / "site.py"), "--check"],
        capture_output=True,
        text=True,
        cwd=root,
    )
    assert result.returncode == 0, result.stderr or result.stdout


def test_every_purchasable_plan_appears_on_the_landing_page():
    from telkap.plans import _fa as fa
    from telkap.plans import all_purchasable

    page = _landing()
    for plan in all_purchasable():
        assert fa(plan.price_toman) in page, plan.code


def test_the_landing_font_is_not_fetched_from_a_cdn():
    """<b>مخاطب این صفحه کاربر ایرانی است.</b>

    فونتی که از fonts.googleapis.com بیاید یا کند می‌آید یا اصلاً
    نمی‌آید، و صفحه‌ی فروشی که با فونت پیش‌فرض ویندوز رندر شود پیش از
    آنکه خوانده شود قضاوت می‌شود.
    """
    page = _landing()

    assert "@import url('https://fonts.googleapis.com" not in page
    assert "/fonts/Vazirmatn-Regular.woff2" in page


def test_the_landing_font_files_are_really_there():
    from pathlib import Path

    fonts = Path(__file__).parent.parent / "site" / "fonts"
    for weight in ("Regular", "Medium", "SemiBold", "Bold"):
        assert (fonts / f"Vazirmatn-{weight}.woff2").exists()


def test_the_landing_page_never_promises_a_refund():
    """<b>قانون کسب‌وکار: عودت وجه نداریم.</b>

    وعده‌ای که روی صفحه‌ی فروش داده شود، بعداً باید یا اجرا شود یا
    توضیح داده شود؛ هر دو گران‌اند.
    """
    page = _landing()

    for promise in ("عودت وجه", "بازگشت وجه", "ضمانت بازگشت", "مسترد"):
        assert promise not in page, promise
    # و صریح می‌گوید که برگشت‌ناپذیر است
    assert "بازگشت‌پذیر نیستند" in page
