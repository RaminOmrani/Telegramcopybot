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

# پورتی که هر برنامه‌ی پروکسی باز می‌کند فرق دارد و کاربر معمولاً نمی‌داند
# کدام است. به‌جای حدس زدن، همه‌ی پورت‌های رایج امتحان می‌شوند و آن‌که
# واقعاً به تلگرام می‌رسد معرفی می‌شود.
PROXY_PORTS = [
    (2334, "Hiddify"),
    (12334, "Hiddify"),
    (2335, "Hiddify"),
    (10808, "v2rayN — socks"),
    (10809, "v2rayN — http"),
    (2080, "Nekoray / sing-box"),
    (2081, "sing-box"),
    (7890, "Clash / Mihomo"),
    (7891, "Clash — socks"),
    (1080, "استاندارد socks"),
    (1081, "استاندارد socks"),
    (8086, "متفرقه"),
    (20170, "Netch"),
    (20171, "Netch"),
]

# برای پورت‌های روی همین دستگاه، رد شدن آنی است؛ پس صبر زیاد فقط وقت تلف
# کردن است.
LOCAL_TIMEOUT = 1.5


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


def tcp_open(host: str, port: int, timeout: float = TIMEOUT) -> str | None:
    """None یعنی وصل شد؛ وگرنه متن خطا برمی‌گردد."""
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return None
    except OSError as exc:
        return str(exc)


def recv_exact(sock: socket.socket, count: int) -> bytes:
    """دقیقاً count بایت می‌خواند؛ recv تنها ممکن است کمتر بدهد."""
    buffer = b""
    while len(buffer) < count:
        chunk = sock.recv(count - len(buffer))
        if not chunk:
            break
        buffer += chunk
    return buffer


def socks5_reaches(sock: socket.socket, host: str, port: int) -> bool:
    """آیا از این SOCKS5 می‌شود به host رسید؟

    اگر مقصد نام باشد، خامِ نام فرستاده می‌شود (نوع ۰x۰۳) نه آدرس عددی، تا
    تبدیل نام هم آن طرفِ تونل انجام شود — همان کاری که socks5h می‌کند و با
    DNS مسموم تنها راه درست است. مقصدهای عددی (مرکز داده‌ها) نوع ۰x۰۱.
    """
    sock.sendall(b"\x05\x01\x00")                    # سلام، بدون احراز هویت
    if recv_exact(sock, 2) != b"\x05\x00":
        return False

    try:
        target = b"\x01" + ipaddress.IPv4Address(host).packed
    except ipaddress.AddressValueError:
        name = host.encode("idna")
        target = b"\x03" + bytes([len(name)]) + name

    sock.sendall(b"\x05\x01\x00" + target + port.to_bytes(2, "big"))

    reply = recv_exact(sock, 4)
    if len(reply) < 4 or reply[0] != 0x05 or reply[1] != 0x00:
        return False

    # باقیِ پاسخ (نشانیِ بسته‌شده و پورت) باید خوانده شود، وگرنه در بافر
    # می‌ماند و دست‌دادنِ TLS بعدی آن را به‌جای پیام TLS می‌خواند — که خطای
    # گمراه‌کننده‌ی «wrong version number» می‌دهد به‌جای خطای واقعی.
    atyp = reply[3]
    if atyp == 0x01:
        rest = 4
    elif atyp == 0x04:
        rest = 16
    elif atyp == 0x03:
        rest = recv_exact(sock, 1)[0]
    else:
        return False
    recv_exact(sock, rest + 2)          # نشانی + دو بایت پورت
    return True


def http_reaches(sock: socket.socket, host: str, port: int) -> bool:
    """آیا این پروکسیِ HTTP اجازه‌ی CONNECT به مقصد را می‌دهد؟"""
    sock.sendall(
        f"CONNECT {host}:{port} HTTP/1.1\r\nHost: {host}:{port}\r\n\r\n".encode()
    )
    return b" 200 " in sock.recv(128)


def real_telegram(sock: socket.socket, host: str) -> str | None:
    """آیا آن‌سوی این اتصال واقعاً تلگرام است؟

    قبول شدنِ درخواستِ SOCKS چیز زیادی ثابت نمی‌کند: پروکسی ممکن است نام
    را با DNS مسمومِ همین شبکه تبدیل کند، به صفحه‌ی فیلترینگ وصل شود و
    همان را «موفق» گزارش کند. تنها چیزی که تقلب‌ناپذیر است، گواهی TLS
    است — صفحه‌ی فیلترینگ گواهی معتبرِ api.telegram.org ندارد.

    None یعنی درست است؛ وگرنه متن اشکال برمی‌گردد.
    """
    try:
        with ssl.create_default_context().wrap_socket(
            sock, server_hostname=host
        ):
            return None
    except ssl.SSLCertVerificationError as exc:
        return f"گواهی معتبر نبود ({exc.verify_message or exc.reason})"
    except (ssl.SSLError, OSError) as exc:
        return f"دست‌دادن TLS شکست خورد: {exc}"


