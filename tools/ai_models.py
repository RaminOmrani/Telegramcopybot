"""فهرست کامل مدل‌های سرویس را از خودِ API می‌گیرد.

به‌جای کپی کردن دستی از صفحه‌ی وب — که هم خسته‌کننده است و هم صفحه
lazy-load دارد و همه‌ی مدل‌ها را نشان نمی‌دهد — این اسکریپت فهرست را
یک‌جا از سرویس می‌پرسد.

<b>کلید از دستگاه شما بیرون نمی‌رود.</b> اسکریپت روی همان ماشینی اجرا
می‌شود که `.env` رویش است و فقط با خودِ سرویس حرف می‌زند.

اجرا:
    python tools/ai_models.py                 # فهرست کامل
    python tools/ai_models.py qwen            # فقط آن‌هایی که «qwen» دارند
    python tools/ai_models.py --json          # خروجی خام برای فرستادن

خروجی خام در `ai-models.json` هم ذخیره می‌شود. اگر خواستید فهرست را
جایی بفرستید، همان فایل را بفرستید — کلید داخلش نیست.

فقط کتابخانه‌های خودِ پایتون لازم است، پس پیش از setup.bat هم کار می‌کند.
"""
from __future__ import annotations

import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "ai-models.json"

# کلیدهایی که ممکن است قیمت را در خود داشته باشند. هر سرویسی نام خودش را
# دارد، پس به‌جای حدس زدن یکی، همه را می‌گردیم.
PRICE_HINTS = (
    "price", "pricing", "cost", "input", "output", "rate", "credit",
    "per_token", "per_million", "prompt", "completion",
)


def read_env() -> tuple[str, str]:
    """کلید و آدرس را از .env برمی‌دارد."""
    env = ROOT / ".env"
    if not env.exists():
        sys.exit(f"فایل .env پیدا نشد: {env}")

    values: dict[str, str] = {}
    for line in env.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        values[key.strip()] = value.strip().strip("'\"")

    key = values.get("AI_API_KEY", "")
    base = values.get("AI_BASE_URL", "https://api.avalai.ir/v1").rstrip("/")
    if not key:
        sys.exit(
            "AI_API_KEY در .env خالی است.\n"
            "کلید را از پنل سرویس بردارید و همان‌جا بگذارید."
        )
    return key, base


def fetch(key: str, base: str) -> dict:
    request = urllib.request.Request(
        f"{base}/models", headers={"Authorization": f"Bearer {key}"}
    )
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", "replace")[:400]
        sys.exit(f"سرویس پاسخ {exc.code} داد:\n{body}")
    except Exception as exc:                       # noqa: BLE001
        sys.exit(f"تماس با سرویس ناموفق بود: {exc}")


def price_fields(models: list[dict]) -> list[str]:
    """کدام فیلدهای این پاسخ بوی قیمت می‌دهند؟"""
    seen: set[str] = set()
    for model in models:
        for field, value in model.items():
            if field in ("id", "object", "created", "owned_by"):
                continue
            if isinstance(value, (int, float, str, dict)) and any(
                hint in field.lower() for hint in PRICE_HINTS
            ):
                seen.add(field)
    return sorted(seen)


def main() -> None:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    raw_only = "--json" in sys.argv

    key, base = read_env()
    print(f"گرفتن فهرست از {base}/models …\n")
    data = fetch(key, base)

    models = data.get("data") if isinstance(data, dict) else data
    if not isinstance(models, list):
        sys.exit(f"پاسخ شکل مورد انتظار را نداشت:\n{str(data)[:400]}")

    OUT.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"✓ {len(models)} مدل. خروجی خام: {OUT}\n")

    if raw_only:
        print(json.dumps(data, ensure_ascii=False, indent=2)[:4000])
        return

    extra = price_fields(models)
    if extra:
        print(f"فیلدهای قیمت‌مانند در پاسخ: {', '.join(extra)}\n")
    else:
        print(
            "این پاسخ قیمت ندارد — سرویس فقط شناسه‌ها را می‌دهد.\n"
            "قیمت را باید از پنل یا «ماشین حساب هزینه»ی سرویس دید.\n"
        )

    needle = args[0].lower() if args else ""
    rows = [m for m in models if needle in str(m.get("id", "")).lower()]
    rows.sort(key=lambda m: str(m.get("id", "")))

    if needle:
        print(f"«{needle}» در {len(rows)} مدل از {len(models)} تا:\n")

    width = max((len(str(m.get("id", ""))) for m in rows), default=10) + 2
    for model in rows:
        line = f"  {str(model.get('id','')):<{width}}{model.get('owned_by','')}"
        for field in extra:
            value = model.get(field)
            if value not in (None, "", {}):
                line += f"  {field}={value}"
        print(line)


if __name__ == "__main__":
    main()
