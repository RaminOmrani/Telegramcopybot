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
LANGS = ("fa", "en", "ar", "ru", "tr", "uz", "hi", "id", "pt")

LANG_NAMES = {
    "fa": "🇮🇷 فارسی",
    "en": "🇬🇧 English",
    "ar": "🇸🇦 العربية",
    "ru": "🇷🇺 Русский",
    "tr": "🇹🇷 Türkçe",
    "uz": "🇺🇿 Oʻzbekcha",
    "hi": "🇮🇳 हिन्दी",
    "id": "🇮🇩 Indonesia",
    "pt": "🇧🇷 Português",
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
    "menu.tasks": {
        "fa": "📋 کارهای کپی", "en": "📋 Copy jobs", "ar": "📋 مهام النسخ",
        "ru": "📋 Задачи копирования", "tr": "📋 Kopyalama işleri",
        "uz": "📋 Nusxalash vazifalari", "hi": "📋 कॉपी कार्य",
        "id": "📋 Tugas salin", "pt": "📋 Tarefas de cópia",
    },
    "menu.new_task": {
        "fa": "➕ کار جدید", "en": "➕ New job", "ar": "➕ مهمة جديدة",
        "ru": "➕ Новая задача", "tr": "➕ Yeni iş", "uz": "➕ Yangi vazifa",
        "hi": "➕ नया कार्य", "id": "➕ Tugas baru", "pt": "➕ Nova tarefa",
    },
    "menu.forward": {
        "fa": "↪️ فوروارد پیشرفته", "en": "↪️ Manual forward",
        "ar": "↪️ إعادة توجيه يدوية", "ru": "↪️ Ручная пересылка",
        "tr": "↪️ Elle iletme", "uz": "↪️ Qoʻlda yuborish",
        "hi": "↪️ मैनुअल फ़ॉरवर्ड", "id": "↪️ Teruskan manual",
        "pt": "↪️ Encaminhar manual",
    },
    "menu.account": {
        "fa": "👤 حساب کاربری", "en": "👤 Account", "ar": "👤 الحساب",
        "ru": "👤 Аккаунт", "tr": "👤 Hesap", "uz": "👤 Hisob",
        "hi": "👤 खाता", "id": "👤 Akun", "pt": "👤 Conta",
    },
    "menu.plans": {
        "fa": "💳 خرید اشتراک", "en": "💳 Buy a plan", "ar": "💳 شراء اشتراك",
        "ru": "💳 Купить тариф", "tr": "💳 Abonelik al",
        "uz": "💳 Tarif sotib olish", "hi": "💳 प्लान खरीदें",
        "id": "💳 Beli paket", "pt": "💳 Comprar plano",
    },
    "menu.wallet": {
        "fa": "👛 کیف پول و دعوت", "en": "👛 Wallet & referrals",
        "ar": "👛 المحفظة والدعوات", "ru": "👛 Кошелёк и рефералы",
        "tr": "👛 Cüzdan ve davet", "uz": "👛 Hamyon va takliflar",
        "hi": "👛 वॉलेट और रेफ़रल", "id": "👛 Dompet & referal",
        "pt": "👛 Carteira e indicações",
    },
    "menu.help": {
        "fa": "📚 راهنما", "en": "📚 Guide", "ar": "📚 الدليل",
        "ru": "📚 Руководство", "tr": "📚 Rehber", "uz": "📚 Qoʻllanma",
        "hi": "📚 गाइड", "id": "📚 Panduan", "pt": "📚 Guia",
    },
    "menu.support": {
        "fa": "🛟 پشتیبانی", "en": "🛟 Support", "ar": "🛟 الدعم",
        "ru": "🛟 Поддержка", "tr": "🛟 Destek", "uz": "🛟 Yordam",
        "hi": "🛟 सहायता", "id": "🛟 Dukungan", "pt": "🛟 Suporte",
    },
    "menu.placeholder": {
        "fa": "یک گزینه را انتخاب کنید…", "en": "Choose an option…",
        "ar": "اختر خياراً…", "ru": "Выберите пункт…", "tr": "Bir seçenek seçin…",
        "uz": "Variantni tanlang…", "hi": "एक विकल्प चुनें…",
        "id": "Pilih opsi…", "pt": "Escolha uma opção…",
    },
    # ------------------------------------------------------- خوش‌آمد
    "start.title": {
        "fa": "🤖 <b>ربات کپی محتوای تلگرام</b>",
        "en": "🤖 <b>Telegram Content Copier</b>",
        "ar": "🤖 <b>بوت نسخ محتوى تلجرام</b>",
        "ru": "🤖 <b>Копировщик контента Telegram</b>",
        "tr": "🤖 <b>Telegram İçerik Kopyalayıcı</b>",
        "uz": "🤖 <b>Telegram kontent nusxalagichi</b>",
        "hi": "🤖 <b>टेलीग्राम कंटेंट कॉपियर</b>",
        "id": "🤖 <b>Penyalin Konten Telegram</b>",
        "pt": "🤖 <b>Copiador de Conteúdo do Telegram</b>",
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
        "ru": (
            "Публикуйте посты любого канала у себя — мгновенно, автоматически "
            "и с нужными вам правками. <b>Быть админом исходного канала не "
            "требуется.</b>"
        ),
        "tr": (
            "Herhangi bir kanalın gönderilerini kendi kanalınızda yayınlayın — "
            "anında, otomatik ve istediğiniz gibi düzenlenmiş. "
            "<b>Kaynak kanalda yönetici olmanıza gerek yok.</b>"
        ),
        "uz": (
            "Istalgan kanalning postlarini oʻz kanalingizda chop eting — "
            "bir zumda, avtomatik va oʻzingiz xohlagandek tahrirlangan holda. "
            "<b>Manba kanalda admin boʻlishingiz shart emas.</b>"
        ),
        "hi": (
            "किसी भी चैनल की पोस्ट अपने चैनल में दोबारा प्रकाशित करें — तुरंत, "
            "अपने आप, और आपकी पसंद के अनुसार संपादित। <b>स्रोत चैनल का एडमिन "
            "होना ज़रूरी नहीं है।</b>"
        ),
        "id": (
            "Terbitkan ulang postingan channel mana pun di channel Anda — "
            "seketika, otomatis, dan disunting sesuai keinginan Anda. "
            "<b>Anda tidak perlu menjadi admin channel sumber.</b>"
        ),
        "pt": (
            "Republique as postagens de qualquer canal no seu — na hora, "
            "automaticamente e editadas do seu jeito. <b>Você não precisa ser "
            "admin do canal de origem.</b>"
        ),
    },
    "start.features_title": {
        "fa": "<b>امکانات اصلی</b>", "en": "<b>What it does</b>",
        "ar": "<b>الإمكانيات</b>", "ru": "<b>Возможности</b>",
        "tr": "<b>Neler yapar</b>", "uz": "<b>Imkoniyatlar</b>",
        "hi": "<b>यह क्या करता है</b>", "id": "<b>Fiturnya</b>",
        "pt": "<b>O que ele faz</b>",
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
        "ru": (
            "  ⚡️ Мгновенное копирование из каналов и групп\n"
            "  📤 Публикация сразу в несколько каналов, у каждого своя подпись\n"
            "  ✂️ Замена слов, удаление ссылок и хэштегов\n"
            "  🖋 Удаление ника источника и подстановка вашего\n"
            "  🚦 Умный фильтр рекламы\n"
            "  💧 Водяные знаки на изображениях\n"
            "  🕓 Копирование старых постов"
        ),
        "tr": (
            "  ⚡️ Kanal ve gruplardan anında kopyalama\n"
            "  📤 Aynı anda birkaç kanala yayın, her birine özel imza\n"
            "  ✂️ Kelime değiştirme, bağlantı ve etiket temizleme\n"
            "  🖋 Kaynak kullanıcı adını kaldırıp kendinizinkini koyma\n"
            "  🚦 Akıllı reklam filtresi\n"
            "  💧 Görsellere filigran\n"
            "  🕓 Eski gönderileri kopyalama"
        ),
        "uz": (
            "  ⚡️ Kanal va guruhlardan bir zumda nusxalash\n"
            "  📤 Bir vaqtda bir nechta kanalga chop etish, har biriga oʻz imzosi\n"
            "  ✂️ Soʻzlarni almashtirish, havola va xeshteglarni olib tashlash\n"
            "  🖋 Manba nomini olib tashlab, oʻzingiznikini qoʻyish\n"
            "  🚦 Aqlli reklama filtri\n"
            "  💧 Rasmlarga suv belgisi\n"
            "  🕓 Eski postlarni nusxalash"
        ),
        "hi": (
            "  ⚡️ चैनल और ग्रुप से तुरंत कॉपी\n"
            "  📤 एक साथ कई चैनलों पर प्रकाशन, हर एक के अपने हस्ताक्षर\n"
            "  ✂️ शब्द बदलना, लिंक और हैशटैग हटाना\n"
            "  🖋 स्रोत का हैंडल हटाकर अपना लगाना\n"
            "  🚦 स्मार्ट विज्ञापन फ़िल्टर\n"
            "  💧 छवियों पर वॉटरमार्क\n"
            "  🕓 पुरानी पोस्ट कॉपी करना"
        ),
        "id": (
            "  ⚡️ Penyalinan instan dari channel dan grup\n"
            "  📤 Terbit ke beberapa channel sekaligus, masing-masing dengan tanda tangannya\n"
            "  ✂️ Ganti kata, hapus tautan dan tagar\n"
            "  🖋 Hapus nama pengguna sumber dan pasang milik Anda\n"
            "  🚦 Filter iklan cerdas\n"
            "  💧 Watermark gambar\n"
            "  🕓 Salin postingan lama"
        ),
        "pt": (
            "  ⚡️ Cópia instantânea de canais e grupos\n"
            "  📤 Publique em vários canais de uma vez, cada um com sua assinatura\n"
            "  ✂️ Substituir palavras, remover links e hashtags\n"
            "  🖋 Remover o @ da origem e colocar o seu\n"
            "  🚦 Filtro inteligente de anúncios\n"
            "  💧 Marca d'água em imagens\n"
            "  🕓 Copiar posts antigos"
        ),
    },
    "start.newcomer": {
        "fa": "🚀 <b>تازه‌کارید؟</b> «📚 راهنما» را بزنید — در سه گام راه می‌افتید.",
        "en": "🚀 <b>New here?</b> Tap «📚 Guide» — three steps and you're running.",
        "ar": "🚀 <b>جديد هنا؟</b> اضغط «📚 الدليل» — ثلاث خطوات وتنطلق.",
        "ru": "🚀 <b>Впервые здесь?</b> Нажмите «📚 Руководство» — три шага и всё готово.",
        "tr": "🚀 <b>Yeni misiniz?</b> «📚 Rehber»e dokunun — üç adımda hazırsınız.",
        "uz": "🚀 <b>Yangimisiz?</b> «📚 Qoʻllanma»ni bosing — uch qadamda tayyor.",
        "hi": "🚀 <b>नए हैं?</b> «📚 गाइड» दबाएँ — तीन चरणों में शुरू।",
        "id": "🚀 <b>Baru di sini?</b> Ketuk «📚 Panduan» — tiga langkah dan siap.",
        "pt": "🚀 <b>Novo por aqui?</b> Toque em «📚 Guia» — três passos e pronto.",
    },
    "start.cta": {
        "fa": "از دکمه‌های زیر شروع کنید 👇",
        "en": "Start with the buttons below 👇",
        "ar": "ابدأ من الأزرار في الأسفل 👇",
        "ru": "Начните с кнопок ниже 👇",
        "tr": "Aşağıdaki düğmelerle başlayın 👇",
        "uz": "Quyidagi tugmalardan boshlang 👇",
        "hi": "नीचे दिए बटनों से शुरू करें 👇",
        "id": "Mulai dari tombol di bawah 👇",
        "pt": "Comece pelos botões abaixo 👇",
    },
    # ------------------------------------------------------ انتخاب زبان
    "lang.ask": {
        "fa": "🌍 زبان خود را انتخاب کنید:", "en": "🌍 Choose your language:",
        "ar": "🌍 اختر لغتك:", "ru": "🌍 Выберите язык:",
        "tr": "🌍 Dilinizi seçin:", "uz": "🌍 Tilingizni tanlang:",
        "hi": "🌍 अपनी भाषा चुनें:", "id": "🌍 Pilih bahasa Anda:",
        "pt": "🌍 Escolha seu idioma:",
    },
    "lang.changed": {
        "fa": "✅ زبان به فارسی تغییر کرد.", "en": "✅ Language set to English.",
        "ar": "✅ تم ضبط اللغة على العربية.", "ru": "✅ Язык изменён на русский.",
        "tr": "✅ Dil Türkçe olarak ayarlandı.", "uz": "✅ Til oʻzbekchaga oʻzgartirildi.",
        "hi": "✅ भाषा हिन्दी पर सेट हो गई।", "id": "✅ Bahasa diatur ke Indonesia.",
        "pt": "✅ Idioma definido para Português.",
    },
    "lang.button": {
        "fa": "🌍 زبان", "en": "🌍 Language", "ar": "🌍 اللغة", "ru": "🌍 Язык",
        "tr": "🌍 Dil", "uz": "🌍 Til", "hi": "🌍 भाषा", "id": "🌍 Bahasa",
        "pt": "🌍 Idioma",
    },
    "lang.partial": {
        "fa": "",
        "en": (
            "<i>The admin panel is Persian-only; everything you need day to "
            "day is translated.</i>"
        ),
        "ar": (
            "<i>لوحة الإدارة بالفارسية فقط؛ وكل ما تحتاجه يومياً مترجم.</i>"
        ),
        "ru": (
            "<i>Админ-панель только на персидском; всё, что нужно вам "
            "ежедневно, переведено.</i>"
        ),
        "tr": (
            "<i>Yönetim paneli yalnızca Farsça; günlük ihtiyacınız olan her "
            "şey çevrildi.</i>"
        ),
        "uz": (
            "<i>Admin paneli faqat forscha; kundalik kerak boʻladigan "
            "hamma narsa tarjima qilingan.</i>"
        ),
        "hi": (
            "<i>एडमिन पैनल केवल फ़ारसी में है; रोज़मर्रा की ज़रूरत की हर चीज़ "
            "अनूदित है।</i>"
        ),
        "id": (
            "<i>Panel admin hanya dalam bahasa Persia; semua yang Anda "
            "butuhkan sehari-hari sudah diterjemahkan.</i>"
        ),
        "pt": (
            "<i>O painel de administração está apenas em persa; tudo o que "
            "você usa no dia a dia está traduzido.</i>"
        ),
    },
    # -------------------------------------------------------- عمومی
    "common.back": {
        "fa": "🔙 بازگشت", "en": "🔙 Back", "ar": "🔙 رجوع", "ru": "🔙 Назад",
        "tr": "🔙 Geri", "uz": "🔙 Orqaga", "hi": "🔙 वापस", "id": "🔙 Kembali",
        "pt": "🔙 Voltar",
    },
    "common.cancel": {
        "fa": "انصراف: /cancel", "en": "Cancel: /cancel", "ar": "إلغاء: /cancel",
        "ru": "Отмена: /cancel", "tr": "İptal: /cancel", "uz": "Bekor qilish: /cancel",
        "hi": "रद्द करें: /cancel", "id": "Batal: /cancel", "pt": "Cancelar: /cancel",
    },
    "common.saved": {
        "fa": "✅ ذخیره شد.", "en": "✅ Saved.", "ar": "✅ تم الحفظ.",
        "ru": "✅ Сохранено.", "tr": "✅ Kaydedildi.", "uz": "✅ Saqlandi.",
        "hi": "✅ सहेजा गया।", "id": "✅ Tersimpan.", "pt": "✅ Salvo.",
    },
    "common.no_access": {
        "fa": "دسترسی ندارید", "en": "You don't have access", "ar": "لا تملك صلاحية",
        "ru": "Нет доступа", "tr": "Erişiminiz yok", "uz": "Ruxsatingiz yoʻq",
        "hi": "आपके पास पहुँच नहीं है", "id": "Anda tidak punya akses",
        "pt": "Você não tem acesso",
    },
    "common.on": {
        "fa": "✅ روشن", "en": "✅ On", "ar": "✅ مُفعّل", "ru": "✅ Вкл",
        "tr": "✅ Açık", "uz": "✅ Yoniq", "hi": "✅ चालू", "id": "✅ Aktif",
        "pt": "✅ Ligado",
    },
    "common.off": {
        "fa": "❌ خاموش", "en": "❌ Off", "ar": "❌ مُعطّل", "ru": "❌ Выкл",
        "tr": "❌ Kapalı", "uz": "❌ Oʻchiq", "hi": "❌ बंद", "id": "❌ Nonaktif",
        "pt": "❌ Desligado",
    },
    "common.error": {
        "fa": "⚠️ خطایی رخ داد. دوباره تلاش کنید یا /start را بزنید.",
        "en": "⚠️ Something went wrong. Try again or send /start.",
        "ar": "⚠️ حدث خطأ. حاول مجدداً أو أرسل /start.",
        "ru": "⚠️ Что-то пошло не так. Попробуйте снова или отправьте /start.",
        "tr": "⚠️ Bir şeyler ters gitti. Tekrar deneyin veya /start gönderin.",
        "uz": "⚠️ Xatolik yuz berdi. Qayta urinib koʻring yoki /start yuboring.",
        "hi": "⚠️ कुछ गड़बड़ हुई। दोबारा कोशिश करें या /start भेजें।",
        "id": "⚠️ Terjadi kesalahan. Coba lagi atau kirim /start.",
        "pt": "⚠️ Algo deu errado. Tente novamente ou envie /start.",
    },
    # ------------------------------------------------------ حساب کاربری
    "account.title": {
        "fa": "👤 <b>حساب کاربری</b>", "en": "👤 <b>Your account</b>",
        "ar": "👤 <b>حسابك</b>", "ru": "👤 <b>Ваш аккаунт</b>",
        "tr": "👤 <b>Hesabınız</b>", "uz": "👤 <b>Hisobingiz</b>",
        "hi": "👤 <b>आपका खाता</b>", "id": "👤 <b>Akun Anda</b>",
        "pt": "👤 <b>Sua conta</b>",
    },
    "account.connect": {
        "fa": "🔐 اتصال اکانت", "en": "🔐 Connect account", "ar": "🔐 ربط الحساب",
        "ru": "🔐 Подключить аккаунт", "tr": "🔐 Hesabı bağla",
        "uz": "🔐 Hisobni ulash", "hi": "🔐 खाता जोड़ें",
        "id": "🔐 Hubungkan akun", "pt": "🔐 Conectar conta",
    },
    "account.logout": {
        "fa": "🚪 خروج از حساب", "en": "🚪 Sign out", "ar": "🚪 تسجيل الخروج",
        "ru": "🚪 Выйти", "tr": "🚪 Çıkış yap", "uz": "🚪 Chiqish",
        "hi": "🚪 साइन आउट", "id": "🚪 Keluar", "pt": "🚪 Sair",
    },
    "account.quota": {
        "fa": "📊 سهمیه و اعتبار من", "en": "📊 My quota & credit",
        "ar": "📊 حصتي ورصيدي", "ru": "📊 Мои лимиты и кредиты",
        "tr": "📊 Kotam ve kredim", "uz": "📊 Kvota va kreditim",
        "hi": "📊 मेरा कोटा और क्रेडिट", "id": "📊 Kuota & kredit saya",
        "pt": "📊 Minha cota e créditos",
    },
    "account.logs": {
        "fa": "🧾 گزارش فعالیت", "en": "🧾 Activity log", "ar": "🧾 سجل النشاط",
        "ru": "🧾 Журнал действий", "tr": "🧾 Etkinlik günlüğü",
        "uz": "🧾 Faoliyat jurnali", "hi": "🧾 गतिविधि लॉग",
        "id": "🧾 Log aktivitas", "pt": "🧾 Registro de atividade",
    },
    "account.digest": {
        "fa": "📬 خلاصه‌ی روزانه", "en": "📬 Daily summary", "ar": "📬 الملخص اليومي",
        "ru": "📬 Ежедневная сводка", "tr": "📬 Günlük özet",
        "uz": "📬 Kunlik xulosa", "hi": "📬 दैनिक सारांश",
        "id": "📬 Ringkasan harian", "pt": "📬 Resumo diário",
    },
    "account.level_simple": {
        "fa": "🧭 منوها: ساده", "en": "🧭 Menus: simple", "ar": "🧭 القوائم: مبسطة",
        "ru": "🧭 Меню: простое", "tr": "🧭 Menüler: basit",
        "uz": "🧭 Menyular: oddiy", "hi": "🧭 मेन्यू: सरल",
        "id": "🧭 Menu: sederhana", "pt": "🧭 Menus: simples",
    },
    "account.level_pro": {
        "fa": "🧭 منوها: پیشرفته", "en": "🧭 Menus: advanced",
        "ar": "🧭 القوائم: متقدمة", "ru": "🧭 Меню: расширенное",
        "tr": "🧭 Menüler: gelişmiş", "uz": "🧭 Menyular: kengaytirilgan",
        "hi": "🧭 मेन्यू: उन्नत", "id": "🧭 Menu: lanjutan",
        "pt": "🧭 Menus: avançado",
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
        "ru": (
            "🔐 Сначала подключите свой аккаунт Telegram.\n"
            "«👤 Аккаунт» → «🔐 Подключить аккаунт»"
        ),
        "tr": (
            "🔐 Önce Telegram hesabınızı bağlamalısınız.\n"
            "«👤 Hesap» → «🔐 Hesabı bağla»"
        ),
        "uz": (
            "🔐 Avval Telegram hisobingizni ulashingiz kerak.\n"
            "«👤 Hisob» → «🔐 Hisobni ulash»"
        ),
        "hi": (
            "🔐 पहले अपना टेलीग्राम खाता जोड़ना होगा।\n"
            "«👤 खाता» → «🔐 खाता जोड़ें»"
        ),
        "id": (
            "🔐 Anda perlu menghubungkan akun Telegram terlebih dahulu.\n"
            "«👤 Akun» → «🔐 Hubungkan akun»"
        ),
        "pt": (
            "🔐 Você precisa conectar sua conta do Telegram primeiro.\n"
            "«👤 Conta» → «🔐 Conectar conta»"
        ),
    },
    "need.subscription": {
        "fa": "⛔️ اشتراک فعالی ندارید.\nبرای ادامه «💳 خرید اشتراک» را بزنید.",
        "en": "⛔️ You don't have an active plan.\nTap «💳 Buy a plan» to continue.",
        "ar": "⛔️ ليس لديك اشتراك فعّال.\nاضغط «💳 شراء اشتراك» للمتابعة.",
        "ru": "⛔️ У вас нет активного тарифа.\nНажмите «💳 Купить тариф», чтобы продолжить.",
        "tr": "⛔️ Aktif aboneliğiniz yok.\nDevam etmek için «💳 Abonelik al»a dokunun.",
        "uz": "⛔️ Faol tarifingiz yoʻq.\nDavom etish uchun «💳 Tarif sotib olish»ni bosing.",
        "hi": "⛔️ आपके पास सक्रिय प्लान नहीं है।\nजारी रखने के लिए «💳 प्लान खरीदें» दबाएँ।",
        "id": "⛔️ Anda tidak punya paket aktif.\nKetuk «💳 Beli paket» untuk melanjutkan.",
        "pt": "⛔️ Você não tem um plano ativo.\nToque em «💳 Comprar plano» para continuar.",
    },
    # ------------------------------------------------------------ کارها
    "task.none": {
        "fa": "هنوز کاری نساخته‌اید. «➕ کار جدید» را بزنید.",
        "en": "No jobs yet. Tap «➕ New job».",
        "ar": "لا توجد مهام بعد. اضغط «➕ مهمة جديدة».",
        "ru": "Задач пока нет. Нажмите «➕ Новая задача».",
        "tr": "Henüz iş yok. «➕ Yeni iş»e dokunun.",
        "uz": "Hozircha vazifa yoʻq. «➕ Yangi vazifa»ni bosing.",
        "hi": "अभी कोई कार्य नहीं। «➕ नया कार्य» दबाएँ।",
        "id": "Belum ada tugas. Ketuk «➕ Tugas baru».",
        "pt": "Nenhuma tarefa ainda. Toque em «➕ Nova tarefa».",
    },
    "task.new": {
        "fa": "➕ ساخت کار جدید", "en": "➕ Create a job", "ar": "➕ إنشاء مهمة",
        "ru": "➕ Создать задачу", "tr": "➕ İş oluştur", "uz": "➕ Vazifa yaratish",
        "hi": "➕ कार्य बनाएँ", "id": "➕ Buat tugas", "pt": "➕ Criar tarefa",
    },
    "task.ask_source": {
        "fa": "📥 آیدی یا لینک کانال <b>مبدا</b> را بفرستید.",
        "en": "📥 Send the <b>source</b> channel's @username or link.",
        "ar": "📥 أرسل معرّف أو رابط القناة <b>المصدر</b>.",
        "ru": "📥 Отправьте @username или ссылку <b>исходного</b> канала.",
        "tr": "📥 <b>Kaynak</b> kanalın @kullanıcı adını veya bağlantısını gönderin.",
        "uz": "📥 <b>Manba</b> kanalning @foydalanuvchi nomi yoki havolasini yuboring.",
        "hi": "📥 <b>स्रोत</b> चैनल का @यूज़रनेम या लिंक भेजें।",
        "id": "📥 Kirim @username atau tautan channel <b>sumber</b>.",
        "pt": "📥 Envie o @usuário ou link do canal de <b>origem</b>.",
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
        "ru": (
            "📤 Отправьте @username или ссылку <b>целевого</b> канала.\n"
            "⚠️ Ваш аккаунт должен иметь право публиковать там."
        ),
        "tr": (
            "📤 <b>Hedef</b> kanalın @kullanıcı adını veya bağlantısını gönderin.\n"
            "⚠️ Hesabınızın orada gönderi yetkisi olmalı."
        ),
        "uz": (
            "📤 <b>Maqsad</b> kanalning @foydalanuvchi nomi yoki havolasini yuboring.\n"
            "⚠️ Hisobingizda u yerda post joylash huquqi boʻlishi kerak."
        ),
        "hi": (
            "📤 <b>गंतव्य</b> चैनल का @यूज़रनेम या लिंक भेजें।\n"
            "⚠️ आपके खाते को वहाँ पोस्ट करने की अनुमति होनी चाहिए।"
        ),
        "id": (
            "📤 Kirim @username atau tautan channel <b>tujuan</b>.\n"
            "⚠️ Akun Anda harus punya izin memposting di sana."
        ),
        "pt": (
            "📤 Envie o @usuário ou link do canal de <b>destino</b>.\n"
            "⚠️ Sua conta precisa ter permissão para publicar lá."
        ),
    },
    # ------------------------------------------------------------ خرید
    "plans.title": {
        "fa": "💳 <b>طرح‌های اشتراک</b>", "en": "💳 <b>Subscription plans</b>",
        "ar": "💳 <b>خطط الاشتراك</b>", "ru": "💳 <b>Тарифные планы</b>",
        "tr": "💳 <b>Abonelik planları</b>", "uz": "💳 <b>Obuna tariflari</b>",
        "hi": "💳 <b>सदस्यता प्लान</b>", "id": "💳 <b>Paket langganan</b>",
        "pt": "💳 <b>Planos de assinatura</b>",
    },
    "plans.choose": {
        "fa": "یک طرح را انتخاب کنید:", "en": "Pick a plan:", "ar": "اختر خطة:",
        "ru": "Выберите тариф:", "tr": "Bir plan seçin:", "uz": "Tarifni tanlang:",
        "hi": "एक प्लान चुनें:", "id": "Pilih paket:", "pt": "Escolha um plano:",
    },
    # ------------------------------------------------------- نام طرح‌ها
    # فقط وقتی استفاده می‌شوند که ادمین نام طرح را از پنل عوض نکرده باشد؛
    # نام دست‌ساز ادمین به هر زبانی همان‌طور که نوشته شده می‌ماند.
    "plan.trial": {
        "fa": "اشتراک آزمایشی", "en": "Trial plan", "ar": "الاشتراك التجريبي",
        "ru": "Пробный тариф", "tr": "Deneme planı", "uz": "Sinov tarifi",
        "hi": "ट्रायल प्लान", "id": "Paket coba", "pt": "Plano de teste",
    },
    "plan.week": {
        "fa": "اشتراک ۷ روزه", "en": "7-day plan", "ar": "اشتراك ٧ أيام",
        "ru": "Тариф на 7 дней", "tr": "7 günlük plan", "uz": "7 kunlik tarif",
        "hi": "7-दिन प्लान", "id": "Paket 7 hari", "pt": "Plano de 7 dias",
    },
    "plan.two_week": {
        "fa": "اشتراک ۱۴ روزه", "en": "14-day plan", "ar": "اشتراك ١٤ يوماً",
        "ru": "Тариф на 14 дней", "tr": "14 günlük plan", "uz": "14 kunlik tarif",
        "hi": "14-दिन प्लान", "id": "Paket 14 hari", "pt": "Plano de 14 dias",
    },
    "plan.month": {
        "fa": "اشتراک ۳۰ روزه", "en": "30-day plan", "ar": "اشتراك ٣٠ يوماً",
        "ru": "Тариф на 30 дней", "tr": "30 günlük plan", "uz": "30 kunlik tarif",
        "hi": "30-दिन प्लान", "id": "Paket 30 hari", "pt": "Plano de 30 dias",
    },
    "plan.custom": {
        "fa": "طرح اختصاصی", "en": "Custom plan", "ar": "الخطة المخصصة",
        "ru": "Индивидуальный тариф", "tr": "Özel plan", "uz": "Maxsus tarif",
        "hi": "कस्टम प्लान", "id": "Paket khusus", "pt": "Plano personalizado",
    },
    # -------------------------------------------------- سهمیه و واحدها
    "quota.unlimited": {
        "fa": "نامحدود", "en": "Unlimited", "ar": "غير محدود", "ru": "Без лимита",
        "tr": "Sınırsız", "uz": "Cheksiz", "hi": "असीमित", "id": "Tanpa batas",
        "pt": "Ilimitado",
    },
    "quota.free": {
        "fa": "رایگان", "en": "free", "ar": "مجاناً", "ru": "бесплатно",
        "tr": "ücretsiz", "uz": "bepul", "hi": "मुफ़्त", "id": "gratis",
        "pt": "grátis",
    },
    "quota.none": {
        "fa": "ندارد", "en": "None", "ar": "لا يوجد", "ru": "Нет", "tr": "Yok",
        "uz": "Yoʻq", "hi": "नहीं", "id": "Tidak ada", "pt": "Nenhum",
    },
    "money.toman": {
        "fa": "{amount} تومان", "en": "{amount} toman", "ar": "{amount} تومان",
        "ru": "{amount} туманов", "tr": "{amount} tümen", "uz": "{amount} tuman",
        "hi": "{amount} तोमान", "id": "{amount} toman", "pt": "{amount} tomans",
    },
    "unit.days": {
        "fa": "روز", "en": "days", "ar": "يوم", "ru": "дней", "tr": "gün",
        "uz": "kun", "hi": "दिन", "id": "hari", "pt": "dias",
    },
    "unit.messages": {
        "fa": "پیام", "en": "messages", "ar": "رسالة", "ru": "сообщений",
        "tr": "mesaj", "uz": "xabar", "hi": "संदेश", "id": "pesan",
        "pt": "mensagens",
    },
    "unit.jobs": {
        "fa": "کار", "en": "jobs", "ar": "مهمة", "ru": "задач", "tr": "iş",
        "uz": "vazifa", "hi": "कार्य", "id": "tugas", "pt": "tarefas",
    },
    "unit.dests": {
        "fa": "مقصد", "en": "destinations", "ar": "وجهة", "ru": "каналов",
        "tr": "hedef", "uz": "manzil", "hi": "गंतव्य", "id": "tujuan",
        "pt": "destinos",
    },
    "unit.watermarks": {
        "fa": "واترمارک", "en": "watermarks", "ar": "علامة مائية",
        "ru": "водяных знаков", "tr": "filigran", "uz": "suv belgisi",
        "hi": "वॉटरमार्क", "id": "watermark", "pt": "marcas d'água",
    },
    "unit.history": {
        "fa": "پیام گذشته", "en": "older posts", "ar": "منشور سابق",
        "ru": "старых постов", "tr": "eski gönderi", "uz": "eski post",
        "hi": "पुरानी पोस्ट", "id": "postingan lama", "pt": "posts antigos",
    },
}
