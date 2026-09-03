"""بخش تعرفه‌ی صفحه‌ی فروش را از روی plans.py می‌سازد.

    python tools/site.py            # بازنویسی site/index.html
    python tools/site.py --check    # فقط می‌گوید کهنه است یا نه

<b>چرا تولید می‌شود و دستی نوشته نمی‌شود.</b> قیمت‌ها و سهمیه‌ها در
<code>telkap/plans.py</code> زندگی می‌کنند و عوض می‌شوند. تا امروز
صفحه‌ی فروش نسخه‌ی دستیِ خودش را داشت، و یک بار که قیمت‌ها عوض شد
صفحه سرِ جای قدیمش ماند — یعنی به مشتری عددی نشان داده می‌شد که در
ربات چیز دیگری بود. حالا یک منبع بیشتر نیست.

CI با <code>--check</code> اجرا می‌شود، پس عوض کردن قیمت بدون
بازسازی صفحه، همان‌جا گیر می‌افتد.
"""
from __future__ import annotations

import sys
from html import escape
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from telkap import plans as plan_mod  # noqa: E402

PAGE = ROOT / "site" / "index.html"
START = "<!-- تعرفه: ساخته‌ی tools/site.py — دستی عوضش نکنید -->"
END = "<!-- پایان تعرفه -->"

CHECK = (
    '<svg class="check" viewBox="0 0 16 16" fill="none" stroke="currentColor" '
    'stroke-width="2"><path d="M3 8.5l3.5 3.5L13 5"/></svg>'
)
DASH = '<span class="dash">—</span>'

BOT = "https://t.me/AdvancedForwardBot"


def _fa(value: int) -> str:
    return plan_mod._fa(value)


def _rows(plan) -> list[str]:
    """پنج سطرِ مهمِ هر طرح — همان‌هایی که تصمیم را عوض می‌کنند."""
    out = [
        (True, f"<b>{plan.messages_label}</b> پیام"),
        (True, f"<b>{_fa(plan.max_tasks)}</b> کار کپی"),
        (True, f"تا <b>{_fa(plan.max_destinations)}</b> مقصد"),
        (
            plan.has(plan_mod.FEAT_PRIVATE),
            "کانال خصوصی",
        ),
        (
            plan.watermark_quota != 0,
            f"<b>{plan.watermark_label}</b> واترمارک"
            if plan.watermark_quota > 0
            else "واترمارک",
        ),
    ]
    return [
        f"<li>{CHECK if on else DASH}<span>{text}</span></li>" for on, text in out
    ]


def _card(plan, *, featured: bool = False, free: bool = False) -> str:
    price = (
        '<div class="price"><b>رایگان</b><span>'
        f"{_fa(plan.days)} روز</span></div>"
        if free
        else f'<div class="price"><b>{_fa(plan.price_toman)}</b><span>تومان</span></div>'
    )
    star = '<div class="badge">⭐ پیشنهاد ما</div>' if featured else ""
    return (
        f'<div class="plan{" pick" if featured else ""}">'
        f"{star}"
        f"<h3>{escape(plan.title)}</h3>"
        f'<p class="tagline">{escape(plan.tagline)}</p>'
        f"{price}"
        f'<ul>{"".join(_rows(plan))}</ul>'
        f'<a class="btn{"" if featured else " ghost"}" href="{BOT}">'
        f'{"شروع کنید" if free else "خرید"}</a>'
        "</div>"
    )


def _monthly(plan) -> str:
    months = max(1, round(plan.days / 30))
    return _fa(plan.price_toman // months)


def build() -> str:
    short = [plan_mod.TRIAL, *plan_mod.purchasable()]
    long_term = plan_mod.long_term()
    popular = plan_mod.POPULAR_CODE

    cards = [
        _card(plan, free=plan.price_toman <= 0, featured=plan.code == popular)
        for plan in short
    ]

    rows = []
    for plan in long_term:
        star = " ⭐" if plan.code == popular else ""
        rows.append(
            "<tr>"
            f"<td>{escape(plan.title)}{star}</td>"
            f"<td>{_fa(plan.days)} روز</td>"
            f"<td><b>{_fa(plan.price_toman)}</b> تومان</td>"
            f"<td>ماهی {_monthly(plan)} تومان</td>"
            "</tr>"
        )

    return "\n".join([
        START,
        '      <div class="plans">',
        *[f"        {card}" for card in cards],
        "      </div>",
        "",
        '      <div class="section-head" style="margin-top:56px">',
        "        <h3>اشتراک بلندمدت</h3>",
        "        <p>هرچه دوره بلندتر، ماهانه ارزان‌تر. سهمیه‌ها هم به همان نسبت "
        "بزرگ‌تر می‌شوند — یعنی طرح یک‌ساله واقعاً دوازده برابر طرح ماهانه "
        "ظرفیت دارد، نه فقط دوازده برابر مدت.</p>",
        "      </div>",
        '      <div class="longterm">',
        "        <table>",
        "          <thead><tr><th>طرح</th><th>مدت</th><th>قیمت</th>"
        "<th>معادل ماهانه</th></tr></thead>",
        "          <tbody>",
        *[f"            {row}" for row in rows],
        "          </tbody>",
        "        </table>",
        "      </div>",
        END,
    ])


def main() -> int:
    text = PAGE.read_text(encoding="utf-8")
    if START not in text or END not in text:
        print("نشانه‌های بخش تعرفه در site/index.html نیست.", file=sys.stderr)
        return 2

    head, rest = text.split(START, 1)
    _old, tail = rest.split(END, 1)
    fresh = head + build() + tail

    if "--check" in sys.argv:
        if fresh != text:
            print(
                "بخش تعرفه‌ی site/index.html با telkap/plans.py نمی‌خواند.\n"
                "اجرا کنید:  python tools/site.py",
                file=sys.stderr,
            )
            return 1
        print("✓ تعرفه به‌روز است")
        return 0

    PAGE.write_text(fresh, encoding="utf-8")
    print(f"✓ {PAGE}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
