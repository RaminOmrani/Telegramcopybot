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
    )


settings = load_settings() if os.getenv("BOT_TOKEN") else None  # type: ignore[assignment]


def get_settings() -> Settings:
    """دسترسی تنبل به تنظیمات تا ایمپورت ماژول‌ها بدون .env هم کار کند."""
    global settings
    if settings is None:
        settings = load_settings()
    return settings
