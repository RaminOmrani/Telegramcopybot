"""بازنویسی نام (تگ) کانفیگ‌های پروکسی.

چرا جایگزینی متن ساده کار نمی‌کند: در `vmess://` کل کانفیگ یک JSON است
که با base64 کدگذاری شده. آیدی کانال داخلش به‌صورت متن دیده نمی‌شود، پس
«جایگزینی کلمات» هیچ اثری روی آن ندارد. باید کدگشایی شود، فیلد نامش عوض
شود، و دوباره کد شود.

بقیه‌ی پروتکل‌ها (`vless`, `trojan`, `ss`, `hysteria2`, `tuic`) شکل URL
دارند و نامشان در بخش `#` انتهای لینک است؛ آن‌ها آسان‌ترند ولی باید
مواظب بود بقیه‌ی لینک دست‌نخورده بماند.

قاعده‌ی کلی این ماژول: <b>اگر مطمئن نبودی، دست نزن</b>. کانفیگ خراب از
کانفیگ با نام کانال قبلی خیلی بدتر است — کاربر نهایی وصل نمی‌شود و
تقصیرش گردن کانالِ ناشر می‌افتد.
"""
from __future__ import annotations

import base64
import binascii
import json
import logging
import re

log = logging.getLogger(__name__)

# پروتکل‌هایی که نامشان در بخش #  انتهای لینک است
FRAGMENT_SCHEMES = (
    "vless",
    "trojan",
    "ss",
    "ssr",
    "hysteria",
    "hysteria2",
    "hy2",
    "tuic",
    "wireguard",
    "juicity",
    "snell",
)

ALL_SCHEMES = ("vmess", *FRAGMENT_SCHEMES)

# لینک کانفیگ تا اولین فاصله یا خط جدید ادامه دارد
LINK_RE = re.compile(
    r"\b(" + "|".join(ALL_SCHEMES) + r")://([^\s<>\"']+)",
    re.IGNORECASE,
)

# کلیدهایی که در JSONهای کانفیگ نقش «نام» دارند
NAME_KEYS = ("ps", "remarks", "remark", "name", "label", "tag", "title")

MAX_TAG_LEN = 60


def _b64decode(raw: str) -> bytes | None:
    """base64 با padding ناقص هم رایج است؛ خودمان کاملش می‌کنیم."""
    cleaned = raw.strip().replace("-", "+").replace("_", "/")
    cleaned = "".join(cleaned.split())
    padding = (-len(cleaned)) % 4
    try:
        return base64.b64decode(cleaned + "=" * padding, validate=False)
    except (binascii.Error, ValueError):
        return None


def _b64encode(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


def clean_tag(tag: str) -> str:
    """تگ را برای گذاشتن در نام کانفیگ آماده می‌کند."""
    # `#` نام را می‌شکند و فاصله‌ی خطی در لینک جا نمی‌شود
    text = " ".join((tag or "").split()).replace("#", "")
    return text[:MAX_TAG_LEN]


# --------------------------------------------------------------- vmess
def rewrite_vmess(link: str, tag: str) -> str:
    """`vmess://` را کدگشایی، فیلد `ps` را عوض، و دوباره کد می‌کند."""
    body = link.split("://", 1)[1]
    raw = _b64decode(body)
    if raw is None:
        return link
    try:
        data = json.loads(raw.decode("utf-8", errors="strict"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return link
    if not isinstance(data, dict):
        return link

    data["ps"] = tag
    # separators فشرده، تا کانفیگ بی‌دلیل بزرگ‌تر از قبل نشود
    packed = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    return "vmess://" + _b64encode(packed.encode("utf-8"))


# ------------------------------------------------- بقیه‌ی پروتکل‌ها
def rewrite_fragment(link: str, tag: str) -> str:
    """نام را در بخش `#` انتهای لینک می‌گذارد."""
    from urllib.parse import quote

    head = link.split("#", 1)[0]
    return f"{head}#{quote(tag, safe='')}"


def rewrite_link(link: str, tag: str) -> str:
    """یک لینک کانفیگ را با نام تازه برمی‌گرداند."""
    scheme = link.split("://", 1)[0].lower()
    if scheme == "vmess":
        return rewrite_vmess(link, tag)
    if scheme in FRAGMENT_SCHEMES:
        return rewrite_fragment(link, tag)
    return link


def find_links(text: str) -> list[str]:
    return [match.group(0) for match in LINK_RE.finditer(text or "")]


def rewrite_text(text: str, tag: str) -> tuple[str, int]:
    """همه‌ی کانفیگ‌های داخل یک متن را با نام تازه برمی‌گرداند.

    خروجی: (متن تازه، تعداد کانفیگ‌های بازنویسی‌شده).
    """
    clean = clean_tag(tag)
    if not clean or not text:
        return text, 0

    changed = 0

    def swap(match: re.Match) -> str:
        nonlocal changed
        original = match.group(0)
        try:
            replacement = rewrite_link(original, clean)
        except Exception:
            log.debug("بازنویسی کانفیگ ناموفق بود؛ دست‌نخورده ماند", exc_info=True)
            return original
        if replacement != original:
            changed += 1
        return replacement

    return LINK_RE.sub(swap, text), changed


# ------------------------------------------- فایل اشتراک (subscription)
def looks_like_subscription(raw: str) -> bool:
    """آیا این متن یک فایل اشتراک base64 است؟"""
    decoded = _b64decode(raw)
    if decoded is None or len(decoded) < 16:
        return False
    try:
        text = decoded.decode("utf-8")
    except UnicodeDecodeError:
        return False
    return bool(find_links(text))


def rewrite_subscription(raw: str, tag: str) -> tuple[str, int]:
    """فایل اشتراکِ base64 را کدگشایی، بازنویسی و دوباره کد می‌کند."""
    decoded = _b64decode(raw)
    if decoded is None:
        return raw, 0
    try:
        text = decoded.decode("utf-8")
    except UnicodeDecodeError:
        return raw, 0
    rewritten, changed = rewrite_text(text, tag)
    if not changed:
        return raw, 0
    return _b64encode(rewritten.encode("utf-8")), changed


# ------------------------------------------------------ JSON کانفیگ
def rewrite_json(data, tag: str) -> int:
    """فیلدهای نام را در یک ساختار JSON — هر جای درخت — عوض می‌کند.

    درجا تغییر می‌دهد و تعداد تغییرها را برمی‌گرداند.
    """
    changed = 0
    if isinstance(data, dict):
        for key, value in data.items():
            if key.lower() in NAME_KEYS and isinstance(value, str):
                data[key] = tag
                changed += 1
            else:
                changed += rewrite_json(value, tag)
    elif isinstance(data, list):
        for item in data:
            changed += rewrite_json(item, tag)
    return changed
