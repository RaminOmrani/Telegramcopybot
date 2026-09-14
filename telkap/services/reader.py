"""«چه کسی این مبدأ را می‌خواند» — یک جواب، یک جا.

<b>چرا این ماژول لازم شد.</b> با آمدنِ حالت ساده، سؤالِ «کلاینتِ این
کار کدام است» دو جواب پیدا کرد: کارِ کامل با اکانتِ خودِ مشتری خوانده
می‌شود، کارِ ساده با اکانت سرویس. ولی این سؤال در <b>چند جا</b> پرسیده
می‌شود — موتور کپی، صف تلاش مجدد، صف تأیید — و همه‌شان جوابِ قدیمی را
می‌دادند.

نتیجه‌اش بی‌صداترین شکلِ ممکن بود: پستِ یک مشتریِ حالت ساده که بار
اول نمی‌رفت، در صف تلاش مجدد می‌نشست و آنجا هر بار به «اکانت کاربری
متصل نیست» می‌خورد تا سقفِ تلاش‌ها تمام شود و دور ریخته شود. نه خطایی
که به چشم بیاید، نه پستی که برسد.

پس جواب یک جا داده می‌شود و بقیه از اینجا می‌پرسند.
"""
from __future__ import annotations

import logging

from telkap.models import Task

log = logging.getLogger(__name__)


async def for_task(task, manager):
    """کلاینتی که می‌تواند مبدأ این کار را بخواند — یا None.

    `task` می‌تواند ردیفِ دیتابیس باشد یا عکسِ فوریِ کار؛ فقط `mode` و
    `source_id` و `user_id` از آن خوانده می‌شود.
    """
    if task is None:
        return None

    mode = getattr(task, "mode", Task.MODE_FULL)
    if mode != Task.MODE_SIMPLE:
        return await manager.ensure_client(getattr(task, "user_id", 0))

    # <b>حالت ساده: اکانت سرویس، و ترجیحاً همان یکی.</b> فایل و پست از
    # دیدِ همان اکانتی خوانده می‌شوند که مبدأ به آن سپرده شده؛ اکانت
    # دیگری ممکن است اصلاً به آن کانال دسترسی نداشته باشد.
    from telkap.services import pool

    source_id = getattr(task, "source_id", None)
    try:
        if source_id:
            account = await pool.lease(int(source_id), getattr(task, "source_ref", ""))
            return await pool.client_for(account)
        return await pool.any_client()
    except pool.NoAccount:
        log.error(
            "کار ساده‌ی %s خواننده‌ای ندارد", getattr(task, "id", "?")
        )
        return None


NO_READER_FULL = "اکانت کاربری متصل نیست"
NO_READER_SIMPLE = "خواننده‌ای برای کانال عمومی در دسترس نیست"


def why_missing(task) -> str:
    """<b>دلیلِ درست، برای دو وضعیتِ کاملاً متفاوت.</b>

    «اکانت کاربری متصل نیست» به مشتریِ حالت ساده گفتن، او را دنبال
    کاری می‌فرستد که اصلاً لازم نیست انجامش دهد — و مشکل سمتِ ماست،
    نه او.
    """
    mode = getattr(task, "mode", Task.MODE_FULL)
    return NO_READER_SIMPLE if mode == Task.MODE_SIMPLE else NO_READER_FULL
