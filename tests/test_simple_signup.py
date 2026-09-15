"""تست ساختِ کار در حالت ساده.

<b>چرا جریانِ جداگانه‌ای شد.</b> سه قدمِ ظاهراً یکسان، اینجا سه چیز
متفاوت‌اند: مبدأ با اکانتِ <i>ما</i> بررسی می‌شود (مشتری اکانتی ندارد)،
مقصد با <i>خودِ ربات</i>، و گلوگاهِ اولِ جریانِ قدیمی — «اکانت وصل است؟» —
دقیقاً همان چیزی است که اینجا نباید پرسیده شود.

<b>و چرا تست‌ها بیشتر روی «نه» تمرکز دارند.</b> مسیر موفق خودش را سرِ
اولین استفاده نشان می‌دهد. چیزی که فروش را می‌بندد، «نه»های بدِ این
جریان است: کاربری که کانال خصوصی می‌دهد و جوابِ مبهم می‌گیرد، یا ربات
را ادمین نکرده و نمی‌فهمد باید چه کند.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from tests.test_copier import _setup


class _Notice:
    def __init__(self) -> None:
        self.text = ""

    async def edit_text(self, text, **kwargs):
        self.text = text
        return self


class _Message:
    """پیامی که فقط یادداشت می‌کند چه جوابی به کاربر رفت."""

    def __init__(self, text: str = "", user_id: int = 7) -> None:
        self.text = text
        self.from_user = SimpleNamespace(id=user_id)
        self.replies: list[str] = []
        self.notices: list[_Notice] = []
        self.bot = None

    async def answer(self, text, **kwargs):
        self.replies.append(text)
        notice = _Notice()
        self.notices.append(notice)
        return notice

    @property
    def last_notice(self) -> str:
        """آخرین پیامِ وضعیتی که <b>واقعاً ویرایش شد</b>.

        مسیر موفق بعد از ویرایشِ «✅ مبدأ…» یک پیام تازه برای قدم بعد
        می‌فرستد. گرفتنِ صرفاً «آخرین» یعنی همان پیامِ خالی — که تست را
        روی مسیر موفق بی‌دلیل قرمز می‌کند.
        """
        for notice in reversed(self.notices):
            if notice.text:
                return notice.text
        return ""


class _State:
    def __init__(self) -> None:
        self.data: dict = {}
        self.state = None

    async def set_state(self, state) -> None:
        self.state = state

    async def get_data(self) -> dict:
        return dict(self.data)

    async def update_data(self, **kwargs) -> None:
        self.data.update(kwargs)

    async def clear(self) -> None:
        self.state = None
        self.data = {}


class _Entity:
    def __init__(self, *, username=None, title="کانال", id=1234) -> None:
        self.username = username
        self.title = title
        self.id = id


class _Client:
    def __init__(self, entity=None, error: Exception | None = None) -> None:
        self._entity, self._error = entity, error

    async def get_entity(self, ref):
        if self._error is not None:
            raise self._error
        return self._entity


def _pool(monkeypatch, client) -> None:
    from telkap.services import pool

    async def fake_any_client():
        if client is None:
            raise pool.NoAccount("هیچ اکانتی نیست")
        return client

    monkeypatch.setattr(pool, "any_client", fake_any_client)


def _quiet_alerts(monkeypatch) -> list[str]:
    from telkap.services import alerts

    said: list[str] = []

    async def fake_send(text, **kwargs):
        said.append(text)
        return 1

    monkeypatch.setattr(alerts, "send", fake_send)
    return said


# ------------------------------------------------------------------ مبدأ


@pytest.mark.asyncio
async def test_a_public_source_is_accepted_without_any_user_account(
    tmp_path, monkeypatch
):
    """<b>کلِ نکته:</b> هیچ‌جا پرسیده نمی‌شود اکانت وصل است یا نه."""
    db_module, _ = await _setup(tmp_path, monkeypatch, settings={})
    try:
        from telkap.handlers import simple_task

        _pool(monkeypatch, _Client(_Entity(username="varzesh3", title="ورزش سه")))
        message, state = _Message("@varzesh3"), _State()
        await simple_task.got_source(message, state)

        assert "ورزش سه" in message.last_notice
        assert state.data["source_id"] == -1001234
        assert state.state == simple_task.Flow.simple_dest
    finally:
        await db_module.close_db()


@pytest.mark.asyncio
async def test_a_private_source_is_refused_with_the_reason_and_the_way_out(
    tmp_path, monkeypatch
):
    """<b>«نه»ی بد، همان‌جا فروش را می‌بندد.</b>

    کانال بی‌نام‌کاربری را اکانت سرویس نمی‌تواند بدون عضو شدن بخواند،
    و عضو شدن لینک دعوت و تأیید ادمینِ مبدأ می‌خواهد — کاری که از
    دستِ مشتری برنمی‌آید. پس باید هم دلیلش گفته شود هم راهِ دیگرش.
    """
    db_module, _ = await _setup(tmp_path, monkeypatch, settings={})
    try:
        from telkap.handlers import simple_task

        _pool(monkeypatch, _Client(_Entity(username=None, title="خصوصی")))
        message, state = _Message("-1001234"), _State()
        await simple_task.got_source(message, state)

        text = message.last_notice
        assert "خصوصی" in text
        assert "اکانت خودتان" in text, "راهِ دیگر گفته نشد"
        assert state.state != simple_task.Flow.simple_dest
    finally:
        await db_module.close_db()


@pytest.mark.asyncio
async def test_with_no_reader_the_customer_is_not_left_guessing(tmp_path, monkeypatch):
    """<b>و ادمین هم باید بفهمد.</b>

    این یعنی یک مشتری همین حالا نتوانست کاری بسازد. اگر فقط به او
    «بعداً امتحان کنید» بگوییم و کسی خبردار نشود، همان «بعداً» هیچ‌وقت
    نمی‌آید.
    """
    db_module, _ = await _setup(tmp_path, monkeypatch, settings={})
    try:
        from telkap.handlers import simple_task

        _pool(monkeypatch, None)
        said = _quiet_alerts(monkeypatch)

        message, state = _Message("@varzesh3"), _State()
        await simple_task.got_source(message, state)

        assert "در دسترس نیست" in message.last_notice
        assert said, "ادمین خبردار نشد که مشتری زمین خورد"
    finally:
        await db_module.close_db()


# ------------------------------------------------------------------ مقصد


class _Chat:
    def __init__(self, chat_id=-100999, title="کانال من") -> None:
        self.id = chat_id
        self.title = title


class _Bot:
    def __init__(self, chat=None, member=None, error=None) -> None:
        self._chat, self._member, self._error = chat, member, error

    async def get_chat(self, ref):
        if self._error is not None:
            raise self._error
        return self._chat

    async def get_me(self):
        return SimpleNamespace(id=777)

    async def get_chat_member(self, chat_id, user_id):
        return self._member


class _Member:
    def __init__(self, status="administrator", can_post=True) -> None:
        self.status = status
        self.can_post_messages = can_post


def _bot(monkeypatch, bot) -> None:
    from telkap.services import alerts

    monkeypatch.setattr(alerts, "bot", lambda: bot)


@pytest.mark.asyncio
async def test_a_destination_the_bot_can_post_to_is_accepted(tmp_path, monkeypatch):
    db_module, _ = await _setup(tmp_path, monkeypatch, settings={})
    try:
        from telkap.handlers import simple_task

        _bot(monkeypatch, _Bot(_Chat(), _Member()))
        message, state = _Message("@mychannel"), _State()
        await simple_task.got_dest(message, state)

        assert state.data["dest_id"] == -100999
        assert state.state == simple_task.Flow.simple_title
    finally:
        await db_module.close_db()


@pytest.mark.asyncio
async def test_a_bot_that_is_not_admin_gets_the_exact_steps(tmp_path, monkeypatch):
    """<b>لحظه‌ای که بیشترین رهاکردن در آن اتفاق می‌افتد.</b>

    کاربر تا اینجا آمده و یک قدم مانده. «خطا» به دردش نمی‌خورد؛ باید
    بداند کدام دکمه را بزند — وگرنه همین‌جا می‌رود.
    """
    db_module, _ = await _setup(tmp_path, monkeypatch, settings={})
    try:
        from telkap.handlers import simple_task

        _bot(monkeypatch, _Bot(error=Exception("Bad Request: chat not found")))
        message, state = _Message("@mychannel"), _State()
        await simple_task.got_dest(message, state)

        text = message.last_notice
        assert "مدیران" in text and "ارسال پیام" in text
        # و کاربر همان‌جا می‌ماند تا دوباره بفرستد، نه اینکه از اول شروع کند
        assert state.state != simple_task.Flow.simple_title
    finally:
        await db_module.close_db()


@pytest.mark.asyncio
async def test_an_admin_without_posting_rights_is_caught_before_the_task_exists(
    tmp_path, monkeypatch
):
    """<b>چرا همین‌جا و نه سرِ اولین پست.</b>

    اگر این وارسی نباشد، کار ساخته می‌شود، کاربر فکر می‌کند تمام است،
    و خرابی ساعت‌ها بعد به‌شکل «هیچ پستی نیامد» پیدا می‌شود — که
    شبیه خرابیِ سرویس است، نه یک قدمِ جامانده.
    """
    db_module, _ = await _setup(tmp_path, monkeypatch, settings={})
    try:
        from telkap.handlers import simple_task

        _bot(monkeypatch, _Bot(_Chat(), _Member(can_post=False)))
        message, state = _Message("@mychannel"), _State()
        await simple_task.got_dest(message, state)

        assert "ارسال پیام" in message.last_notice
        assert state.state != simple_task.Flow.simple_title
    finally:
        await db_module.close_db()


# ------------------------------------------------------------------ ساخت


@pytest.mark.asyncio
async def test_the_task_is_created_in_simple_mode(tmp_path, monkeypatch):
    db_module, _ = await _setup(tmp_path, monkeypatch, settings={})
    try:
        from sqlalchemy import select

        from telkap.handlers import simple_task
        from telkap.models import Task

        monkeypatch.setattr(simple_task, "show_task", _noop)
        state = _State()
        state.data = {
            "source_ref": "@varzesh3", "source_title": "ورزش سه",
            "source_id": -1001234,
            "dest_ref": "@mychannel", "dest_title": "کانال من", "dest_id": -100999,
        }
        message = _Message("خبر ورزشی")
        await simple_task.got_title(message, state)

        async with db_module.get_session() as db:
            tasks = list(
                (await db.execute(select(Task).where(Task.mode == Task.MODE_SIMPLE)))
                .scalars()
            )
        assert len(tasks) == 1
        task = tasks[0]
        assert task.title == "خبر ورزشی"
        assert task.source_id == -1001234
        assert task.dest_id == -100999
    finally:
        await db_module.close_db()


@pytest.mark.asyncio
async def test_the_customer_is_told_the_archive_is_not_copied(tmp_path, monkeypatch):
    """<b>انتظاری که باید <i>پیش</i> از اولین پست تنظیم شود.</b>

    آرشیو کپی نمی‌شود. اگر همین حالا گفته نشود، مشتری کانالش را نگاه
    می‌کند، خالی می‌بیند و فکر می‌کند کار نمی‌کند — در حالی که فقط
    هنوز پستِ تازه‌ای در مبدأ نیامده.
    """
    db_module, _ = await _setup(tmp_path, monkeypatch, settings={})
    try:
        from telkap.handlers import simple_task

        monkeypatch.setattr(simple_task, "show_task", _noop)
        state = _State()
        state.data = {
            "source_ref": "@a", "source_title": "الف", "source_id": -1001,
            "dest_ref": "@b", "dest_title": "ب", "dest_id": -1002,
        }
        message = _Message("-")
        await simple_task.got_title(message, state)

        said = " ".join(message.replies)
        assert "قبلی منتشر نمی‌شوند" in said, "درباره‌ی آرشیو چیزی گفته نشد"
    finally:
        await db_module.close_db()


async def _noop(*args, **kwargs) -> None:
    return None


def test_the_old_dead_end_now_offers_a_way_through():
    """<b>بن‌بستی که فروش را می‌بست.</b>

    «اول اکانتت را وصل کن» تنها جوابِ ساختِ کار بود — دقیقاً همان
    جمله‌ای که مشتری‌ها می‌گویند به‌خاطرش تست نمی‌کنند.
    """
    from pathlib import Path

    source = (
        Path(__file__).parent.parent / "telkap" / "handlers" / "tasks.py"
    ).read_text(encoding="utf-8")

    gate = source.split("if user is None or not user.is_logged_in:", 1)[1][:600]
    assert "offer(" in gate, "بن‌بست هنوز سر جایش است"


@pytest.mark.asyncio
async def test_the_customer_is_warned_about_premium_emoji_up_front(
    tmp_path, monkeypatch
):
    """<b>محدودیتی که خودش را سرِ اولین پست نشان می‌دهد.</b>

    تلگرام اجازه‌ی فرستادنِ ایموجی پریمیوم را فقط به رباتی می‌دهد که
    روی Fragment یوزرنیم خریده باشد؛ راهِ دومش (صاحبِ ربات پریمیوم
    دارد) فقط در چت خصوصی و گروه کار می‌کند و <b>کانال در آن فهرست
    نیست</b>. پس در حالت ساده پست با ایموجیِ معمولیِ زیرش می‌رسد.

    این خودش خرابی نیست — ولی اگر از قبل گفته نشود، مشتری آن را
    خرابی می‌بیند. و بدتر: فکر می‌کند اشتراکش کم است و پول بیشتری
    می‌دهد برای چیزی که با پول درست نمی‌شود.
    """
    db_module, _ = await _setup(tmp_path, monkeypatch, settings={})
    try:
        from telkap.handlers import simple_task

        monkeypatch.setattr(simple_task, "show_task", _noop)
        state = _State()
        state.data = {
            "source_ref": "@a", "source_title": "الف", "source_id": -1001,
            "dest_ref": "@b", "dest_title": "ب", "dest_id": -1002,
        }
        message = _Message("-")
        await simple_task.got_title(message, state)

        said = " ".join(message.replies)
        assert "پریمیوم" in said, "درباره‌ی ایموجی پریمیوم چیزی گفته نشد"
    finally:
        await db_module.close_db()


@pytest.mark.asyncio
async def test_the_mode_comparison_names_both_real_limits():
    """<b>صفحه‌ای که مشتری پیش از انتخاب می‌خواند.</b>

    اگر فقط «مبدأ باید عمومی باشد» را بگوید، ایموجی پریمیوم بعداً
    به‌شکل غافلگیری می‌آید — و غافلگیریِ بعد از خرید، همان چیزی است که
    مشتری را برمی‌گرداند. هر دو محدودیت باید <b>همین‌جا</b> باشند.
    """
    from telkap.handlers import simple_task

    said: list[str] = []

    class _Call:
        data = "simple:why"

        class message:
            @staticmethod
            async def answer(text, **kwargs):
                said.append(text)

        @staticmethod
        async def answer(*args, **kwargs):
            return None

    await simple_task.cb_why(_Call())

    text = " ".join(said)
    assert "عمومی" in text, "محدودیتِ «مبدأ عمومی» گفته نشد"
    assert "پریمیوم" in text, "محدودیتِ ایموجی پریمیوم گفته نشد"
