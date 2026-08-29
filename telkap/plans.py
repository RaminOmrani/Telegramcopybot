"""تعریف پلن‌های اشتراک، سطح دسترسی هر پلن و بسته‌های اعتبار جداگانه."""
from __future__ import annotations

from dataclasses import dataclass

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
AI_UNIT_TOMAN = 500                # هر کار هوش مصنوعی روی یک پست

CREDIT_WATERMARK = "wm"
CREDIT_HISTORY = "hist"
CREDIT_AI = "ai"

CREDIT_KINDS: dict[str, tuple[str, str, int]] = {
    # کد: (عنوان، توضیح، قیمت پیش‌فرض هر واحد)
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
    CREDIT_AI: (
        "🤖 اعتبار هوش مصنوعی",
        "هر بازنویسی، ترجمه یا خلاصه‌ی یک پست، یک واحد کم می‌شود.",
        AI_UNIT_TOMAN,
    ),
}

# قیمت زنده‌ی هر واحد اعتبار. از پنل ادمین قابل تغییر است و
# `planstore` هنگام بالا آمدن ربات مقدار ذخیره‌شده را اینجا می‌نشاند.
CREDIT_UNITS: dict[str, int] = {kind: info[2] for kind, info in CREDIT_KINDS.items()}


def credit_unit(kind: str) -> int:
    """قیمت فعلی هر واحد از یک نوع اعتبار (تومان)."""
    return CREDIT_UNITS.get(kind, 0)

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

    @property
    def perks(self) -> tuple[str, ...]:
        """فهرست امکانات طرح، ساخته‌شده از خودِ عددها.

        متن ثابت نیست تا وقتی ادمین سقفی را از پنل عوض می‌کند، توضیح طرح
        هم همان‌جا به‌روز شود و دو جا نگهداری نخواهد.
        """
        lines = [
            "🚀 پیام نامحدود (بدون سقف دوره)"
            if self.period_messages < 0
            else f"{_fa(self.period_messages)} پیام در کل دوره",
            f"مدت: {_fa(self.days)} روز",
            f"{_fa(self.max_tasks)} کار کپی فعال",
            f"تا {_fa(self.max_destinations)} کانال مقصد برای هر کار",
        ]
        if self.has(FEAT_PRIVATE):
            lines.append("کپی از کانال‌های عمومی و خصوصی")
        elif self.has(FEAT_PUBLIC):
            lines.append("کپی از کانال‌های عمومی")
        if self.watermark_quota:
            lines.append(f"💧 {self.watermark_label} واترمارک")
        if self.history_quota:
            lines.append(f"🕓 {self.history_label} پیام گذشته")
        if self.has(FEAT_VIP):
            lines.append("پشتیبانی ویژه (VIP)")
        return tuple(lines)


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
)

WEEK = Plan(
    code="week",
    title="اشتراک ۷ روزه",
    days=7,
    price_toman=129_000,
    tagline="پلن پایه برای شروع",
    max_tasks=3,
    max_destinations=3,
    period_messages=2_000,
    watermark_quota=0,
    history_quota=0,
    features=frozenset({FEAT_PUBLIC}),
)

TWO_WEEK = Plan(
    code="two_week",
    title="اشتراک ۱۴ روزه",
    days=14,
    price_toman=229_000,
    tagline="پلن محبوب، با کانال خصوصی و واترمارک",
    max_tasks=6,
    max_destinations=5,
    period_messages=10_000,
    watermark_quota=10,
    history_quota=0,
    features=frozenset({FEAT_PUBLIC, FEAT_PRIVATE, FEAT_WATERMARK}),
)

MONTH = Plan(
    code="month",
    title="اشتراک ۳۰ روزه",
    days=30,
    price_toman=429_000,
    tagline="پلن حرفه‌ای با همه‌ی امکانات",
    max_tasks=20,
    max_destinations=10,
    period_messages=20_000,
    watermark_quota=20,
    history_quota=50,
    features=frozenset({FEAT_PUBLIC, FEAT_PRIVATE, FEAT_WATERMARK, FEAT_HISTORY, FEAT_VIP}),
)

CUSTOM = Plan(
    code="custom",
    title="طرح اختصاصی",
    days=30,
    price_toman=890_000,
    tagline="۳۰ روزه، مثل طرح ۳۰ روزه ولی با پیام نامحدود و سهمیه‌های بالاتر",
    max_tasks=50,
    max_destinations=20,
    period_messages=UNLIMITED,
    watermark_quota=50,
    history_quota=100,
    features=frozenset({FEAT_PUBLIC, FEAT_PRIVATE, FEAT_WATERMARK, FEAT_HISTORY, FEAT_VIP}),
    # نامحدود نمایش داده می‌شود، ولی برای جلوگیری از سوءاستفاده سقف
    # روزانه‌ی مصرف منصفانه دارد
    fair_use_daily=10_000,
)

# مقادیر کارخانه‌ای؛ هرگز تغییر نمی‌کنند و «بازگردانی به پیش‌فرض» از
# همین‌ها می‌خواند.
DEFAULT_PLANS: dict[str, Plan] = {
    p.code: p for p in (TRIAL, WEEK, TWO_WEEK, MONTH, CUSTOM)
}

# طرح‌های زنده. هرچه ادمین از پنل عوض کند `planstore` اینجا می‌نشاند، پس
# همه‌ی مصرف‌کننده‌ها باید از همین بخوانند نه از ثابت‌های بالا.
PLANS: dict[str, Plan] = dict(DEFAULT_PLANS)

PURCHASABLE_CODES: tuple[str, ...] = (WEEK.code, TWO_WEEK.code, MONTH.code, CUSTOM.code)
POPULAR_CODE = TWO_WEEK.code


def get_plan(code: str) -> Plan | None:
    return PLANS.get(code)


def all_plans() -> dict[str, Plan]:
    """همه‌ی طرح‌ها با مقادیر فعلی (شامل تغییرهای ادمین)."""
    return PLANS


def purchasable() -> tuple[Plan, ...]:
    """طرح‌های قابل خرید، به ترتیب نمایش."""
    return tuple(PLANS[code] for code in PURCHASABLE_CODES if code in PLANS)


def credit_price(kind: str, quantity: int) -> int:
    """قیمت کل یک بسته‌ی اعتبار به تومان."""
    if kind not in CREDIT_KINDS or quantity <= 0:
        return 0
    return credit_unit(kind) * quantity


def toman(amount: int) -> str:
    return f"{_fa(amount)} تومان"
