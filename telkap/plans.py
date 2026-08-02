"""تعریف پلن‌های اشتراک و سطح دسترسی هر پلن."""
from __future__ import annotations

from dataclasses import dataclass, field

# قابلیت‌هایی که به پلن وابسته‌اند
FEAT_PUBLIC = "public_source"      # کپی از کانال عمومی
FEAT_PRIVATE = "private_source"    # کپی از کانال خصوصی
FEAT_WATERMARK = "watermark"       # واترمارک تصاویر
FEAT_HISTORY = "history"           # کپی پیام‌های گذشته
FEAT_VIP = "vip_support"           # پشتیبانی ویژه


@dataclass(frozen=True)
class Plan:
    code: str
    title: str
    days: int
    price_toman: int
    tagline: str
    max_tasks: int
    features: frozenset[str]
    perks: tuple[str, ...] = field(default_factory=tuple)

    def has(self, feature: str) -> bool:
        return feature in self.features

    @property
    def price_label(self) -> str:
        return f"{self.price_toman:,} تومان".replace(",", "،")


TRIAL = Plan(
    code="trial",
    title="اشتراک آزمایشی",
    days=1,
    price_toman=0,
    tagline="تست رایگان امکانات پایه",
    max_tasks=1,
    features=frozenset({FEAT_PUBLIC}),
    perks=("۱ کار کپی فعال", "کپی از کانال‌های عمومی", "فیلتر و جایگزینی متن"),
)

WEEK = Plan(
    code="week",
    title="اشتراک ۷ روزه",
    days=7,
    price_toman=69_000,
    tagline="پلن پایه برای تست ربات",
    max_tasks=3,
    features=frozenset({FEAT_PUBLIC}),
    perks=("پشتیبانی کامل", "امکانات نامحدود", "فعال‌سازی فوری", "کپی از کانال‌های عمومی"),
)

TWO_WEEK = Plan(
    code="two_week",
    title="اشتراک ۱۴ روزه",
    days=14,
    price_toman=129_000,
    tagline="پلن محبوب با امکانات کامل",
    max_tasks=6,
    features=frozenset({FEAT_PUBLIC, FEAT_PRIVATE, FEAT_WATERMARK}),
    perks=(
        "پشتیبانی کامل",
        "امکانات نامحدود",
        "فعال‌سازی فوری",
        "کپی از کانال‌های عمومی",
        "کپی از کانال‌های خصوصی",
        "واترمارک تصاویر",
    ),
)

MONTH = Plan(
    code="month",
    title="اشتراک ۳۰ روزه",
    days=30,
    price_toman=229_000,
    tagline="پلن حرفه‌ای برای کاربران پرتعداد",
    max_tasks=20,
    features=frozenset({FEAT_PUBLIC, FEAT_PRIVATE, FEAT_WATERMARK, FEAT_HISTORY, FEAT_VIP}),
    perks=(
        "پشتیبانی ویژه (VIP)",
        "تمام امکانات پیشرفته",
        "فعال‌سازی فوری",
        "کپی از کانال‌های عمومی و خصوصی",
        "واترمارک تصاویر",
        "کپی پیام‌های گذشته",
    ),
)

PLANS: dict[str, Plan] = {p.code: p for p in (TRIAL, WEEK, TWO_WEEK, MONTH)}
PURCHASABLE: tuple[Plan, ...] = (WEEK, TWO_WEEK, MONTH)
POPULAR_CODE = TWO_WEEK.code


def get_plan(code: str) -> Plan | None:
    return PLANS.get(code)
