"""ساخت صفحه‌های پنل.

قالب‌ها با رشته‌ی پایتون نوشته شده‌اند نه Jinja. برای این تعداد صفحه، یک
وابستگی تازه و یک پوشه‌ی template ارزشش را ندارد.

هر مقداری که از دیتابیس می‌آید از `esc()` رد می‌شود. نام کاربر و یادداشت
رسید را خودِ کاربر نوشته و هیچ‌کدام قابل اعتماد نیستند.

<b>درباره‌ی ظاهر.</b> پنل قبلی فقط جدول‌های لخت بود. کسی که روزی چند بار
به آن سر می‌زند باید در یک نگاه بفهمد چه چیزی نیاز به کار دارد؛ برای
همین حالا رنگ و فاصله و ترتیب کار می‌کنند: کارتِ عددی برای چیزهایی که
باید ببینید، رنگ فقط جایی که معنا دارد (منتظر، مسدود، تمام‌شده)، و
دکمه‌ی خطرناک شکلِ دکمه‌ی خطرناک.
"""
from __future__ import annotations

import json
from html import escape

from telkap import i18n

CSS = """
:root {
  color-scheme: light dark;
  --bg: #f4f5f7; --card: #ffffff; --ink: #14161a; --muted: #6b7280;
  --line: #e4e6eb; --soft: #f0f2f5;
  --accent: #2f6df6; --accent-ink: #ffffff;
  --ok: #0a8f5b; --ok-bg: #e7f7f0;
  --bad: #d92d20; --bad-bg: #fdecea;
  --warn: #b45309; --warn-bg: #fdf3e3;
  --radius: 14px;
  --shadow: 0 1px 2px rgba(16,24,40,.05), 0 1px 3px rgba(16,24,40,.04);
}
@media (prefers-color-scheme: dark) {
  :root {
    --bg: #0e1014; --card: #171a20; --ink: #e9ebef; --muted: #98a0ac;
    --line: #262a33; --soft: #1d212a;
    --accent: #6d9bff; --accent-ink: #0e1014;
    --ok: #34d399; --ok-bg: #10291f;
    --bad: #f87171; --bad-bg: #2a1414;
    --warn: #fbbf24; --warn-bg: #2a2010;
    --shadow: none;
  }
}
* { box-sizing: border-box; }
html { -webkit-text-size-adjust: 100%; }
body {
  margin: 0; background: var(--bg); color: var(--ink);
  font: 15px/1.7 system-ui, "Segoe UI", Vazirmatn, Tahoma, sans-serif;
  direction: rtl;
}
a { color: var(--accent); text-decoration: none; }
a:hover { text-decoration: underline; }

/* ── سربرگ ─────────────────────────────────────────────────────── */
header {
  background: var(--card); border-bottom: 1px solid var(--line);
  padding: 0 22px; display: flex; align-items: center; gap: 6px;
  flex-wrap: wrap; position: sticky; top: 0; z-index: 5;
}
header .brand {
  font-weight: 700; padding: 15px 0; margin-left: 20px;
  display: flex; align-items: center; gap: 8px; white-space: nowrap;
}
header nav { display: flex; gap: 4px; flex-wrap: wrap; }
header nav a {
  padding: 9px 14px; color: var(--muted); border-radius: 9px;
  font-size: 14px; white-space: nowrap;
}
header nav a:hover { background: var(--soft); text-decoration: none; }
header nav a.on { color: var(--accent); background: var(--soft); font-weight: 600; }
header nav a .dot {
  display: inline-block; min-width: 18px; padding: 0 5px; margin-right: 5px;
  background: var(--bad); color: #fff; border-radius: 999px;
  font-size: 11px; line-height: 18px; text-align: center; font-weight: 700;
}
header .who {
  margin-right: auto; color: var(--muted); font-size: 13px;
  display: flex; align-items: center; gap: 10px;
}
main { max-width: 1180px; margin: 0 auto; padding: 26px 22px 70px; }

/* ── عنوان‌ها ───────────────────────────────────────────────────── */
h1 { font-size: 22px; margin: 0 0 4px; letter-spacing: -.2px; }
.sub { color: var(--muted); margin: 0 0 24px; font-size: 14px; }
section { margin-top: 34px; }
section > h2 {
  font-size: 15px; margin: 0 0 13px; color: var(--muted);
  font-weight: 600; letter-spacing: .2px;
}

/* ── کارت‌های عددی ──────────────────────────────────────────────── */
.cards { display: grid; grid-template-columns: repeat(auto-fit, minmax(165px, 1fr)); gap: 13px; }
.card {
  background: var(--card); border: 1px solid var(--line);
  border-radius: var(--radius); padding: 16px 18px; box-shadow: var(--shadow);
}
.card .label { color: var(--muted); font-size: 13px; }
.card .value {
  font-size: 26px; font-weight: 700; margin-top: 5px;
  font-variant-numeric: tabular-nums; letter-spacing: -.5px;
}
.card .value.ok { color: var(--ok); }
.card .value.warn { color: var(--warn); }
.card .value.bad { color: var(--bad); }
.card a.value:hover { text-decoration: none; opacity: .8; }

/* ── جدول ──────────────────────────────────────────────────────── */
.wrap {
  overflow-x: auto; border: 1px solid var(--line);
  border-radius: var(--radius); background: var(--card); box-shadow: var(--shadow);
}
table { border-collapse: collapse; width: 100%; min-width: 560px; }
th, td { padding: 12px 15px; text-align: right; border-bottom: 1px solid var(--line); }
th {
  color: var(--muted); font-weight: 600; font-size: 12.5px;
  white-space: nowrap; background: var(--soft);
}
tbody tr:hover { background: var(--soft); }
tr:last-child td { border-bottom: 0; }
td.actions { white-space: nowrap; }
.empty { padding: 44px 20px; text-align: center; color: var(--muted); }
.empty .big { font-size: 30px; display: block; margin-bottom: 8px; opacity: .5; }

/* ── دکمه ──────────────────────────────────────────────────────── */
.btn {
  display: inline-block; border: 1px solid var(--line); background: var(--card);
  color: var(--ink); border-radius: 9px; padding: 7px 14px; cursor: pointer;
  font: inherit; font-size: 14px; transition: .12s;
}
.btn:hover { border-color: var(--accent); text-decoration: none; }
.btn.primary {
  background: var(--accent); border-color: var(--accent); color: var(--accent-ink);
  font-weight: 600;
}
.btn.primary:hover { opacity: .9; }
.btn.ok { color: var(--ok); border-color: color-mix(in srgb, var(--ok) 35%, var(--line)); }
.btn.ok:hover { background: var(--ok-bg); border-color: var(--ok); }
.btn.bad { color: var(--bad); border-color: color-mix(in srgb, var(--bad) 35%, var(--line)); }
.btn.bad:hover { background: var(--bad-bg); border-color: var(--bad); }
.btn.small { padding: 4px 10px; font-size: 13px; }
.btn[disabled] { opacity: .45; cursor: not-allowed; }
form.inline { display: inline; }
.row { display: flex; gap: 9px; flex-wrap: wrap; align-items: center; }

/* ── برچسب ─────────────────────────────────────────────────────── */
.pill {
  display: inline-block; font-size: 12px; padding: 3px 10px; border-radius: 999px;
  border: 1px solid var(--line); color: var(--muted); background: var(--soft);
  white-space: nowrap;
}
.pill.ok { color: var(--ok); background: var(--ok-bg); border-color: transparent; }
.pill.bad { color: var(--bad); background: var(--bad-bg); border-color: transparent; }
.pill.warn { color: var(--warn); background: var(--warn-bg); border-color: transparent; }

/* ── پیام ──────────────────────────────────────────────────────── */
.flash {
  border: 1px solid var(--line); border-right: 3px solid var(--muted);
  border-radius: 10px; padding: 13px 16px; margin-bottom: 22px;
  background: var(--card);
}
.flash.ok { border-right-color: var(--ok); background: var(--ok-bg); color: var(--ok); }
.flash.bad { border-right-color: var(--bad); background: var(--bad-bg); color: var(--bad); }
.note {
  background: var(--soft); border-radius: 10px; padding: 13px 16px;
  color: var(--muted); font-size: 13.5px; margin: 16px 0;
}
.note.warn { background: var(--warn-bg); color: var(--warn); }

/* ── فرم ───────────────────────────────────────────────────────── */
input[type=search], input[type=text], input[type=number], select {
  background: var(--card); color: var(--ink); border: 1px solid var(--line);
  border-radius: 9px; padding: 9px 13px; font: inherit;
}
input[type=search] { min-width: 240px; }
input:focus, select:focus { outline: 2px solid var(--accent); outline-offset: -1px; }
label.field { display: block; margin-bottom: 15px; }
label.field .cap {
  display: block; font-size: 13px; color: var(--muted); margin-bottom: 6px;
}
label.field input, label.field select { width: 100%; max-width: 420px; }
label.field .hint { font-size: 12.5px; color: var(--muted); margin-top: 5px; }

/* ── چیدمان دو ستونه ────────────────────────────────────────────── */
.split { display: grid; grid-template-columns: repeat(auto-fit, minmax(310px, 1fr)); gap: 15px; }
.panel {
  background: var(--card); border: 1px solid var(--line);
  border-radius: var(--radius); padding: 20px; box-shadow: var(--shadow);
}
.panel h2 { margin: 0 0 4px; font-size: 15.5px; color: var(--ink); }
.panel .sub { margin: 0 0 16px; font-size: 13px; }
dl.facts { margin: 0; display: grid; grid-template-columns: auto 1fr; gap: 9px 16px; }
dl.facts dt { color: var(--muted); font-size: 13.5px; }
dl.facts dd { margin: 0; }

.receipt { max-height: 78px; border-radius: 7px; border: 1px solid var(--line); }
.money { white-space: nowrap; font-variant-numeric: tabular-nums; }
.mini { color: var(--muted); font-size: 12px; }
code {
  background: var(--soft); padding: 2px 7px; border-radius: 6px;
  font-size: 13px; direction: ltr; display: inline-block;
}
.gate { max-width: 440px; margin: 14vh auto; text-align: center; }
.gate .panel { padding: 32px 28px; }
"""

