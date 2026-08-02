"""تصمیم‌گیری درباره‌ی کپی یا رد کردن یک پیام."""
from __future__ import annotations

import hashlib
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from telkap.services.transform import URL_RE, RuleLike, looks_like_ad


@dataclass(slots=True)
class Decision:
    allowed: bool
    reason: str = ""

    def __bool__(self) -> bool:  # pragma: no cover - راحتی استفاده
        return self.allowed


ALLOW = Decision(True)


@dataclass(slots=True)
class MessageFacts:
    """اطلاعات استخراج‌شده از پیام؛ مستقل از کتابخانه‌ی تلگرام تا قابل تست باشد."""

    text: str = ""
    media_kind: str = "text"
    is_forwarded: bool = False
    has_buttons: bool = False
    size_bytes: int = 0


def content_hash(facts: MessageFacts) -> str:
    raw = f"{facts.media_kind}|{facts.text.strip()}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]


def should_copy(
    facts: MessageFacts,
    settings: dict[str, Any],
    rules: Iterable[RuleLike] = (),
) -> Decision:
    """بررسی همه‌ی فیلترها. متن ورودی باید متن *اصلی* پیش از تبدیل باشد."""
    text = facts.text or ""
    lowered = text.lower()

    allowed_media = settings.get("allowed_media") or []
    if facts.media_kind not in allowed_media:
        return Decision(False, f"نوع محتوا «{facts.media_kind}» در تنظیمات غیرفعال است")

    if settings.get("block_forwarded") and facts.is_forwarded:
        return Decision(False, "پست فورواردشده است")

    if settings.get("block_with_buttons") and facts.has_buttons:
        return Decision(False, "پست دکمه شیشه‌ای دارد")

    if settings.get("block_with_links") and URL_RE.search(text):
        return Decision(False, "پست حاوی لینک است")

    if settings.get("block_ads") and looks_like_ad(text):
        return Decision(False, "پست تبلیغاتی تشخیص داده شد")

    limit_mb = int(settings.get("skip_media_over_mb") or 0)
    if limit_mb and facts.size_bytes > limit_mb * 1024 * 1024:
        return Decision(False, f"حجم فایل بیش از {limit_mb} مگابایت است")

    min_len = int(settings.get("min_length") or 0)
    if min_len and len(text.strip()) < min_len:
        return Decision(False, f"متن کوتاه‌تر از {min_len} نویسه است")

    max_len = int(settings.get("max_length") or 0)
    if max_len and len(text.strip()) > max_len:
        return Decision(False, f"متن بلندتر از {max_len} نویسه است")

    rules = list(rules)
    blocked = [r for r in rules if r.kind == "block" and r.enabled and r.pattern]
    for rule in blocked:
        if rule.pattern.lower() in lowered:
            return Decision(False, f"کلمه‌ی ممنوعه: {rule.pattern}")

    allow_list = [r for r in rules if r.kind == "allow" and r.enabled and r.pattern]
    if allow_list and not any(r.pattern.lower() in lowered for r in allow_list):
        return Decision(False, "هیچ‌کدام از کلمات مجاز در پست نبود")

    return ALLOW
