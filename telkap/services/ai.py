"""لایه‌ی اتصال به مدل زبانی.

با هر سرویسی که رابط OpenAI را می‌فهمد کار می‌کند — AvalAI، خودِ OpenAI،
OpenRouter، یا حتی Ollama روی همان سرور. آدرس و نام مدل‌ها از `.env` می‌آید،
پس عوض کردن سرویس‌دهنده یعنی عوض کردن دو خط تنظیمات، نه بازنویسی کد.

<b>سه اصل که همه‌جای این فایل رعایت شده‌اند:</b>

۱. <b>خرابی مدل نباید کپی را بخواباند.</b> هر تابع در بدترین حالت `None`
   برمی‌گرداند، نه استثنا. اگر سرویس قطع باشد، پست‌ها همان‌طور که پیش از
   هوش مصنوعی می‌رفتند، می‌روند. قابلیتی که خراب شدنش کل محصول را بخواباند،
   بدتر از نبودنش است.

۲. <b>مدل مناسبِ هر کار.</b> دسته‌بندی «آیا این تبلیغ است؟» با مدل کوچک
   همان‌قدر خوب انجام می‌شود که با مدل بزرگ، و ده برابر ارزان‌تر است.
   بازنویسی متن فارسی این‌طور نیست. پس دو مدل داریم نه یکی.

۳. <b>هزینه شمرده می‌شود.</b> هر فراخوانی توکن‌هایش ثبت می‌گردد تا بشود
   گفت این کاربر چقدر خرج برداشته — بدون این عدد، فروختنش حدس است.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
from dataclasses import dataclass, field

import aiohttp

from telkap.config import get_settings

log = logging.getLogger(__name__)

# مدل‌ها را با نقششان صدا می‌زنیم نه با نامشان، تا عوض کردن مدل به کد
# دست نزند
ROLE_SMALL = "small"        # دسته‌بندی، برچسب، تصمیم‌های کوتاه
ROLE_MAIN = "main"          # بازنویسی، ترجمه، خلاصه — جایی که کیفیت مهم است
ROLE_VISION = "vision"      # خواندن تصویر
ROLE_EMBED = "embed"        # بردار معنایی

TIMEOUT_SECONDS = 45
RETRIES = 2


@dataclass(slots=True)
class Usage:
    """توکن‌های مصرف‌شده‌ی یک فراخوانی."""

    prompt: int = 0
    completion: int = 0

    @property
    def total(self) -> int:
        return self.prompt + self.completion


@dataclass(slots=True)
class Reply:
    text: str
    usage: Usage = field(default_factory=Usage)
    model: str = ""


def configured() -> bool:
    """آیا اصلاً کلیدی تنظیم شده؟ بدون آن هیچ قابلیتی نباید ظاهر شود."""
    cfg = get_settings()
    return bool(cfg.ai_api_key and cfg.ai_base_url)


def model_for(role: str) -> str:
    cfg = get_settings()
    return {
        ROLE_SMALL: cfg.ai_model_small,
        ROLE_MAIN: cfg.ai_model_main,
        ROLE_VISION: cfg.ai_model_vision,
        ROLE_EMBED: cfg.ai_model_embed,
    }.get(role, cfg.ai_model_small)


async def _post(path: str, payload: dict) -> dict | None:
    """یک درخواست به سرویس، با تلاش دوباره در خطاهای گذرا.

    خروجی None یعنی نشد — و صدازننده باید بدون هوش مصنوعی ادامه دهد.
    """
    if not configured():
        return None
    cfg = get_settings()
    url = f"{cfg.ai_base_url.rstrip('/')}/{path.lstrip('/')}"
    headers = {
        "Authorization": f"Bearer {cfg.ai_api_key}",
        "Content-Type": "application/json",
    }
    timeout = aiohttp.ClientTimeout(total=TIMEOUT_SECONDS)

    for attempt in range(RETRIES + 1):
        try:
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.post(url, json=payload, headers=headers) as response:
                    if response.status == 200:
                        return await response.json()
                    body = (await response.text())[:300]
                    # ۴۰۰ یعنی درخواست ما غلط است؛ تکرارش همان جواب را می‌دهد
                    if 400 <= response.status < 500 and response.status != 429:
                        log.error("مدل درخواست را رد کرد (%s): %s", response.status, body)
                        return None
                    log.warning("مدل پاسخ %s داد: %s", response.status, body)
        except TimeoutError:
            log.warning("مدل در %s ثانیه جواب نداد", TIMEOUT_SECONDS)
        except Exception:
            log.warning("تماس با مدل ناموفق بود", exc_info=True)

        if attempt < RETRIES:
            await asyncio.sleep(2**attempt)
    return None


def _usage_of(data: dict) -> Usage:
    raw = data.get("usage") or {}
    return Usage(
        prompt=int(raw.get("prompt_tokens", 0) or 0),
        completion=int(raw.get("completion_tokens", 0) or 0),
    )


async def chat(
    prompt: str,
    *,
    role: str = ROLE_SMALL,
    system: str = "",
    temperature: float = 0.3,
    max_tokens: int = 800,
    image_url: str = "",
) -> Reply | None:
    """یک پرسش و یک پاسخ. اگر نشد، None."""
    content: object = prompt
    if image_url:
        content = [
            {"type": "text", "text": prompt},
            {"type": "image_url", "image_url": {"url": image_url}},
        ]

    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": content})

    model = model_for(ROLE_VISION if image_url else role)
    data = await _post(
        "chat/completions",
        {
            "model": model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        },
    )
    if not data:
        return None
    try:
        text = data["choices"][0]["message"]["content"] or ""
    except (KeyError, IndexError, TypeError):
        log.error("پاسخ مدل شکل مورد انتظار را نداشت: %s", str(data)[:300])
        return None
    return Reply(text=text.strip(), usage=_usage_of(data), model=model)


_JSON_BLOCK = re.compile(r"\{.*\}", re.DOTALL)


async def ask_json(
    prompt: str,
    *,
    role: str = ROLE_SMALL,
    system: str = "",
    max_tokens: int = 400,
) -> tuple[dict | None, Usage]:
    """پاسخی که باید JSON باشد.

    مدل‌ها دوست دارند دور JSON توضیح بنویسند یا در ```json بپیچندش. به‌جای
    اینکه به ادب مدل تکیه کنیم، اولین بلوک `{...}` را بیرون می‌کشیم.
    """
    reply = await chat(
        prompt,
        role=role,
        system=system or "فقط JSON معتبر برگردان، بدون هیچ توضیح اضافه.",
        temperature=0,
        max_tokens=max_tokens,
    )
    if reply is None:
        return None, Usage()

    match = _JSON_BLOCK.search(reply.text)
    if match is None:
        log.warning("پاسخ مدل JSON نبود: %s", reply.text[:200])
        return None, reply.usage
    try:
        parsed = json.loads(match.group(0))
    except json.JSONDecodeError:
        log.warning("JSON مدل خراب بود: %s", match.group(0)[:200])
        return None, reply.usage
    return (parsed if isinstance(parsed, dict) else None), reply.usage


async def embed(texts: list[str]) -> list[list[float]] | None:
    """بردار معنایی چند متن، به همان ترتیبی که داده شده‌اند."""
    clean = [text.strip() for text in texts if text and text.strip()]
    if not clean:
        return []
    data = await _post(
        "embeddings", {"model": model_for(ROLE_EMBED), "input": clean}
    )
    if not data:
        return None
    try:
        rows = sorted(data["data"], key=lambda row: row.get("index", 0))
        return [row["embedding"] for row in rows]
    except (KeyError, TypeError):
        log.error("پاسخ embedding شکل مورد انتظار را نداشت")
        return None


async def embed_one(text: str) -> list[float] | None:
    vectors = await embed([text])
    return vectors[0] if vectors else None


def similarity(first: list[float], second: list[float]) -> float:
    """کسینوس شباهت دو بردار، بین ۰ و ۱.

    بردارهای این مدل‌ها معمولاً نرمال‌اند و ضرب داخلی کافی است، ولی تکیه
    کردن به آن یعنی اگر مدلی عوض شد، عددها بی‌صدا غلط می‌شوند.
    """
    if not first or not second or len(first) != len(second):
        return 0.0
    dot = sum(a * b for a, b in zip(first, second, strict=True))
    size_a = sum(a * a for a in first) ** 0.5
    size_b = sum(b * b for b in second) ** 0.5
    if not size_a or not size_b:
        return 0.0
    return max(0.0, min(1.0, dot / (size_a * size_b)))


async def check_role(role: str) -> tuple[bool, str]:
    """یک نقش را واقعاً امتحان می‌کند.

    نام مدل باید مو‌به‌مو با فهرست سرویس بخواند؛ یک حرف اضافه یعنی ۴۰۴. این
    تابع هست تا به‌جای حدس زدن، جواب را از خود سرویس بگیریم.
    """
    name = model_for(role)
    if not name:
        return False, "نامی تنظیم نشده"

    if role == ROLE_EMBED:
        vector = await embed_one("سلام")
        if vector is None:
            return False, "جواب نداد — نام مدل یا دسترسی حساب"
        return True, f"سالم، بردار {len(vector)} بُعدی"

    reply = await chat("فقط بنویس: سلام", role=role, max_tokens=20)
    if reply is None:
        return False, "جواب نداد — نام مدل یا دسترسی حساب"
    return True, f"سالم، {reply.usage.total} توکن"


async def health() -> tuple[bool, str]:
    """هر چهار نقش را تست می‌کند و گزارشی خوانا می‌دهد."""
    if not configured():
        return False, (
            "❌ <b>تنظیم نشده</b>\n\n"
            "<code>AI_API_KEY</code> در فایل <code>.env</code> خالی است. "
            "تا پر نشود هیچ قابلیت هوش مصنوعی فعال نمی‌شود."
        )

    labels = {
        ROLE_SMALL: "دسته‌بندی (small)",
        ROLE_MAIN: "بازنویسی (main)",
        ROLE_VISION: "تصویر (vision)",
        ROLE_EMBED: "بردار معنایی (embed)",
    }
    lines: list[str] = []
    healthy = 0
    for role, label in labels.items():
        ok, note = await check_role(role)
        healthy += ok
        lines.append(
            f"{'✅' if ok else '❌'} <b>{label}</b>\n"
            f"    <code>{model_for(role)}</code> — {note}"
        )

    head = (
        f"🤖 <b>هوش مصنوعی</b> — {healthy} از {len(labels)} مدل سالم\n"
        f"سرویس: <code>{get_settings().ai_base_url}</code>\n"
    )
    tail = (
        ""
        if healthy == len(labels)
        else (
            "\n\n<i>مدلی که ❌ خورده یا نامش با فهرست سرویس نمی‌خواند، یا حساب "
            "شما به آن دسترسی ندارد. نام درست را از پنل سرویس بردارید و در "
            "<code>.env</code> بگذارید.</i>"
        )
    )
    return healthy == len(labels), head + "\n" + "\n".join(lines) + tail