TABS = (
    ("/", "نمای کلی", ""),
    ("/payments", "رسیدها", "waiting"),
    ("/users", "کاربران", ""),
    ("/tasks", "کارها", ""),
    ("/settings", "پرداخت", ""),
)


def esc(value) -> str:
    return escape(str(value if value is not None else ""), quote=True)


def money(amount: int) -> str:
    return f"{i18n.num(int(amount), 'fa')} تومان"


def page(
    title: str,
    body: str,
    *,
    active: str = "",
    who: str = "",
    waiting: int = 0,
) -> str:
    """یک صفحه‌ی کامل.

    <code>waiting</code> تعداد رسیدهای منتظر است و روی خودِ تب می‌نشیند.
    کارِ روزمره‌ی این پنل همان است، و اگر برای دیدنش باید تب را باز کرد،
    گاهی باز نمی‌شود.
    """
    chips = []
    for href, label, badge in TABS:
        mark = (
            f"<span class='dot'>{esc(i18n.num(waiting, 'fa'))}</span>"
            if badge == "waiting" and waiting
            else ""
        )
        cls = "on" if href == active else ""
        chips.append(f'<a href="{esc(href)}" class="{cls}">{esc(label)}{mark}</a>')

    identity = (
        f'<span class="who">{esc(who)}<a href="/logout">خروج</a></span>' if who else ""
    )
    return (
        "<!doctype html><html lang='fa' dir='rtl'><head>"
        "<meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width, initial-scale=1'>"
        "<meta name='robots' content='noindex, nofollow'>"
        f"<title>{esc(title)} — پنل مدیریت</title>"
        f"<style>{CSS}</style></head><body>"
        "<header><span class='brand'>🤖 پنل مدیریت</span>"
        f"<nav>{''.join(chips)}</nav>{identity}</header>"
        f"<main>{body}</main></body></html>"
    )


