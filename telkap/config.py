"""بارگذاری تنظیمات از فایل .env / متغیرهای محیطی."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent.parent


def _int_list(raw: str) -> list[int]:
    return [int(part) for part in raw.replace(" ", "").split(",") if part]


def _flag(raw: str) -> bool:
    """مقدار بله/خیر در .env؛ هرچه جز این‌ها باشد خاموش حساب می‌شود."""
    return raw.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Settings:
    bot_token: str
    api_id: int
    api_hash: str
    fernet_key: str
    admin_ids: list[int] = field(default_factory=list)
    database_url: str = "sqlite+aiosqlite:///data/telkap.db"
    bot_username: str = ""
    support_username: str = ""
    force_join_channel: str = ""
    trial_days: int = 1
    rate_per_minute: int = 20
    download_dir: Path = BASE_DIR / "data" / "downloads"
    log_level: str = "INFO"
    # اختلاف ساعت محلی با UTC، برای زمان‌بندی کارها (ایران = 3.5)
    timezone_offset: float = 3.5
    card_number: str = ""
    card_holder: str = ""
    backup_hours: int = 12
    # چند روز لاگ فعالیت نگه داشته شود (۰ = برای همیشه)
    log_retention_days: int = 14
    # پروکسی برای شبکه‌هایی که تلگرام مسدود است، مثل socks5://127.0.0.1:10808
    proxy_url: str = ""

    # اثر انگشتی که اکانت کاربری به تلگرام معرفی می‌کند. این مقدار هم در
    # «دستگاه‌های متصل» خود کاربر دیده می‌شود و هم تلگرام آن را می‌بیند؛
    # نامی که خودش را «ربات» معرفی کند، ریسک محدود شدن را بالا می‌برد.
    device_model: str = "Desktop"
    system_version: str = "Windows 10"
    app_version: str = "4.16.8"

    # پشتیبان‌گیری بیرون از سرور: شناسه‌ی کانال خصوصی که ربات در آن ادمین
    # است. خالی یعنی فقط نسخه‌ی محلی گرفته می‌شود.
    backup_chat_id: str = ""

    # ── پنل وب ────────────────────────────────────────────────────────
    # پنل داخل همان پروسه‌ی ربات بالا می‌آید، پس چیز تازه‌ای نصب نمی‌شود.
    # پیش‌فرض خاموش است: تا خودتان روشنش نکنید هیچ پورتی باز نمی‌شود.
    web_enabled: bool = False
    # روی 127.0.0.1 یعنی فقط از خود سرور در دسترس است و باید یک وب‌سرور
    # جلویش بگذارید. برای دسترسی مستقیم از بیرون 0.0.0.0 بگذارید — ولی
    # آن‌وقت بدون HTTPS، کوکی ورود روی شبکه لخت می‌رود.
    web_host: str = "127.0.0.1"
    web_port: int = 8080
    # آدرسی که کاربر در مرورگر می‌بیند، مثل https://botpanel.softmiliac.com
    # لینک ورود از روی همین ساخته می‌شود.
    web_base_url: str = ""

    # ── هوش مصنوعی ────────────────────────────────────────────────────
    # هر سرویسی که رابط OpenAI را بفهمد. کلید که خالی باشد، هیچ‌کدام از
    # قابلیت‌های هوش مصنوعی در منوها ظاهر نمی‌شوند.
    ai_base_url: str = "https://api.avalai.ir/v1"
    ai_api_key: str = ""
    # نقش‌ها جدا هستند چون هزینه‌شان ده‌ها برابر فرق می‌کند: دسته‌بندی با
    # مدل کوچک همان نتیجه را می‌دهد، بازنویسی فارسی نه.
    ai_model_small: str = "gemini-3.5-flash-lite"
    ai_model_main: str = "gemini-3.6-flash"
    ai_model_vision: str = "gemini-3.5-flash-lite"
    ai_model_embed: str = "text-embedding-3-small"

    def is_admin(self, user_id: int) -> bool:
        return user_id in self.admin_ids


def load_settings() -> Settings:
    missing = [key for key in ("BOT_TOKEN", "API_ID", "API_HASH", "FERNET_KEY") if not os.getenv(key)]
    if missing:
        raise RuntimeError(
            "متغیرهای محیطی زیر تنظیم نشده‌اند: " + ", ".join(missing) + "\n"
            "فایل .env.example را به .env کپی کرده و مقادیر را پر کنید."
        )

    download_dir = Path(os.getenv("DOWNLOAD_DIR", "data/downloads"))
    if not download_dir.is_absolute():
        download_dir = BASE_DIR / download_dir

    return Settings(
        bot_token=os.environ["BOT_TOKEN"],
        api_id=int(os.environ["API_ID"]),
        api_hash=os.environ["API_HASH"],
        fernet_key=os.environ["FERNET_KEY"],
        admin_ids=_int_list(os.getenv("ADMIN_IDS", "")),
        database_url=os.getenv("DATABASE_URL", "sqlite+aiosqlite:///data/telkap.db"),
        bot_username=os.getenv("BOT_USERNAME", "").lstrip("@"),
        support_username=os.getenv("SUPPORT_USERNAME", "").lstrip("@"),
        force_join_channel=os.getenv("FORCE_JOIN_CHANNEL", "").lstrip("@"),
        trial_days=int(os.getenv("TRIAL_DAYS", "1")),
        rate_per_minute=int(os.getenv("RATE_PER_MINUTE", "20")),
        download_dir=download_dir,
        log_level=os.getenv("LOG_LEVEL", "INFO").upper(),
        timezone_offset=float(os.getenv("TIMEZONE_OFFSET", "3.5")),
        card_number=os.getenv("CARD_NUMBER", "").strip(),
        card_holder=os.getenv("CARD_HOLDER", "").strip(),
        backup_hours=int(os.getenv("BACKUP_HOURS", "12")),
        log_retention_days=int(os.getenv("LOG_RETENTION_DAYS", "14")),
        proxy_url=os.getenv("PROXY_URL", "").strip(),
        device_model=os.getenv("DEVICE_MODEL", "Desktop").strip() or "Desktop",
        system_version=os.getenv("SYSTEM_VERSION", "Windows 10").strip() or "Windows 10",
        app_version=os.getenv("APP_VERSION", "4.16.8").strip() or "4.16.8",
        backup_chat_id=os.getenv("BACKUP_CHAT_ID", "").strip(),
        web_enabled=_flag(os.getenv("WEB_ENABLED", "")),
        web_host=os.getenv("WEB_HOST", "127.0.0.1").strip() or "127.0.0.1",
        web_port=int(os.getenv("WEB_PORT", "8080")),
        web_base_url=os.getenv("WEB_BASE_URL", "").strip().rstrip("/"),
        ai_base_url=os.getenv("AI_BASE_URL", "https://api.avalai.ir/v1").strip(),
        ai_api_key=os.getenv("AI_API_KEY", "").strip(),
        ai_model_small=os.getenv("AI_MODEL_SMALL", "gemini-3.5-flash-lite").strip(),
        ai_model_main=os.getenv("AI_MODEL_MAIN", "gemini-3.6-flash").strip(),
        ai_model_vision=os.getenv("AI_MODEL_VISION", "gemini-3.5-flash-lite").strip(),
        ai_model_embed=os.getenv("AI_MODEL_EMBED", "text-embedding-3-small").strip(),
    )


settings = load_settings() if os.getenv("BOT_TOKEN") else None  # type: ignore[assignment]


def get_settings() -> Settings:
    """دسترسی تنبل به تنظیمات تا ایمپورت ماژول‌ها بدون .env هم کار کند."""
    global settings
    if settings is None:
        settings = load_settings()
    return settings
