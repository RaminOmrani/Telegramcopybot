"""کلیدهای تازه را به `.env` موجود اضافه می‌کند، بدون دست زدن به مقادیر شما.

با هر به‌روزرسانی، `.env.example` کلیدهای تازه پیدا می‌کند. جایگزین کردنِ
`.env` با آن یعنی از دست دادن توکن و کلیدها؛ ولی دستی مقایسه کردن هم
خسته‌کننده است و یکی دو تا جا می‌ماند.

این اسکریپت فقط کلیدهای **نبوده** را ته فایل اضافه می‌کند، همراه با
توضیح‌شان. هیچ مقدار موجودی عوض یا پاک نمی‌شود، و پیش از نوشتن هم یک
نسخه‌ی پشتیبان کنار فایل می‌گذارد.

اجرا:
    python tools/envsync.py            # نشان بده چه چیزی کم است
    python tools/envsync.py --apply    # اضافه‌شان کن

هیچ مقداری چاپ نمی‌شود — فقط نام کلیدها. پس خروجی‌اش را می‌شود فرستاد.
"""
from __future__ import annotations

import re
import shutil
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ENV = ROOT / ".env"
EXAMPLE = ROOT / ".env.example"

# خطی مثل «WEB_PORT=8080». خط‌هایی که با # شروع می‌شوند نمونه‌اند نه کلید.
KEY_LINE = re.compile(r"^([A-Z][A-Z0-9_]*)=")


def keys_of(text: str) -> set[str]:
    return {
        match.group(1)
        for line in text.splitlines()
        if (match := KEY_LINE.match(line.strip()))
    }


def blocks_of(text: str) -> list[tuple[str, list[str]]]:
    """هر کلید را با توضیح بالای خودش برمی‌گرداند.

    توضیح مهم است: کلیدی که بدون توضیحش اضافه شود، بعداً کسی نمی‌داند
    چه کار می‌کند و چه مقداری قبول دارد.
    """
    blocks: list[tuple[str, list[str]]] = []
    comment: list[str] = []

    for line in text.splitlines():
        stripped = line.strip()
        match = KEY_LINE.match(stripped)
        if match:
            blocks.append((match.group(1), [*comment, line]))
            comment = []
        elif not stripped:
            comment = []          # خط خالی یعنی توضیحِ قبلی به این کلید نمی‌چسبد
        else:
            comment.append(line)

    return blocks


def main() -> None:
    apply = "--apply" in sys.argv

    if not EXAMPLE.exists():
        sys.exit(f"فایل نمونه پیدا نشد: {EXAMPLE}")
    if not ENV.exists():
        sys.exit(
            f"فایل .env پیدا نشد: {ENV}\n"
            "این اسکریپت برای کامل کردنِ .env موجود است، نه ساختنش.\n"
            "اگر تازه شروع می‌کنید، .env.example را به .env کپی کنید."
        )

    example_text = EXAMPLE.read_text(encoding="utf-8")
    env_text = ENV.read_text(encoding="utf-8")

    have = keys_of(env_text)
    blocks = blocks_of(example_text)
    missing = [(key, lines) for key, lines in blocks if key not in have]
    extra = sorted(have - {key for key, _ in blocks})

    print(f"کلیدهای .env شما: {len(have)}")
    print(f"کلیدهای .env.example: {len(blocks)}")

    if extra:
        print(f"\nفقط در .env شما هست ({len(extra)}) — دست نمی‌خورد:")
        for key in extra:
            print(f"  · {key}")

    if not missing:
        print("\n✓ چیزی کم ندارید. کاری لازم نیست.")
        return

    print(f"\nکم دارید ({len(missing)}):")
    for key, _ in missing:
        print(f"  + {key}")

    if not apply:
        print("\nهیچ چیزی نوشته نشد. برای اضافه کردنشان:")
        print("    .\\envsync.bat --apply")
        return

    backup = ENV.with_name(f".env.bak-{datetime.now():%Y%m%d-%H%M%S}")
    shutil.copy2(ENV, backup)

    added = ["", "", f"# ── کلیدهای اضافه‌شده در {datetime.now():%Y-%m-%d} ──"]
    for _, lines in missing:
        added.extend(["", *lines])

    with ENV.open("a", encoding="utf-8") as handle:
        handle.write("\n".join(added) + "\n")

    print(f"\n✓ {len(missing)} کلید به ته .env اضافه شد.")
    print(f"  پشتیبان قبلی: {backup.name}")
    print("  مقادیر قبلی شما دست نخورده‌اند.")
    print("\n  ⚠️ پشتیبان هم توکن و کلیدها را دارد — جای امن نگهش دارید.")


if __name__ == "__main__":
    main()