def gate(message: str, *, bad: bool = False) -> str:
    """صفحه‌ای که به کسی نشان داده می‌شود که هنوز وارد نشده."""
    body = (
        "<div class='gate'><div class='panel'>"
        "<h1>🤖 پنل مدیریت</h1>"
        f"<p class='sub' style='margin:14px 0 0'>{esc(message)}</p>"
        "</div></div>"
    )
    return (
        "<!doctype html><html lang='fa' dir='rtl'><head>"
        "<meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width, initial-scale=1'>"
        "<meta name='robots' content='noindex, nofollow'>"
        "<title>ورود — پنل مدیریت</title>"
        f"<style>{CSS}</style></head><body><main>{body}</main></body></html>"
    )


def login(*, error: str = "", pending_key: str = "") -> str:
    """صفحه‌ی ورود — یک مرحله در هر نما.

    <b>چرا دو صفحه و نه یکی.</b> نشان دادن هم‌زمانِ رمز و کد یعنی
    کاربر نمی‌داند کدام را باید پر کند. هر بار فقط همان چیزی پرسیده
    می‌شود که الان لازم است.
    """
    warn = f"<div class='flash bad'>{esc(error)}</div>" if error else ""

    if pending_key:
        body = (
            "<h1>🔐 کد ورود</h1>"
            "<p class='sub'>کدی که در تلگرام برایتان آمد را وارد کنید.</p>"
            f"{warn}"
            "<form method='post' action='/login'>"
            f"<input type='hidden' name='key' value='{esc(pending_key)}'>"
            "<label class='field'><span class='cap'>کد شش‌رقمی</span>"
            "<input type='text' name='code' inputmode='numeric' autocomplete='one-time-code'"
            " autofocus maxlength='6' dir='ltr' style='text-align:center;"
            "font-size:22px;letter-spacing:6px'></label>"
            "<button class='btn primary' style='width:100%'>ورود</button>"
            "</form>"
            "<p class='mini' style='margin-top:16px'>"
            "<a href='/login'>← بازگشت</a> · کد پنج دقیقه اعتبار دارد</p>"
        )
    else:
        body = (
            "<h1>🤖 پنل مدیریت</h1>"
            "<p class='sub'>برای ورود، نام کاربری و رمزتان را بزنید.</p>"
            f"{warn}"
            "<form method='post' action='/login'>"
            "<label class='field'><span class='cap'>نام کاربری</span>"
            "<input type='text' name='username' autocomplete='username' autofocus"
            " dir='ltr'></label>"
            "<label class='field'><span class='cap'>رمز</span>"
            "<input type='password' name='password' autocomplete='current-password'"
            " dir='ltr'></label>"
            "<button class='btn primary' style='width:100%'>ادامه</button>"
            "</form>"
            "<p class='mini' style='margin-top:16px'>پس از رمز، یک کد در تلگرام "
            "برایتان می‌آید.<br>حساب ندارید؟ در ربات: ⚙️ سیستم ← 🖥 پنل وب</p>"
        )

    return (
        "<!doctype html><html lang='fa' dir='rtl'><head>"
        "<meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width, initial-scale=1'>"
        "<meta name='robots' content='noindex, nofollow'>"
        "<title>ورود — پنل مدیریت</title>"
        f"<style>{CSS}</style></head><body><main>"
        f"<div class='gate'><div class='panel'>{body}</div></div>"
        "</main></body></html>"
    )


