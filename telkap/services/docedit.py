"""بازنویسی تگ داخل فایل‌های پیوست، پیش از ارسال به کانال مقصد.

کانال‌های پروکسی معمولاً کانفیگ را به‌صورت فایل می‌فرستند، نه متن. کپی
کردن آن فایل یعنی نام کانال قبلی داخلش می‌ماند و کاربر نهایی همان را
می‌بیند.

اینجا فایل دانلود، شناسایی، بازنویسی و با نام تازه بسته‌بندی می‌شود.

اصل حاکم: <b>فایلی که مطمئن نیستیم را دست نمی‌زنیم</b>. اگر قالب را
نشناسیم یا بازنویسی نگیرد، فایل اصلی بی‌تغییر می‌رود. فایل خرابِ
کانفیگ از فایل با نام قدیمی بدتر است.
"""
from __future__ import annotations

import io
import json
import logging
import zipfile
from pathlib import Path

from telkap.services import configs

log = logging.getLogger(__name__)

# بزرگ‌تر از این را باز نمی‌کنیم؛ کانفیگ واقعی هیچ‌وقت این‌قدر بزرگ نیست
MAX_EDIT_BYTES = 5 * 1024 * 1024

# پسوندهایی که متن‌اند و ارزش نگاه کردن دارند
TEXT_SUFFIXES = {
    "",
    ".txt",
    ".conf",
    ".config",
    ".json",
    ".yaml",
    ".yml",
    ".ini",
    ".list",
    ".sub",
    ".npv",
    ".npv4",
    ".dark",
    ".hc",
    ".ehi",
}

# فایل‌های کانفیگی که محتوایشان رمزنگاری‌شده است و بازنویسی تگِ داخلشان
# ممکن نیست. برای این‌ها فقط نام فایل عوض می‌شود — که خودش هم دیده
# می‌شود و هم کارِ کانالِ ناشر را می‌کند.
#
# `.npvt` (برنامه‌ی NPV Tunnel) با سرآیند NPVT1 شروع می‌شود و بدنه‌اش
# رمز است؛ با هیچ فشرده‌سازی استانداردی باز نمی‌شود.
SEALED_SUFFIXES = {".npvt", ".nm", ".dpk", ".hat", ".sks"}

SEALED_MAGIC = (b"NPVT",)


def _decode(raw: bytes) -> str | None:
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return None


def rewrite_bytes(raw: bytes, tag: str) -> tuple[bytes, int]:
    """محتوای یک فایل را بازنویسی می‌کند.

    خروجی: (محتوای تازه، تعداد تغییرها). تعداد ۰ یعنی دست‌نخورده.
    """
    clean = configs.clean_tag(tag)
    if not clean or not raw or len(raw) > MAX_EDIT_BYTES:
        return raw, 0

    # ۱) زیپ — بعضی برنامه‌ها کانفیگ را در بسته می‌گذارند
    if raw[:2] == b"PK":
        return _rewrite_zip(raw, clean)

    text = _decode(raw)
    if text is None:
        return raw, 0      # دودویی و ناشناخته؛ دست نمی‌زنیم

    # ۲) JSON — فیلدهای نام هر جای درخت باشند عوض می‌شوند
    stripped = text.strip()
    if stripped[:1] in "{[":
        try:
            data = json.loads(stripped)
        except json.JSONDecodeError:
            data = None
        if data is not None:
            changed = configs.rewrite_json(data, clean)
            # لینک‌های کانفیگ ممکن است داخل رشته‌های همان JSON هم باشند
            packed = json.dumps(data, ensure_ascii=False, indent=2)
            packed, links = configs.rewrite_text(packed, clean)
            total = changed + links
            return (packed.encode("utf-8"), total) if total else (raw, 0)

    # ۳) فایل اشتراک base64
    if configs.looks_like_subscription(text):
        rewritten, changed = configs.rewrite_subscription(text, clean)
        return (rewritten.encode("utf-8"), changed) if changed else (raw, 0)

    # ۴) متن ساده‌ی حاوی لینک کانفیگ
    rewritten, changed = configs.rewrite_text(text, clean)
    return (rewritten.encode("utf-8"), changed) if changed else (raw, 0)


