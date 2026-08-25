"""تعریف پلن‌های اشتراک، سطح دسترسی هر پلن و بسته‌های اعتبار جداگانه."""
from __future__ import annotations

from dataclasses import dataclass, field

# قابلیت‌هایی که به پلن وابسته‌اند
FEAT_PUBLIC = "public_source"      # کپی از کانال عمومی
FEAT_PRIVATE = "private_source"    # کپی از کانال خصوصی
FEAT_WATERMARK = "watermark"       # واترمارک تصاویر
FEAT_HISTORY = "history"           # کپی پیام‌های گذشته
FEAT_VIP = "vip_support"           # پشتیبانی ویژه

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


@dataclass(frozen=True)
class Plan:
    code: str
    title: str
    days: int
    price_toman: int
    tagline: str
    max_tasks: int              # چند کار کپی همزمان
    max_destinations: int       # چند کانال مقصد برای هر کار (شامل مقصد اصلی)
    daily_messages: int         # سقف پیام در شبانه‌روز (۰ = نامحدود)
    features: frozenset[str]
    perks: tuple[str, ...] = field(default_factory=tuple)

    def has(self, feature: str) -> bool:
        return feature in self.features

    @property
    def price_label(self) -> str:
        return f"{self.price_toman:,} تومان".replace(",", "،")

    @property
    def daily_label(self) -> str:
        if self.daily_messages <= 0:
            return "نامحدود"
        return f"{self.daily_messages:,}".replace(",", "،")

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
    daily_messages=50,
    features=frozenset({FEAT_PUBLIC}),
    perks=(
        "۱ کار کپی فعال",
        "۱ کانال مقصد",
        "۵۰ پیام در روز",
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
    daily_messages=500,
    features=frozenset({FEAT_PUBLIC}),
    perks=(
        "۳ کار کپی فعال",
        "تا ۳ کانال مقصد برای هر کار",
        "۵۰۰ پیام در روز",
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
    daily_messages=1_500,
    features=frozenset({FEAT_PUBLIC, FEAT_PRIVATE, FEAT_WATERMARK}),
    perks=(
        "۶ کار کپی فعال",
        "تا ۵ کانال مقصد برای هر کار",
        "۱٬۵۰۰ پیام در روز",
        "کپی از کانال‌های عمومی و خصوصی",
        "واترمارک تصاویر (بدون هزینه‌ی جداگانه)",
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
    daily_messages=4_000,
    features=frozenset({FEAT_PUBLIC, FEAT_PRIVATE, FEAT_WATERMARK, FEAT_HISTORY, FEAT_VIP}),
    perks=(
        "۲۰ کار کپی فعال",
        "تا ۱۰ کانال مقصد برای هر کار",
        "۴٬۰۰۰ پیام در روز",
        "کپی از کانال‌های عمومی و خصوصی",
        "واترمارک تصاویر",
        "کپی پیام‌های گذشته",
        "پشتیبانی ویژه (VIP)",
    ),
)

CUSTOM = Plan(
    code="custom",
    title="طرح اختصاصی",
    days=30,
    price_toman=890_000,
    tagline="بدون سقف پیام، همه‌ی امکانات باز",
    max_tasks=100,
    max_destinations=20,
    daily_messages=0,  # نامحدود
    features=frozenset({FEAT_PUBLIC, FEAT_PRIVATE, FEAT_WATERMARK, FEAT_HISTORY, FEAT_VIP}),
    perks=(
        "🚀 پیام نامحدود در روز",
        "۱۰۰ کار کپی فعال",
        "تا ۲۰ کانال مقصد برای هر کار",
        "کپی از کانال‌های عمومی و خصوصی",
        "واترمارک نامحدود",
        "کپی پیام‌های گذشته بدون هزینه‌ی اضافه",
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
    return f"{amount:,} تومان".replace(",", "،")
