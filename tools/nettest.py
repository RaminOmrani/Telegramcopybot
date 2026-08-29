"""بررسی می‌کند این سرور به تلگرام راه دارد یا نه.

وقتی ربات روی `bot.get_me()` گیر می‌کند و می‌گوید
«Cannot connect to host api.telegram.org:443»، مشکل از کد نیست؛
شبکه‌ی سرور به تلگرام راه نمی‌دهد. این اسکریپت می‌گوید دقیقاً کجا بسته
شده و آیا پروکسیِ داخل `.env` کار می‌کند یا نه.

اجرا:
    python tools/nettest.py

فقط کتابخانه‌های خودِ پایتون لازم است، پس پیش از setup.bat هم کار می‌کند.
"""
from __future__ import annotations

import ipaddress
import socket
import ssl
import sys
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parent.parent

BOT_API = "api.telegram.org"

# تلگرام برای اکانت کاربری (Telethon) از این مرکز داده‌ها استفاده می‌کند.
# ممکن است Bot API باز باشد ولی این‌ها بسته — آن وقت ربات بالا می‌آید اما
# «اتصال اکانت» کار نمی‌کند. پس هر دو را جدا تست می‌کنیم.
DATA_CENTERS = [
    ("DC2 (اروپا)", "149.154.167.51"),
    ("DC4 (اروپا)", "149.154.167.91"),
]

TIMEOUT = 10


def ok(message: str) -> None:
    print(f"  [✓] {message}")


def bad(message: str) -> None:
    print(f"  [×] {message}")


def read_env() -> dict[str, str]:
    env = ROOT / ".env"
    if not env.exists():
        return {}
    values: dict[str, str] = {}
    for line in env.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        values[key.strip()] = value.strip().strip("'\"")
    return values


def resolve(host: str) -> list[str]:
    infos = socket.getaddrinfo(host, 443, proto=socket.IPPROTO_TCP)
    return sorted({info[4][0] for info in infos})


def poisoned(addresses: list[str]) -> bool:
    """آیا DNS نام را به یک آدرس داخلی برگردانده؟

    وقتی api.telegram.org به چیزی مثل 10.10.34.35 تبدیل می‌شود، یعنی
    DNS شبکه جواب جعلی می‌دهد و شما را به صفحه‌ی فیلترینگ می‌فرستد.
    این را باید بدانیم چون تنظیم درستِ پروکسی را عوض می‌کند.
    """
    for address in addresses:
        try:
            ip = ipaddress.ip_address(address)
        except ValueError:
            continue
        if ip.is_private or ip.is_loopback or ip.is_reserved:
            return True
    return False


def tcp_open(host: str, port: int) -> str | None:
    """None یعنی وصل شد؛ وگرنه متن خطا برمی‌گردد."""
    try:
        with socket.create_connection((host, port), timeout=TIMEOUT):
            return None
    except OSError as exc:
        return str(exc)


def check_dns() -> tuple[bool, bool]:
    """(تبدیل شد؟، جواب جعلی بود؟)"""
    print(f"\n۱) نام {BOT_API} به آدرس تبدیل می‌شود؟")
    try:
        addresses = resolve(BOT_API)
    except OSError as exc:
        bad(f"تبدیل نام ناموفق بود: {exc}")
        print("      یعنی DNS سرور کار نمی‌کند یا نام را عمداً بسته‌اند.")
        print("      DNS را روی 1.1.1.1 یا 8.8.8.8 بگذارید و دوباره بزنید.")
        return False, False

    if poisoned(addresses):
        bad(f"جواب جعلی: {'، '.join(addresses)}")
        print("      این یک آدرس داخلی است، نه سرور واقعی تلگرام. یعنی DNS")
        print("      شبکه عمداً جواب اشتباه می‌دهد و شما را به صفحه‌ی")
        print("      فیلترینگ می‌فرستد.")
        print("      مهم: در این حالت پروکسی را حتماً socks5h بنویسید نه socks5،")
        print("      وگرنه باز هم همین آدرس جعلی استفاده می‌شود.")
        return True, True

    ok(f"بله: {'، '.join(addresses)}")
    return True, False


