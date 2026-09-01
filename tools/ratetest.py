"""چرا نرخ ارز گرفته نمی‌شود.

    .venv/bin/python tools/ratetest.py

<b>مسئله‌ای که حل می‌کند.</b> وقتی نرخ تازه نمی‌شود، سه چیزِ کاملاً
متفاوت ممکن است خراب باشد: سرور اصلاً به صرافی نمی‌رسد، می‌رسد ولی
پاسخ را نمی‌فهمیم، یا همه‌چیز سالم است و فقط «نرخ خودکار» خاموش مانده.
از بیرون هر سه یک‌شکل‌اند — نرخی که عوض نمی‌شود.

این ابزار هر منبع را جدا می‌زند و می‌گوید کدام‌یک است. روی <b>خودِ
سرور</b> اجرایش کنید، نه روی سیستم خودتان: چیزی که اهمیت دارد این است
که سرور به صرافی می‌رسد یا نه، و آن دو شبکه‌ی متفاوت‌اند.
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import aiohttp  # noqa: E402

from telkap.services import coins, usdtrate  # noqa: E402

OK = "\033[32m✓\033[0m"
BAD = "\033[31m✗\033[0m"
DIM = "\033[2m"
END = "\033[0m"


async def check_source(source, session) -> bool:
    """یک منبع را می‌زند و نتیجه را چاپ می‌کند."""
    print(f"    {source.method} {source.url}")
    try:
        toman = await usdtrate._fetch(source, session)
    except usdtrate.RateError as exc:
        print(f"    {BAD} {exc}\n")
        return False
    print(f"    {OK} {toman:,} تومان\n")
    return True


async def main() -> int:
    print("\n\033[1mتست گرفتن نرخ ارز\033[0m")
    print(f"{DIM}هر منبع جدا زده می‌شود تا معلوم شود کدام کار می‌کند.{END}\n")

    reachable = 0
    total = 0

    async with aiohttp.ClientSession() as session:
        for code in coins.all_codes():
            spec = coins.get(code)
            print(f"\033[1m{spec.label}\033[0m  (بازار {spec.market})")
            for source in usdtrate._sources(spec):
                total += 1
                print(f"  {source.name}")
                if await check_source(source, session):
                    reachable += 1

    print("\033[1m" + "─" * 52 + "\033[0m")

    if reachable == total:
        print(f"{OK} همه‌ی منبع‌ها جواب دادند.\n")
        print(f"{DIM}پس اگر نرخ در ربات تازه نمی‌شود، مشکل شبکه نیست —")
        print("«نرخ خودکار» را در پنل روشن کنید:")
        print(f"⚙️ سیستم ← 💳 راه‌های پرداخت ← 🔄 نرخ خودکار{END}\n")
        return 0

    if reachable:
        print(f"{OK} بعضی منبع‌ها جواب دادند ({reachable} از {total}).\n")
        print(f"{DIM}ربات خودش سراغ منبعِ سالم می‌رود، پس نرخ باید کار کند.")
        print(f"جای نگرانی نیست، ولی ارزش دارد که حواستان باشد.{END}\n")
        return 0

    print(f"{BAD} هیچ منبعی جواب نداد.\n")
    print("محتمل‌ترین علت‌ها، به ترتیب:\n")
    print("  ۱. صرافی ایرانی است و سرور خارج از ایران — بعضی صرافی‌ها")
    print("     درخواست از IP خارجی را رد می‌کنند.")
    print("  ۲. سرور اصلاً به اینترنت بیرون نمی‌رسد. تست کنید:")
    print(f"     {DIM}curl -sS https://api.nobitex.ir/v2/orderbook/USDTIRT{END}")
    print("  ۳. صرافی موقتاً پایین است. یک ساعت بعد دوباره امتحان کنید.\n")
    print(f"\033[33mتا رفع شدنش، نرخ را دستی بگذارید تا فروش نخوابد:{END}")
    print("⚙️ سیستم ← 💳 راه‌های پرداخت ← 🔄 نرخ خودکار (خاموش) ← 💱 نرخ تتر\n")
    return 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
