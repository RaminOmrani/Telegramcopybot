"""چرا نرخ ارز گرفته نمی‌شود.

    .venv/bin/python tools/ratetest.py

<b>مسئله‌ای که حل می‌کند.</b> «نرخ نمی‌آید» چند علتِ کاملاً متفاوت
دارد و از بیرون همه یک‌شکل‌اند. این ابزار سه لایه را جدا می‌سنجد، به
همان ترتیبی که یک درخواست از آن‌ها رد می‌شود:

    ۱. نام به IP ترجمه می‌شود؟        (DNS)
    ۲. اگر نه، از راه HTTPS چطور؟     (DoH)
    ۳. خودِ صرافی چه می‌گوید؟         (HTTP)

جدا کردنشان مهم است چون درمانشان فرق دارد: خطای لایه‌ی اول یعنی
سرور نام را پیدا نمی‌کند و باید از راه دیگری بپرسد؛ خطای لایه‌ی سوم
یعنی صرافی ما را رد کرده و DNS بی‌گناه است.

روی <b>خودِ سرور</b> اجرایش کنید، نه روی سیستم خودتان: چیزی که اهمیت
دارد این است که سرور به صرافی می‌رسد یا نه.
"""
from __future__ import annotations

import asyncio
import socket
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import aiohttp  # noqa: E402

from telkap.services import coins, dnsfix, usdtrate  # noqa: E402

OK = "\033[32m✓\033[0m"
BAD = "\033[31m✗\033[0m"
WARN = "\033[33m!\033[0m"
DIM = "\033[2m"
B = "\033[1m"
END = "\033[0m"

HOSTS = ("api.nobitex.ir", "api.telegram.org")


def system_dns(host: str) -> str:
    """با DNS خودِ سیستم. رشته‌ی خالی یعنی نشد."""
    try:
        return socket.getaddrinfo(host, 443, family=socket.AF_INET)[0][4][0]
    except OSError:
        return ""


async def main() -> int:
    print(f"\n{B}تست گرفتن نرخ ارز{END}")
    print(f"{DIM}سه لایه جدا سنجیده می‌شود: ترجمه‌ی نام، ترجمه از راه HTTPS، "
          f"و پاسخ خودِ صرافی.{END}\n")

    # ── لایه‌ی یک و دو: ترجمه‌ی نام ────────────────────────────────
    print(f"{B}۱) ترجمه‌ی نام{END}")
    dns_ok = {}
    doh_ok = {}

    async with aiohttp.ClientSession() as plain:
        for host in HOSTS:
            address = system_dns(host)
            dns_ok[host] = bool(address)
            if address:
                print(f"  {OK} {host:22} → {address}   {DIM}(DNS سیستم){END}")
                continue

            print(f"  {BAD} {host:22} با DNS سیستم ترجمه نشد")
            found = await dnsfix.over_https(host, session=plain)
            doh_ok[host] = bool(found)
            if found:
                print(f"      {OK} ولی از راه HTTPS شد → {found[0]}")
            else:
                print(f"      {BAD} از راه HTTPS هم نشد")
    print()

    # ── لایه‌ی سه: خودِ صرافی ──────────────────────────────────────
    print(f"{B}۲) پاسخ صرافی{END}")
    print(f"{DIM}با همان نشستی که ربات استفاده می‌کند — یعنی اگر DNS سیستم "
          f"نشد، از راه HTTPS.{END}\n")

    reachable = 0
    total = 0
    async with dnsfix.session() as session:
        for code in coins.all_codes():
            spec = coins.get(code)
            print(f"  {B}{spec.label}{END}  (بازار {spec.market})")
            for source in usdtrate._sources(spec):
                total += 1
                print(f"    {source.name}")
                try:
                    toman = await usdtrate._fetch(source, session)
                except usdtrate.RateError as exc:
                    print(f"      {BAD} {exc}")
                else:
                    reachable += 1
                    print(f"      {OK} {toman:,} تومان")
            print()

    # ── نتیجه ─────────────────────────────────────────────────────
    print(f"{B}" + "─" * 54 + f"{END}")

    if reachable:
        rescued = any(doh_ok.values())
        print(f"{OK} صرافی جواب داد ({reachable} از {total}).\n")
        if rescued:
            print(f"{DIM}توجه: نام با DNS سیستم ترجمه نشد و از راه HTTPS "
                  f"به‌دست آمد.")
            print("ربات همین کار را خودش می‌کند، پس نرخ کار می‌کند — ولی "
                  "یک وابستگی")
            print(f"بیشتر دارید. اگر خواستید ریشه‌ای حلش کنید، DNS سرور را "
                  f"عوض کنید.{END}\n")
        print("حالا «نرخ خودکار» را در پنل روشن کنید:")
        print(f"{DIM}⚙️ سیستم ← 💳 راه‌های پرداخت ← 🔄 نرخ خودکار{END}\n")
        return 0

    print(f"{BAD} هیچ منبعی جواب نداد.\n")

    if not dns_ok.get("api.telegram.org"):
        print(f"{WARN} {B}DNS سرور به‌کلی خراب است{END} — حتی تلگرام هم ترجمه نشد.")
        print("   این ربطی به صرافی ندارد. روی سرور:\n")
        print(f"   {DIM}resolvectl status   یا   cat /etc/resolv.conf{END}\n")
        return 1

    if not dns_ok.get("api.nobitex.ir") and not doh_ok.get("api.nobitex.ir"):
        print(f"{WARN} {B}نامِ صرافی هیچ‌جا ترجمه نمی‌شود{END} — نه با DNS سرور،")
        print("   نه از راه HTTPS. یعنی نوبیتکس برای درخواست‌های خارج از")
        print("   ایران پاسخ DNS نمی‌دهد.\n")
        print(f"   {B}تا وقتی این‌طور است، نرخ خودکار روی این سرور کار نمی‌کند.{END}")
        print("   نرخ را دستی بگذارید تا فروش نخوابد:\n")
        print(f"   {DIM}⚙️ سیستم ← 💳 راه‌های پرداخت ← 💱 نرخ تتر{END}\n")
        return 1

    print("   نام ترجمه شد ولی صرافی پاسخ نداد — یعنی مشکل از DNS نیست.")
    print("   محتمل‌ترین علت: نوبیتکس درخواست از IP خارجی را رد می‌کند.\n")
    print(f"   {DIM}تا رفع شدنش نرخ را دستی بگذارید:")
    print(f"   ⚙️ سیستم ← 💳 راه‌های پرداخت ← 💱 نرخ تتر{END}\n")
    return 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
