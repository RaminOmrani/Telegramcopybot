"""تست‌های خط لوله‌ی تبدیل متن."""
from __future__ import annotations

from dataclasses import dataclass

import pytest

from telkap.services.defaults import merged_settings
from telkap.services.transform import apply_transforms, looks_like_ad, strip_signature


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


def test_word_replacement():
    rules = [R("replace", "الماس", "جواهر")]
    out = apply_transforms("قیمت الماس امروز", cfg(remove_source_signature=False), rules)
    assert out == "قیمت جواهر امروز"


def test_replacement_can_delete_word():
    rules = [R("replace", "تبلیغ", "")]
    out = apply_transforms("این یک تبلیغ است", cfg(remove_source_signature=False), rules)
    assert "تبلیغ" not in out


def test_disabled_rule_is_ignored():
    rules = [R("replace", "الف", "ب", enabled=False)]
    out = apply_transforms("الف", cfg(remove_source_signature=False), rules)
    assert out == "الف"


def test_regex_rule():
    rules = [R("regex", r"\d{4}", "****")]
    out = apply_transforms("کد 1234 است", cfg(remove_source_signature=False), rules)
    assert out == "کد **** است"


def test_invalid_regex_does_not_crash():
    rules = [R("regex", "[unclosed", "x")]
    out = apply_transforms("متن سالم", cfg(remove_source_signature=False), rules)
    assert out == "متن سالم"


def test_remove_links():
    text = "خبر مهم https://example.com/page و t.me/somechannel"
    out = apply_transforms(text, cfg(remove_links=True, remove_source_signature=False))
    assert "example.com" not in out
    assert "t.me" not in out
    assert "خبر مهم" in out


def test_remove_hashtags_keeps_words():
    out = apply_transforms("خبر #ورزشی امروز", cfg(remove_hashtags=True, remove_source_signature=False))
    assert "#ورزشی" not in out
    assert "خبر" in out and "امروز" in out


def test_remove_mentions():
    settings = cfg(remove_mentions=True, remove_source_signature=False)
    out = apply_transforms("سلام @somechannel بدرود", settings)
    assert "@somechannel" not in out


def test_signature_stripped_from_end():
    text = "متن اصلی پست\n\n@sourcechannel"
    out = apply_transforms(text, cfg(remove_source_signature=True))
    assert out == "متن اصلی پست"


def test_signature_replaced_with_own_id():
    text = "متن اصلی پست\n\n@sourcechannel"
    out = apply_transforms(text, cfg(remove_source_signature=True, signature="@mychannel"))
    assert "@sourcechannel" not in out
    assert out.endswith("@mychannel")
    assert out.startswith("متن اصلی پست")


def test_signature_with_link_form():
    out = strip_signature("پست\n\nhttps://t.me/source")
    assert out == "پست"


def test_signature_does_not_eat_body_text():
    text = "خط اول\nخط دوم با @user در وسط"
    assert strip_signature(text) == text


def test_header_and_footer():
    out = apply_transforms(
        "بدنه",
        cfg(header="🔥 داغ", footer="@mychannel", remove_source_signature=False),
    )
    assert out.splitlines()[0] == "🔥 داغ"
    assert out.splitlines()[-1] == "@mychannel"
    assert "بدنه" in out


def test_empty_text_with_footer_only():
    out = apply_transforms("", cfg(footer="@mychannel", remove_source_signature=False))
    assert out == "@mychannel"


def test_collapses_extra_blank_lines():
    out = apply_transforms("الف\n\n\n\nب", cfg(remove_source_signature=False))
    assert out == "الف\n\nب"


@pytest.mark.parametrize(
    "text,expected",
    [
        ("جهت تبلیغات با ادمین تبلیغات در ارتباط باشید", True),
        ("سفارش تبلیغ: @adsadmin", True),
        ("امروز هوا آفتابی است", False),
        ("", False),
    ],
)
def test_ad_detection(text, expected):
    assert looks_like_ad(text) is expected
