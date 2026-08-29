"""پشتیبانی از پروکسی، برای شبکه‌هایی که تلگرام در آن‌ها مسدود است.

یک نشانی پروکسی در `.env` گذاشته می‌شود و هر دو اتصال — ربات (aiogram)
و اکانت کاربری (Telethon) — از همان عبور می‌کنند.

نمونه‌ها:
    socks5://127.0.0.1:10808
    http://127.0.0.1:10809
    socks5://user:pass@127.0.0.1:1080
"""
from __future__ import annotations

import logging
from urllib.parse import urlparse

log = logging.getLogger(__name__)

SOCKS_SCHEMES = {"socks5", "socks5h", "socks4", "socks4a"}
HTTP_SCHEMES = {"http", "https"}
SUPPORTED = SOCKS_SCHEMES | HTTP_SCHEMES


class ProxyError(ValueError):
    """نشانی پروکسی نامعتبر است."""


def parse(url: str) -> dict | None:
    """نشانی پروکسی را به اجزایش می‌شکند. خروجی None یعنی پروکسی ندارد."""
    url = (url or "").strip()
    if not url:
        return None

    parsed = urlparse(url)
    scheme = (parsed.scheme or "").lower()
    if scheme not in SUPPORTED:
        raise ProxyError(
            f"نوع پروکسی «{scheme or '؟'}» پشتیبانی نمی‌شود. "
            f"از یکی از این‌ها استفاده کنید: {'، '.join(sorted(SUPPORTED))}"
        )
    if not parsed.hostname or not parsed.port:
        raise ProxyError(
            "نشانی پروکسی باید شامل آدرس و پورت باشد، مثل socks5://127.0.0.1:10808"
        )

    return {
        "scheme": scheme,
        "host": parsed.hostname,
        "port": int(parsed.port),
        "username": parsed.username or None,
        "password": parsed.password or None,
    }


def for_telethon(url: str):
    """پروکسی را به قالبی که Telethon می‌فهمد برمی‌گرداند."""
    parts = parse(url)
    if parts is None:
        return None

    # Telethon انواع socks و http را با python-socks مدیریت می‌کند
    proxy_type = "http" if parts["scheme"] in HTTP_SCHEMES else parts["scheme"]
    if proxy_type == "socks5h":
        proxy_type = "socks5"
    elif proxy_type == "socks4a":
        proxy_type = "socks4"

    config = {
        "proxy_type": proxy_type,
        "addr": parts["host"],
        "port": parts["port"],
        "rdns": True,
    }
    if parts["username"]:
        config["username"] = parts["username"]
        config["password"] = parts["password"] or ""
    return config


def for_aiogram(url: str) -> str | None:
    """نشانی را به شکلی درمی‌آورد که python_socks بفهمد.

    aiogram نشانی را به python_socks می‌سپارد و آن فقط socks5 و socks4 و
    http را می‌شناسد؛ socks5h را با ValueError رد می‌کند. پس همان‌جا
    تبدیلش می‌کنیم.

    تبدیل socks5h به socks5 چیزی را از دست نمی‌دهد: python_socks برای
    socks5 وقتی rdns مشخص نشده باشد آن را True می‌گیرد، یعنی تبدیل نام
    همان طرفِ تونل انجام می‌شود — همان کاری که socks5h می‌خواهد. این با
    DNS مسموم تعیین‌کننده است، چون تبدیل نام در این سمت به آدرس صفحه‌ی
    فیلترینگ می‌رسد.
    """
    parts = parse(url)
    if parts is None:
        return None

    scheme = parts["scheme"]
    if scheme == "socks5h":
        scheme = "socks5"
    elif scheme == "https":
        # python_socks فقط http را می‌شناسد. خودِ گفتگو با پروکسی روی
        # همان اتصال است؛ https اینجا فقط املای دیگری از همان است.
        scheme = "http"
    elif scheme == "socks4a":
        # socks4 در python_socks پیش‌فرض rdns=False دارد و راهی برای
        # عوض کردنش از راه نشانی نیست. با DNS سالم مشکلی ندارد.
        log.warning(
            "socks4a به socks4 تبدیل شد؛ تبدیل نام این سمت انجام می‌شود. "
            "اگر DNS شبکه جواب جعلی می‌دهد، به‌جایش socks5h بگذارید."
        )
        scheme = "socks4"

    auth = ""
    if parts["username"]:
        auth = f"{parts['username']}:{parts['password'] or ''}@"
    return f"{scheme}://{auth}{parts['host']}:{parts['port']}"


def describe(url: str) -> str:
    """توصیف کوتاه و بدون رمز، برای لاگ."""
    parts = parse(url)
    if parts is None:
        return "بدون پروکسی"
    auth = "با نام کاربری" if parts["username"] else "بدون احراز هویت"
    return f"{parts['scheme']}://{parts['host']}:{parts['port']} ({auth})"
