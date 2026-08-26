"""راهنمای ربات به زبان‌های غیرفارسی.

متن فارسی در `handlers/guide.py` می‌ماند چون به قیمت و سقف طرح‌ها وابسته
است و از روی همان مقادیر ساخته می‌شود. اینجا فقط ترجمه‌ها هستند: متنِ
ثابت، بدون محاسبه.

هر بخشی که هنوز ترجمه نشده باشد خودبه‌خود فارسی می‌ماند — نه خالی
می‌شود و نه ناقص. پس می‌شود زبان‌ها را کم‌کم کامل کرد بدون اینکه چیزی
بشکند.
"""
from __future__ import annotations

RULE = "━━━━━━━━━━━━━━━━━━"

# کلید بخش → {زبان: عنوان دکمه}
TITLES: dict[str, dict[str, str]] = {
    "start": {
        "en": "🚀 Quick start", "ar": "🚀 بداية سريعة", "ru": "🚀 Быстрый старт",
        "tr": "🚀 Hızlı başlangıç", "uz": "🚀 Tez boshlash",
        "hi": "🚀 त्वरित शुरुआत", "id": "🚀 Mulai cepat", "pt": "🚀 Início rápido",
    },
    "account": {
        "en": "👤 Connecting your account", "ar": "👤 ربط حسابك",
        "ru": "👤 Подключение аккаунта", "tr": "👤 Hesabı bağlama",
        "uz": "👤 Hisobni ulash", "hi": "👤 खाता जोड़ना",
        "id": "👤 Menghubungkan akun", "pt": "👤 Conectar sua conta",
    },
    "tasks": {
        "en": "📋 What is a copy job", "ar": "📋 ما هي مهمة النسخ",
        "ru": "📋 Что такое задача", "tr": "📋 Kopyalama işi nedir",
        "uz": "📋 Nusxalash vazifasi nima", "hi": "📋 कॉपी कार्य क्या है",
        "id": "📋 Apa itu tugas salin", "pt": "📋 O que é uma tarefa",
    },
    "dests": {
        "en": "📤 Several destinations", "ar": "📤 عدة وجهات",
        "ru": "📤 Несколько каналов", "tr": "📤 Birden çok hedef",
        "uz": "📤 Bir nechta manzil", "hi": "📤 कई गंतव्य",
        "id": "📤 Beberapa tujuan", "pt": "📤 Vários destinos",
    },
}

