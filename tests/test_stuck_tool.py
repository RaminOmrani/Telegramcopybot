"""تست ابزارِ «پست‌هایی که نرفته‌اند چه هستند».

<b>مهم‌ترین تستِ اینجا نگهبانِ اکانت است.</b> این ابزار با همان اکانتی
وصل می‌شود که ربات. اگر هم‌زمان با ربات اجرا شود، تلگرام ممکن است کلید
را باطل کند و اکانت از ربات بیرون بیفتد — یعنی ابزاری که برای تشخیص
ساخته شده، خودش خرابی بسازد.
"""
from __future__ import annotations

import importlib.util
import subprocess
from pathlib import Path

STUCK = Path(__file__).parent.parent / "tools" / "stuck.py"


def _load():
    spec = importlib.util.spec_from_file_location("stuck_tool", STUCK)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_when_it_cannot_tell_it_assumes_the_bot_is_running(monkeypatch):
    """<b>خطای ارزان در برابر خطای گران.</b>

    اگر نتوانیم وضعیت سرویس را بپرسیم، «روشن» فرض می‌کنیم: بدترین
    حالتش این است که کاربر یک بار دیگر stop بزند. فرضِ «خاموش» بدترین
    حالتش بیرون افتادنِ اکانتِ صاحب کانال است.
    """
    stuck = _load()

    def boom(*args, **kwargs):
        raise FileNotFoundError("systemctl")

    monkeypatch.setattr(stuck.subprocess, "run", boom)
    assert stuck.bot_is_running() is True


def test_a_stopped_bot_lets_it_run(monkeypatch):
    stuck = _load()
    monkeypatch.setattr(
        stuck.subprocess, "run",
        lambda *a, **k: subprocess.CompletedProcess(a, 3, stdout="inactive\n"),
    )
    assert stuck.bot_is_running() is False


def test_a_restarting_bot_counts_as_running(monkeypatch):
    """«activating» یعنی همین الان دارد وصل می‌شود — بدترین لحظه برای
    یک اتصالِ دوم با همان کلید."""
    stuck = _load()
    monkeypatch.setattr(
        stuck.subprocess, "run",
        lambda *a, **k: subprocess.CompletedProcess(a, 0, stdout="activating\n"),
    )
    assert stuck.bot_is_running() is True


def test_media_is_described_without_downloading_it():
    from telethon.tl import types as t

    stuck = _load()
    video = t.MessageMediaDocument(document=t.Document(
        id=1, access_hash=1, file_reference=b"", date=None,
        mime_type="video/mp4", size=10, dc_id=2, attributes=[],
    ))
    assert stuck.describe(video) == "MessageMediaDocument (document=Document mime=video/mp4)"
    # رسانه‌ای که Telethonِ ما نمی‌شناسد — همان مظنونِ اصلی
    assert stuck.describe(t.MessageMediaUnsupported()) == "MessageMediaUnsupported"
    assert stuck.describe(None) == "-"


def test_the_output_labels_are_plain_ascii():
    """کنسول VNC فارسی را نشان نمی‌دهد."""
    labels = [
        line for line in STUCK.read_text(encoding="utf-8").splitlines()
        if ("out(" in line or "print(" in line) and not line.strip().startswith("#")
    ]
    persian = [
        line for line in labels if any("؀" <= ch <= "ۿ" for ch in line)
    ]
    assert not persian, persian[:3]
