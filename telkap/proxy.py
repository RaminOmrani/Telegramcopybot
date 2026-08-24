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
    """aiogram خودِ نشانی را می‌پذیرد؛ فقط اعتبارسنجی می‌کنیم."""
    parts = parse(url)
    return url.strip() if parts else None


def describe(url: str) -> str:
    """توصیف کوتاه و بدون رمز، برای لاگ."""
    parts = parse(url)
    if parts is None:
        return "بدون پروکسی"
    auth = "با نام کاربری" if parts["username"] else "بدون احراز هویت"
    return f"{parts['scheme']}://{parts['host']}:{parts['port']} ({auth})"
