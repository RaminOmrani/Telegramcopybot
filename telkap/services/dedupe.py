"""تشخیص پست تکراری — دقیق، هم‌ارز، یا شبیه.

مسئله‌ی واقعی: یک خبر در سه کانال منبع تکرار می‌شود، ولی هیچ‌کدام
دقیقاً یکی نیستند. هرکدام امضای خودش را پای پست گذاشته، یکی یک ایموجی
بیشتر دارد، دیگری «🔴 فوری» به اولش اضافه کرده.

مقایسه‌ی متن خام این‌ها را سه پست متفاوت می‌بیند و هر سه منتشر می‌شوند —
یعنی همان چیزی که کاربر می‌خواست جلویش را بگیرد. سه سطح داریم:

- `exact`      متن باید مو‌به‌مو یکی باشد (سخت‌گیرترین، کمترین اشتباه)
- `normalized` امضا، ایموجی، لینک و نیم‌فاصله کنار می‌روند و بعد مقایسه
- `similar`    درصدی از شباهت کافی است (برای بازنویسی‌های سبک)

پیش‌فرض `normalized` است چون معنای واقعی «تکراری» همین است.
"""
from __future__ import annotations

import hashlib
import re
import unicodedata

from telkap.services.transform import EMOJI_RE, HASHTAG_RE, MENTION_RE, URL_RE

MODE_EXACT = "exact"
MODE_NORMALIZED = "normalized"
MODE_SIMILAR = "similar"

MODES = (MODE_EXACT, MODE_NORMALIZED, MODE_SIMILAR)

MODE_LABELS = {
    MODE_EXACT: "دقیق (مو‌به‌مو یکی باشد)",
    MODE_NORMALIZED: "هم‌ارز (بدون امضا و ایموجی)",
    MODE_SIMILAR: "شبیه (درصدی از شباهت)",
}

# متن نرمال‌شده‌ی کوتاه‌تر از این قابل اتکا نیست: «🔥» و «🔥🔥» هر دو خالی
# می‌شوند و اگر مبنا قرار بگیرند، پست‌های بی‌ربط تکراری اعلام می‌شوند.
MIN_NORMALIZED_LEN = 12

# چند پست اخیرِ هر کانال برای مقایسه‌ی شباهت خوانده می‌شود
SIMILAR_WINDOW = 300

# نگاشت حروف عربی به فارسی + ارقام به لاتین
_TRANSLATE = str.maketrans(
    {
        "ي": "ی", "ك": "ک", "ة": "ه", "أ": "ا", "إ": "ا", "آ": "ا", "ٱ": "ا",
        "ؤ": "و", "ئ": "ی",
        "‌": " ", "‍": " ", "‎": " ", "‏": " ",
        "ـ": "",   # کشیدگی
        "٠": "0", "١": "1", "٢": "2", "٣": "3", "٤": "4",
        "٥": "5", "٦": "6", "٧": "7", "٨": "8", "٩": "9",
        "۰": "0", "۱": "1", "۲": "2", "۳": "3", "۴": "4",
        "۵": "5", "۶": "6", "۷": "7", "۸": "8", "۹": "9",
    }
)

# اعراب و علائم ترکیبی عربی
_MARKS_RE = re.compile(r"[ً-ْٰٓ-ٕ]")
_PUNCT_RE = re.compile(r"[^\w\s]", re.UNICODE)
_SPACE_RE = re.compile(r"\s+")


def normalize(text: str) -> str:
    """متن را به شکل قابل مقایسه درمی‌آورد.

    امضای کانال، ایموجی، لینک و هشتگ حذف می‌شوند چون دقیقاً همان‌هایند
    که بین دو نسخه‌ی یک خبر فرق می‌کنند.
    """
    if not text:
        return ""
    out = unicodedata.normalize("NFKC", text)
    out = URL_RE.sub(" ", out)
    out = MENTION_RE.sub(" ", out)
    out = HASHTAG_RE.sub(" ", out)
    out = EMOJI_RE.sub(" ", out)
    out = out.translate(_TRANSLATE)
    out = _MARKS_RE.sub("", out)
    out = _PUNCT_RE.sub(" ", out)
    return _SPACE_RE.sub(" ", out).strip().lower()


def _digest(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]


def normalized_hash(media_kind: str, text: str) -> str:
    """اثر انگشت هم‌ارز. اگر متن معناداری نماند، به متن خام برمی‌گردیم."""
    canonical = normalize(text)
    if len(canonical) < MIN_NORMALIZED_LEN:
        return _digest(f"{media_kind}|{(text or '').strip()}")
    return _digest(f"{media_kind}|{canonical}")


# ------------------------------------------------------------- شباهت
def _tokens(text: str) -> list[str]:
    return [word for word in normalize(text).split() if len(word) > 1]


def simhash(text: str) -> int:
    """اثر انگشت ۶۴ بیتی که با تغییر کوچکِ متن، کمی تغییر می‌کند.

    برخلاف هش معمولی که با یک حرف کاملاً عوض می‌شود، اینجا فاصله‌ی دو
    اثر انگشت می‌گوید دو متن چقدر به هم نزدیک‌اند.
    """
    tokens = _tokens(text)
    if not tokens:
        return 0
    vector = [0] * 64
    for token in tokens:
        value = int.from_bytes(
            hashlib.blake2b(token.encode("utf-8"), digest_size=8).digest(), "big"
        )
        for bit in range(64):
            vector[bit] += 1 if value >> bit & 1 else -1
    result = 0
    for bit in range(64):
        if vector[bit] > 0:
            result |= 1 << bit
    # عدد ۶۴ بیتیِ بی‌علامت در ستون INTEGER دیتابیس جا نمی‌شود (آنجا
    # علامت‌دار است)، پس همین‌جا علامت‌دار می‌شود. `distance` با ماسک
    # کار می‌کند و فرقی نمی‌کند.
    return result - (1 << 64) if result >= (1 << 63) else result


_MASK64 = (1 << 64) - 1


def distance(left: int, right: int) -> int:
    """تعداد بیت‌های متفاوت بین دو اثر انگشت."""
    return bin((left ^ right) & _MASK64).count("1")


def max_distance(percent: int) -> int:
    """درصد شباهتِ خواسته‌شده را به بیشینه‌ی فاصله‌ی مجاز تبدیل می‌کند."""
    clean = max(50, min(int(percent or 0), 100))
    return round(64 * (100 - clean) / 100)


def looks_similar(left: int, right: int, percent: int = 80) -> bool:
    if not left or not right:
        return False
    return distance(left, right) <= max_distance(percent)


def mode_of(cfg: dict) -> str:
    value = (cfg.get("duplicate_mode") or MODE_NORMALIZED).strip()
    return value if value in MODES else MODE_NORMALIZED
