"""تست تجزیه‌ی نشانی پروکسی."""
from __future__ import annotations

import pytest

from telkap import proxy


def test_empty_means_no_proxy():
    assert proxy.parse("") is None
    assert proxy.parse("   ") is None
    assert proxy.for_telethon("") is None
    assert proxy.for_aiogram("") is None
    assert proxy.describe("") == "بدون پروکسی"


@pytest.mark.parametrize(
    "url,scheme,host,port",
    [
        ("socks5://127.0.0.1:10808", "socks5", "127.0.0.1", 10808),
        ("http://127.0.0.1:10809", "http", "127.0.0.1", 10809),
        ("socks4://proxy.local:1080", "socks4", "proxy.local", 1080),
        ("  socks5://127.0.0.1:1080  ", "socks5", "127.0.0.1", 1080),
    ],
)
def test_parses_common_forms(url, scheme, host, port):
    parts = proxy.parse(url)
    assert parts["scheme"] == scheme
    assert parts["host"] == host
    assert parts["port"] == port


def test_parses_credentials():
    parts = proxy.parse("socks5://ali:secret@10.0.0.5:1080")
    assert parts["username"] == "ali"
    assert parts["password"] == "secret"


@pytest.mark.parametrize(
    "bad",
    [
        "127.0.0.1:1080",          # بدون نوع
        "ftp://127.0.0.1:21",      # نوع پشتیبانی‌نشده
        "socks5://127.0.0.1",      # بدون پورت
        "socks5://",               # بدون آدرس
    ],
)
def test_rejects_invalid(bad):
    with pytest.raises(proxy.ProxyError):
        proxy.parse(bad)


def test_telethon_format():
    cfg = proxy.for_telethon("socks5://127.0.0.1:10808")
    assert cfg == {
        "proxy_type": "socks5",
        "addr": "127.0.0.1",
        "port": 10808,
        "rdns": True,
    }


def test_telethon_maps_http_and_aliases():
    assert proxy.for_telethon("https://p:8080")["proxy_type"] == "http"
    assert proxy.for_telethon("socks5h://p:1080")["proxy_type"] == "socks5"
    assert proxy.for_telethon("socks4a://p:1080")["proxy_type"] == "socks4"


def test_telethon_includes_auth():
    cfg = proxy.for_telethon("socks5://ali:secret@10.0.0.5:1080")
    assert cfg["username"] == "ali"
    assert cfg["password"] == "secret"


def test_describe_hides_password():
    text = proxy.describe("socks5://ali:secret@10.0.0.5:1080")
    assert "secret" not in text
    assert "10.0.0.5:1080" in text
