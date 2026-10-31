"""مرحله‌ی هوش مصنوعی در مسیر کپی.

`apply_transforms` همگام است و هوش مصنوعی ناهمگام، پس این مرحله نمی‌تواند
داخلش بنشیند. اینجا پیش از آن اجرا می‌شود: متن خام از مبدا می‌آید، با
قابلیت‌های روشنِ این کار پردازش می‌شود، و نتیجه به خط لوله‌ی همیشگی
می‌رود.

<b>یک بار برای همه‌ی مقصدها.</b> خروجی این مرحله ورودیِ `apply_transforms`
هر مقصد می‌شود. اگر به‌جایش برای هر مقصد جدا صدا زده می‌شد، کاری که یک
بار لازم است چند برابر هزینه می‌برد بی‌آنکه نتیجه فرق کند — امضا و فوتر
هر مقصد بعد از این مرحله اعمال می‌شوند.

<b>ترتیب عمدی است:</b> خلاصه ← بازنویسی ← ترجمه. خلاصه اول می‌آید چون
روی متن بلند کار می‌کند، و ترجمه آخر تا متنِ نهایی را ترجمه کند نه
نسخه‌ی میانی را.

<b>هزینه.</b> هر عملیات یک واحد اعتبار می‌برد. اعتبار پیش از فراخوانی کم
می‌شود نه بعدش، وگرنه دو کار موازیِ یک کاربر می‌توانستند هر دو از یک
واحد استفاده کنند. اگر مدل جواب نداد، همان واحد برمی‌گردد.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field

from telkap.plans import CREDIT_AI
from telkap.services import ai, aiskills, credits

log = logging.getLogger(__name__)

# کلید تنظیمات → (نام عملیات برای لاگ)
OP_SUMMARIZE = "ai_summarize"
OP_REWRITE = "ai_rewrite"
OP_TRANSLATE = "ai_translate"

# ترتیب اجرا. عوض کردنش نتیجه را عوض می‌کند، پس صریح نوشته شده.
ORDER = (OP_SUMMARIZE, OP_REWRITE, OP_TRANSLATE)

LABELS = {
    OP_SUMMARIZE: "خلاصه",
    OP_REWRITE: "بازنویسی",
    OP_TRANSLATE: "ترجمه",
}


@dataclass(slots=True)
class Pass:
    """نتیجه‌ی مرحله، برای گزارش دادن به کاربر و ثبت در لاگ."""

    text: str
    applied: list[str] = field(default_factory=list)
    spent: int = 0
    out_of_credit: bool = False

    @property
    def changed(self) -> bool:
        return bool(self.applied)


def wanted(cfg: dict) -> list[str]:
    """کدام عملیات‌ها برای این کار روشن‌اند، به ترتیب اجرا."""
    return [op for op in ORDER if cfg.get(op)]


async def _run(op: str, text: str, cfg: dict) -> aiskills.Outcome | None:
    if op == OP_SUMMARIZE:
        return await aiskills.summarize(text, sentences=int(cfg.get("ai_sentences") or 2))
    if op == OP_REWRITE:
        return await aiskills.rewrite(text, style=str(cfg.get("ai_style") or "same"))
    if op == OP_TRANSLATE:
        return await aiskills.translate(text, target=str(cfg.get("ai_language") or "en"))
    return None


async def enhance(text: str, cfg: dict, user_id: int) -> Pass:
    """متن را با قابلیت‌های روشنِ این کار پردازش می‌کند.

    هرگز استثنا نمی‌دهد. اگر سرویس نبود، اعتبار تمام شده بود، یا مدل
    جواب نداد، متن اصلی برمی‌گردد و کپی مثل قبل ادامه پیدا می‌کند.
    """
    ops = wanted(cfg)
    if not ops or not text.strip():
        return Pass(text)

    # پیش از هر کاری: سرویس اصلاً تنظیم شده؟ این بررسی به دیتابیس دست
    # نمی‌زند، پس برای کارهایی که هوش مصنوعی ندارند هزینه‌ای ندارد.
    if not ai.configured():
        return Pass(text)

    current = text
    applied: list[str] = []
    spent = 0

    for op in ops:
        if not await credits.consume(user_id, CREDIT_AI, 1):
            log.info("اعتبار هوش مصنوعی کاربر %s تمام شد", user_id)
            return Pass(current, applied, spent, out_of_credit=True)

        try:
            outcome = await _run(op, current, cfg)
        except Exception:                       # noqa: BLE001 — کپی نباید بخوابد
            log.exception("مرحله‌ی %s ناموفق بود", op)
            outcome = None

        # مدل جواب نداد یا این عملیات برای این متن معنا نداشت (مثل خلاصه‌ی
        # متن کوتاه) — واحدی که کم کردیم باید برگردد.
        if outcome is None or not outcome.text.strip():
            await credits.add(user_id, CREDIT_AI, 1, note=f"برگشت {op}")
            continue

        current = outcome.text
        applied.append(op)
        spent += 1

    return Pass(current, applied, spent)


def summary(result: Pass) -> str:
    """یک خط برای لاگ فعالیت. خالی یعنی چیزی برای گفتن نیست."""
    if result.out_of_credit:
        done = "، ".join(LABELS[op] for op in result.applied)
        return f"اعتبار هوش مصنوعی تمام شد{(' — انجام‌شده: ' + done) if done else ''}"
    if not result.applied:
        return ""
    return "، ".join(LABELS[op] for op in result.applied) + f" ({result.spent} واحد)"
