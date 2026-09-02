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
/* ── فونت ───────────────────────────────────────────────────────── */
/*
   وزیرمتن، از خودِ سرور نه از CDN. کاربر ایرانی صفحه را باز می‌کند و
   فونتی که از CDN خارجی بیاید یا کند می‌آید یا اصلاً نمی‌آید — و
   صفحه‌ای که فونتش نیامده با فونت پیش‌فرض ویندوز رندر می‌شود، که
   برای فارسی بد است. دویست کیلوبایت روی سرور، این ریسک را حذف می‌کند.

   font-display: swap تا متن بی‌درنگ دیده شود و بعد فونت جا بیفتد،
   نه اینکه صفحه سفید بماند.
*/
@font-face {
  font-family: 'Vazirmatn'; font-style: normal; font-weight: 400;
  src: local('Vazirmatn'), url('/static/fonts/Vazirmatn-Regular.woff2') format('woff2');
  font-display: swap;
}
@font-face {
  font-family: 'Vazirmatn'; font-style: normal; font-weight: 500;
  src: local('Vazirmatn Medium'), url('/static/fonts/Vazirmatn-Medium.woff2') format('woff2');
  font-display: swap;
}
@font-face {
  font-family: 'Vazirmatn'; font-style: normal; font-weight: 600;
  src: local('Vazirmatn SemiBold'), url('/static/fonts/Vazirmatn-SemiBold.woff2') format('woff2');
  font-display: swap;
}
@font-face {
  font-family: 'Vazirmatn'; font-style: normal; font-weight: 700;
  src: local('Vazirmatn Bold'), url('/static/fonts/Vazirmatn-Bold.woff2') format('woff2');
  font-display: swap;
}

/* ── رنگ ────────────────────────────────────────────────────────── */
/*
   <b>تیره، به‌عنوان پیش‌فرض.</b> این پنل ابزارِ کار است نه صفحه‌ی
   خواندنی: کسی ساعت‌ها بازش می‌گذارد و بارها سر می‌زند. سفیدِ خالی
   هم خسته‌کننده است و هم هویتی ندارد.

   لاجوردی به‌عنوان رنگِ اصلی چون هم روی تیره می‌درخشد و هم رنگِ
   آشنای تلگرام است — محصول کنارِ چیزی می‌نشیند که کاربر همان لحظه
   بازش دارد.
*/
:root {
  color-scheme: dark;
  --bg: #0b0e14;
  --bg-soft: #11151d;
  --card: #151a24;
  --card-2: #1b212d;
  --ink: #e8ecf4;
  --muted: #8b95a8;
  --line: #232a38;
  --soft: #1a202b;

  --accent: #3b82f6;
  --accent-2: #60a5fa;
  --accent-ink: #ffffff;
  --accent-soft: rgba(59,130,246,.14);

  --ok: #34d399;      --ok-bg: rgba(52,211,153,.13);
  --bad: #f87171;     --bad-bg: rgba(248,113,113,.13);
  --warn: #fbbf24;    --warn-bg: rgba(251,191,36,.13);

  --radius: 14px;
  --shadow: 0 1px 2px rgba(0,0,0,.3), 0 8px 24px rgba(0,0,0,.18);
}

/* روشن، برای کسی که صریح خواسته باشد */
@media (prefers-color-scheme: light) {
  :root {
    color-scheme: light;
    --bg: #f2f4f8; --bg-soft: #e9edf3; --card: #ffffff; --card-2: #f7f9fc;
    --ink: #10141c; --muted: #5c6679; --line: #e2e7ef; --soft: #f0f3f8;
    --accent: #2563eb; --accent-2: #1d4ed8; --accent-soft: rgba(37,99,235,.10);
    --ok: #059669;   --ok-bg: rgba(5,150,105,.10);
    --bad: #dc2626;  --bad-bg: rgba(220,38,38,.09);
    --warn: #b45309; --warn-bg: rgba(180,83,9,.10);
    --shadow: 0 1px 2px rgba(16,24,40,.05), 0 4px 16px rgba(16,24,40,.06);
  }
}

