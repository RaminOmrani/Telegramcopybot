"""پیش‌نمایش پنل وب، بدون بالا آوردن ربات و دیتابیس.

    python tools/preview.py [پوشه‌ی خروجی]

چند صفحه‌ی نمونه‌ی پنل را با داده‌ی ساختگی می‌سازد و به‌صورت فایل
HTML مستقل می‌نویسد تا بشود همان‌جا روی کامپیوتر بازشان کرد و
طراحی را دید.

<b>چرا فونت داخل فایل جاسازی می‌شود.</b> پنل واقعی فونت را از
<code>/static/fonts/</code> می‌گیرد؛ فایلی که با دوبار کلیک روی
دسکتاپ باز شود چنین مسیری ندارد و فونت نمی‌آید — یعنی دقیقاً همان
چیزی که می‌خواهیم نشان بدهیم دیده نمی‌شود. پس فقط برای پیش‌نمایش،
فونت‌ها به‌صورت data: داخل خودِ فایل می‌روند.
"""
from __future__ import annotations

import base64
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from telkap.web import render  # noqa: E402
from telkap.web.render import (  # noqa: E402
    card,
    chart,
    form,
    money,
    panel,
    pill,
    table,
)

FONT_DIR = ROOT / "telkap" / "web" / "static" / "fonts"
CSRF = "نمونه"


def _standalone(html: str) -> str:
    """نشانی فونت‌ها را با خودِ فونت جایگزین می‌کند."""

    def swap(match: re.Match) -> str:
        name = match.group(1)
        path = FONT_DIR / name
        if not path.exists():
            return match.group(0)
        blob = base64.b64encode(path.read_bytes()).decode()
        return f"url('data:font/woff2;base64,{blob}')"

    return re.sub(r"url\('[^']*/static/fonts/([^']+)'\)", swap, html)


def _dashboard() -> str:
    return (
        "<h1>نمای کلی</h1>"
        "<p class='sub'>آنچه همین حالا منتظر شماست، بالاتر از آمار.</p>"
        "<div class='cards'>"
        + card("رسید منتظر", "۳", "warn", href="/payments")
        + card("اشتراک فعال", "۴۸", "ok")
        + card("درآمد این ماه", money(18_640_000), "ok")
        + card("کار کپی روشن", "۱۳۷")
        + "</div>"
        + panel(
            "درآمد هفت روز گذشته",
            chart(
                [
                    ("شنبه", 1_290_000),
                    ("یکشنبه", 2_580_000),
                    ("دوشنبه", 990_000),
                    ("سه‌شنبه", 3_440_000),
                    ("چهارشنبه", 2_150_000),
                    ("پنجشنبه", 4_290_000),
                    ("جمعه", 3_900_000),
                ],
                unit="تومان",
            ),
        )
        + panel(
            "تازه‌ترین رسیدها",
            table(
                ["کاربر", "مبلغ", "روش", "وضعیت", ""],
                [
                    "<tr><td>رامین<div class='mini'>۷۷۳۱۲۴۵۵</div></td>"
                    f"<td class='money'>{money(429_000)}</td><td>کارت به کارت</td>"
                    f"<td>{pill('منتظر', 'warn')}</td>"
                    "<td class='actions'><a class='btn small' href='#'>بررسی</a></td></tr>",
                    "<tr><td>سارا<div class='mini'>۹۱۴۵۵۰۲۱</div></td>"
                    f"<td class='money'>{money(1_149_000)}</td><td>زرین‌پال</td>"
                    f"<td>{pill('تأیید شد', 'ok')}</td>"
                    "<td class='actions'><a class='btn small' href='#'>بررسی</a></td></tr>",
                    "<tr><td>مهدی<div class='mini'>۲۲۹۸۷۴۱۰</div></td>"
                    f"<td class='money'>{money(229_000)}</td><td>تتر TRC20</td>"
                    f"<td>{pill('رد شد', 'bad')}</td>"
                    "<td class='actions'><a class='btn small' href='#'>بررسی</a></td></tr>",
                ],
            ),
        )
    )


