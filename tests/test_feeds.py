"""تست خواندن فید RSS و Atom.

<b>چرا تست‌های امنیتی اینجا پررنگ‌اند.</b> آدرس فید را کاربر
می‌نویسد. یک آدرس بی‌ضررِ ظاهری می‌تواند سرور را وادار کند شبکه‌ی
داخلی خودش را بخواند و جوابش را در کانال آن کاربر منتشر کند — از
جمله پنل وبِ همین ربات که روی localhost گوش می‌دهد.
"""
from __future__ import annotations

import pytest

from telkap.services import feeds
from telkap.services.feeds import FeedError

RSS = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:media="http://search.yahoo.com/mrss/">
  <channel>
    <title>خبرگزاری نمونه</title>
    <link>https://example.com</link>
    <item>
      <title>خبر اول</title>
      <link>https://example.com/1</link>
      <guid>https://example.com/1</guid>
      <description>&lt;p&gt;متن &lt;b&gt;اول&lt;/b&gt;&lt;/p&gt;&lt;p&gt;پاراگراف دوم&lt;/p&gt;</description>
      <pubDate>Mon, 01 Sep 2026 10:00:00 +0000</pubDate>
      <enclosure url="https://example.com/1.jpg" type="image/jpeg" length="1000"/>
    </item>
    <item>
      <title>خبر دوم</title>
      <link>/2</link>
      <guid isPermaLink="false">tag:example.com,2026:2</guid>
      <description>متن دوم</description>
    </item>
  </channel>
</rss>
""".encode()

ATOM = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <title>وبلاگ نمونه</title>
  <entry>
    <title>مطلب اول</title>
    <id>urn:uuid:1</id>
    <link rel="alternate" href="https://blog.example.com/1"/>
    <link rel="edit" href="https://blog.example.com/edit/1"/>
    <published>2026-09-01T10:00:00Z</published>
    <summary>خلاصه‌ی مطلب</summary>
  </entry>
</feed>
""".encode()


# ── پارس ─────────────────────────────────────────────────────────────


def test_reads_a_plain_rss_feed():
    items = feeds.parse(RSS, base_url="https://example.com/feed")

    assert len(items) == 2
    assert items[0].title == "خبر اول"
    assert items[0].link == "https://example.com/1"
    assert items[0].image == "https://example.com/1.jpg"
    assert items[0].published is not None


def test_reads_an_atom_feed():
    items = feeds.parse(ATOM, base_url="https://blog.example.com/feed")

    assert len(items) == 1
    assert items[0].title == "مطلب اول"
    assert items[0].guid == "urn:uuid:1"
    assert items[0].summary == "خلاصه‌ی مطلب"


def test_atom_picks_the_readable_link_not_the_edit_one():
    """Atom چند لینک می‌دهد؛ فقط alternate آنی است که کاربر باید ببیند."""
    assert feeds.parse(ATOM)[0].link == "https://blog.example.com/1"


def test_relative_links_become_absolute():
    """لینک نسبی در تلگرام قابل کلیک نیست."""
    items = feeds.parse(RSS, base_url="https://example.com/feed")

    assert items[1].link == "https://example.com/2"


def test_html_in_the_description_becomes_readable_text():
    """تگ خام در کانال کاربر منتشر نمی‌شود، و پاراگراف‌ها به هم نمی‌چسبند."""
    summary = feeds.parse(RSS)[0].summary

    assert "<" not in summary
    assert summary == "متن اول\n\nپاراگراف دوم"


def test_entities_are_unescaped():
    assert feeds.clean_html("&quot;نقل&quot; &amp; قول") == '"نقل" & قول'


def test_an_item_with_neither_title_nor_body_is_dropped():
    """پست خالی در کانال کاربر از نبودنش بدتر است."""
    xml = """<rss><channel>
        <item><link>https://a.example/1</link></item>
        <item><title>واقعی</title></item>
    </channel></rss>""".encode()

    items = feeds.parse(xml)

    assert [item.title for item in items] == ["واقعی"]


def test_a_feed_with_no_items_is_an_error_not_an_empty_list():
    """اگر خالی برگردد، کاربر فکر می‌کند کار می‌کند و منتظر می‌ماند."""
    with pytest.raises(FeedError):
        feeds.parse("<rss><channel><title>خالی</title></channel></rss>".encode())


def test_broken_xml_gives_a_persian_error():
    with pytest.raises(FeedError, match="معتبر"):
        feeds.parse(b"<rss><channel><item>")


def test_html_page_is_rejected_as_not_a_feed():
    """کاربر معمولاً آدرس خودِ سایت را می‌زند، نه آدرس فید را."""
    with pytest.raises(FeedError):
        feeds.parse(b"<!DOCTYPE html><html><body>salam</body></html>")