* { box-sizing: border-box; }
html { -webkit-text-size-adjust: 100%; }
body {
  margin: 0; background: var(--bg); color: var(--ink);
  font-family: 'Vazirmatn', 'Vazir', 'IRANSans', 'Shabnam', system-ui, Tahoma, sans-serif;
  font-size: 15px; line-height: 1.75;
  direction: rtl;
  -webkit-font-smoothing: antialiased;
}
a { color: var(--accent-2); text-decoration: none; }
a:hover { text-decoration: underline; }

/* ── چیدمان ────────────────────────────────────────────────────── */
/*
   نوار کناری، نه تب بالا. با پنج بخش تب کار می‌کرد؛ با نُه بخش دیگر
   نه — و کسی که پنل را باز نگه می‌دارد باید همیشه بداند کجاست و
   کجاها هست.
*/
.layout { display: flex; min-height: 100vh; }

aside {
  width: 244px; flex: 0 0 244px;
  background: var(--bg-soft); border-left: 1px solid var(--line);
  padding: 20px 14px; position: sticky; top: 0; height: 100vh; overflow-y: auto;
}
aside .brand {
  font-weight: 700; font-size: 16px; padding: 4px 12px 20px;
  display: flex; align-items: center; gap: 9px; letter-spacing: -.2px;
}
aside .brand .mark {
  width: 30px; height: 30px; border-radius: 9px; display: grid; place-items: center;
  background: linear-gradient(135deg, var(--accent), #8b5cf6);
  color: #fff; font-size: 15px;
}
aside .group {
  font-size: 11px; color: var(--muted); padding: 16px 12px 7px;
  letter-spacing: .5px; font-weight: 600; text-transform: uppercase;
}
aside nav a {
  display: flex; align-items: center; gap: 10px;
  padding: 9px 12px; border-radius: 10px; color: var(--ink);
  font-size: 14px; margin-bottom: 2px; transition: .13s;
}
aside nav a:hover { background: var(--soft); text-decoration: none; }
aside nav a.on {
  background: var(--accent-soft); color: var(--accent-2); font-weight: 600;
  box-shadow: inset 2px 0 0 var(--accent);
}
aside nav a .ico { font-size: 15px; width: 18px; text-align: center; }
aside nav a .dot {
  margin-right: auto; min-width: 20px; padding: 0 6px;
  background: var(--bad); color: #fff; border-radius: 999px;
  font-size: 11px; line-height: 19px; text-align: center; font-weight: 700;
}
aside .foot {
  margin-top: 24px; padding: 14px 12px; border-top: 1px solid var(--line);
  font-size: 12px; color: var(--muted);
}

.content { flex: 1; min-width: 0; }
.topbar {
  display: flex; align-items: center; gap: 12px; padding: 15px 28px;
  border-bottom: 1px solid var(--line); background: var(--card);
  position: sticky; top: 0; z-index: 5;
}
.topbar b { font-size: 15px; font-weight: 600; }
.topbar .who { margin-right: auto; color: var(--muted); font-size: 13px; }

@media (max-width: 880px) {
  .layout { display: block; }
  aside {
    width: auto; position: static; height: auto; border-left: 0;
    border-bottom: 1px solid var(--line); padding: 12px;
  }
  aside nav { display: flex; flex-wrap: wrap; gap: 5px; }
  aside nav a { margin-bottom: 0; }
  aside .group, aside .foot { display: none; }
  .topbar { padding: 13px 16px; }
}

main { max-width: 1200px; margin: 0 auto; padding: 26px 28px 80px; }
@media (max-width: 880px) { main { padding: 20px 16px 60px; } }

/* ── عنوان‌ها ───────────────────────────────────────────────────── */
h1 { font-size: 23px; margin: 0 0 5px; letter-spacing: -.3px; font-weight: 700; }
.sub { color: var(--muted); margin: 0 0 24px; font-size: 14px; }
section { margin-top: 32px; }
section > h2 {
  font-size: 13px; margin: 0 0 13px; color: var(--muted);
  font-weight: 600; letter-spacing: .4px; text-transform: uppercase;
}

/* ── کارت‌های عددی ──────────────────────────────────────────────── */
.cards { display: grid; grid-template-columns: repeat(auto-fit, minmax(168px, 1fr)); gap: 13px; }
.card {
  background: var(--card); border: 1px solid var(--line);
  border-radius: var(--radius); padding: 17px 19px; box-shadow: var(--shadow);
  position: relative; overflow: hidden;
}
.card::after {
  content: ''; position: absolute; inset: 0 auto auto 0;
  width: 3px; height: 100%; background: var(--line);
}
.card .label { color: var(--muted); font-size: 12.5px; font-weight: 500; }
.card .value {
  font-size: 27px; font-weight: 700; margin-top: 6px; display: block;
  font-variant-numeric: tabular-nums; letter-spacing: -.6px; line-height: 1.25;
}
.card .value.ok { color: var(--ok); }
.card .value.warn { color: var(--warn); }
.card .value.bad { color: var(--bad); }
.card:has(.value.ok)::after { background: var(--ok); }
.card:has(.value.warn)::after { background: var(--warn); }
.card:has(.value.bad)::after { background: var(--bad); }
.card a.value:hover { text-decoration: none; opacity: .82; }

/* ── جدول ──────────────────────────────────────────────────────── */
.wrap {
  overflow-x: auto; border: 1px solid var(--line);
  border-radius: var(--radius); background: var(--card); box-shadow: var(--shadow);
}
table { border-collapse: collapse; width: 100%; min-width: 560px; }
th, td { padding: 13px 16px; text-align: right; border-bottom: 1px solid var(--line); }
th {
  color: var(--muted); font-weight: 600; font-size: 12px;
  white-space: nowrap; background: var(--card-2);
  letter-spacing: .3px;
}
tbody tr { transition: .12s; }
tbody tr:hover { background: var(--card-2); }
tr:last-child td { border-bottom: 0; }
td.actions { white-space: nowrap; }
.empty { padding: 48px 20px; text-align: center; color: var(--muted); }
.empty .big { font-size: 34px; display: block; margin-bottom: 10px; opacity: .45; }

/* ── دکمه ──────────────────────────────────────────────────────── */
.btn {
  display: inline-block; border: 1px solid var(--line); background: var(--card-2);
  color: var(--ink); border-radius: 10px; padding: 8px 15px; cursor: pointer;
  font: inherit; font-size: 13.5px; font-weight: 500; transition: .13s;
}
.btn:hover { border-color: var(--accent); text-decoration: none; }
.btn.primary {
  background: var(--accent); border-color: var(--accent); color: var(--accent-ink);
  font-weight: 600;
}
.btn.primary:hover { background: var(--accent-2); border-color: var(--accent-2); }
.btn.ok { color: var(--ok); border-color: rgba(52,211,153,.35); }
.btn.ok:hover { background: var(--ok-bg); border-color: var(--ok); }
.btn.bad { color: var(--bad); border-color: rgba(248,113,113,.35); }
.btn.bad:hover { background: var(--bad-bg); border-color: var(--bad); }
.btn.small { padding: 5px 11px; font-size: 12.5px; }
.btn[disabled] { opacity: .45; cursor: not-allowed; }
form.inline { display: inline; }
.row { display: flex; gap: 9px; flex-wrap: wrap; align-items: center; }

/* ── برچسب ─────────────────────────────────────────────────────── */
.pill {
  display: inline-block; font-size: 11.5px; padding: 3px 11px; border-radius: 999px;
  border: 1px solid var(--line); color: var(--muted); background: var(--soft);
  white-space: nowrap; font-weight: 500;
}
.pill.ok { color: var(--ok); background: var(--ok-bg); border-color: transparent; }
.pill.bad { color: var(--bad); background: var(--bad-bg); border-color: transparent; }
.pill.warn { color: var(--warn); background: var(--warn-bg); border-color: transparent; }

/* ── پیام ──────────────────────────────────────────────────────── */
.flash {
  border: 1px solid var(--line); border-right: 3px solid var(--muted);
  border-radius: 11px; padding: 13px 17px; margin-bottom: 22px;
  background: var(--card);
}
.flash.ok { border-right-color: var(--ok); background: var(--ok-bg); color: var(--ok); }
.flash.bad { border-right-color: var(--bad); background: var(--bad-bg); color: var(--bad); }
.note {
  background: var(--soft); border-radius: 11px; padding: 13px 17px;
  color: var(--muted); font-size: 13.5px; margin: 16px 0;
}
.note.warn { background: var(--warn-bg); color: var(--warn); }

/* ── فرم ───────────────────────────────────────────────────────── */
input[type=search], input[type=text], input[type=number],
input[type=password], select {
  background: var(--bg-soft); color: var(--ink); border: 1px solid var(--line);
  border-radius: 10px; padding: 10px 13px; font: inherit; font-size: 14px;
}
input[type=search] { min-width: 240px; }
input:focus, select:focus {
  outline: none; border-color: var(--accent);
  box-shadow: 0 0 0 3px var(--accent-soft);
}
input::placeholder { color: var(--muted); }
label.field { display: block; margin-bottom: 16px; }
label.field .cap {
  display: block; font-size: 12.5px; color: var(--muted); margin-bottom: 7px;
  font-weight: 500;
}
label.field input, label.field select { width: 100%; max-width: 420px; }
label.field .hint { font-size: 12.5px; color: var(--muted); margin-top: 6px; display: block; }

/* ── چیدمان دو ستونه ────────────────────────────────────────────── */
.split { display: grid; grid-template-columns: repeat(auto-fit, minmax(320px, 1fr)); gap: 15px; }
.split3 { display: grid; grid-template-columns: repeat(auto-fit, minmax(230px, 1fr)); gap: 15px; }
.panel {
  background: var(--card); border: 1px solid var(--line);
  border-radius: var(--radius); padding: 21px; box-shadow: var(--shadow);
}
.panel h2 { margin: 0 0 5px; font-size: 15.5px; color: var(--ink); font-weight: 600; }
.panel .sub { margin: 0 0 17px; font-size: 13px; }
dl.facts { margin: 0; display: grid; grid-template-columns: auto 1fr; gap: 10px 18px; }
dl.facts dt { color: var(--muted); font-size: 13.5px; }
dl.facts dd { margin: 0; }

.attention { border-right: 3px solid var(--warn); }
.attention.none { border-right-color: var(--ok); }

/* ── نمودار ────────────────────────────────────────────────────── */
.chart { display: flex; align-items: flex-end; gap: 5px; height: 140px; padding: 4px 0; }
.chart .bar { flex: 1; display: flex; flex-direction: column; justify-content: flex-end; }
.chart .fill {
  background: linear-gradient(180deg, var(--accent-2), var(--accent));
  border-radius: 5px 5px 2px 2px; min-height: 3px; transition: .2s;
}
.chart .bar:hover .fill { filter: brightness(1.25); }
.chart .tick {
  font-size: 10px; color: var(--muted); text-align: center; margin-top: 7px;
  white-space: nowrap;
}
.legend { display: flex; gap: 18px; flex-wrap: wrap; margin-top: 14px; font-size: 13px; color: var(--muted); }
.legend b { font-variant-numeric: tabular-nums; color: var(--ink); }

.receipt { max-height: 78px; border-radius: 8px; border: 1px solid var(--line); }
.money { white-space: nowrap; font-variant-numeric: tabular-nums; }
.mini { color: var(--muted); font-size: 12px; }
code {
  background: var(--soft); padding: 2px 8px; border-radius: 7px;
  font-size: 13px; direction: ltr; display: inline-block;
  font-family: ui-monospace, "SF Mono", Menlo, monospace;
}
.gate { max-width: 430px; margin: 12vh auto; }
.gate .panel { padding: 34px 30px; }
.gate h1 { text-align: center; }
.gate .sub { text-align: center; }
"""


# بخش‌های پنل، گروه‌بندی‌شده. گروه‌ها فقط تزئین نیستند: با ده بخش،
# «کجا دنبالش بگردم» خودش یک سؤال می‌شود.
NAV = (
    ("کار روزمره", (
        ("/", "📊", "نمای کلی", ""),
        ("/payments", "🧾", "رسیدها", "waiting"),
        ("/users", "👥", "کاربران", ""),
        ("/tasks", "📋", "کارهای کپی", ""),
    )),
    ("مالی و گزارش", (
        ("/finance", "💰", "درآمد", ""),
        ("/activity", "🧭", "رویدادها", ""),
    )),
    ("تنظیمات", (
        ("/settings", "💳", "راه‌های پرداخت", ""),
        ("/account", "🔐", "حساب من", ""),
    )),
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

    <code>waiting</code> تعداد رسیدهای منتظر است و روی خودِ منو
    می‌نشیند. کارِ روزمره‌ی این پنل همان است، و اگر برای دیدنش باید
    صفحه‌ای باز کرد، گاهی باز نمی‌شود — و رسیدِ دیده‌نشده یعنی
    مشتریِ منتظر.
    """
    groups = []
    for group, items in NAV:
        links = []
        for href, icon, label, badge in items:
            mark = (
                f"<span class='dot'>{esc(i18n.num(waiting, 'fa'))}</span>"
                if badge == "waiting" and waiting
                else ""
            )
            cls = "on" if href == active else ""
            links.append(
                f"<a href='{esc(href)}' class='{cls}'>"
                f"<span class='ico'>{icon}</span>"
                f"<span>{esc(label)}</span>{mark}</a>"
            )
        groups.append(
            f"<div class='group'>{esc(group)}</div><nav>{''.join(links)}</nav>"
        )

    identity = (
        f"<span class='who'>{esc(who)}</span>"
        "<a class='btn small' href='/logout'>خروج</a>"
        if who
        else ""
    )
    return (
        "<!doctype html><html lang='fa' dir='rtl'><head>"
        "<meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width, initial-scale=1'>"
        "<meta name='robots' content='noindex, nofollow'>"
        f"<title>{esc(title)} — پنل مدیریت</title>"
        f"<style>{CSS}</style></head><body><div class='layout'>"
        "<aside><div class='brand'><span class='mark'>⚡</span>"
        "<span>فورواردبات</span></div>"
        f"{''.join(groups)}"
        "<div class='foot'>پنل مدیریت · فورواردبات</div></aside>"
        f"<div class='content'><div class='topbar'>"
        f"<b>{esc(title)}</b>{identity}</div>"
        f"<main>{body}</main></div></div></body></html>"
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


def chart(points: list[tuple[str, int]], *, unit: str = "") -> str:
    """نمودار میله‌ای، با CSS خالص.

    <b>چرا کتابخانه‌ی نمودار نیاورده‌ام.</b> یک نمودار میله‌ی ساده با
    چند خط CSS ساخته می‌شود؛ آوردن یک کتابخانه‌ی جاوااسکریپت یعنی
    صفحه‌ای که بدون اینترنتِ باز کار نمی‌کند — و این پنل روی سروری
    است که دسترسی‌اش محدود است.
    """
    if not points:
        return "<div class='empty'>هنوز داده‌ای نیست.</div>"

    top = max(value for _label, value in points) or 1
    bars = []
    for label, value in points:
        height = max(3, round(value * 100 / top))
        hint = f"{i18n.num(value, 'fa')} {unit}".strip()
        bars.append(
            f"<div class='bar' title='{esc(label)} — {esc(hint)}'>"
            f"<div class='fill' style='height:{height}%'></div>"
            f"<div class='tick'>{esc(label)}</div></div>"
        )
    return f"<div class='chart'>{''.join(bars)}</div>"


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
