"""تعریف پلن‌های اشتراک، سطح دسترسی هر پلن و بسته‌های اعتبار جداگانه."""
from __future__ import annotations

from dataclasses import dataclass, field

# قابلیت‌هایی که به پلن وابسته‌اند
FEAT_PUBLIC = "public_source"      # کپی از کانال عمومی
FEAT_PRIVATE = "private_source"    # کپی از کانال خصوصی
FEAT_WATERMARK = "watermark"       # واترمارک تصاویر
FEAT_HISTORY = "history"           # کپی پیام‌های گذشته
FEAT_VIP = "vip_support"           # پشتیبانی ویژه
FEAT_MESSAGES = "messages"         # سهمیه‌ی پیام (همیشه هست، فقط عددش فرق می‌کند)

# بسته‌های اعتباری که جدا از اشتراک خریده می‌شوند (تومان به ازای هر واحد)
WATERMARK_UNIT_TOMAN = 1_000       # هر تصویر واترمارک‌شده
HISTORY_UNIT_TOMAN = 1_000         # هر پیام قدیمی کپی‌شده

CREDIT_WATERMARK = "wm"
CREDIT_HISTORY = "hist"

CREDIT_KINDS: dict[str, tuple[str, str, int]] = {
    # کد: (عنوان، توضیح، قیمت هر واحد)
    CREDIT_WATERMARK: (
        "💧 اعتبار واترمارک",
        "هر تصویری که واترمارک بخورد، یک واحد کم می‌شود.",
        WATERMARK_UNIT_TOMAN,
    ),
    CREDIT_HISTORY: (
        "🕓 اعتبار کپی پیام‌های گذشته",
        "هر پیام قدیمی که کپی شود، یک واحد کم می‌شود.",
        HISTORY_UNIT_TOMAN,
    ),
}

# تعدادهای پیشنهادی در دکمه‌ها؛ کاربر می‌تواند عدد دلخواه هم بدهد
CREDIT_PACKS: tuple[int, ...] = (50, 100, 500, 1000)


# مقدار سهمیه‌ها: UNLIMITED یعنی بی‌نهایت، ۰ یعنی اصلاً در طرح نیست.
UNLIMITED = -1

_FA_DIGITS = str.maketrans("0123456789", "۰۱۲۳۴۵۶۷۸۹")


def _fa(value: int) -> str:
    """عدد با ارقام فارسی و جداکننده‌ی هزارگان."""
    return f"{value:,}".replace(",", "،").translate(_FA_DIGITS)


def quota_label(value: int) -> str:
    if value < 0:
        return "نامحدود"
    if value == 0:
        return "ندارد"
    return _fa(value)


@dataclass(frozen=True)
class Plan:
    """یک طرح اشتراک.

    <b>همه‌ی سهمیه‌ها برای کل دوره‌ی اشتراک‌اند، نه روزانه.</b> یعنی «۲۰۰۰
    پیام» یعنی ۲۰۰۰ پیام در کل ۳۰ روز. با تمدید یا خرید طرح تازه، سهمیه
    از نو پر می‌شود.
    """

    code: str
    title: str
    days: int
    price_toman: int
    tagline: str
    max_tasks: int              # چند کار کپی همزمان
    max_destinations: int       # چند کانال مقصد برای هر کار (شامل مقصد اصلی)
    period_messages: int        # سقف پیام در کل دوره
    watermark_quota: int        # چند تصویر در کل دوره واترمارک می‌خورد
    history_quota: int          # چند پیام قدیمی در کل دوره کپی می‌شود
    features: frozenset[str]
    perks: tuple[str, ...] = field(default_factory=tuple)
    # سقف «مصرف منصفانه» روزانه برای طرح‌های نامحدود. به کاربر نامحدود
    # نشان داده می‌شود ولی جلوی سوءاستفاده را می‌گیرد. ۰ یعنی بدون سقف.
    fair_use_daily: int = 0

    def has(self, feature: str) -> bool:
        return feature in self.features

    def quota(self, feature: str) -> int:
        """سهمیه‌ی کل دوره برای یک قابلیت مصرفی."""
        if feature == FEAT_WATERMARK:
            return self.watermark_quota
        if feature == FEAT_HISTORY:
            return self.history_quota
        if feature == FEAT_MESSAGES:
            return self.period_messages
        return UNLIMITED

    @property
    def price_label(self) -> str:
        return f"{_fa(self.price_toman)} تومان"

    @property
    def messages_label(self) -> str:
        return quota_label(self.period_messages)

    @property
    def watermark_label(self) -> str:
        return quota_label(self.watermark_quota)

    @property
    def history_label(self) -> str:
        return quota_label(self.history_quota)

    @property
    def extra_destinations(self) -> int:
        """مقصدهای اضافی، بدون شمردن مقصد اصلی."""
        return max(0, self.max_destinations - 1)


