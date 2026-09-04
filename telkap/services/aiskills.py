"""قابلیت‌های هوش مصنوعی — لایه‌ی بالای `ai.py`.

`ai.py` فقط حمل‌ونقل است: پرسش می‌فرستد و پاسخ می‌گیرد. اینجا جای
تصمیم‌هاست: چه بپرسیم، کِی بپرسیم، و مهم‌تر از همه <b>کِی نپرسیم</b>.

<b>الگوی دو مرحله‌ای — چیزی که این ماژول را اقتصادی می‌کند</b>

اگر روی هر پست مدل صدا زده شود، هزینه‌ی یک کاربر پرمصرف از حق اشتراکش
بیشتر می‌شود. راه‌حل مدل ارزان‌تر نیست، <b>کمتر صدا زدن</b> است:

    مرحله‌ی ۱ — روش ارزان (امتیاز کلیدواژه، SimHash). رایگان و آنی.
    مرحله‌ی ۲ — فقط وقتی مرحله‌ی ۱ «نمی‌دانم» گفت، مدل صدا زده می‌شود.

اکثر پست‌ها در مرحله‌ی ۱ قطعی تعیین تکلیف می‌شوند: پستی که هیچ نشانه‌ی
تبلیغاتی ندارد امتیازش صفر است و لازم نیست از مدل بپرسیم. فقط موارد مرزی
— که کسر کوچکی از کل‌اند — به مدل می‌رسند.

<b>هیچ‌کدام از این توابع استثنا پرتاب نمی‌کنند.</b> در بدترین حالت `None`
برمی‌گردانند و صدازننده همان مسیر پیش از هوش مصنوعی را می‌رود. قابلیتی که
خراب شدنش کپی را بخواباند، از نبودنش بدتر است.

مصرف توکن در خروجی هر تابع می‌آید تا صدازننده بتواند اعتبار کم کند؛ خودِ
این ماژول به دیتابیس دست نمی‌زند.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field

from telkap.services import ai, dedupe

log = logging.getLogger(__name__)

# ── لحن‌های بازنویسی ────────────────────────────────────────────────
# کاربر از منو یکی را برمی‌دارد؛ کلیدها در دیتابیس ذخیره می‌شوند پس
# نباید عوض شوند.
STYLES: dict[str, str] = {
    "same": "همان لحن متن اصلی",
    "formal": "رسمی و خبری",
    "friendly": "خودمانی و صمیمی",
    "short": "کوتاه و فشرده",
    "marketing": "تبلیغاتی و جذاب",
}

LANGUAGES: dict[str, str] = {
    "fa": "فارسی",
    "en": "انگلیسی",
    "ar": "عربی",
    "tr": "ترکی استانبولی",
    "ru": "روسی",
}

# فاصله‌ی SimHash که در آن نمی‌شود قطعی گفت. زیر این بازه قطعاً یکی‌اند و
# بالایش قطعاً دو خبر متفاوت — روی متن فارسی واقعی سنجیده شده. فقط همین
# میان‌بازه به مدل می‌رسد.
UNSURE_LOW = 12
UNSURE_HIGH = 20

# شباهت بردار معنایی که از آن بالاتر «همان خبر» شمرده می‌شود.
SAME_STORY_AT = 0.88


@dataclass(slots=True)
class Outcome:
    """خروجی یک مهارت، به‌همراه هزینه‌ای که برداشته.

    `usage` صفر یعنی مدل اصلاً صدا زده نشد — یا لازم نبود، یا پاسخ از
    مرحله‌ی ارزان درآمد. صدازننده از روی همین تصمیم می‌گیرد اعتبار کم
    کند یا نه.
    """

    text: str = ""
    usage: ai.Usage = field(default_factory=ai.Usage)

    @property
    def used_model(self) -> bool:
        return self.usage.total > 0


@dataclass(slots=True)
class AdVerdict:
    is_ad: bool
    reason: str
    usage: ai.Usage = field(default_factory=ai.Usage)
    asked_model: bool = False


# ── بازنویسی، ترجمه، خلاصه ──────────────────────────────────────────

_KEEP_RULES = (
    "قواعد سخت‌گیرانه:\n"
    "۱. فقط خروجی نهایی را بنویس. هیچ توضیح، مقدمه یا علامت نقل‌قولی نگذار.\n"
    "۲. ایموجی‌ها، لینک‌ها، هشتگ‌ها و آیدی‌های @ را دست‌نخورده نگه دار.\n"
    "۳. عدد، قیمت، تاریخ و نام خاص را عوض نکن.\n"
    "۴. اگر متن ورودی خالی یا بی‌معنی بود، همان را بی‌تغییر برگردان."
)


async def rewrite(text: str, *, style: str = "same") -> Outcome | None:
    """متن را با لحن خواسته‌شده بازمی‌نویسد، بدون عوض کردن معنا."""
    if not ai.configured() or not text.strip():
        return None

    tone = STYLES.get(style, STYLES["same"])
    reply = await ai.chat(
        f"این پست تلگرامی را بازنویسی کن با لحن {tone}:\n\n{text}",
        role=ai.ROLE_MAIN,
        system=(
            "تو یک ویراستار فارسی‌زبان هستی. متن را بازنویسی می‌کنی طوری که "
            "معنا دقیقاً همان بماند ولی جمله‌ها از نو ساخته شوند.\n" + _KEEP_RULES
        ),
        temperature=0.6,
        max_tokens=1200,
    )
    if reply is None:
        return None
    return Outcome(text=reply.text, usage=reply.usage)


async def translate(text: str, *, target: str = "en") -> Outcome | None:
    """متن را به زبان مقصد ترجمه می‌کند."""
    if not ai.configured() or not text.strip():
        return None

    name = LANGUAGES.get(target, LANGUAGES["en"])
    reply = await ai.chat(
        f"این پست تلگرامی را به {name} ترجمه کن:\n\n{text}",
        role=ai.ROLE_MAIN,
        system=(
            f"تو مترجم حرفه‌ای به {name} هستی. ترجمه باید روان و طبیعی باشد، "
            "نه کلمه‌به‌کلمه.\n" + _KEEP_RULES
        ),
        temperature=0.3,
        max_tokens=1500,
    )
    if reply is None:
        return None
    return Outcome(text=reply.text, usage=reply.usage)


async def summarize(text: str, *, sentences: int = 2) -> Outcome | None:
    """متن بلند را در چند جمله خلاصه می‌کند.

    متن کوتاه خلاصه نمی‌شود: خلاصه‌ی یک پست سه‌خطی خودش است، و فراخوانی
    مدل برایش هزینه‌ی بی‌دلیل می‌شود.
    """
    if not ai.configured() or len(text.strip()) < 200:
        return None

    reply = await ai.chat(
        f"این پست را در حداکثر {sentences} جمله خلاصه کن:\n\n{text}",
        role=ai.ROLE_MAIN,
        system=(
            "تو خلاصه‌نویس فارسی هستی. مهم‌ترین نکته‌ها را نگه می‌داری و "
            "حاشیه را دور می‌ریزی.\n" + _KEEP_RULES
        ),
        temperature=0.2,
        max_tokens=400,
    )
    if reply is None:
        return None
    return Outcome(text=reply.text, usage=reply.usage)


# ── تشخیص تبلیغ، دو مرحله‌ای ────────────────────────────────────────


def _certain(score: int, threshold: int) -> bool | None:
    """آیا امتیاز کلیدواژه‌ای خودش قطعی است؟ None یعنی مرزی.

    دو امتیاز بالاتر از آستانه یعنی چند نشانه‌ی قوی کنار هم — تبلیغ است.
    دو امتیاز پایین‌تر یعنی تقریباً هیچ نشانه‌ای نیست. بینشان جایی است که
    فهرست کلیدواژه کم می‌آورد و مدل ارزش صدا زدن دارد.
    """
    if score >= threshold + 2:
        return True
    if score <= threshold - 2:
        return False
    return None


async def ad_verdict(text: str, *, score: int, threshold: int) -> AdVerdict:
    """تبلیغ است یا نه — با مدل، فقط وقتی امتیاز مرزی باشد.

    خروجی همیشه معتبر است: اگر مدل نبود یا جواب نداد، به همان تصمیم
    امتیازی برمی‌گردیم. پس روشن کردن این قابلیت هیچ‌وقت رفتار را بدتر
    از قبل نمی‌کند.
    """
    sure = _certain(score, threshold)
    if sure is not None:
        return AdVerdict(sure, "امتیاز کلیدواژه‌ای قطعی بود")

    fallback = AdVerdict(score >= threshold, "امتیاز مرزی بود و مدل در دسترس نبود")
    if not ai.configured():
        return fallback

    parsed, usage = await ai.ask_json(
        "آیا این پست تلگرامی تبلیغ است؟\n\n"
        f"{text[:1500]}\n\n"
        'با این قالب پاسخ بده: {"ad": true|false, "why": "دلیل کوتاه فارسی"}',
        role=ai.ROLE_SMALL,
        system=(
            "تو پست‌های کانال‌های تلگرام فارسی را دسته‌بندی می‌کنی. «تبلیغ» "
            "یعنی پستی که هدفش فروش چیزی، معرفی کانال دیگر، یا جذب مشتری "
            "است. خبر، آموزش و محتوای عادی تبلیغ نیستند — حتی اگر اسم "
            "محصول یا قیمتی در آن‌ها باشد."
        ),
        max_tokens=200,
    )
    if parsed is None or "ad" not in parsed:
        return AdVerdict(fallback.is_ad, fallback.reason, usage, asked_model=True)

    why = str(parsed.get("why", "")).strip()[:120] or "تشخیص مدل"
    return AdVerdict(bool(parsed["ad"]), why, usage, asked_model=True)


async def image_is_ad(image_url: str) -> AdVerdict | None:
    """آیا تبلیغ داخل خودِ تصویر است؟

    تبلیغ‌های داخل عکس از همه‌ی فیلترهای متنی رد می‌شوند، چون در متن پست
    هیچ ردی ندارند. این تنها راه گرفتنشان است.
    """
    if not ai.configured() or not image_url:
        return None

    reply = await ai.chat(
        "آیا در این تصویر تبلیغ، شماره تماس، آیدی کانال دیگر، شماره کارت "
        "یا بنر تبلیغاتی دیده می‌شود؟ "
        'فقط با این قالب پاسخ بده: {"ad": true|false, "why": "دلیل کوتاه"}',
        role=ai.ROLE_VISION,
        image_url=image_url,
        temperature=0,
        max_tokens=200,
    )
    if reply is None:
        return None

    lowered = reply.text.lower()
    found = '"ad": true' in lowered or '"ad":true' in lowered
    why = reply.text.strip()[:120]
    return AdVerdict(found, why or "بررسی تصویر", reply.usage, asked_model=True)


# ── مسیریابی موضوعی ─────────────────────────────────────────────────


async def route_topic(text: str, topics: list[str]) -> Outcome | None:
    """می‌گوید این پست به کدام موضوع می‌خورد.

    مسیریابی کلیدواژه‌ای فقط کلمه را می‌بیند؛ این معنا را می‌بیند. پستی
    درباره‌ی «تخفیف نوروزی» به موضوع «فروش» می‌رود حتی اگر کلمه‌ی «فروش»
    در آن نباشد.

    خروجی یکی از خودِ `topics` است یا `None` — هرگز موضوعِ ازخودساخته.
    """
    if not ai.configured() or not text.strip() or len(topics) < 2:
        return None

    listing = "\n".join(f"- {topic}" for topic in topics)
    parsed, usage = await ai.ask_json(
        f"این پست به کدام‌یک از این موضوع‌ها می‌خورد؟\n{listing}\n\n"
        f"پست:\n{text[:1200]}\n\n"
        'قالب پاسخ: {"topic": "یکی از موضوع‌های بالا، عیناً"}',
        role=ai.ROLE_SMALL,
        system=(
            "تو پست‌ها را به موضوع‌ها نسبت می‌دهی. فقط از فهرست داده‌شده "
            "انتخاب کن و عیناً همان را بنویس. اگر هیچ‌کدام نمی‌خورد، "
            'مقدار "" بگذار.'
        ),
        max_tokens=120,
    )
    if parsed is None:
        return None

    picked = str(parsed.get("topic", "")).strip()
    # موضوعِ ازخودساخته را نمی‌پذیریم؛ مسیریابی به کانالی که وجود ندارد
    # بدتر از مسیریابی نکردن است.
    if picked not in topics:
        return Outcome(text="", usage=usage)
    return Outcome(text=picked, usage=usage)


# ── تکراری معنایی، دو مرحله‌ای ──────────────────────────────────────


def needs_semantic_check(new_hash: int, old_hash: int) -> bool:
    """آیا این جفت در بازه‌ی مرزی SimHash هستند؟

    زیر `UNSURE_LOW` خودِ SimHash قطعی می‌گوید یکی‌اند و بالای
    `UNSURE_HIGH` قطعی می‌گوید نیستند. فقط بینشان ارزش پرسیدن از مدل را
    دارد — و همین است که هزینه را پایین نگه می‌دارد.
    """
    gap = dedupe.distance(new_hash, old_hash)
    return UNSURE_LOW < gap <= UNSURE_HIGH


async def same_story(text: str, candidates: list[str]) -> tuple[int | None, ai.Usage]:
    """آیا این متن همان خبرِ یکی از نامزدهاست؟

    خروجی: (نمایه‌ی نامزدِ منطبق یا None، مصرف توکن).

    نامزدها باید از پیش با `needs_semantic_check` غربال شده باشند؛ این
    تابع خودش غربال نمی‌کند چون هش‌ها را در اختیار ندارد.
    """
    if not ai.configured() or not text.strip() or not candidates:
        return None, ai.Usage()

    vectors = await ai.embed([text, *candidates])
    # مدل‌های embedding مصرف را در پاسخ برمی‌گردانند ولی `ai.embed` فقط
    # بردارها را می‌دهد؛ هزینه‌شان در برابر مدل متنی ناچیز است.
    if not vectors or len(vectors) != len(candidates) + 1:
        return None, ai.Usage()

    head, rest = vectors[0], vectors[1:]
    best, best_score = None, 0.0
    for index, vector in enumerate(rest):
        score = ai.similarity(head, vector)
        if score > best_score:
            best, best_score = index, score

    if best is None or best_score < SAME_STORY_AT:
        return None, ai.Usage()
    return best, ai.Usage()
