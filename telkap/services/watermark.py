"""درج واترمارک متنی روی تصاویر."""
from __future__ import annotations

import logging
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
    "/Library/Fonts/Arial.ttf",
)


def _load_font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    for path in _FONT_CANDIDATES:
        if Path(path).exists():
            try:
                return ImageFont.truetype(path, size)
            except OSError:
                continue
    return ImageFont.load_default()


def _anchor(position: str, img_w: int, img_h: int, tw: int, th: int, pad: int) -> tuple[int, int]:
    mapping = {
        "top-left": (pad, pad),
        "top-right": (img_w - tw - pad, pad),
        "bottom-left": (pad, img_h - th - pad),
        "bottom-right": (img_w - tw - pad, img_h - th - pad),
        "center": ((img_w - tw) // 2, (img_h - th) // 2),
    }
    return mapping.get(position, mapping["bottom-right"])


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

    در صورت هر خطایی، مسیر فایل اصلی برگردانده می‌شود تا کپی متوقف نشود.
    """
    if not text.strip():
        return src
    try:
        with Image.open(src) as base:
            base = base.convert("RGBA")
            width, height = base.size

            font_size = max(12, int(width * max(1, min(size_percent, 20)) / 100))
            font = _load_font(font_size)

            layer = Image.new("RGBA", base.size, (255, 255, 255, 0))
            draw = ImageDraw.Draw(layer)
            box = draw.textbbox((0, 0), text, font=font)
            tw, th = box[2] - box[0], box[3] - box[1]

            pad = max(8, font_size // 2)
            x, y = _anchor(position, width, height, tw, th, pad)
            alpha = int(max(0, min(opacity, 100)) * 255 / 100)

            # سایه‌ی نازک برای خوانایی روی پس‌زمینه‌ی روشن
            draw.text((x + 2, y + 2), text, font=font, fill=(0, 0, 0, int(alpha * 0.6)))
            draw.text((x, y), text, font=font, fill=(255, 255, 255, alpha))

            out = Image.alpha_composite(base, layer).convert("RGB")
            dest.parent.mkdir(parents=True, exist_ok=True)
            out.save(dest, format="JPEG", quality=92)
            return dest
    except Exception:
        log.exception("درج واترمارک ناموفق بود؛ تصویر اصلی ارسال می‌شود")
        return src