def test_the_item_count_is_capped():
    body = b"".join(
        b"<item><title>t%d</title></item>" % i for i in range(feeds.MAX_ITEMS + 40)
    )
    items = feeds.parse(b"<rss><channel>" + body + b"</channel></rss>")

    assert len(items) == feeds.MAX_ITEMS


def test_feed_title_is_read_for_naming_the_job():
    assert feeds.feed_title(RSS) == "خبرگزاری نمونه"
    assert feeds.feed_title(ATOM) == "وبلاگ نمونه"
    assert feeds.feed_title(b"not xml") == ""


# ── شناسه‌ی پایدار ───────────────────────────────────────────────────


def test_the_same_item_always_gets_the_same_key():
    """اگر کلید ثابت نباشد، هر بار خواندن یعنی انتشار دوباره."""
    first = feeds.parse(RSS)[0]
    second = feeds.parse(RSS)[0]

    assert first.key == second.key


def test_different_items_get_different_keys():
    items = feeds.parse(RSS)

    assert items[0].key != items[1].key


def test_the_key_fits_in_a_signed_bigint():
    """ستون src_msg_id علامت‌دار است؛ عدد بزرگ‌تر آنجا جا نمی‌شود."""
    for item in feeds.parse(RSS):
        assert 0 <= item.key < 2**63


def test_an_item_without_a_guid_falls_back_to_its_link():
    items = feeds.parse("""<rss><channel>
        <item><title>ت</title><link>https://a.example/x</link></item>
    </channel></rss>""".encode())

    assert items[0].guid == "https://a.example/x"


def test_an_item_without_guid_or_link_still_deduplicates():
    """بدون این، آیتمِ بی‌شناسه هر بار دوباره منتشر می‌شد."""
    xml = "<rss><channel><item><title>بدون شناسه</title></item></channel></rss>".encode()

    assert feeds.parse(xml)[0].key == feeds.parse(xml)[0].key


# ── امنیت آدرس ───────────────────────────────────────────────────────


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1:8080/panel",     # پنل وب خودمان
        "http://localhost/",
        "http://[::1]/",
        "http://169.254.169.254/latest/meta-data/",   # متادیتای ابری
        "http://10.0.0.5/",
        "http://192.168.1.1/",
        "http://0.0.0.0/",
    ],
)
async def test_internal_addresses_are_refused(url):
    with pytest.raises(FeedError):
        await feeds.check_url(url)


@pytest.mark.asyncio
async def test_non_http_schemes_are_refused():
    """file:// یعنی خواندن فایل‌های خودِ سرور."""
    for url in ("file:///etc/passwd", "ftp://example.com/f", "gopher://a.example/"):
        with pytest.raises(FeedError, match="http"):
            await feeds.check_url(url)


@pytest.mark.asyncio
async def test_an_empty_address_is_refused():
    with pytest.raises(FeedError):
        await feeds.check_url("   ")


@pytest.mark.asyncio
async def test_a_bare_domain_gets_https(monkeypatch):
    """کاربر معمولاً «example.com/feed» می‌نویسد نه با https:// ."""
    async def fake_resolve(host):
        return ["93.184.216.34"]

    monkeypatch.setattr(feeds, "_resolve", fake_resolve)

    assert await feeds.check_url("example.com/feed") == "https://example.com/feed"


@pytest.mark.asyncio
async def test_a_public_address_passes(monkeypatch):
    async def fake_resolve(host):
        return ["93.184.216.34"]

    monkeypatch.setattr(feeds, "_resolve", fake_resolve)

    assert await feeds.check_url("https://example.com/rss") == "https://example.com/rss"


@pytest.mark.asyncio
async def test_a_public_name_pointing_at_a_private_ip_is_refused(monkeypatch):
    """<b>مهم‌ترین تست این فایل.</b>

    نام دامنه بی‌ضرر است ولی به ۱۲۷٫۰٫۰٫۱ حل می‌شود. اگر بررسی روی
    نام انجام می‌شد نه روی آی‌پیِ حل‌شده، این از فیلتر رد می‌شد.
    """
    async def fake_resolve(host):
        return ["127.0.0.1"]

    monkeypatch.setattr(feeds, "_resolve", fake_resolve)

    with pytest.raises(FeedError, match="داخلی"):
        await feeds.check_url("https://evil.example.com/rss")


@pytest.mark.asyncio
async def test_any_private_answer_is_enough_to_refuse(monkeypatch):
    """DNS چند جواب می‌دهد؛ یکی داخلی باشد کافی است که رد شود."""
    async def fake_resolve(host):
        return ["93.184.216.34", "10.1.2.3"]

    monkeypatch.setattr(feeds, "_resolve", fake_resolve)

    with pytest.raises(FeedError, match="داخلی"):
        await feeds.check_url("https://mixed.example.com/rss")


def test_a_non_ip_answer_counts_as_private():
    """چیزی که آی‌پی نیست، قابل اعتماد هم نیست."""
    assert feeds._is_private("چیز عجیب") is True
    assert feeds._is_private("93.184.216.34") is False