def _rewrite_zip(raw: bytes, tag: str) -> tuple[bytes, int]:
    """اعضای متنیِ یک زیپ را بازنویسی و دوباره بسته‌بندی می‌کند.

    اگر هیچ عضوی عوض نشد، فایل اصلی برگردانده می‌شود — بسته‌بندی دوباره‌ی
    بی‌دلیل، فقط ریسکِ خراب کردن یک بسته‌ی سالم است.
    """
    try:
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            members = archive.infolist()
            contents = {info.filename: archive.read(info) for info in members}
    except (zipfile.BadZipFile, RuntimeError, ValueError):
        return raw, 0

    changed = 0
    for name, blob in list(contents.items()):
        if Path(name).suffix.lower() not in TEXT_SUFFIXES:
            continue
        new_blob, count = rewrite_bytes(blob, tag)
        if count:
            contents[name] = new_blob
            changed += count

    if not changed:
        return raw, 0

    buffer = io.BytesIO()
    try:
        with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as out:
            for info in members:
                out.writestr(info.filename, contents[info.filename])
    except Exception:
        log.warning("بسته‌بندی دوباره‌ی زیپ ناموفق بود؛ فایل اصلی می‌رود", exc_info=True)
        return raw, 0
    return buffer.getvalue(), changed


def worth_opening(filename: str) -> bool:
    """آیا این فایل ارزش دست زدن دارد؟ عکس و ویدیو قطعاً نه."""
    suffix = Path(filename or "").suffix.lower()
    return suffix in TEXT_SUFFIXES or suffix in SEALED_SUFFIXES or suffix == ".zip"


def is_sealed(filename: str, raw: bytes = b"") -> bool:
    """آیا محتوای این فایل رمزنگاری‌شده است؟

    هم از روی پسوند تشخیص داده می‌شود و هم از روی سرآیند خودِ فایل، چون
    بعضی کانال‌ها پسوند را عوض می‌کنند.
    """
    if Path(filename or "").suffix.lower() in SEALED_SUFFIXES:
        return True
    return any(raw.startswith(magic) for magic in SEALED_MAGIC)


def new_name(filename: str, pattern: str, tag: str) -> str:
    """نام تازه‌ی فایل. `pattern` می‌تواند {tag} و {name} داشته باشد."""
    original = Path(filename or "config")
    stem = original.stem or "config"
    suffix = original.suffix
    clean = configs.clean_tag(tag) or stem
    try:
        built = (pattern or "{tag}").format(tag=clean, name=stem)
    except (KeyError, IndexError, ValueError):
        built = clean
    # کاراکترهایی که در نام فایل دردسر می‌سازند
    safe = "".join(ch for ch in built if ch not in '\\/:*?"<>|').strip()
    return (safe or stem)[:80] + suffix


def rewrite_file(path: str | Path, tag: str, *, rename: str = "") -> tuple[Path, int]:
    """فایل روی دیسک را بازنویسی می‌کند.

    خروجی: (مسیر فایلی که باید فرستاده شود، تعداد تغییرها). اگر چیزی عوض
    نشده باشد همان مسیر اصلی برمی‌گردد.

    برای فایل‌های رمزنگاری‌شده فقط نام عوض می‌شود. نامِ فایل بیرون از
    بسته‌ی رمز است، پس همیشه شدنی است — و کاربر نهایی هم همان را در
    کانال می‌بیند.
    """
    source = Path(path)
    if not source.exists() or not worth_opening(source.name):
        return source, 0

    try:
        raw = source.read_bytes()
    except OSError:
        return source, 0

    if is_sealed(source.name, raw):
        renamed = _rename_only(source, tag, rename)
        return renamed, (1 if renamed != source else 0)

    new_raw, changed = rewrite_bytes(raw, tag)
    if not changed:
        return source, 0

    target = _unique_target(source, tag, rename)
    try:
        target.write_bytes(new_raw)
    except OSError:
        log.warning("نوشتن فایل بازنویسی‌شده ناموفق بود", exc_info=True)
        return source, 0
    return target, changed


def _unique_target(source: Path, tag: str, rename: str) -> Path:
    target = source.with_name(new_name(source.name, rename or "{tag}", tag))
    if target == source:
        target = source.with_name(f"tagged-{source.name}")
    return target


def _rename_only(source: Path, tag: str, rename: str) -> Path:
    """محتوا دست‌نخورده می‌ماند و فقط نام فایل عوض می‌شود."""
    if not configs.clean_tag(tag):
        return source
    target = _unique_target(source, tag, rename)
    try:
        source.rename(target)
    except OSError:
        log.warning("تغییر نام فایل رمزنگاری‌شده ناموفق بود", exc_info=True)
        return source
    return target
