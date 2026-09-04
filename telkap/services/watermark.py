"""درج واترمارک روی تصاویر — متن، ایموجی رنگی، یا لوگو.

هر سه حالت به یک «لایه‌ی نشان» (یک تصویر RGBA شفاف) تبدیل می‌شوند و بعد
یک‌جور روی عکس می‌نشینند؛ پس موقعیت، اندازه و شفافیت برای همه یکسان کار
می‌کند و اضافه کردن حالت تازه فقط یک سازنده‌ی لایه می‌خواهد.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

log = logging.getLogger(__name__)

POSITIONS = {
    "top-left": "بالا چپ",
    "top-right": "بالا راست",
    "bottom-left": "پایین چپ",
    "bottom-right": "پایین راست",
    "center": "وسط",
}

_FONT_CANDIDATES = (
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/TTF/DejaVuSans.ttf",
    "C:/Windows/Fonts/arialbd.ttf",
    "C:/Windows/Fonts/tahomabd.ttf",
    "/Library/Fonts/Arial.ttf",
)

# فونت‌های ایموجی رنگی. Segoe (ویندوز) هر اندازه‌ای را می‌پذیرد ولی
# NotoColorEmoji فقط در اندازه‌ی ثابت خودش رندر می‌شود؛ در آن حالت بزرگ
# کشیده و بعد کوچک می‌شود.
_EMOJI_FONT_CANDIDATES = (
    "C:/Windows/Fonts/seguiemj.ttf",
    "/usr/share/fonts/truetype/noto/NotoColorEmoji.ttf",
    "/System/Library/Fonts/Apple Color Emoji.ttc",
)
_EMOJI_FIXED_SIZES = (109, 128, 137, 160)

# نویسه‌هایی که باید با فونت ایموجی کشیده شوند
_EMOJI_RANGES = (
    (0x1F000, 0x1FAFF),
    (0x2600, 0x27BF),
    (0x2B00, 0x2BFF),
    (0x1F1E6, 0x1F1FF),      # پرچم‌ها
    (0xFE00, 0xFE0F),        # واریانت سلکتور
    (0x200D, 0x200D),        # ZWJ، برای ایموجی‌های ترکیبی
    (0x20E3, 0x20E3),        # کی‌کپ
    (0x2190, 0x21FF),
    (0x2B50, 0x2B50),
)


@dataclass(frozen=True, slots=True)
class Preset:
    """یک حالت آماده‌ی چیدمان برای پیش‌نمایش."""

    key: str
    label: str
    position: str
    text_size: int          # درصد عرض، برای واترمارک متنی
    logo_size: int          # درصد عرض، برای لوگو
    opacity: int

    def size_for(self, kind: str) -> int:
        return self.logo_size if kind == "logo" else self.text_size


# شش حالتی که در پیش‌نمایش به کاربر نشان داده می‌شود
PRESETS: tuple[Preset, ...] = (
    Preset("br", "پایین راست · کوچک", "bottom-right", 4, 15, 70),
    Preset("bl", "پایین چپ · کوچک", "bottom-left", 4, 15, 70),
    Preset("tr", "بالا راست · کوچک", "top-right", 4, 15, 70),
    Preset("tl", "بالا چپ · کوچک", "top-left", 4, 15, 70),
    Preset("brb", "پایین راست · بزرگ", "bottom-right", 7, 28, 85),
    Preset("stamp", "وسط · مهر کم‌رنگ", "center", 12, 45, 25),
)

PRESET_BY_KEY = {preset.key: preset for preset in PRESETS}


def _is_emoji(char: str) -> bool:
    point = ord(char)
    return any(low <= point <= high for low, high in _EMOJI_RANGES)


def _runs(text: str) -> list[tuple[bool, str]]:
    """متن را به تکه‌های «ایموجی» و «غیرایموجی» می‌شکند.

    نویسه‌های ترکیبی (ZWJ و واریانت سلکتور) داخل همان تکه‌ی ایموجی
    می‌مانند تا خانواده‌ها و پرچم‌ها یک گلیف واحد کشیده شوند.
    """
    result: list[tuple[bool, str]] = []
    for char in text:
        emoji = _is_emoji(char)
        if result and result[-1][0] == emoji:
            result[-1] = (emoji, result[-1][1] + char)
        else:
            result.append((emoji, char))
    return result


def _load_font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    for path in _FONT_CANDIDATES:
        if Path(path).exists():
            try:
                return ImageFont.truetype(path, size)
            except OSError:
                continue
    return ImageFont.load_default()


def _load_emoji_font(size: int):
    """فونت ایموجی و اندازه‌ای که واقعاً پذیرفت.

    خروجی (font, actual_size) است؛ اگر actual_size با size فرق داشته باشد
    باید تصویرِ کشیده‌شده بعداً مقیاس بخورد.
    """
    for path in _EMOJI_FONT_CANDIDATES:
        if not Path(path).exists():
            continue
        try:
            return ImageFont.truetype(path, size), size
        except OSError:
            pass
        for fixed in _EMOJI_FIXED_SIZES:
            try:
                return ImageFont.truetype(path, fixed), fixed
            except OSError:
                continue
    return None, 0


def _draw_run(text: str, font, *, color, shadow: bool) -> Image.Image | None:
    """یک تکه متن را روی تصویر شفافِ اندازه‌ی خودش می‌کشد."""
    probe = ImageDraw.Draw(Image.new("RGBA", (1, 1)))
    try:
        box = probe.textbbox((0, 0), text, font=font)
    except Exception:
        return None
    width, height = box[2] - box[0], box[3] - box[1]
    if width <= 0 or height <= 0:
        return None

    pad = 4 if shadow else 0
    tile = Image.new("RGBA", (width + pad + 2, height + pad + 2), (255, 255, 255, 0))
    draw = ImageDraw.Draw(tile)
    origin = (-box[0], -box[1])
    if shadow:
        # سایه‌ی نازک تا روی پس‌زمینه‌ی روشن هم خوانا بماند
        draw.text((origin[0] + 2, origin[1] + 2), text, font=font, fill=(0, 0, 0, 160))
    draw.text(origin, text, font=font, fill=color)
    return tile.crop(tile.getbbox() or (0, 0, tile.width, tile.height))


def _draw_emoji_run(text: str, size: int) -> Image.Image | None:
    """تکه‌ی ایموجی را رنگی می‌کشد و در صورت لزوم به اندازه می‌رساند."""
    font, actual = _load_emoji_font(size)
    if font is None:
        return None
    probe = ImageDraw.Draw(Image.new("RGBA", (1, 1)))
    try:
        box = probe.textbbox((0, 0), text, font=font, embedded_color=True)
    except Exception:
        return None
    width, height = box[2] - box[0], box[3] - box[1]
    if width <= 0 or height <= 0:
        return None

    tile = Image.new("RGBA", (width + 2, height + 2), (255, 255, 255, 0))
    draw = ImageDraw.Draw(tile)
    try:
        draw.text((-box[0], -box[1]), text, font=font, embedded_color=True)
    except Exception:
        return None
    tile = tile.crop(tile.getbbox() or (0, 0, tile.width, tile.height))

    if actual != size and tile.height:
        # فونت اندازه‌ی دلخواه را نپذیرفت؛ بزرگ کشیده شد و حالا کوچک می‌شود
        scale = size / tile.height
        new_size = (max(1, int(tile.width * scale)), max(1, int(tile.height * scale)))
        tile = tile.resize(new_size, Image.LANCZOS)
    return tile


def render_text_mark(text: str, font_size: int) -> Image.Image | None:
    """متن (با ایموجی رنگی) را به یک لایه‌ی شفاف تبدیل می‌کند."""
    text = (text or "").strip()
    if not text:
        return None

    font = _load_font(font_size)
    tiles: list[Image.Image] = []
    for emoji, chunk in _runs(text):
        if emoji:
            tile = _draw_emoji_run(chunk, font_size)
            if tile is None:
                # فونت ایموجی نبود؛ همان نویسه با فونت معمولی کشیده می‌شود
                tile = _draw_run(chunk, font, color=(255, 255, 255, 255), shadow=True)
        else:
            tile = _draw_run(chunk, font, color=(255, 255, 255, 255), shadow=True)
        if tile is not None:
            tiles.append(tile)

    if not tiles:
        return None
    width = sum(tile.width for tile in tiles)
    height = max(tile.height for tile in tiles)
    layer = Image.new("RGBA", (width, height), (255, 255, 255, 0))
    offset = 0
    for tile in tiles:
        layer.paste(tile, (offset, (height - tile.height) // 2), tile)
        offset += tile.width
    return layer


def has_color_emoji_font() -> bool:
    """آیا روی این سیستم فونت ایموجی رنگی هست؟"""
    return any(Path(path).exists() for path in _EMOJI_FONT_CANDIDATES)


def _anchor(position: str, img_w: int, img_h: int, tw: int, th: int, pad: int) -> tuple[int, int]:
    mapping = {
        "top-left": (pad, pad),
        "top-right": (img_w - tw - pad, pad),
        "bottom-left": (pad, img_h - th - pad),
        "bottom-right": (img_w - tw - pad, img_h - th - pad),
        "center": ((img_w - tw) // 2, (img_h - th) // 2),
    }
    return mapping.get(position, mapping["bottom-right"])


def _compose(base: Image.Image, mark: Image.Image, position: str, opacity: int) -> Image.Image:
    """لایه‌ی نشان را با شفافیت خواسته‌شده روی عکس می‌نشاند."""
    factor = max(0, min(opacity, 100)) / 100
    alpha = mark.getchannel("A").point(lambda value: int(value * factor))
    mark = mark.copy()
    mark.putalpha(alpha)

    width, height = base.size
    pad = max(8, width // 60)
    x, y = _anchor(position, width, height, mark.width, mark.height, pad)

    layer = Image.new("RGBA", base.size, (255, 255, 255, 0))
    layer.paste(mark, (x, y), mark)
    return Image.alpha_composite(base, layer)


def _fit_logo(mark: Image.Image, width: int, height: int, size_percent: int) -> Image.Image:
    """لوگو را به نسبت عرض عکس بزرگ یا کوچک می‌کند."""
    target_w = max(24, int(width * max(3, min(size_percent, 60)) / 100))
    ratio = target_w / mark.width
    target_h = max(24, int(mark.height * ratio))
    if target_h > height // 2:          # روی عکس‌های کشیده جا بماند
        target_h = max(24, height // 2)
        target_w = max(24, int(mark.width * target_h / mark.height))
    return mark.resize((target_w, target_h), Image.LANCZOS)


def _save(image: Image.Image, dest: Path) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    image.convert("RGB").save(dest, format="JPEG", quality=92)
    return dest


# ------------------------------------------------------------- حالت‌ها
def watermark_ready(cfg: dict) -> bool:
    """آیا واترمارک برای این کار پیکربندی کامل دارد؟

    حالت متنی به متن نیاز دارد و حالت لوگو به فایل لوگوی موجود.
    """
    if not cfg.get("watermark_enabled"):
        return False
    if cfg.get("watermark_kind", "text") == "logo":
        logo = cfg.get("watermark_logo") or ""
        return bool(logo) and Path(logo).exists()
    return bool((cfg.get("watermark_text") or "").strip())


def apply_watermark(src: Path, dest: Path, cfg: dict) -> Path:
    """بر اساس نوع انتخابی کاربر، واترمارک متنی یا لوگویی درج می‌کند."""
    position = cfg.get("watermark_position", "bottom-right")
    opacity = int(cfg.get("watermark_opacity", 60))
    size_percent = int(cfg.get("watermark_size", 4))
    if cfg.get("watermark_kind", "text") == "logo":
        return add_logo_watermark(
            src,
            dest,
            Path(cfg.get("watermark_logo") or ""),
            position=position,
            opacity=opacity,
            size_percent=size_percent or 15,
        )
    return add_text_watermark(
        src,
        dest,
        cfg.get("watermark_text", ""),
        position=position,
        opacity=opacity,
        size_percent=size_percent,
    )


def add_logo_watermark(
    src: Path,
    dest: Path,
    logo: Path,
    *,
    position: str = "bottom-right",
    opacity: int = 60,
    size_percent: int = 15,
) -> Path:
    """لوگو (تصویر) را روی عکس درج می‌کند و مسیر خروجی را برمی‌گرداند.

    لوگو به نسبت عرض تصویر بزرگ یا کوچک می‌شود و شفافیتش اعمال می‌گردد؛
    اگر خودش پس‌زمینه‌ی شفاف داشته باشد (PNG یا استیکر) حفظ می‌شود.

    در صورت هر خطایی، مسیر فایل اصلی برگردانده می‌شود تا کپی متوقف نشود.
    """
    if not logo or not Path(logo).exists():
        return src
    try:
        with Image.open(src) as base, Image.open(logo) as raw:
            base = base.convert("RGBA")
            mark = _fit_logo(raw.convert("RGBA"), base.width, base.height, size_percent)
            return _save(_compose(base, mark, position, opacity), dest)
    except Exception:
        log.exception("درج لوگو ناموفق بود؛ تصویر اصلی ارسال می‌شود")
        return src


def add_text_watermark(
    src: Path,
    dest: Path,
    text: str,
    *,
    position: str = "bottom-right",
    opacity: int = 60,
    size_percent: int = 4,
) -> Path:
    """واترمارک متنی روی تصویر درج می‌کند و مسیر خروجی را برمی‌گرداند.

    ایموجی رنگی هم پشتیبانی می‌شود، به شرط وجود فونت ایموجی روی سیستم.
    در صورت هر خطایی، مسیر فایل اصلی برگردانده می‌شود تا کپی متوقف نشود.
    """
    if not text.strip():
        return src
    try:
        with Image.open(src) as base:
            base = base.convert("RGBA")
            font_size = max(12, int(base.width * max(1, min(size_percent, 20)) / 100))
            mark = render_text_mark(text, font_size)
            if mark is None:
                return src
            return _save(_compose(base, mark, position, opacity), dest)
    except Exception:
        log.exception("درج واترمارک ناموفق بود؛ تصویر اصلی ارسال می‌شود")
        return src


# ----------------------------------------------------------- پیش‌نمایش
def sample_photo(dest: Path) -> Path:
    """یک عکس نمونه می‌سازد، برای وقتی که پست واقعی در دسترس نیست."""
    width, height = 1000, 700
    image = Image.new("RGB", (width, height), (22, 34, 58))
    draw = ImageDraw.Draw(image)
    for row in range(0, height, 4):
        shade = row / height
        draw.line(
            [(0, row), (width, row)],
            fill=(int(22 + shade * 40), int(34 + shade * 60), int(58 + shade * 90)),
        )
    draw.ellipse([690, 70, 940, 320], fill=(240, 186, 60))
    draw.rectangle([70, 430, 540, 478], fill=(255, 255, 255))
    draw.rectangle([70, 508, 390, 546], fill=(196, 208, 226))
    draw.rectangle([70, 576, 300, 606], fill=(150, 166, 190))
    dest.parent.mkdir(parents=True, exist_ok=True)
    image.save(dest, format="JPEG", quality=92)
    return dest


def preview_variants(base: Path, out_dir: Path, cfg: dict) -> list[tuple[Preset, Path]]:
    """برای هر حالت آماده یک تصویر می‌سازد تا کاربر انتخاب کند."""
    kind = cfg.get("watermark_kind", "text")
    results: list[tuple[Preset, Path]] = []
    out_dir.mkdir(parents=True, exist_ok=True)
    for preset in PRESETS:
        variant = {
            **cfg,
            "watermark_enabled": True,
            "watermark_position": preset.position,
            "watermark_opacity": preset.opacity,
            "watermark_size": preset.size_for(kind),
        }
        target = out_dir / f"pv-{preset.key}.jpg"
        made = apply_watermark(base, target, variant)
        if Path(made) != base:
            results.append((preset, Path(made)))
    return results
