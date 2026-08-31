"""مدیریت اکانت‌های کاربری (Telethon) و اتصال آن‌ها به موتور کپی.

چرا اکانت کاربری و نه صرفاً Bot API؟ چون ربات‌های تلگرام فقط پیام کانالی
را می‌بینند که در آن ادمین باشند. برای «کپی بدون نیاز به ادمین بودن» و
«کپی از کانال خصوصی» و «کپی پیام‌های گذشته» باید با اکانت کاربری خود
کاربر (که عضو یا بیننده‌ی کانال است) به تلگرام وصل شد.
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field

from sqlalchemy import select
from telethon import TelegramClient, events
from telethon.errors import (
    AuthKeyUnregisteredError,
    PhoneCodeExpiredError,
    PhoneCodeInvalidError,
    PhoneNumberInvalidError,
    SessionPasswordNeededError,
    SessionRevokedError,
)
from telethon.sessions import StringSession
from telethon.tl.functions.messages import CheckChatInviteRequest
from telethon.utils import get_peer_id

from telkap import proxy
from telkap.config import get_settings
from telkap.crypto import decrypt, encrypt
from telkap.db import get_session, log_activity
from telkap.models import Task, User
from telkap.services import health

log = logging.getLogger(__name__)


class LoginError(Exception):
    """خطای قابل نمایش به کاربر در جریان ورود."""


@dataclass
class PendingLogin:
    client: TelegramClient
    phone: str
    phone_code_hash: str = ""
    needs_password: bool = False


@dataclass
class UserRuntime:
    client: TelegramClient
    handlers: list = field(default_factory=list)
    source_map: dict[int, list[int]] = field(default_factory=dict)  # chat_id -> [task_id]


class UserbotManager:
    """چرخه‌ی حیات همه‌ی کلاینت‌های کاربری."""

    def __init__(self) -> None:
        self._runtimes: dict[int, UserRuntime] = {}
        self._pending: dict[int, PendingLogin] = {}
        self._locks: dict[int, asyncio.Lock] = {}
        self._copier = None  # به‌صورت تنبل ست می‌شود تا وابستگی حلقوی نشود
        self._notifier = None

    def bind_copier(self, copier) -> None:
        self._copier = copier

    def bind_notifier(self, notifier) -> None:
        """تابعی که پیام به کاربر می‌فرستد، برای هشدارهای سلامت اکانت."""
        self._notifier = notifier

    # ------------------------------------------------------------------ ورود
    def _new_client(self, session: str = "") -> TelegramClient:
        cfg = get_settings()
        client = TelegramClient(
            StringSession(session),
            cfg.api_id,
            cfg.api_hash,
            # اثر انگشت باید شبیه یک کلاینت معمولی باشد. نام قبلی
            # («Copy Bot») عملاً خودِ اکانت را به‌عنوان ابزار خودکار معرفی
            # می‌کرد — هم در فهرست دستگاه‌های کاربر، هم برای تلگرام.
            device_model=cfg.device_model,
            system_version=cfg.system_version,
            app_version=cfg.app_version,
            lang_code="fa",
            system_lang_code="fa",
            proxy=proxy.for_telethon(cfg.proxy_url),
            connection_retries=5,
            retry_delay=2,
        )
        # فرمت‌ها را خودمان با entity می‌فرستیم؛ اگر Telethon متن را دوباره
        # به‌عنوان markdown تفسیر کند، نویسه‌هایی مثل * و _ متن را خراب می‌کنند
        client.parse_mode = None
        return client

    async def start_login(self, user_id: int, phone: str) -> None:
        """کد ورود را به شماره‌ی کاربر ارسال می‌کند."""
        await self.cancel_login(user_id)
        client = self._new_client()
        await client.connect()
        try:
            sent = await client.send_code_request(phone)
        except PhoneNumberInvalidError as exc:
            await client.disconnect()
            raise LoginError("شماره تلفن نامعتبر است. با فرمت +98... وارد کنید.") from exc
        except Exception as exc:
            await client.disconnect()
            raise LoginError(f"ارسال کد ناموفق بود: {exc}") from exc
        self._pending[user_id] = PendingLogin(
            client=client, phone=phone, phone_code_hash=sent.phone_code_hash
        )

    def pending(self, user_id: int) -> PendingLogin | None:
        return self._pending.get(user_id)

    async def submit_code(self, user_id: int, code: str) -> bool:
        """کد را بررسی می‌کند. True یعنی ورود کامل شد، False یعنی رمز دو مرحله‌ای لازم است."""
        pending = self._pending.get(user_id)
        if not pending:
            raise LoginError("جریان ورود منقضی شده است. دوباره /login را بزنید.")
        try:
            await pending.client.sign_in(
                phone=pending.phone, code=code, phone_code_hash=pending.phone_code_hash
            )
        except SessionPasswordNeededError:
            pending.needs_password = True
            return False
        except PhoneCodeInvalidError as exc:
            raise LoginError("کد وارد شده نادرست است. دوباره تلاش کنید.") from exc
        except PhoneCodeExpiredError as exc:
            await self.cancel_login(user_id)
            raise LoginError("کد منقضی شده است. دوباره /login را بزنید.") from exc
        await self._finish_login(user_id, pending)
        return True

    async def submit_password(self, user_id: int, password: str) -> bool:
        pending = self._pending.get(user_id)
        if not pending:
            raise LoginError("جریان ورود منقضی شده است. دوباره /login را بزنید.")
        try:
            await pending.client.sign_in(password=password)
        except Exception as exc:
            raise LoginError("رمز دو مرحله‌ای نادرست است. دوباره تلاش کنید.") from exc
        await self._finish_login(user_id, pending)
        return True

    async def _finish_login(self, user_id: int, pending: PendingLogin) -> None:
        me = await pending.client.get_me()
        session_string = pending.client.session.save()
        async with get_session() as db:
            user = await db.get(User, user_id)
            if user is None:
                raise LoginError("کاربر یافت نشد.")
            user.session_enc = encrypt(session_string)
            user.phone = pending.phone
            user.account_id = me.id
            user.account_name = " ".join(filter(None, [me.first_name, me.last_name])) or me.username
            user.account_premium = bool(getattr(me, "premium", False))
            await db.commit()
        self._pending.pop(user_id, None)
        # همان کلاینت متصل را به‌عنوان رانتایم نگه می‌داریم
        self._runtimes[user_id] = UserRuntime(client=pending.client)
        await self.reload_user(user_id)
        await log_activity(user_id=user_id, event="login", detail=f"اکانت {me.id} متصل شد")

    async def cancel_login(self, user_id: int) -> None:
        pending = self._pending.pop(user_id, None)
        if pending:
            try:
                await pending.client.disconnect()
            except Exception:
                log.debug("قطع کلاینت در حال ورود ناموفق بود", exc_info=True)

    # -------------------------------------------------------------- کلاینت‌ها
    def _lock(self, user_id: int) -> asyncio.Lock:
        if user_id not in self._locks:
            self._locks[user_id] = asyncio.Lock()
        return self._locks[user_id]

    def get_client(self, user_id: int) -> TelegramClient | None:
        runtime = self._runtimes.get(user_id)
        return runtime.client if runtime else None

    async def ensure_client(self, user_id: int) -> TelegramClient | None:
        """کلاینت متصل کاربر را برمی‌گرداند و در صورت نیاز از دیتابیس می‌سازد."""
        async with self._lock(user_id):
            runtime = self._runtimes.get(user_id)
            if runtime and runtime.client.is_connected():
                return runtime.client

            async with get_session() as db:
                user = await db.get(User, user_id)
                if user is None or not user.session_enc:
                    return None
                session_string = decrypt(user.session_enc)

            if not session_string:
                log.error("رمزگشایی سشن کاربر %s ناموفق بود", user_id)
                return None

            client = self._new_client(session_string)
            try:
                await client.connect()
                if not await client.is_user_authorized():
                    raise AuthKeyUnregisteredError(request=None)
            except (AuthKeyUnregisteredError, SessionRevokedError) as exc:
                # تا امروز اینجا بی‌صدا خروج انجام می‌شد و کاربر فقط
                # می‌دید ربات کار نمی‌کند؛ حالا دلیلش را می‌گوییم
                log.warning("سشن کاربر %s باطل شده است", user_id)
                await health.record(user_id, health.classify(exc), notifier=self._notifier)
                await self.logout(user_id, revoked=True)
                return None
            except Exception as exc:
                log.exception("اتصال کلاینت کاربر %s ناموفق بود", user_id)
                diagnosis = health.classify(exc)
                if diagnosis.state != health.STATE_OK:
                    await health.record(user_id, diagnosis, notifier=self._notifier)
                return None

            await health.clear(user_id)          # اتصال موفق یعنی دوباره سالم است
            self._runtimes[user_id] = UserRuntime(client=client)
            return client

    async def logout(self, user_id: int, *, revoked: bool = False) -> None:
        # سشن را روی سرور تلگرام هم باطل می‌کنیم تا رشته‌ی ذخیره‌شده بی‌اثر شود
        if not revoked:
            runtime = self._runtimes.get(user_id)
            if runtime and runtime.client.is_connected():
                try:
                    await runtime.client.log_out()
                except Exception:
                    log.debug("ابطال سشن روی تلگرام ناموفق بود", exc_info=True)
        await self.stop_user(user_id)
        async with get_session() as db:
            user = await db.get(User, user_id)
            if user:
                user.session_enc = None
                user.account_id = None
                user.account_name = None
                await db.commit()
        await log_activity(
            user_id=user_id,
            event="logout",
            detail="سشن باطل شد" if revoked else "خروج توسط کاربر",
            level="warning" if revoked else "info",
        )

    async def stop_user(self, user_id: int) -> None:
        runtime = self._runtimes.pop(user_id, None)
        if not runtime:
            return
        for handler in runtime.handlers:
            try:
                runtime.client.remove_event_handler(handler)
            except Exception:
                log.debug("حذف هندلر ناموفق بود", exc_info=True)
        try:
            await runtime.client.disconnect()
        except Exception:
            log.debug("قطع کلاینت ناموفق بود", exc_info=True)

    # ------------------------------------------------------------- هندلرها
    async def reload_user(self, user_id: int) -> int:
        """هندلرهای کاربر را بر اساس کارهای فعال او دوباره می‌سازد.

        تعداد کارهای فعال ثبت‌شده را برمی‌گرداند.
        """
        client = await self.ensure_client(user_id)
        if client is None or self._copier is None:
            return 0

        runtime = self._runtimes[user_id]
        for handler in runtime.handlers:
            try:
                client.remove_event_handler(handler)
            except Exception:
                log.debug("حذف هندلر قدیمی ناموفق بود", exc_info=True)
        runtime.handlers.clear()
        runtime.source_map.clear()

        async with get_session() as db:
            rows = await db.execute(
                select(Task).where(Task.user_id == user_id, Task.enabled.is_(True))
            )
            tasks = list(rows.scalars())

        chat_ids: list[int] = []
        for task in tasks:
            resolved = task.source_id or await self.resolve_chat_id(client, task.source_ref)
            if resolved is None:
                log.warning("کانال مبدا «%s» برای کار %s پیدا نشد", task.source_ref, task.id)
                continue
            if task.source_id != resolved:
                async with get_session() as db:
                    db_task = await db.get(Task, task.id)
                    if db_task:
                        db_task.source_id = resolved
                        await db.commit()
            runtime.source_map.setdefault(resolved, []).append(task.id)
            chat_ids.append(resolved)

        if not chat_ids:
            return 0

        copier = self._copier
        new_handler = copier.make_new_message_handler(user_id)
        album_handler = copier.make_album_handler(user_id)
        edit_handler = copier.make_edit_handler(user_id)
        delete_handler = copier.make_delete_handler(user_id)

        client.add_event_handler(new_handler, events.NewMessage(chats=chat_ids))
        client.add_event_handler(album_handler, events.Album(chats=chat_ids))
        client.add_event_handler(edit_handler, events.MessageEdited(chats=chat_ids))
        client.add_event_handler(delete_handler, events.MessageDeleted(chats=chat_ids))
        runtime.handlers.extend([new_handler, album_handler, edit_handler, delete_handler])

        log.info("کاربر %s: %d کار فعال روی %d کانال", user_id, len(tasks), len(set(chat_ids)))
        return len(tasks)

    def tasks_for_chat(self, user_id: int, chat_id: int) -> list[int]:
        runtime = self._runtimes.get(user_id)
        if not runtime:
            return []
        return runtime.source_map.get(chat_id, [])

    # ------------------------------------------------------------- کمکی‌ها
    @staticmethod
    async def resolve_chat_id(client: TelegramClient, ref: str) -> int | None:
        """آیدی عددی نشان‌دار کانال را از یوزرنیم/لینک/آیدی به دست می‌آورد.

        خروجی همان قالبی است که رویدادها برمی‌گردانند (مثلاً ‎-100…‎ برای کانال).
        """
        entity = await UserbotManager.resolve_entity(client, ref)
        if entity is None:
            return None
        try:
            return get_peer_id(entity)
        except Exception:
            log.debug("get_peer_id برای «%s» ناموفق بود", ref, exc_info=True)
            return None

    @staticmethod
    async def resolve_entity(client: TelegramClient, ref: str):
        """entity تلگرام را از یوزرنیم، لینک دعوت، یا آیدی عددی می‌سازد."""
        ref = (ref or "").strip()
        if not ref:
            return None
        try:
            if ref.lstrip("-").isdigit():
                return await client.get_entity(int(ref))
            cleaned = ref.replace("https://", "").replace("http://", "")
            cleaned = cleaned.replace("t.me/", "").replace("telegram.me/", "")
            cleaned = cleaned.lstrip("@").strip("/")
            if cleaned.startswith("+") or cleaned.startswith("joinchat/"):
                # لینک دعوت کانال خصوصی: باید از قبل عضو باشیم
                invite = cleaned.removeprefix("joinchat/").lstrip("+")
                result = await client(CheckChatInviteRequest(invite))
                chat = getattr(result, "chat", None)
                if chat is None:
                    raise LoginError("برای کانال خصوصی باید ابتدا با اکانت خود عضو شوید.")
                return chat
            return await client.get_entity(cleaned)
        except LoginError:
            raise
        except Exception:
            log.debug("resolve نشدن «%s»", ref, exc_info=True)
            return None

    async def restore_all(self) -> None:
        """در استارتاپ، همه‌ی کاربرانی که سشن دارند را دوباره وصل می‌کند."""
        async with get_session() as db:
            rows = await db.execute(select(User.id).where(User.session_enc.is_not(None)))
            user_ids = [row for row in rows.scalars()]
        if not user_ids:
            log.info("هیچ سشن ذخیره‌شده‌ای برای بازیابی نبود")
            return
        log.info("بازیابی %d سشن کاربری...", len(user_ids))
        for user_id in user_ids:
            try:
                await self.reload_user(user_id)
            except Exception:
                log.exception("بازیابی کاربر %s ناموفق بود", user_id)

    async def shutdown(self) -> None:
        for user_id in list(self._runtimes):
            await self.stop_user(user_id)
        for user_id in list(self._pending):
            await self.cancel_login(user_id)


manager = UserbotManager()