TRIAL = Plan(
    code="trial",
    title="اشتراک آزمایشی",
    days=1,
    price_toman=0,
    tagline="تست رایگان امکانات پایه",
    max_tasks=1,
    max_destinations=1,
    period_messages=20,
    watermark_quota=0,
    history_quota=0,
    features=frozenset({FEAT_PUBLIC}),
    perks=(
        "۲۰ پیام برای تست",
        "۱ کار کپی فعال",
        "۱ کانال مقصد",
        "کپی از کانال‌های عمومی",
    ),
)

WEEK = Plan(
    code="week",
    title="اشتراک ۷ روزه",
    days=7,
    price_toman=129_000,
    tagline="پلن پایه برای شروع",
    max_tasks=3,
    max_destinations=3,
    period_messages=200,
    watermark_quota=0,
    history_quota=0,
    features=frozenset({FEAT_PUBLIC}),
    perks=(
        "۲۰۰ پیام در کل دوره",
        "۳ کار کپی فعال",
        "تا ۳ کانال مقصد برای هر کار",
        "کپی از کانال‌های عمومی",
        "فیلتر، جایگزینی کلمات، هدر و فوتر",
    ),
)

TWO_WEEK = Plan(
    code="two_week",
    title="اشتراک ۱۴ روزه",
    days=14,
    price_toman=229_000,
    tagline="پلن محبوب، با کانال خصوصی و واترمارک",
    max_tasks=6,
    max_destinations=5,
    period_messages=1_000,
    watermark_quota=10,
    history_quota=0,
    features=frozenset({FEAT_PUBLIC, FEAT_PRIVATE, FEAT_WATERMARK}),
    perks=(
        "۱٬۰۰۰ پیام در کل دوره",
        "۶ کار کپی فعال",
        "تا ۵ کانال مقصد برای هر کار",
        "کپی از کانال‌های عمومی و خصوصی",
        "۱۰ واترمارک",
    ),
)

MONTH = Plan(
    code="month",
    title="اشتراک ۳۰ روزه",
    days=30,
    price_toman=429_000,
    tagline="پلن حرفه‌ای با همه‌ی امکانات",
    max_tasks=20,
    max_destinations=10,
    period_messages=2_000,
    watermark_quota=20,
    history_quota=50,
    features=frozenset({FEAT_PUBLIC, FEAT_PRIVATE, FEAT_WATERMARK, FEAT_HISTORY, FEAT_VIP}),
    perks=(
        "۲٬۰۰۰ پیام در کل دوره",
        "۲۰ کار کپی فعال",
        "تا ۱۰ کانال مقصد برای هر کار",
        "کپی از کانال‌های عمومی و خصوصی",
        "۲۰ واترمارک",
        "۵۰ پیام گذشته",
        "پشتیبانی ویژه (VIP)",
    ),
)

CUSTOM = Plan(
    code="custom",
    title="طرح اختصاصی",
    days=30,
    price_toman=890_000,
    tagline="پیام نامحدود، بالاترین سهمیه‌ها",
    max_tasks=50,
    max_destinations=20,
    period_messages=UNLIMITED,
    watermark_quota=50,
    history_quota=100,
    features=frozenset({FEAT_PUBLIC, FEAT_PRIVATE, FEAT_WATERMARK, FEAT_HISTORY, FEAT_VIP}),
    # نامحدود نمایش داده می‌شود، ولی برای جلوگیری از سوءاستفاده سقف
    # روزانه‌ی مصرف منصفانه دارد
    fair_use_daily=10_000,
    perks=(
        "🚀 پیام نامحدود",
        "۵۰ کار کپی فعال",
        "تا ۲۰ کانال مقصد برای هر کار",
        "کپی از کانال‌های عمومی و خصوصی",
        "۵۰ واترمارک",
        "۱۰۰ پیام گذشته",
        "پشتیبانی ویژه (VIP)",
    ),
)

PLANS: dict[str, Plan] = {p.code: p for p in (TRIAL, WEEK, TWO_WEEK, MONTH, CUSTOM)}
PURCHASABLE: tuple[Plan, ...] = (WEEK, TWO_WEEK, MONTH, CUSTOM)
POPULAR_CODE = TWO_WEEK.code


def get_plan(code: str) -> Plan | None:
    return PLANS.get(code)


def credit_price(kind: str, quantity: int) -> int:
    """قیمت کل یک بسته‌ی اعتبار به تومان."""
    info = CREDIT_KINDS.get(kind)
    if info is None or quantity <= 0:
        return 0
    return info[2] * quantity


def toman(amount: int) -> str:
    return f"{_fa(amount)} تومان"