def _resellers() -> str:
    row = (
        "<tr><td><a href='#'>سارا</a><div class='mini'>۹۱۴۵۵۰۲۱</div></td>"
        f"<td>{pill('۲۵٪', 'ok')}</td>"
        "<td class='money'>۱۲</td><td class='money'>۹</td>"
        f"<td class='money'>{money(4_380_000)}</td>"
        f"<td class='money'>{money(620_000)}</td>"
        "<td class='actions'>"
        + form(
            "#",
            CSRF,
            "<input type='number' value='25' style='width:5rem'>"
            "<button class='btn small'>ذخیره</button>",
        )
        + form("#", CSRF, "<button class='btn small bad'>حذف نمایندگی</button>")
        + "</td></tr>"
    )
    row2 = (
        "<tr><td><a href='#'>مهدی</a><div class='mini'>۲۲۹۸۷۴۱۰</div></td>"
        f"<td>{pill('۱۵٪', 'ok')}</td>"
        "<td class='money'>۳</td><td class='money'>۳</td>"
        f"<td class='money'>{money(1_090_000)}</td>"
        f"<td class='money'>{money(0)}</td>"
        "<td class='actions'>"
        + form(
            "#",
            CSRF,
            "<input type='number' value='15' style='width:5rem'>"
            "<button class='btn small'>ذخیره</button>",
        )
        + form("#", CSRF, "<button class='btn small bad'>حذف نمایندگی</button>")
        + "</td></tr>"
    )
    return (
        "<h1>نمایندگی</h1>"
        "<p class='sub'>نماینده کیف پولش را شارژ می‌کند و بعد هر وقت خواست، "
        "اشتراک را با درصد تخفیف خودش مستقیم برای مشتری‌اش فعال می‌کند — "
        "بدون رسید و بدون انتظار تأیید.</p>"
        "<div class='flash ok'>نمایندگی با ۲۵٪ تخفیف ثبت شد.</div>"
        "<div class='cards'>"
        + card("نماینده‌ها", "۲")
        + card("فروش نمایندگی", "۱۵")
        + card("گردش نمایندگی", money(5_470_000), "ok")
        + card("تخفیف پیش‌فرض", "۲۰٪")
        + "</div>"
        + panel(
            "نماینده‌ی تازه",
            form(
                "#",
                CSRF,
                "<input type='text' placeholder='آیدی عددی کاربر'> "
                "<input type='number' value='20' style='width:6rem'> "
                "<button class='btn'>نماینده کن</button>",
            ),
            sub="کاربر باید یک‌بار ربات را استارت کرده باشد تا آیدی‌اش شناخته شود.",
        )
        + panel(
            "نماینده‌ها",
            table(
                ["نماینده", "تخفیف", "فروش", "مشتری", "پرداختی", "کیف پول", ""],
                [row, row2],
            ),
        )
        + panel(
            "آخرین فروش‌ها",
            table(
                ["زمان", "نماینده", "مشتری", "طرح", "پرداختی", "قیمت فهرست"],
                [
                    "<tr><td>۱۴۰۴/۰۶/۱۱ ۱۴:۲۰</td><td><a href='#'>سارا</a></td>"
                    "<td><a href='#'>نیما</a></td><td>سه ماهه</td>"
                    f"<td class='money'>{money(861_000)}</td>"
                    f"<td class='money'>{money(1_149_000)}</td></tr>",
                    "<tr><td>۱۴۰۴/۰۶/۱۰ ۰۹:۰۵</td><td><a href='#'>مهدی</a></td>"
                    "<td><a href='#'>پویا</a></td><td>یک ماهه</td>"
                    f"<td class='money'>{money(364_000)}</td>"
                    f"<td class='money'>{money(429_000)}</td></tr>",
                ],
            ),
        )
        + panel(
            "تخفیف پیش‌فرض",
            form(
                "#",
                CSRF,
                "<input type='number' value='20' style='width:6rem'> "
                "<button class='btn'>ذخیره</button>",
            ),
            sub="درصدی که به نماینده‌ی تازه داده می‌شود، اگر جداگانه چیزی نگذارید.",
        )
    )


PAGES: dict[str, tuple[str, str, str]] = {
    "dashboard.html": ("نمای کلی", "/", _dashboard()),
    "resellers.html": ("نمایندگی", "/resellers", _resellers()),
}


def main() -> int:
    out = Path(sys.argv[1] if len(sys.argv) > 1 else ROOT / "data" / "preview")
    out.mkdir(parents=True, exist_ok=True)

    # هر صفحه در هر دو تم، چون تم یک انتخاب است نه یک پیش‌فرضِ ثابت و
    # هر دو باید دیده شوند.
    for theme in render.THEMES:
        for name, (title, active, body) in PAGES.items():
            html = render.page(
                title, body, active=active, who="۷۷۳۱۲۴۵۵", waiting=3,
                theme=theme, path=active,
            )
            path = out / f"{Path(name).stem}-{theme}.html"
            path.write_text(_standalone(html), encoding="utf-8")
            print(f"✓ {path}")

        path = out / f"login-{theme}.html"
        path.write_text(_standalone(render.login(theme=theme)), encoding="utf-8")
        print(f"✓ {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
