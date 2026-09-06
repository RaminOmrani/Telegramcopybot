"""تصویر پروفایل ربات را می‌سازد.

<b>چرا کد و نه یک فایل آماده.</b> آواتار در چند جا با چند اندازه لازم
می‌شود — پروفایل ربات، صفحه‌ی فروش، فاوآیکون — و هر بار دست‌کاری
دستی یعنی سه نسخه‌ی کمی متفاوت. اینجا یک منبع هست و بقیه از رویش
ساخته می‌شوند.

<b>و چرا این‌قدر ساده.</b> تلگرام آواتار را در فهرست گفت‌وگوها حدود
۶۴ پیکسل نشان می‌دهد و گرد می‌بُرد. هر جزئیاتی ریزتر از آن، در
همان‌جایی که بیشترین دیده‌شدن را دارد گِل می‌شود. پس یک نشانِ درشت،
پُر، و وسط — با حاشیه‌ای که برشِ دایره‌ای چیزی از آن نبُرد.

اجرا:
    python tools/avatar.py            # هر سه طرح در site/brand/
"""
from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw

SIZE = 512
# چهار برابر می‌کشیم و کوچک می‌کنیم: لبه‌ها نرم می‌شوند بی‌آنکه به
# کتابخانه‌ی برداری نیاز باشد
SCALE = 4

OUT = Path(__file__).resolve().parent.parent / "site" / "brand"


def _gradient(size: int, top: tuple[int, int, int], bottom: tuple[int, int, int]):
    """گرادیان قطری — از بالا-راست به پایین-چپ."""
    base = Image.new("RGB", (size, size), top)
    draw = ImageDraw.Draw(base)
    for step in range(size * 2):
        ratio = step / (size * 2 - 1)
        color = tuple(
            round(top[i] + (bottom[i] - top[i]) * ratio) for i in range(3)
        )
        draw.line([(step, 0), (0, step)], fill=color, width=2)
    return base


def _chevron(draw, cx: int, cy: int, height: int, thickness: int, color) -> None:
    """یک «>» ضخیم با نوکِ تیز، دور محورِ (cx, cy)."""
    half = height // 2
    width = round(half * 0.72)
    draw.polygon(
        [
            (cx - width, cy - half),
            (cx - width + thickness, cy - half),
            (cx + width, cy),
            (cx - width + thickness, cy + half),
            (cx - width, cy + half),
            (cx + width - thickness, cy),
        ],
        fill=color,
    )


def double_chevron(top, bottom, mark) -> Image.Image:
    """دو پیکان پشت هم: «فوروارد»، در ساده‌ترین شکلی که هست."""
    size = SIZE * SCALE
    image = _gradient(size, top, bottom).convert("RGBA")
    draw = ImageDraw.Draw(image)

    height = round(size * 0.44)
    thickness = round(size * 0.085)
    gap = round(size * 0.15)
    # کمی به چپ، چون نوکِ پیکان وزنِ دیداری را به راست می‌برد
    cx = size // 2 - round(size * 0.03)
    _chevron(draw, cx - gap // 2, size // 2, height, thickness, mark)
    _chevron(draw, cx + gap // 2, size // 2, height, thickness, mark)
    return image.resize((SIZE, SIZE), Image.LANCZOS)


def chevron_with_trail(top, bottom, mark, dot) -> Image.Image:
    """یک پیکان با ردِ حرکت — همان معنی، با کمی حرکت."""
    size = SIZE * SCALE
    image = _gradient(size, top, bottom).convert("RGBA")
    draw = ImageDraw.Draw(image)

    height = round(size * 0.46)
    thickness = round(size * 0.09)
    cx = size // 2 + round(size * 0.09)
    _chevron(draw, cx, size // 2, height, thickness, mark)

    # سه نقطه‌ی کوچک‌شونده پشت پیکان
    radius = round(size * 0.043)
    for index in range(3):
        x = cx - round(size * 0.20) - index * round(size * 0.115)
        r = radius - index * round(radius * 0.22)
        draw.ellipse(
            [(x - r, size // 2 - r), (x + r, size // 2 + r)], fill=dot
        )
    return image.resize((SIZE, SIZE), Image.LANCZOS)


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    white = (255, 255, 255, 255)

    designs = {
        # آبیِ تلگرام: کنارِ بقیه‌ی گفت‌وگوها طبیعی می‌نشیند
        "avatar-blue.png": double_chevron((59, 130, 246), (29, 78, 216), white),
        # سرمه‌ای با پیکانِ روشن: همان پالتِ تیره‌ی پنل و مینی‌اپ
        "avatar-dark.png": chevron_with_trail(
            (30, 41, 59), (11, 14, 20), (52, 211, 153, 255), (52, 211, 153, 150)
        ),
        # بنفشِ روشن: در فهرستِ پر از آبی، جدا دیده می‌شود
        "avatar-violet.png": chevron_with_trail(
            (139, 92, 246), (76, 29, 149), white, (255, 255, 255, 130)
        ),
    }

    for name, image in designs.items():
        image.save(OUT / name, "PNG")
        print(f"✓ site/brand/{name}")


if __name__ == "__main__":
    main()
