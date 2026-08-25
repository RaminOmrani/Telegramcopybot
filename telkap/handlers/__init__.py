"""روترهای ربات. ترتیب ثبت مهم است: جریان‌های حالت‌دار پیش از هندلرهای عمومی."""
from aiogram import Router

from telkap.handlers import (
    account,
    admin,
    admin_channels,
    admin_users,
    billing,
    chatpicker,
    clone,
    destinations,
    forward,
    guide,
    history,
    preview,
    settings,
    start,
    tasks,
)


def build_router() -> Router:
    root = Router(name="root")
    root.include_router(start.router)
    root.include_router(guide.router)
    root.include_router(admin.router)
    root.include_router(admin_users.router)
    root.include_router(admin_channels.router)
    root.include_router(account.router)
    root.include_router(tasks.router)
    root.include_router(chatpicker.router)
    root.include_router(destinations.router)
    root.include_router(preview.router)
    root.include_router(clone.router)
    root.include_router(settings.router)
    root.include_router(history.router)
    root.include_router(billing.router)
    # فوروارد آخر ثبت می‌شود چون هندلر پیام فورواردشده‌اش فیلتر حالت ندارد
    root.include_router(forward.router)
    return root
