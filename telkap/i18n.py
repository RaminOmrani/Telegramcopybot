"""چندزبانه کردن ربات.

ربات از ابتدا فارسی نوشته شده و متن‌هایش در ده‌ها فایل پخش‌اند. بیرون
کشیدن همه‌شان یک‌جا هم پرریسک است هم بی‌فایده — بخش زیادی از آن‌ها (پنل
مدیریت) فقط شما و همکارانتان می‌بینید.

پس اینجا <b>مسیر اصلی کاربر</b> ترجمه می‌شود: خوش‌آمد، منوی اصلی، حساب
کاربری، خرید، ساخت کار، و پیام‌های پرتکرار. هر کلیدی که ترجمه نداشته
باشد خودبه‌خود فارسی می‌ماند، پس هیچ صفحه‌ای خالی نمی‌شود.

زبان در یک ContextVar نگه داشته می‌شود که میدل‌ور در ابتدای هر پیام
پُرش می‌کند؛ اینطور `t()` را می‌شود هرجا صدا زد بدون اینکه زبان را از
هندلر تا عمق توابع دست‌به‌دست کنیم.
"""
from __future__ import annotations

from contextvars import ContextVar

DEFAULT = "fa"
LANGS = ("fa", "en", "ar")

LANG_NAMES = {
    "fa": "🇮🇷 فارسی",
    "en": "🇬🇧 English",
    "ar": "🇸🇦 العربية",
}

# زبان‌هایی که خط راست‌به‌چپ دارند
RTL = frozenset({"fa", "ar"})

_current: ContextVar[str] = ContextVar("lang", default=DEFAULT)

ARABIC_DIGITS = str.maketrans("0123456789", "٠١٢٣٤٥٦٧٨٩")
PERSIAN_DIGITS = str.maketrans("0123456789", "۰۱۲۳۴۵۶۷۸۹")


def set_current(lang: str) -> None:
    _current.set(normalize(lang))


def current() -> str:
    return _current.get()


def normalize(lang: str | None) -> str:
    value = (lang or "").strip().lower()[:2]
    return value if value in LANGS else DEFAULT


def pick(telegram_code: str | None) -> str:
    """زبان پیشنهادی بر اساس زبان تلگرامِ کاربر.

    فقط برای اولین بار؛ بعدش انتخاب خودِ کاربر ملاک است.
    """
    return normalize(telegram_code)


def num(value, lang: str | None = None) -> str:
    """عدد با ارقام و جداکننده‌ی متناسب با زبان."""
    code = normalize(lang or current())
    text = f"{value:,}" if isinstance(value, int) else str(value)
    if code == "fa":
        return text.replace(",", "،").translate(PERSIAN_DIGITS)
    if code == "ar":
        return text.replace(",", "٬").translate(ARABIC_DIGITS)
    return text


def t(key: str, lang: str | None = None, **kwargs) -> str:
    """متن ترجمه‌شده. اگر ترجمه نبود، فارسی برمی‌گردد."""
    code = normalize(lang or current())
    entry = CATALOG.get(key)
    if entry is None:
        return key                      # کلید ناشناخته: خودِ کلید، تا در چشم بیاید
    text = entry.get(code) or entry.get(DEFAULT) or ""
    return text.format(**kwargs) if kwargs else text


def has(key: str, lang: str) -> bool:
    """آیا این کلید واقعاً به این زبان ترجمه شده؟ (برای تست‌ها)"""
    return bool(CATALOG.get(key, {}).get(normalize(lang)))


# ------------------------------------------------- زبانِ ذخیره‌شده‌ی کاربر
# در هر پیام لازم است؛ زبان هم به‌ندرت عوض می‌شود، پس کش می‌شود.
_cache: dict[int, str] = {}


def forget(user_id: int) -> None:
    _cache.pop(user_id, None)


def reset_cache() -> None:
    _cache.clear()


