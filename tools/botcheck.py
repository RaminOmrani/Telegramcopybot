"""توکنِ داخل .env مالِ کدام ربات است.

<b>چرا لازم شد.</b> وقتی مینی‌اپ می‌گوید «امضای تلگرام با توکن این
سرور نمی‌خواند»، یک احتمالش این است که دکمه‌ی مینی‌اپ روی یک ربات
تنظیم شده و <code>.env</code> توکنِ رباتِ دیگری را دارد. تشخیصش
ساده است — کافی است بپرسیم این توکن مالِ کیست — ولی راهِ سرراستش
(<code>curl …/bot&lt;TOKEN&gt;/getMe</code>) توکن را روی صفحه و در
تاریخچه‌ی شل می‌گذارد.

این اسکریپت همان را می‌پرسد و <b>هیچ‌وقت خودِ توکن را چاپ نمی‌کند</b>.
به‌جایش یک اثر انگشت کوتاه می‌دهد: دو توکنِ یکسان اثر انگشت یکسان
دارند، ولی از روی اثر انگشت نمی‌شود توکن را ساخت. پس خروجی‌اش را
می‌شود بی‌خطر فرستاد.

اجرا:
    sudo -u telkap /opt/telkap/.venv/bin/python tools/botcheck.py
"""
from __future__ import annotations

import asyncio
import hashlib
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def fingerprint(token: str) -> str:
    """اثر انگشتِ توکن — قابل مقایسه، غیرقابل بازگشت."""
    return hashlib.sha256(token.encode()).hexdigest()[:12]


async def main() -> int:
    from dotenv import load_dotenv

    load_dotenv(Path(__file__).resolve().parent.parent / ".env")
    token = (os.getenv("BOT_TOKEN") or "").strip()

    if not token:
        print("✗ BOT_TOKEN در .env نیست.")
        return 1

    print(f"اثر انگشت توکن: {fingerprint(token)}")
    if ":" not in token:
        print("✗ شکل توکن درست نیست؛ باید مثل «123456:AA…» باشد.")
        return 1
    print(f"شناسه‌ی ربات در توکن: {token.split(':', 1)[0]}")

    import aiohttp

    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                f"https://api.telegram.org/bot{token}/getMe", timeout=20
            ) as response:
                data = await response.json()
    except Exception as exc:
        print(f"✗ به تلگرام نرسیدیم: {exc}")
        return 1

    if not data.get("ok"):
        print(f"✗ تلگرام این توکن را نپذیرفت: {data.get('description')}")
        print("  یعنی توکن باطل شده و .env هنوز قدیمی است.")
        return 1

    me = data["result"]
    print(f"✓ این توکن مالِ @{me.get('username')} است (شناسه {me.get('id')}).")
    print()
    print("حالا در BotFather ببینید دکمه‌ی مینی‌اپ روی همین ربات تنظیم شده")
    print("یا روی ربات دیگری. اگر نام‌ها یکی نبود، علت همان است.")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
