"""تست ترتیبِ اسکریپت به‌روزرسانی.

<b>چرا اسکریپت bash هم تست دارد.</b> این فایل تنها چیزی است که بین
«کدِ تازه» و «مشتری‌هایی که منتظرند» می‌ایستد، و خودش تست واحد ندارد.
ولی چند <b>ترتیب</b> در آن هست که اگر جابه‌جا شوند هیچ‌کس تا روزِ
خرابی نمی‌فهمد — و آن روز، قطعیِ سرویس است.

اینجا فقط همان ترتیب‌ها سنجیده می‌شوند، نه رفتار کامل اسکریپت.
"""
from __future__ import annotations

from pathlib import Path

import pytest

SCRIPT = Path(__file__).parent.parent / "deploy" / "update.sh"


@pytest.fixture(scope="module")
def script() -> str:
    return SCRIPT.read_text(encoding="utf-8")


def test_the_new_code_is_compiled_before_the_bot_is_stopped(script: str):
    """<b>کامیتِ خراب نباید ربات را پایین بیاورد.</b>

    اگر بررسی بعد از توقف بیفتد، کامیتی با خطای نحوی اول سرویس را
    می‌خواباند و بعد اسکریپت باید برش گرداند — یعنی دو قطعی به‌جای
    صفر.
    """
    assert "compileall" in script, "بررسی نحوی اصلاً نیست"
    assert script.index("compileall") < script.index('systemctl stop "$SERVICE"')


def test_dependencies_are_only_installed_when_they_changed(script: str):
    """در بیشترِ به‌روزرسانی‌ها فقط کدِ خودمان عوض می‌شود؛ وارسیِ کل
    فهرست وسط قطعی، ثانیه‌های بی‌دلیل است."""
    assert "_DEPS_CHANGED" in script
    assert "-- requirements.txt" in script


def test_the_backup_still_happens_after_the_bot_is_stopped(script: str):
    """<b>این یکی عمداً داخل پنجره‌ی قطعی می‌ماند.</b>

    کپی گرفتن از دیتابیسی که در حال نوشته شدن است می‌تواند نسخه‌ی
    نیمه‌کاره بدهد، و پشتیبانِ خراب بدتر از نداشتنش است چون به آن
    اعتماد می‌کنید. سرعت به این یکی نمی‌ارزد.
    """
    assert script.index('systemctl stop "$SERVICE"') < script.index("_backup_db\n")


def test_the_downtime_is_measured_and_printed(script: str):
    """«قطعی کم شد» بدون عدد یک ادعاست، نه یک واقعیت."""
    assert "_DOWN_FROM" in script
    assert "مدت قطعی" in script
    # شمارنده باید درست پیش از توقف شروع شود، نه زودتر
    assert script.index("_DOWN_FROM=$(date +%s)") < script.index(
        'systemctl stop "$SERVICE"'
    )


def test_the_script_never_copies_env_over_itself(script: str):
    """<b>خطی که اگر روزی اضافه شود، همه‌ی نشست‌های کاربران را می‌کشد.</b>

    FERNET_KEY داخل .env است و بدون آن، سشن رمزنگاری‌شده‌ی هر کاربر
    برای همیشه غیرقابل بازگشایی می‌شود.
    """
    for danger in ("cp .env.example .env", "cp -f .env.example"):
        assert danger not in script, danger


def test_a_failed_start_rolls_back(script: str):
    """اگر نسخه‌ی تازه بالا نیاید، باید خودش برگردد — نه اینکه سرویس
    خوابیده بماند تا کسی سر بزند."""
    assert "git reset --hard" in script
    assert script.index('systemctl start "$SERVICE"') < script.index("git reset --hard")


# ------------------------------------------------------------ ابزارها
TOOLS = Path(__file__).parent.parent / "tools"


@pytest.mark.parametrize("name", ["why.py", "botcheck.py"])
def test_the_tools_work_from_any_directory(name: str):
    """<b>تله‌ای که یک بار گرفتارش شدیم.</b>

    مسیر دیتابیس در تنظیمات نسبی است. اجرای این ابزارها از پوشه‌ای
    دیگر — که کاملاً طبیعی است، چون مسیرشان را کامل می‌نویسیم — به
    «unable to open database file» می‌خورد، و آن پیام شبیه خرابیِ
    دیتابیس به نظر می‌رسد نه یک اشتباه ساده در پوشه‌ی جاری.
    """
    source = (TOOLS / name).read_text(encoding="utf-8")
    assert "os.chdir(" in source, f"{name} به پوشه‌ی جاری وابسته است"


def test_the_database_path_in_settings_is_still_relative():
    """<b>نگهبانِ فرضی که تستِ بالا رویش بنا شده.</b>

    اگر روزی مسیر دیتابیس مطلق شود، آن chdirها دیگر لازم نیستند و
    این تست یادآوری می‌کند که می‌شود برشان داشت.
    """
    import inspect

    from telkap.config import Settings

    default = inspect.signature(Settings).parameters["database_url"].default
    assert not str(default).startswith("sqlite+aiosqlite:////")
