"""تست‌های تصمیم‌گیری فیلترها."""
from __future__ import annotations

from dataclasses import dataclass

from telkap.services.defaults import merged_settings
from telkap.services.filters import MessageFacts, content_hash, should_copy


@dataclass
class R:
    kind: str
    pattern: str
    replacement: str = ""
    enabled: bool = True


def cfg(**overrides):
    base = merged_settings(None)
    base.update(overrides)
    return base


def test_allows_plain_text_by_default():
    assert should_copy(MessageFacts(text="سلام"), cfg()).allowed


def test_blocks_disabled_media_kind():
    settings = cfg(allowed_media=["text"])
    decision = should_copy(MessageFacts(text="", media_kind="video"), settings)
    assert not decision.allowed
    assert "video" in decision.reason


def test_block_word_rejects():
    rules = [R("block", "قمار")]
    decision = should_copy(MessageFacts(text="سایت قمار آنلاین"), cfg(), rules)
    assert not decision.allowed
    assert "قمار" in decision.reason


def test_block_word_is_case_insensitive():
    rules = [R("block", "casino")]
    assert not should_copy(MessageFacts(text="Best CASINO ever"), cfg(), rules).allowed


def test_allow_list_rejects_when_no_match():
    rules = [R("allow", "ورزش")]
    assert not should_copy(MessageFacts(text="اخبار اقتصادی"), cfg(), rules).allowed
    assert should_copy(MessageFacts(text="اخبار ورزش"), cfg(), rules).allowed


def test_empty_allow_list_does_not_block():
    rules = [R("allow", "", enabled=True)]
    assert should_copy(MessageFacts(text="هر متنی"), cfg(), rules).allowed


def test_block_with_links():
    settings = cfg(block_with_links=True)
    assert not should_copy(MessageFacts(text="ببینید https://x.com"), settings).allowed
    assert should_copy(MessageFacts(text="بدون لینک"), settings).allowed


def test_block_forwarded():
    settings = cfg(block_forwarded=True)
    assert not should_copy(MessageFacts(text="متن", is_forwarded=True), settings).allowed


def test_block_buttons():
    settings = cfg(block_with_buttons=True)
    assert not should_copy(MessageFacts(text="متن", has_buttons=True), settings).allowed


def test_min_and_max_length():
    assert not should_copy(MessageFacts(text="کوتاه"), cfg(min_length=20)).allowed
    assert not should_copy(MessageFacts(text="ا" * 100), cfg(max_length=50)).allowed
    assert should_copy(MessageFacts(text="ا" * 30), cfg(min_length=10, max_length=50)).allowed


def test_size_limit():
    facts = MessageFacts(text="", media_kind="video", size_bytes=60 * 1024 * 1024)
    assert not should_copy(facts, cfg(skip_media_over_mb=50)).allowed
    assert should_copy(facts, cfg(skip_media_over_mb=0)).allowed


def test_ad_filter():
    settings = cfg(block_ads=True)
    assert not should_copy(MessageFacts(text="جهت تبلیغات: @ads"), settings).allowed
    assert should_copy(MessageFacts(text="خبر معمولی"), settings).allowed


def test_content_hash_is_stable_and_distinct():
    a = MessageFacts(text="یکسان", media_kind="photo")
    b = MessageFacts(text="یکسان", media_kind="photo")
    c = MessageFacts(text="متفاوت", media_kind="photo")
    assert content_hash(a) == content_hash(b)
    assert content_hash(a) != content_hash(c)
