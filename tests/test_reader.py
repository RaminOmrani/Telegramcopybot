"""تست «چه کسی این مبدأ را می‌خواند».

<b>چرا این ماژول لازم شد.</b> با آمدنِ حالت ساده، سؤالِ «کلاینتِ این کار
کدام است» دو جواب پیدا کرد. ولی این سؤال در <b>سه جا</b> پرسیده می‌شود —
موتور کپی، صف تلاش مجدد، صف تأیید — و هر سه جوابِ قدیمی را می‌دادند.

<b>و شکلِ خرابی‌اش بی‌صداترین ممکن بود:</b> پستِ یک مشتریِ حالت ساده که
بار اول نمی‌رفت، در صف تلاش مجدد می‌نشست و آنجا هر بار به «اکانت کاربری
متصل نیست» می‌خورد تا سقفِ تلاش‌ها تمام شود و دور ریخته شود. نه خطایی که
به چشم بیاید، نه پستی که برسد. در صف تأیید بدتر بود: پست <b>هیچ‌وقت</b>
آزاد نمی‌شد.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from tests.test_copier import _setup


class _Manager:
    def __init__(self, client=None) -> None:
        self.client = client
        self.asked: list[int] = []

    async def ensure_client(self, user_id):
        self.asked.append(user_id)
        return self.client


def _service_account(monkeypatch, client):
    """استخری که همیشه همین کلاینت را می‌دهد."""
    from telkap.services import pool

    async def fake_lease(source_id, source_ref=""):
        return SimpleNamespace(id=1)

    async def fake_client_for(account):
        return client

    async def fake_any():
        return client

    monkeypatch.setattr(pool, "lease", fake_lease)
    monkeypatch.setattr(pool, "client_for", fake_client_for)
    monkeypatch.setattr(pool, "any_client", fake_any)


def _task(mode: str, **kwargs):
    from telkap.models import Task

    return SimpleNamespace(
        id=1, mode=mode, source_id=kwargs.get("source_id", -1001),
        source_ref="@ch", user_id=7,
    ) if mode else Task()


@pytest.mark.asyncio
async def test_a_full_task_reads_with_the_customers_account(tmp_path, monkeypatch):
    from telkap.models import Task
    from telkap.services import reader

    account = object()
    manager = _Manager(account)
    got = await reader.for_task(_task(Task.MODE_FULL), manager)

    assert got is account
    assert manager.asked == [7], "اکانت مشتری اصلاً پرسیده نشد"


@pytest.mark.asyncio
async def test_a_simple_task_never_asks_for_the_customers_account(
    tmp_path, monkeypatch
):
    """<b>کلِ نکته.</b> مشتریِ حالت ساده اکانتی ندارد؛ پرسیدنش یعنی
    جوابِ None و بعدش یک خرابیِ ساختگی."""
    from telkap.models import Task
    from telkap.services import reader

    service = object()
    _service_account(monkeypatch, service)
    manager = _Manager(None)

    got = await reader.for_task(_task(Task.MODE_SIMPLE), manager)

    assert got is service
    assert manager.asked == [], "بی‌دلیل سراغ اکانت مشتری رفت"


@pytest.mark.asyncio
async def test_with_no_service_account_the_simple_task_gets_nothing(monkeypatch):
    from telkap.models import Task
    from telkap.services import pool, reader

    async def boom(*args, **kwargs):
        raise pool.NoAccount("هیچ اکانتی نیست")

    monkeypatch.setattr(pool, "lease", boom)

    assert await reader.for_task(_task(Task.MODE_SIMPLE), _Manager(None)) is None


def test_the_reason_matches_the_mode():
    """<b>دلیلِ درست، برای دو وضعیتِ کاملاً متفاوت.</b>

    «اکانت کاربری متصل نیست» به مشتریِ حالت ساده گفتن، او را دنبال
    کاری می‌فرستد که اصلاً لازم نیست انجامش دهد — و مشکل سمتِ ماست،
    نه او.
    """
    from telkap.models import Task
    from telkap.services import reader

    assert "اکانت کاربری" in reader.why_missing(_task(Task.MODE_FULL))
    assert "اکانت کاربری" not in reader.why_missing(_task(Task.MODE_SIMPLE))


# ------------------------------------------- صف‌ها، با مشتریِ بدونِ اکانت


class _Messages:
    def __init__(self, messages) -> None:
        self.messages = messages

    async def get_input_entity(self, ref):
        # هر کلاینت واقعی این را دارد. کش گرم فرض می‌شود؛ حالتِ سردش
        # پایین‌تر جداگانه سنجیده شده.
        return ref

    async def get_messages(self, chat_id, ids=None):
        return list(self.messages)


class _Copier:
    def __init__(self) -> None:
        self.sent: list[int] = []

    async def process(self, user_id, task_id, messages, **kwargs) -> bool:
        self.sent.append(task_id)
        return True


async def _simple(db_module, task_id: int) -> None:
    from telkap.models import Task

    async with db_module.get_session() as db:
        task = await db.get(Task, task_id)
        task.mode = Task.MODE_SIMPLE
        task.source_id = -1001
        await db.commit()


@pytest.mark.asyncio
async def test_a_simple_customers_retry_actually_retries(tmp_path, monkeypatch):
    """<b>خرابیِ بی‌صدایی که این تست جلویش را می‌گیرد.</b>

    پستی که بار اول نرفت، در صف می‌نشست و هر بار به «اکانت کاربری
    متصل نیست» می‌خورد تا سقفِ تلاش‌ها تمام شود و دور ریخته شود —
    برای مشتری‌ای که اصلاً قرار نبود اکانتی داشته باشد.
    """
    db_module, task_id = await _setup(tmp_path, monkeypatch, settings={})
    try:
        from telkap.models import RetryItem
        from telkap.services.retry import RetryWorker
        from tests.test_copier import FakeMessage

        await _simple(db_module, task_id)
        _service_account(monkeypatch, _Messages([FakeMessage(id=5, message="سلام")]))

        async with db_module.get_session() as db:
            db.add(
                RetryItem(
                    task_id=task_id, user_id=7, src_chat_id=-1001,
                    src_msg_ids="5",
                )
            )
            await db.commit()

        copier = _Copier()
        worker = RetryWorker(_Manager(None), copier)
        assert await worker.run_once() == 1, "تلاش مجدد برای مشتریِ بدون اکانت نشد"
        assert copier.sent == [task_id]
    finally:
        await db_module.close_db()


@pytest.mark.asyncio
async def test_a_simple_customers_approval_queue_actually_releases(
    tmp_path, monkeypatch
):
    """<b>و اینجا بدتر بود:</b> پست هیچ‌وقت آزاد نمی‌شد. نه دور ریخته
    می‌شد نه می‌رفت — تا ابد در صف."""
    db_module, task_id = await _setup(tmp_path, monkeypatch, settings={})
    try:
        from telkap.models import PendingPost
        from telkap.services.pending import ReleaseWorker
        from tests.test_copier import FakeMessage

        await _simple(db_module, task_id)
        _service_account(monkeypatch, _Messages([FakeMessage(id=5, message="سلام")]))

        async with db_module.get_session() as db:
            row = PendingPost(
                task_id=task_id, user_id=7, src_chat_id=-1001,
                src_msg_ids="5", reason=PendingPost.REASON_APPROVAL,
            )
            db.add(row)
            await db.commit()
            await db.refresh(row)
            item = row

        copier = _Copier()
        worker = ReleaseWorker(_Manager(None), copier)
        assert await worker.release(item), "پست در صف تأیید آزاد نشد"
        assert copier.sent == [task_id]
    finally:
        await db_module.close_db()


# ------------------------------------------------- resolve کردن مبدأ


class _ColdClient:
    """کلاینتی که تازه بالا آمده — کشِ موجودیتش خالی است.

    <b>این دقیقاً وضعیتِ هر اکانت سرویس پس از هر ری‌استارت است.</b>
    سشنِ رشته‌ای فقط کلید احراز هویت را نگه می‌دارد؛ access_hash ها
    نه. پس آیدی عددی برای Telethon بی‌معناست تا وقتی یک بار از روی
    نام resolve شود.
    """

    def __init__(self, warm: bool = False) -> None:
        self.warm = warm
        self.by_name: list[str] = []
        self.by_id: list[int] = []

    async def get_input_entity(self, ref):
        if isinstance(ref, int):
            self.by_id.append(ref)
            if self.warm:
                return f"cached:{ref}"
            raise ValueError(
                f"Could not find the input entity for PeerChannel({ref})"
            )
        self.by_name.append(ref)
        return f"resolved:{ref}"


@pytest.mark.asyncio
async def test_a_cold_session_falls_back_to_the_channel_name():
    """<b>خرابی‌ای که کلِ حالت ساده را بعد از اولین ری‌استارت خواباند.</b>

    کار در همان اجرایی که ساخته شده بود کار می‌کرد — چون نام کانال
    همان لحظه resolve شده و در حافظه بود. بعد از ری‌استارت، هر دقیقه
    با «Could not find the input entity» می‌ترکید: اکانت سالم، کار
    روشن، بدون خطا در دیتابیس، و هیچ پستی نمی‌آمد.
    """
    from telkap.services import reader

    client = _ColdClient()
    got = await reader.entity_for(client, -1001000431759, "@varzesh3")

    assert got == "resolved:@varzesh3"
    assert client.by_name == ["@varzesh3"]


@pytest.mark.asyncio
async def test_a_warm_cache_is_used_and_the_name_is_not_looked_up():
    """resolve کردن یک درخواستِ شبکه است. هر دقیقه، برای هر مبدأ،
    تا ابد — همان چیزی که اکانت را به محدودیت نرخ می‌رساند."""
    from telkap.services import reader

    client = _ColdClient(warm=True)
    got = await reader.entity_for(client, -1001000431759, "@varzesh3")

    assert got == "cached:-1001000431759"
    assert client.by_name == [], "با وجود کش، باز هم از تلگرام پرسید"


@pytest.mark.asyncio
async def test_a_numeric_ref_is_not_mistaken_for_a_username():
    """کارهای قدیمی `source_ref` عددی دارند. فرستادنش به
    `get_input_entity` به‌عنوان نام، فقط همان خطای اول را تکرار
    می‌کند با پیامی گیج‌کننده‌تر."""
    from telkap.services import reader

    client = _ColdClient()
    got = await reader.entity_for(client, -1001000431759, "-1001000431759")

    assert got == -1001000431759
    assert client.by_name == []
