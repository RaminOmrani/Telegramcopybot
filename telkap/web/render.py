"""ساخت صفحه‌های پنل.

قالب‌ها با رشته‌ی پایتون نوشته شده‌اند نه Jinja. برای شش صفحه، یک وابستگی
تازه و یک پوشه‌ی template ارزشش را ندارد — به‌خصوص که استقرار روی ویندوز
است و هرچه کمتر نصب شود بهتر.

هر مقداری که از دیتابیس می‌آید از `esc()` رد می‌شود. نام کاربر و یادداشت
رسید را خودِ کاربر نوشته و هیچ‌کدام قابل اعتماد نیستند.
"""
from __future__ import annotations

from html import escape

from telkap import i18n

CSS = """
:root {
  color-scheme: light dark;
  --bg: #f6f7f9; --card: #ffffff; --ink: #16181d; --muted: #6b7280;
  --line: #e5e7eb; --accent: #2563eb; --ok: #059669; --bad: #dc2626;
  --warn: #d97706;
}
@media (prefers-color-scheme: dark) {
  :root {
    --bg: #0f1115; --card: #171a21; --ink: #e8eaed; --muted: #9aa1ab;
    --line: #262b34; --accent: #60a5fa; --ok: #34d399; --bad: #f87171;
    --warn: #fbbf24;
  }
}
* { box-sizing: border-box; }
body {
  margin: 0; background: var(--bg); color: var(--ink);
  font: 15px/1.7 system-ui, "Segoe UI", Tahoma, sans-serif;
  direction: rtl;
}
a { color: var(--accent); text-decoration: none; }
a:hover { text-decoration: underline; }
header {
  background: var(--card); border-bottom: 1px solid var(--line);
  padding: 0 20px; display: flex; align-items: center; gap: 22px;
  flex-wrap: wrap; position: sticky; top: 0; z-index: 5;
}
header .brand { font-weight: 700; padding: 14px 0; margin-left: 8px; }
header nav { display: flex; gap: 18px; flex-wrap: wrap; }
header nav a { padding: 14px 0; color: var(--muted); border-bottom: 2px solid transparent; }
header nav a.on { color: var(--ink); border-bottom-color: var(--accent); }
header .who { margin-right: auto; color: var(--muted); font-size: 13px; }
main { max-width: 1100px; margin: 0 auto; padding: 24px 20px 60px; }
h1 { font-size: 21px; margin: 0 0 4px; }
.sub { color: var(--muted); margin: 0 0 22px; font-size: 14px; }
.cards { display: grid; grid-template-columns: repeat(auto-fit, minmax(170px, 1fr)); gap: 14px; }
.card {
  background: var(--card); border: 1px solid var(--line);
  border-radius: 12px; padding: 16px 18px;
}
.card .label { color: var(--muted); font-size: 13px; }
.card .value { font-size: 25px; font-weight: 700; margin-top: 6px; }
.card .value.ok { color: var(--ok); }
.card .value.warn { color: var(--warn); }
section { margin-top: 30px; }
section > h2 { font-size: 16px; margin: 0 0 12px; }
.wrap { overflow-x: auto; border: 1px solid var(--line); border-radius: 12px; background: var(--card); }
table { border-collapse: collapse; width: 100%; min-width: 560px; }
th, td { padding: 11px 14px; text-align: right; border-bottom: 1px solid var(--line); }
th { color: var(--muted); font-weight: 600; font-size: 13px; white-space: nowrap; }
tr:last-child td { border-bottom: 0; }
.empty { padding: 34px; text-align: center; color: var(--muted); }
.btn {
  display: inline-block; border: 1px solid var(--line); background: transparent;
  color: var(--ink); border-radius: 8px; padding: 6px 13px; cursor: pointer;
  font: inherit; font-size: 14px;
}
.btn:hover { border-color: var(--accent); }
.btn.ok { color: var(--ok); }
.btn.bad { color: var(--bad); }
form.inline { display: inline; }
.pill {
  font-size: 12px; padding: 2px 9px; border-radius: 999px;
  border: 1px solid var(--line); color: var(--muted);
}
.pill.ok { color: var(--ok); }
.pill.bad { color: var(--bad); }
.flash {
  border: 1px solid var(--line); border-radius: 10px; padding: 12px 16px;
  margin-bottom: 20px; background: var(--card);
}
.flash.ok { border-color: var(--ok); }
.flash.bad { border-color: var(--bad); }
.receipt { max-height: 76px; border-radius: 6px; border: 1px solid var(--line); }
.money { white-space: nowrap; font-variant-numeric: tabular-nums; }
.mini { color: var(--muted); font-size: 12px; }
.gate { max-width: 430px; margin: 15vh auto; text-align: center; }
.gate .card { padding: 30px 26px; }
input[type=search] {
  background: var(--card); color: var(--ink); border: 1px solid var(--line);
  border-radius: 8px; padding: 8px 12px; font: inherit; min-width: 230px;
}
"""

TABS = (
    ("/", "نمای کلی"),
    ("/payments", "رسیدها"),
    ("/users", "کاربران"),
)


def esc(value) -> str:
    return escape(str(value if value is not None else ""), quote=True)


def money(amount: int) -> str:
    return f"{i18n.num(int(amount), 'fa')} تومان"


def page(title: str, body: str, *, active: str = "", who: str = "") -> str:
    tabs = "".join(
        f'<a href="{esc(href)}" class="{"on" if href == active else ""}">{esc(label)}</a>'
        for href, label in TABS
    )
    identity = (
        f'<span class="who">{esc(who)} · <a href="/logout">خروج</a></span>' if who else ""
    )
    return (
        "<!doctype html><html lang='fa' dir='rtl'><head>"
        "<meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width, initial-scale=1'>"
        "<meta name='robots' content='noindex, nofollow'>"
        f"<title>{esc(title)} — پنل مدیریت</title>"
        f"<style>{CSS}</style></head><body>"
        f"<header><span class='brand'>🤖 پنل مدیریت</span>"
        f"<nav>{tabs}</nav>{identity}</header>"
        f"<main>{body}</main></body></html>"
    )


def gate(message: str, *, bad: bool = False) -> str:
    """صفحه‌ای که به کسی نشان داده می‌شود که هنوز وارد نشده."""
    body = (
        "<div class='gate'><div class='card'>"
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


def card(label: str, value: str, tone: str = "") -> str:
    return (
        f"<div class='card'><div class='label'>{esc(label)}</div>"
        f"<div class='value {tone}'>{esc(value)}</div></div>"
    )


def table(headers: list[str], rows: list[str], *, empty: str = "چیزی نیست.") -> str:
    if not rows:
        return f"<div class='wrap'><div class='empty'>{esc(empty)}</div></div>"
    head = "".join(f"<th>{esc(h)}</th>" for h in headers)
    return (
        f"<div class='wrap'><table><thead><tr>{head}</tr></thead>"
        f"<tbody>{''.join(rows)}</tbody></table></div>"
    )
