"""روترهای ربات. ترتیب ثبت مهم است: جریان‌های حالت‌دار پیش از هندلرهای عمومی."""
from aiogram import Router

from telkap.handlers import (
    account,
    admin,
    admin_channels,
    admin_coupons,
    admin_limits,
    admin_plans,
    admin_referral,
    admin_users,
    billing,
    chatpicker,
    clone,
    destinations,
    forward,
    guide,
    history,
    preview,
    reseller,
    settings,
    start,
    support,
    tasks,
    wallet,
    watermark_wizard,
)


def build_router() -> Router:
    root = Router(name="root")
    root.include_router(start.router)
    root.include_router(guide.router)
    root.include_router(admin.router)
    root.include_router(admin_users.router)
    root.include_router(admin_channels.router)
    root.include_router(admin_plans.router)
    root.include_router(admin_limits.router)
    root.include_router(admin_referral.router)
    root.include_router(admin_coupons.router)
    root.include_router(support.router)
    root.include_router(account.router)
    root.include_router(tasks.router)
    root.include_router(chatpicker.router)
    root.include_router(destinations.router)
    root.include_router(preview.router)
    root.include_router(clone.router)
    root.include_router(watermark_wizard.router)
    root.include_router(settings.router)
    root.include_router(history.router)
    root.include_router(wallet.router)
    root.include_router(reseller.router)
    root.include_router(billing.router)
    # فوروارد آخر ثبت می‌شود چون هندلر پیام فورواردشده‌اش فیلتر حالت ندارد
    root.include_router(forward.router)
    return root