def probe(
    host: str, port: int, target: str = BOT_API, verify: bool = True
) -> tuple[str, str | None] | None:
    """(نوع پروکسی، اشکالِ TLS) — یا None اگر اصلاً پروکسی‌ای آنجا نباشد.

    `verify=False` برای مقصدهای عددی مثل مرکز داده‌هاست که TLS با نام
    ندارند؛ آنجا رسیدن به مقصد همان چیزی است که می‌شود سنجید.
    """
    for scheme, reaches in (("socks5h", socks5_reaches), ("http", http_reaches)):
        try:
            with socket.create_connection((host, port), timeout=TIMEOUT) as sock:
                sock.settimeout(TIMEOUT)
                if not reaches(sock, target, 443):
                    continue
                return scheme, real_telegram(sock, target) if verify else None
        except OSError:
            return None                 # پورت باز نیست؛ نوع دوم را هم لازم نیست
        except Exception:               # noqa: BLE001 — پروتکل نخواند، نوع بعدی
            continue
    return None


def scan() -> list[tuple[int, str, str]]:
    print("\nگشتن دنبال پروکسی روی پورت‌های رایج …")
    found = []
    for port, name in PROXY_PORTS:
        if tcp_open("127.0.0.1", port, LOCAL_TIMEOUT):
            continue                    # بسته است؛ سراغ دست‌دادن هم نمی‌رویم
        result = probe("127.0.0.1", port)
        if result is None:
            continue
        scheme, tls_problem = result
        if tls_problem:
            bad(f"پورت {port} ({name}) — وصل می‌شود ولی تلگرام نیست")
            continue
        ok(f"پورت {port} ({name}) — {scheme} و به تلگرامِ واقعی می‌رسد")
        found.append((port, scheme, name))
    if not found:
        bad("روی هیچ‌کدام از پورت‌های رایج پروکسی سالمی نبود")
    return found


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


def check_data_centers(via: tuple[str, int] | None) -> bool:
    """مرکز داده‌ها را از همان راهی می‌سنجد که ربات هم می‌رود.

    اگر پروکسی دارد، تست مستقیم بی‌معنی است: Telethon هم از پروکسی رد
    می‌شود، پس بسته بودنِ راه مستقیم چیزی درباره‌ی «اتصال اکانت» نمی‌گوید.
    """
    where = "از راه پروکسی" if via else "مستقیم"
    print(f"\n۴) مرکز داده‌های تلگرام ({where}) — برای «اتصال اکانت»")

    healthy = False
    for name, ip in DATA_CENTERS:
        if via:
            # مرکز داده‌ها TLS با نام ندارند، پس فقط رسیدن سنجیده می‌شود.
            reached = probe(via[0], via[1], target=ip, verify=False) is not None
            error = None if reached else "از پروکسی رد نشد"
        else:
            error = tcp_open(ip, 443)

        if error:
            bad(f"{name} {ip}:443 — {error}")
        else:
            ok(f"{name} {ip}:443 باز است")
            healthy = True

    if not healthy:
        print("      ربات کار می‌کند ولی «اتصال اکانت» کاربران نه.")
    return healthy


def check_proxy(url: str) -> str:
    """«ok» یا دلیلِ نرسیدن: bad_url / no_port / no_reach / intercepted."""
    print(f"\n۳) پروکسی داخل .env: {url}")
    parsed = urlparse(url)
    if not parsed.hostname or not parsed.port:
        bad("نشانی ناقص است؛ باید مثل socks5://127.0.0.1:10808 باشد")
        return "bad_url"

    error = tcp_open(parsed.hostname, parsed.port)
    if error:
        bad(f"خودِ پروکسی جواب نمی‌دهد: {error}")
        print("      یعنی برنامه‌ی پروکسی (Hiddify/v2rayN/…) روشن نیست، یا")
        print("      روشن است ولی پورت دیگری باز کرده — هر برنامه پورت خودش")
        print("      را دارد و ۱۰۸۰۸ فقط پیش‌فرضِ v2rayN است.")
        return "no_port"
    ok("پورت باز است")

    # باز بودن پورت یعنی چیزی آنجا هست، نه اینکه راهش به تلگرام باز است.
    # پس واقعاً از همان پروکسی به api.telegram.org وصل می‌شویم.
    result = probe(parsed.hostname, parsed.port)
    if result is None:
        bad("ولی راهش به تلگرام باز نیست")
        print("      پروکسی روشن است ولی خودش به تلگرام وصل نمی‌شود — کانفیگش")
        print("      منقضی شده یا سرورش جواب نمی‌دهد. در خودِ برنامه امتحان کنید.")
        return "no_reach"

    _, tls_problem = result
    if tls_problem is None:
        ok("و از همین پروکسی به تلگرامِ واقعی می‌رسد ✓")
        return "ok"

    bad(f"وصل می‌شود ولی آن‌سویش تلگرام نیست — {tls_problem}")
    print("      یعنی پروکسی نام را با DNS همین شبکه تبدیل کرده، به صفحه‌ی")
    print("      فیلترینگ رسیده، و همان را «موفق» گزارش کرده است.")
    print("      چاره در خودِ برنامه‌ی پروکسی است، نه در .env:")
    print("        · DNS آن را روی یک نشانی راه‌دور بگذارید (مثل")
    print("          https://8.8.8.8/dns-query) تا تبدیل نام از تونل رد شود")
    print("        · یا حالت TUN را روشن کنید و PROXY_URL را خالی بگذارید")
    return "intercepted"