async def language_of(user_id: int, *, fallback: str | None = None) -> str:
    """زبان کاربر از دیتابیس؛ اگر هنوز کاربری نیست، از زبان تلگرامش."""
    cached = _cache.get(user_id)
    if cached is not None:
        return cached

    from sqlalchemy import select

    from telkap.db import get_session
    from telkap.models import User

    try:
        async with get_session() as db:
            stored = await db.scalar(
                select(User.language).where(User.id == user_id)
            )
    except Exception:
        return pick(fallback)       # پیش از init_db یا در خطای گذرا

    lang = normalize(stored) if stored else pick(fallback)
    _cache[user_id] = lang
    return lang


async def set_language(user_id: int, lang: str) -> str:
    from telkap.db import get_session
    from telkap.models import User

    clean = normalize(lang)
    async with get_session() as db:
        user = await db.get(User, user_id)
        if user is not None:
            user.language = clean
            await db.commit()
    _cache[user_id] = clean
    set_current(clean)
    return clean


# ---------------------------------------------------------------- کاتالوگ
# کلید → {زبان: متن}. فارسی همیشه هست و نقش پشتوانه را دارد.
CATALOG: dict[str, dict[str, str]] = {
    # ---------------------------------------------------- منوی اصلی
    "menu.tasks": {"fa": "📋 کارهای کپی", "en": "📋 Copy jobs", "ar": "📋 مهام النسخ"},
    "menu.new_task": {"fa": "➕ کار جدید", "en": "➕ New job", "ar": "➕ مهمة جديدة"},
    "menu.forward": {
        "fa": "↪️ فوروارد پیشرفته",
        "en": "↪️ Manual forward",
        "ar": "↪️ إعادة توجيه يدوية",
    },
    "menu.account": {"fa": "👤 حساب کاربری", "en": "👤 Account", "ar": "👤 الحساب"},
    "menu.plans": {"fa": "💳 خرید اشتراک", "en": "💳 Buy a plan", "ar": "💳 شراء اشتراك"},
    "menu.wallet": {
        "fa": "👛 کیف پول و دعوت",
        "en": "👛 Wallet & referrals",
        "ar": "👛 المحفظة والدعوات",
    },
    "menu.help": {"fa": "📚 راهنما", "en": "📚 Guide", "ar": "📚 الدليل"},
    "menu.support": {"fa": "🛟 پشتیبانی", "en": "🛟 Support", "ar": "🛟 الدعم"},
    "menu.placeholder": {
        "fa": "یک گزینه را انتخاب کنید…",
        "en": "Choose an option…",
        "ar": "اختر خياراً…",
    },
    # ------------------------------------------------------- خوش‌آمد
    "start.title": {
        "fa": "🤖 <b>ربات کپی محتوای تلگرام</b>",
        "en": "🤖 <b>Telegram Content Copier</b>",
        "ar": "🤖 <b>بوت نسخ محتوى تلجرام</b>",
    },
    "start.pitch": {
        "fa": (
            "پست‌های هر کانالی را به‌صورت خودکار و لحظه‌ای، با تغییرات دلخواه، "
            "در کانال خودتان منتشر کنید — <b>بدون نیاز به ادمین بودن در کانال "
            "مبدا</b>."
        ),
        "en": (
            "Republish any channel's posts in your own channel — instantly, "
            "automatically, and edited the way you want. "
            "<b>You do not need to be an admin of the source channel.</b>"
        ),
        "ar": (
            "أعد نشر منشورات أي قناة في قناتك — فوراً وتلقائياً وبالتعديلات "
            "التي تريدها. <b>لا حاجة لأن تكون مشرفاً في القناة المصدر.</b>"
        ),
    },
    "start.features_title": {
        "fa": "<b>امکانات اصلی</b>",
        "en": "<b>What it does</b>",
        "ar": "<b>الإمكانيات</b>",
    },
    "start.features": {
        "fa": (
            "  ⚡️ کپی لحظه‌ای از کانال یا گروه\n"
            "  📤 انتشار همزمان در چند کانال، با امضای اختصاصی هرکدام\n"
            "  ✂️ جایگزینی کلمات، حذف لینک و هشتگ\n"
            "  🖋 حذف آیدی مبدا و جایگزینی با آیدی شما\n"
            "  🚦 فیلتر هوشمند تبلیغات\n"
            "  💧 واترمارک تصاویر\n"
            "  🕓 کپی پیام‌های گذشته"
        ),
        "en": (
            "  ⚡️ Instant copying from channels and groups\n"
            "  📤 Publish to several channels at once, each with its own sign-off\n"
            "  ✂️ Replace words, strip links and hashtags\n"
            "  🖋 Remove the source handle and put yours instead\n"
            "  🚦 Smart advertising filter\n"
            "  💧 Image watermarks\n"
            "  🕓 Copy older posts"
        ),
        "ar": (
            "  ⚡️ نسخ فوري من القنوات والمجموعات\n"
            "  📤 النشر في عدة قنوات دفعة واحدة، لكل منها توقيعها\n"
            "  ✂️ استبدال الكلمات وحذف الروابط والوسوم\n"
            "  🖋 إزالة معرّف المصدر ووضع معرّفك مكانه\n"
            "  🚦 فلتر ذكي للإعلانات\n"
            "  💧 علامة مائية على الصور\n"
            "  🕓 نسخ المنشورات السابقة"
        ),
    },
    "start.newcomer": {
        "fa": "🚀 <b>تازه‌کارید؟</b> «📚 راهنما» را بزنید — در سه گام راه می‌افتید.",
        "en": "🚀 <b>New here?</b> Tap «📚 Guide» — three steps and you're running.",
        "ar": "🚀 <b>جديد هنا؟</b> اضغط «📚 الدليل» — ثلاث خطوات وتنطلق.",
    },
    "start.cta": {
        "fa": "از دکمه‌های زیر شروع کنید 👇",
        "en": "Start with the buttons below 👇",
        "ar": "ابدأ من الأزرار في الأسفل 👇",
    },
    # ------------------------------------------------------ انتخاب زبان
    "lang.ask": {
        "fa": "🌍 زبان خود را انتخاب کنید:",
        "en": "🌍 Choose your language:",
        "ar": "🌍 اختر لغتك:",
    },
    "lang.changed": {
        "fa": "✅ زبان به فارسی تغییر کرد.",
        "en": "✅ Language set to English.",
        "ar": "✅ تم ضبط اللغة على العربية.",
    },
    "lang.button": {"fa": "🌍 زبان", "en": "🌍 Language", "ar": "🌍 اللغة"},
    "lang.partial": {
        "fa": "",
        "en": (
            "<i>The admin panel and the full guide are Persian-only for now; "
            "everything you need day to day is translated.</i>"
        ),
        "ar": (
            "<i>لوحة الإدارة والدليل الكامل بالفارسية حالياً؛ وكل ما تحتاجه "
            "يومياً مترجم.</i>"
        ),
    },
    # -------------------------------------------------------- عمومی
    "common.back": {"fa": "🔙 بازگشت", "en": "🔙 Back", "ar": "🔙 رجوع"},
    "common.cancel": {"fa": "انصراف: /cancel", "en": "Cancel: /cancel", "ar": "إلغاء: /cancel"},
    "common.saved": {"fa": "✅ ذخیره شد.", "en": "✅ Saved.", "ar": "✅ تم الحفظ."},
    "common.no_access": {
        "fa": "دسترسی ندارید",
        "en": "You don't have access",
        "ar": "لا تملك صلاحية",
    },
    "common.on": {"fa": "✅ روشن", "en": "✅ On", "ar": "✅ مُفعّل"},
    "common.off": {"fa": "❌ خاموش", "en": "❌ Off", "ar": "❌ مُعطّل"},
    "common.error": {
        "fa": "⚠️ خطایی رخ داد. دوباره تلاش کنید یا /start را بزنید.",
        "en": "⚠️ Something went wrong. Try again or send /start.",
        "ar": "⚠️ حدث خطأ. حاول مجدداً أو أرسل /start.",
    },
    # ------------------------------------------------------ حساب کاربری
    "account.title": {
        "fa": "👤 <b>حساب کاربری</b>",
        "en": "👤 <b>Your account</b>",
        "ar": "👤 <b>حسابك</b>",
    },
    "account.connect": {
        "fa": "🔐 اتصال اکانت",
        "en": "🔐 Connect account",
        "ar": "🔐 ربط الحساب",
    },
    "account.logout": {
        "fa": "🚪 خروج از حساب",
        "en": "🚪 Sign out",
        "ar": "🚪 تسجيل الخروج",
    },
    "account.quota": {
        "fa": "📊 سهمیه و اعتبار من",
        "en": "📊 My quota & credit",
        "ar": "📊 حصتي ورصيدي",
    },
    "account.logs": {
        "fa": "🧾 گزارش فعالیت",
        "en": "🧾 Activity log",
        "ar": "🧾 سجل النشاط",
    },
    "account.digest": {
        "fa": "📬 خلاصه‌ی روزانه",
        "en": "📬 Daily summary",
        "ar": "📬 الملخص اليومي",
    },
    "account.level_simple": {
        "fa": "🧭 منوها: ساده",
        "en": "🧭 Menus: simple",
        "ar": "🧭 القوائم: مبسطة",
    },
    "account.level_pro": {
        "fa": "🧭 منوها: پیشرفته",
        "en": "🧭 Menus: advanced",
        "ar": "🧭 القوائم: متقدمة",
    },
    # ---------------------------------------------------- پیام‌های لازم
    "need.login": {
        "fa": (
            "🔐 برای این کار باید اکانت کاربری‌تان را وصل کنید.\n"
            "«👤 حساب کاربری» ← «🔐 اتصال اکانت»"
        ),
        "en": (
            "🔐 You need to connect your Telegram account first.\n"
            "«👤 Account» → «🔐 Connect account»"
        ),
        "ar": (
            "🔐 عليك ربط حسابك في تلجرام أولاً.\n"
            "«👤 الحساب» ← «🔐 ربط الحساب»"
        ),
    },
    "need.subscription": {
        "fa": (
            "⛔️ اشتراک فعالی ندارید.\n"
            "برای ادامه «💳 خرید اشتراک» را بزنید."
        ),
        "en": (
            "⛔️ You don't have an active plan.\n"
            "Tap «💳 Buy a plan» to continue."
        ),
        "ar": (
            "⛔️ ليس لديك اشتراك فعّال.\n"
            "اضغط «💳 شراء اشتراك» للمتابعة."
        ),
    },
    # ------------------------------------------------------------ کارها
    "task.none": {
        "fa": "هنوز کاری نساخته‌اید. «➕ کار جدید» را بزنید.",
        "en": "No jobs yet. Tap «➕ New job».",
        "ar": "لا توجد مهام بعد. اضغط «➕ مهمة جديدة».",
    },
    "task.new": {
        "fa": "➕ ساخت کار جدید",
        "en": "➕ Create a job",
        "ar": "➕ إنشاء مهمة",
    },
    "task.ask_source": {
        "fa": "📥 آیدی یا لینک کانال <b>مبدا</b> را بفرستید.",
        "en": "📥 Send the <b>source</b> channel's @username or link.",
        "ar": "📥 أرسل معرّف أو رابط القناة <b>المصدر</b>.",
    },
    "task.ask_dest": {
        "fa": (
            "📤 آیدی یا لینک کانال <b>مقصد</b> را بفرستید.\n"
            "⚠️ اکانت شما باید در آن اجازه‌ی ارسال داشته باشد."
        ),
        "en": (
            "📤 Send the <b>destination</b> channel's @username or link.\n"
            "⚠️ Your account must be allowed to post there."
        ),
        "ar": (
            "📤 أرسل معرّف أو رابط القناة <b>الهدف</b>.\n"
            "⚠️ يجب أن يكون لحسابك صلاحية النشر فيها."
        ),
    },
    # ------------------------------------------------------------ خرید
    "plans.title": {
        "fa": "💳 <b>طرح‌های اشتراک</b>",
        "en": "💳 <b>Subscription plans</b>",
        "ar": "💳 <b>خطط الاشتراك</b>",
    },
    "plans.choose": {
        "fa": "یک طرح را انتخاب کنید:",
        "en": "Pick a plan:",
        "ar": "اختر خطة:",
    },
}
