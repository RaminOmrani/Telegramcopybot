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
    admin_reports,
    admin_system,
    admin_users,
    approvals,
    billing,
    chatpicker,
    churn,
    clone,
    destinations,
    forward,
    guide,
    history,
    language,
    preview,
    reseller,
    settings,
    start,
    support,
    task_templates,
    tasks,
    wallet,
    watermark_wizard,
)
from telkap.middlewares import CapMiddleware
from telkap.services import roles

# بخش‌هایی که همه‌ی هندلرهایشان یک دسترسی می‌خواهند. قفل روی خودِ روتر
# می‌نشیند تا با اضافه شدن هندلر تازه، گارد جا نماند.
LOCKED: tuple[tuple[Router, str], ...] = (
    (admin_users.router, roles.CAP_USERS),
    (admin_channels.router, roles.CAP_SYSTEM),
    (admin_plans.router, roles.CAP_MONEY),
    (admin_limits.router, roles.CAP_MONEY),
    (admin_referral.router, roles.CAP_MONEY),
    (admin_coupons.router, roles.CAP_MONEY),
    (admin_reports.router, roles.CAP_REPORTS),
    (admin_system.router, roles.CAP_SYSTEM),
)


for _router, _cap in LOCKED:
    _guard = CapMiddleware(_cap)
    _router.message.middleware(_guard)
    _router.callback_query.middleware(_guard)


def build_router() -> Router:
    root = Router(name="root")
    root.include_router(start.router)
    root.include_router(guide.router)
    root.include_router(language.router)
    root.include_router(admin.router)
    root.include_router(admin_users.router)
    root.include_router(admin_channels.router)
    root.include_router(admin_plans.router)
    root.include_router(admin_limits.router)
    root.include_router(admin_referral.router)
    root.include_router(admin_coupons.router)
    root.include_router(admin_reports.router)
    root.include_router(admin_system.router)
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
    root.include_router(churn.router)
    root.include_router(approvals.router)
    root.include_router(task_templates.router)
    # فوروارد آخر ثبت می‌شود چون هندلر پیام فورواردشده‌اش فیلتر حالت ندارد
    root.include_router(forward.router)
    return root
