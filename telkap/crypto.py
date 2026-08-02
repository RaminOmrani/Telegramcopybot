"""رمزنگاری سشن کاربران پیش از ذخیره در دیتابیس.

سشن‌رشته‌ی تلگرام معادل دسترسی کامل به حساب کاربر است، بنابراین هرگز
به‌صورت خام ذخیره نمی‌شود؛ با کلید Fernet موجود در .env رمز می‌شود.
"""
from __future__ import annotations

from cryptography.fernet import Fernet, InvalidToken

from telkap.config import get_settings

_fernet: Fernet | None = None


def _cipher() -> Fernet:
    global _fernet
    if _fernet is None:
        _fernet = Fernet(get_settings().fernet_key.encode())
    return _fernet


def encrypt(plain: str) -> str:
    return _cipher().encrypt(plain.encode()).decode()


def decrypt(token: str) -> str | None:
    try:
        return _cipher().decrypt(token.encode()).decode()
    except (InvalidToken, ValueError):
        return None