# کلید بخش → {زبان: متن کامل}
BODIES: dict[str, dict[str, str]] = {
    # ------------------------------------------------------------ خانه
    "home": {
        "en": (
            "📚 <b>Bot guide</b>\n"
            f"{RULE}\n\n"
            "This bot republishes any channel's posts in your own channel — "
            "automatically, instantly, and edited the way you want — "
            "<b>without you being an admin of the source channel</b>.\n\n"
            "New here? Start with «🚀 Quick start».\n\n"
            "Pick a topic 👇"
        ),
        "ar": (
            "📚 <b>دليل البوت</b>\n"
            f"{RULE}\n\n"
            "ينشر هذا البوت منشورات أي قناة في قناتك — تلقائياً وفوراً "
            "وبالتعديلات التي تريدها — <b>دون أن تكون مشرفاً في القناة "
            "المصدر</b>.\n\n"
            "جديد هنا؟ ابدأ من «🚀 بداية سريعة».\n\n"
            "اختر موضوعاً 👇"
        ),
        "ru": (
            "📚 <b>Руководство</b>\n"
            f"{RULE}\n\n"
            "Бот публикует посты любого канала у вас — автоматически, "
            "мгновенно и с нужными правками — <b>при этом вам не нужно быть "
            "админом исходного канала</b>.\n\n"
            "Впервые здесь? Начните с «🚀 Быстрый старт».\n\n"
            "Выберите тему 👇"
        ),
        "tr": (
            "📚 <b>Bot rehberi</b>\n"
            f"{RULE}\n\n"
            "Bu bot, herhangi bir kanalın gönderilerini kendi kanalınızda "
            "yayınlar — otomatik, anında ve istediğiniz gibi düzenlenmiş — "
            "<b>kaynak kanalda yönetici olmanıza gerek kalmadan</b>.\n\n"
            "Yeni misiniz? «🚀 Hızlı başlangıç» ile başlayın.\n\n"
            "Bir konu seçin 👇"
        ),
        "uz": (
            "📚 <b>Bot qoʻllanmasi</b>\n"
            f"{RULE}\n\n"
            "Bu bot istalgan kanalning postlarini oʻz kanalingizda chop etadi — "
            "avtomatik, bir zumda va siz xohlagandek tahrirlangan holda — "
            "<b>manba kanalda admin boʻlishingiz shart emas</b>.\n\n"
            "Yangimisiz? «🚀 Tez boshlash»dan boshlang.\n\n"
            "Mavzuni tanlang 👇"
        ),
        "hi": (
            "📚 <b>बॉट गाइड</b>\n"
            f"{RULE}\n\n"
            "यह बॉट किसी भी चैनल की पोस्ट आपके चैनल में दोबारा प्रकाशित करता है — "
            "अपने आप, तुरंत, और आपकी पसंद के अनुसार संपादित — <b>और इसके लिए "
            "आपको स्रोत चैनल का एडमिन होने की ज़रूरत नहीं</b>।\n\n"
            "नए हैं? «🚀 त्वरित शुरुआत» से शुरू करें।\n\n"
            "एक विषय चुनें 👇"
        ),
        "id": (
            "📚 <b>Panduan bot</b>\n"
            f"{RULE}\n\n"
            "Bot ini menerbitkan ulang postingan channel mana pun di channel "
            "Anda — otomatis, seketika, dan disunting sesuai keinginan Anda — "
            "<b>tanpa Anda harus menjadi admin channel sumber</b>.\n\n"
            "Baru di sini? Mulai dari «🚀 Mulai cepat».\n\n"
            "Pilih topik 👇"
        ),
        "pt": (
            "📚 <b>Guia do bot</b>\n"
            f"{RULE}\n\n"
            "Este bot republica as postagens de qualquer canal no seu — "
            "automaticamente, na hora e editadas do seu jeito — <b>sem que "
            "você precise ser admin do canal de origem</b>.\n\n"
            "Novo por aqui? Comece por «🚀 Início rápido».\n\n"
            "Escolha um tópico 👇"
        ),
    },
    # ----------------------------------------------------- شروع سریع
    "start": {
        "en": (
            "🚀 <b>Quick start — 3 steps</b>\n"
            f"{RULE}\n\n"
            "<b>Step 1 — Connect your account</b>\n"
            "«👤 Account» → «🔐 Connect account»\n"
            "Send your phone number with the country code (like "
            "<code>+447700900123</code>), then the code Telegram sends you.\n"
            "<i>Why is this needed? Telegram bots can only see posts in "
            "channels where they are admins. This bot reads the source channel "
            "using your account, so you never have to be an admin anywhere.</i>\n\n"
            "<b>Step 2 — Create a copy job</b>\n"
            "«➕ New job» → source channel → destination channel\n"
            "You can type the @username or pick from your chat list.\n"
            "⚠️ Your account must be allowed to post in the <b>destination</b>.\n\n"
            "<b>Step 3 — Set it up</b>\n"
            "Open the job and set filters, word replacements, your sign-off "
            "and watermark.\n\n"
            "✅ That's it. The job is live from the moment you create it, and "
            "the source channel's next post gets copied.\n\n"
            "<i>Tip: before anything is actually sent, use «🧪 Test settings» "
            "to see the result.</i>"
        ),
        "ar": (
            "🚀 <b>بداية سريعة — ٣ خطوات</b>\n"
            f"{RULE}\n\n"
            "<b>الخطوة ١ — اربط حسابك</b>\n"
            "«👤 الحساب» ← «🔐 ربط الحساب»\n"
            "أرسل رقم هاتفك مع رمز الدولة، ثم الرمز الذي يرسله تلجرام.\n"
            "<i>لماذا؟ لأن بوتات تلجرام لا ترى إلا منشورات القنوات التي هي "
            "مشرفة فيها. هذا البوت يقرأ القناة المصدر بحسابك أنت، فلا تحتاج "
            "لأن تكون مشرفاً في أي مكان.</i>\n\n"
            "<b>الخطوة ٢ — أنشئ مهمة نسخ</b>\n"
            "«➕ مهمة جديدة» ← القناة المصدر ← القناة الهدف\n"
            "يمكنك كتابة المعرّف أو الاختيار من قائمة محادثاتك.\n"
            "⚠️ يجب أن يكون لحسابك صلاحية النشر في القناة <b>الهدف</b>.\n\n"
            "<b>الخطوة ٣ — اضبطها</b>\n"
            "افتح المهمة واضبط الفلاتر واستبدال الكلمات وتوقيعك والعلامة "
            "المائية.\n\n"
            "✅ هذا كل شيء. المهمة تعمل من لحظة إنشائها، وأول منشور جديد في "
            "القناة المصدر سيُنسخ.\n\n"
            "<i>نصيحة: قبل أن يُرسل أي شيء فعلياً، استخدم «🧪 اختبار "
            "الإعدادات» لترى النتيجة.</i>"
        ),
        "ru": (
            "🚀 <b>Быстрый старт — 3 шага</b>\n"
            f"{RULE}\n\n"
            "<b>Шаг 1 — Подключите аккаунт</b>\n"
            "«👤 Аккаунт» → «🔐 Подключить аккаунт»\n"
            "Отправьте номер телефона с кодом страны, затем код из Telegram.\n"
            "<i>Зачем это? Боты Telegram видят посты только в тех каналах, где "
            "они админы. Этот бот читает исходный канал вашим аккаунтом, "
            "поэтому вам нигде не нужно быть админом.</i>\n\n"
            "<b>Шаг 2 — Создайте задачу</b>\n"
            "«➕ Новая задача» → исходный канал → целевой канал\n"
            "Можно ввести @username или выбрать из списка чатов.\n"
            "⚠️ Ваш аккаунт должен иметь право публиковать в <b>целевом</b> канале.\n\n"
            "<b>Шаг 3 — Настройте</b>\n"
            "Откройте задачу и задайте фильтры, замены слов, подпись и водяной знак.\n\n"
            "✅ Готово. Задача работает с момента создания, и следующий пост "
            "исходного канала будет скопирован.\n\n"
            "<i>Совет: прежде чем что-то реально отправится, нажмите "
            "«🧪 Проверить настройки» и посмотрите результат.</i>"
        ),
        "tr": (
            "🚀 <b>Hızlı başlangıç — 3 adım</b>\n"
            f"{RULE}\n\n"
            "<b>Adım 1 — Hesabınızı bağlayın</b>\n"
            "«👤 Hesap» → «🔐 Hesabı bağla»\n"
            "Telefon numaranızı ülke koduyla gönderin, sonra Telegram'ın "
            "gönderdiği kodu girin.\n"
            "<i>Neden gerekli? Telegram botları yalnızca yönetici oldukları "
            "kanalların gönderilerini görebilir. Bu bot kaynak kanalı sizin "
            "hesabınızla okur, böylece hiçbir yerde yönetici olmanız "
            "gerekmez.</i>\n\n"
            "<b>Adım 2 — Bir kopyalama işi oluşturun</b>\n"
            "«➕ Yeni iş» → kaynak kanal → hedef kanal\n"
            "@kullanıcı adını yazabilir veya sohbet listenizden seçebilirsiniz.\n"
            "⚠️ Hesabınızın <b>hedef</b> kanalda gönderi yetkisi olmalı.\n\n"
            "<b>Adım 3 — Ayarlayın</b>\n"
            "İşi açın; filtreleri, kelime değişimlerini, imzanızı ve "
            "filigranı ayarlayın.\n\n"
            "✅ Bu kadar. İş oluşturduğunuz andan itibaren çalışır ve kaynak "
            "kanalın bir sonraki gönderisi kopyalanır.\n\n"
            "<i>İpucu: gerçekten bir şey gönderilmeden önce «🧪 Ayarları "
            "test et» ile sonucu görün.</i>"
        ),
        "uz": (
            "🚀 <b>Tez boshlash — 3 qadam</b>\n"
            f"{RULE}\n\n"
            "<b>1-qadam — Hisobingizni ulang</b>\n"
            "«👤 Hisob» → «🔐 Hisobni ulash»\n"
            "Telefon raqamingizni mamlakat kodi bilan yuboring, keyin "
            "Telegram yuborgan kodni kiriting.\n"
            "<i>Nega kerak? Telegram botlari faqat oʻzlari admin boʻlgan "
            "kanallarning postlarini koʻra oladi. Bu bot manba kanalni sizning "
            "hisobingiz bilan oʻqiydi, shuning uchun hech qayerda admin "
            "boʻlishingiz shart emas.</i>\n\n"
            "<b>2-qadam — Nusxalash vazifasini yarating</b>\n"
            "«➕ Yangi vazifa» → manba kanal → maqsad kanal\n"
            "@foydalanuvchi nomini yozishingiz yoki chatlar roʻyxatidan "
            "tanlashingiz mumkin.\n"
            "⚠️ Hisobingizda <b>maqsad</b> kanalda post joylash huquqi "
            "boʻlishi kerak.\n\n"
            "<b>3-qadam — Sozlang</b>\n"
            "Vazifani oching va filtrlar, soʻz almashtirishlari, imzoyingiz "
            "va suv belgisini sozlang.\n\n"
            "✅ Tamom. Vazifa yaratilgan paytdan ishlaydi va manba kanalning "
            "keyingi posti nusxalanadi.\n\n"
            "<i>Maslahat: haqiqatan biror narsa yuborilishidan oldin "
            "«🧪 Sozlamalarni sinash» bilan natijani koʻring.</i>"
        ),
        "hi": (
            "🚀 <b>त्वरित शुरुआत — 3 चरण</b>\n"
            f"{RULE}\n\n"
            "<b>चरण 1 — अपना खाता जोड़ें</b>\n"
            "«👤 खाता» → «🔐 खाता जोड़ें»\n"
            "देश कोड सहित अपना फ़ोन नंबर भेजें, फिर टेलीग्राम द्वारा भेजा गया "
            "कोड डालें।\n"
            "<i>यह क्यों ज़रूरी है? टेलीग्राम बॉट केवल उन्हीं चैनलों की पोस्ट "
            "देख सकते हैं जहाँ वे एडमिन हों। यह बॉट स्रोत चैनल आपके खाते से "
            "पढ़ता है, इसलिए आपको कहीं भी एडमिन बनने की ज़रूरत नहीं।</i>\n\n"
            "<b>चरण 2 — एक कॉपी कार्य बनाएँ</b>\n"
            "«➕ नया कार्य» → स्रोत चैनल → गंतव्य चैनल\n"
            "आप @यूज़रनेम टाइप कर सकते हैं या अपनी चैट सूची से चुन सकते हैं।\n"
            "⚠️ आपके खाते को <b>गंतव्य</b> चैनल में पोस्ट करने की अनुमति "
            "होनी चाहिए।\n\n"
            "<b>चरण 3 — इसे सेट करें</b>\n"
            "कार्य खोलें और फ़िल्टर, शब्द बदलाव, अपने हस्ताक्षर और वॉटरमार्क "
            "सेट करें।\n\n"
            "✅ बस इतना ही। कार्य बनते ही चालू हो जाता है और स्रोत चैनल की "
            "अगली पोस्ट कॉपी हो जाती है।\n\n"
            "<i>सुझाव: कुछ भी वास्तव में भेजे जाने से पहले «🧪 सेटिंग्स "
            "जाँचें» से परिणाम देखें।</i>"
        ),
        "id": (
            "🚀 <b>Mulai cepat — 3 langkah</b>\n"
            f"{RULE}\n\n"
            "<b>Langkah 1 — Hubungkan akun Anda</b>\n"
            "«👤 Akun» → «🔐 Hubungkan akun»\n"
            "Kirim nomor telepon Anda dengan kode negara, lalu kode yang "
            "dikirim Telegram.\n"
            "<i>Mengapa perlu? Bot Telegram hanya bisa melihat postingan di "
            "channel tempat mereka menjadi admin. Bot ini membaca channel "
            "sumber memakai akun Anda, jadi Anda tidak perlu jadi admin di "
            "mana pun.</i>\n\n"
            "<b>Langkah 2 — Buat tugas salin</b>\n"
            "«➕ Tugas baru» → channel sumber → channel tujuan\n"
            "Anda bisa mengetik @username atau memilih dari daftar chat.\n"
            "⚠️ Akun Anda harus punya izin memposting di channel <b>tujuan</b>.\n\n"
            "<b>Langkah 3 — Atur</b>\n"
            "Buka tugas dan atur filter, penggantian kata, tanda tangan dan "
            "watermark.\n\n"
            "✅ Selesai. Tugas aktif sejak dibuat, dan postingan berikutnya "
            "dari channel sumber akan disalin.\n\n"
            "<i>Tips: sebelum ada yang benar-benar terkirim, gunakan «🧪 Uji "
            "pengaturan» untuk melihat hasilnya.</i>"
        ),
        "pt": (
            "🚀 <b>Início rápido — 3 passos</b>\n"
            f"{RULE}\n\n"
            "<b>Passo 1 — Conecte sua conta</b>\n"
            "«👤 Conta» → «🔐 Conectar conta»\n"
            "Envie seu número de telefone com o código do país e depois o "
            "código que o Telegram enviar.\n"
            "<i>Por que isso é preciso? Bots do Telegram só enxergam posts de "
            "canais onde são admins. Este bot lê o canal de origem usando a "
            "sua conta, então você não precisa ser admin em lugar nenhum.</i>\n\n"
            "<b>Passo 2 — Crie uma tarefa de cópia</b>\n"
            "«➕ Nova tarefa» → canal de origem → canal de destino\n"
            "Você pode digitar o @usuário ou escolher da sua lista de conversas.\n"
            "⚠️ Sua conta precisa poder publicar no canal de <b>destino</b>.\n\n"
            "<b>Passo 3 — Configure</b>\n"
            "Abra a tarefa e defina filtros, substituições de palavras, sua "
            "assinatura e marca d'água.\n\n"
            "✅ Pronto. A tarefa fica ativa assim que é criada e o próximo "
            "post do canal de origem será copiado.\n\n"
            "<i>Dica: antes que algo seja realmente enviado, use «🧪 Testar "
            "configurações» para ver o resultado.</i>"
        ),
    },
}


def title(key: str, lang: str) -> str | None:
    """عنوان دکمه‌ی یک بخش به این زبان، یا None اگر ترجمه نشده."""
    return TITLES.get(key, {}).get(lang)


def body(key: str, lang: str) -> str | None:
    """متن یک بخش به این زبان، یا None اگر ترجمه نشده."""
    return BODIES.get(key, {}).get(lang)


def coverage(lang: str) -> int:
    """چند بخش به این زبان ترجمه شده — برای گزارش پیشرفت."""
    return sum(1 for entry in BODIES.values() if entry.get(lang))