def check_direct() -> bool:
    print(f"\n۲) اتصال مستقیم به {BOT_API}:443 باز است؟")
    error = tcp_open(BOT_API, 443)
    if error:
        bad(f"بسته است: {error}")
        return False
    ok("پورت باز شد")

    try:
        request = urllib.request.Request(
            f"https://{BOT_API}/", headers={"User-Agent": "telkap-nettest"}
        )
        with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
            body = response.read(200).decode("utf-8", "replace")
        ok(f"پاسخ HTTPS آمد: {body[:80]}")
    except urllib.error.HTTPError as exc:
        # ۴۰۴ هم یعنی سرور تلگرام جواب داده — همین کافی است
        ok(f"سرور تلگرام پاسخ {exc.code} داد (یعنی دسترسی هست)")
    except (urllib.error.URLError, ssl.SSLError, OSError) as exc:
        bad(f"TLS یا HTTP شکست خورد: {exc}")
        print("      پورت باز بود ولی محتوا رد شد — نشانه‌ی فیلترینگ میانی.")
        return False
    return True


def check_data_centers() -> bool:
    print("\n۳) مرکز داده‌های تلگرام (برای «اتصال اکانت») باز است؟")
    healthy = False
    for name, ip in DATA_CENTERS:
        error = tcp_open(ip, 443)
        if error:
            bad(f"{name} {ip}:443 — {error}")
        else:
            ok(f"{name} {ip}:443 باز است")
            healthy = True
    if not healthy:
        print("      ربات شاید کار کند ولی «اتصال اکانت» نه.")
    return healthy


def check_proxy(url: str) -> bool:
    print(f"\n۴) پروکسی داخل .env: {url}")
    parsed = urlparse(url)
    if not parsed.hostname or not parsed.port:
        bad("نشانی ناقص است؛ باید مثل socks5://127.0.0.1:10808 باشد")
        return False

    error = tcp_open(parsed.hostname, parsed.port)
    if error:
        bad(f"خودِ پروکسی جواب نمی‌دهد: {error}")
        print("      یعنی برنامه‌ی پروکسی (v2ray/xray/…) روی سرور روشن نیست")
        print("      یا پورتش با چیزی که در .env نوشته‌اید فرق دارد.")
        return False
    ok("پروکسی روشن است و پورتش باز است")

    if parsed.scheme.lower().startswith("socks"):
        print("      (تست عبورِ واقعی از socks کتابخانه‌ی جداگانه می‌خواهد؛")
        print("       اگر ربات باز هم وصل نشد، این را نصب کنید:")
        print("       .venv\\Scripts\\pip install aiohttp-socks python-socks)")
    return True


def main() -> None:
    print("بررسی دسترسی این سرور به تلگرام")
    print("=" * 46)

    dns, fake_dns = check_dns()
    direct = check_direct() if dns and not fake_dns else False
    check_data_centers()

    env = read_env()
    proxy_url = env.get("PROXY_URL", "").strip()
    proxy_ok = check_proxy(proxy_url) if proxy_url else None
    if proxy_ok is None:
        print("\n۴) پروکسی داخل .env: تنظیم نشده")

    # با DNS مسموم، socks5 ساده نام را خودش تبدیل می‌کند و باز به همان
    # آدرس جعلی می‌رسد؛ socks5h تبدیل نام را به خودِ پروکسی می‌سپارد.
    scheme = "socks5h" if fake_dns else "socks5"

    print("\n" + "=" * 46)
    print("نتیجه:")
    if direct:
        print("  دسترسی مستقیم هست. اگر ربات باز هم وصل نمی‌شود، احتمالاً")
        print("  PROXY_URL در .env پر است ولی پروکسی کار نمی‌کند — خالی‌اش کنید.")
    elif proxy_ok:
        print("  دسترسی مستقیم نیست ولی پروکسی روشن است.")
        if fake_dns and not proxy_url.lower().startswith("socks5h"):
            print("  ⚠ ولی DNS این شبکه جواب جعلی می‌دهد. نشانی پروکسی را در")
            print("    .env به socks5h عوض کنید، وگرنه باز هم وصل نمی‌شود:")
            print(f"       PROXY_URL={proxy_url.replace('socks5://', 'socks5h://', 1)}")
        else:
            print("  ربات را دوباره اجرا کنید؛ باید از پروکسی رد شود.")
    else:
        print("  نه دسترسی مستقیم هست و نه پروکسی سالمی تنظیم شده.")
        print("  ربات تا وقتی این درست نشود بالا نمی‌آید. دو راه:")
        print("    ۱) VPN سرور را در حالت TUN / Global روشن کنید")
        print("    ۲) یا یک پروکسی روی خود سرور بالا بیاورید و در .env بگذارید:")
        print(f"       PROXY_URL={scheme}://127.0.0.1:10808")
        sys.exit(1)


if __name__ == "__main__":
    main()