def main() -> None:
    print("بررسی دسترسی این سرور به تلگرام")
    print("=" * 46)

    dns, fake_dns = check_dns()
    direct = check_direct() if dns and not fake_dns else False

    # پروکسی پیش از مرکز داده‌ها سنجیده می‌شود، چون تعیین می‌کند آن‌ها را
    # از کدام راه باید سنجید.
    env = read_env()
    proxy_url = env.get("PROXY_URL", "").strip()
    status = check_proxy(proxy_url) if proxy_url else "unset"
    if status == "unset":
        print("\n۳) پروکسی داخل .env: تنظیم نشده")
    proxy_ok = status == "ok"

    via = None
    if proxy_ok:
        parsed = urlparse(proxy_url)
        via = (parsed.hostname or "127.0.0.1", parsed.port or 0)
    check_data_centers(via)

    # با DNS مسموم، socks5 ساده نام را خودش تبدیل می‌کند و باز به همان
    # آدرس جعلی می‌رسد؛ socks5h تبدیل نام را به خودِ پروکسی می‌سپارد.
    scheme = "socks5h" if fake_dns else "socks5"

    # شاید برنامه‌ی پروکسی روشن باشد ولی روی پورت دیگری. پیش از اعلام
    # نتیجه می‌گردیم، تا نتیجه بتواند پورت درست را نام ببرد.
    # وقتی پروکسی وصل می‌شود ولی سر از صفحه‌ی فیلترینگ درمی‌آورد، گشتن
    # بی‌فایده است: پورت درست است و اشکال از DNS داخلِ همان برنامه است.
    hunt = not direct and not proxy_ok and status != "intercepted"
    found = scan() if hunt else []

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
    elif status == "intercepted":
        print("  پروکسی روشن است و پورتش هم درست، ولی به تلگرامِ واقعی")
        print("  نمی‌رسد — نام را با DNS همین شبکه تبدیل می‌کند و سر از")
        print("  صفحه‌ی فیلترینگ درمی‌آورد.")
        print("\n  .env را دست نزنید؛ اشکال داخلِ خودِ برنامه‌ی پروکسی است.")
        print("  در Hiddify یکی از این دو:")
        print("    ۱) تنظیمات ← DNS را روی نشانی راه‌دور بگذارید، مثل")
        print("       https://8.8.8.8/dns-query یا https://1.1.1.1/dns-query")
        print("    ۲) یا «پیاده‌سازی Tun» را روشن کنید — آن وقت کل سیستم از")
        print("       تونل رد می‌شود و در .env بنویسید: PROXY_URL=")
    else:
        if found:
            port, found_scheme, name = found[0]
            print(f"  پروکسی پیدا شد: {name} روی پورت {port}.")
            print("  همین یک خط را در .env بگذارید (خط PROXY_URL فعلی را عوض کنید):")
            print(f"\n       PROXY_URL={found_scheme}://127.0.0.1:{port}\n")
            print("  بعد دوباره .\\nettest.bat را بزنید تا تأیید شود.")
        else:
            print("  نه دسترسی مستقیم هست و نه پروکسی سالمی روی این سرور.")
            print("  اگر Hiddify یا v2rayN روشن است، پورتش را از خود برنامه")
            print("  بردارید و در .env بگذارید:")
            print(f"\n       PROXY_URL={scheme}://127.0.0.1:<همان پورت>\n")
            print("  در Hiddify: تنظیمات ← پیکربندی ← «پورت پروکسی».")
            print("  اگر برنامه حالت TUN دارد، آن را روشن کنید تا کل سیستم")
            print("  از تونل رد شود؛ آن وقت PROXY_URL را خالی بگذارید.")
        sys.exit(1)


if __name__ == "__main__":
    main()