def card(label: str, value: str, tone: str = "", *, href: str = "") -> str:
    inner = (
        f"<a class='value {tone}' href='{esc(href)}'>{esc(value)}</a>"
        if href
        else f"<div class='value {tone}'>{esc(value)}</div>"
    )
    return f"<div class='card'><div class='label'>{esc(label)}</div>{inner}</div>"


def table(
    headers: list[str], rows: list[str], *, empty: str = "چیزی نیست.", icon: str = "📭"
) -> str:
    if not rows:
        return (
            f"<div class='wrap'><div class='empty'>"
            f"<span class='big'>{icon}</span>{esc(empty)}</div></div>"
        )
    head = "".join(f"<th>{esc(h)}</th>" for h in headers)
    return (
        f"<div class='wrap'><table><thead><tr>{head}</tr></thead>"
        f"<tbody>{''.join(rows)}</tbody></table></div>"
    )


def pill(text: str, tone: str = "") -> str:
    return f"<span class='pill {tone}'>{esc(text)}</span>"


def panel(title: str, body: str, *, sub: str = "") -> str:
    caption = f"<p class='sub'>{esc(sub)}</p>" if sub else ""
    return f"<div class='panel'><h2>{esc(title)}</h2>{caption}{body}</div>"


def form(action: str, csrf: str, inner: str, *, confirm: str = "") -> str:
    """یک فرم POST با توکن CSRF.

    <b>توکن دستی فراموش می‌شود.</b> هر فرمِ بدون آن یک راه است برای
    اینکه سایتی دیگر، از مرورگرِ ادمینِ واردشده، عملی را انجام بدهد —
    تأیید یک رسید، مسدود کردن یک کاربر. اینجا جمع شده تا جا نیفتد.
    """
    # متن تأیید داخل یک رشته‌ی جاوااسکریپت می‌نشیند که خودش داخل یک
    # صفت HTML است — دو لایه. json.dumps لایه‌ی جاوااسکریپت را درست
    # می‌کند و escape لایه‌ی HTML را؛ دست‌نویس کردنشان همان جایی است
    # که نقل‌قولِ داخل متن، فرم را می‌شکند.
    guard = (
        f' onsubmit="return confirm({escape(json.dumps(confirm), quote=True)})"'
        if confirm
        else ""
    )
    return (
        f"<form class='inline' method='post' action='{esc(action)}'{guard}>"
        f"<input type='hidden' name='csrf' value='{esc(csrf)}'>{inner}</form>"
    )
