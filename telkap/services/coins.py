"""ارزهایی که پذیرفته می‌شوند، و اینکه قیمت هرکدام از کجا می‌آید.

<b>یک جا برای همه‌ی تفاوت‌ها.</b> تتر و ترون هر دو روی شبکه‌ی ترون‌اند
و <b>به یک نشانی</b> واریز می‌شوند، ولی در سه چیز فرق دارند: تتر
توکن TRC20 است و ترون ارز بومی شبکه؛ اعشارشان فرق دارد؛ و بازارشان
در صرافی جداست. اگر این تفاوت‌ها در کد پخش شوند، اضافه کردن ارز
بعدی یعنی گشتن در ده فایل.

<b>قیمت هر دو از یک جا می‌آید: نوبیتکس.</b> بازار
<code>USDTIRT</code> برای تتر و <code>TRXIRT</code> برای ترون. هر دو
قیمت را به <b>ریال</b> می‌دهند، پس هر دو باید بر ده تقسیم شوند.
یعنی نرخ تومانی ترون مستقیم از بازار می‌آید، نه از ضرب قیمت دلاری
در نرخ دلار — یک مرحله کمتر، یک جای کمترِ خطا.

<b>هشدار درباره‌ی ترون.</b> تتر ارز باثبات است و قیمتش تقریباً روی
یک دلار می‌ماند؛ ترون در یک روز پنج تا پانزده درصد بالا و پایین
می‌رود. مبلغ در لحظه‌ی ساخت درخواست قفل می‌شود، پس اگر مشتری نیم
ساعت بعد بپردازد ممکن است ارزشش کم شده باشد. برای همین نرخ ترون
باید خودکار باشد و حاشیه‌اش بیشتر از تتر.
"""
from __future__ import annotations

from dataclasses import dataclass

USDT = "usdt"
TRX = "trx"


@dataclass(frozen=True)
class Coin:
    code: str
    label: str          # چیزی که کاربر می‌بیند
    symbol: str
    decimals: int       # چند رقم اعشار در واحد خام شبکه
    market: str         # نام بازار در نوبیتکس
    contract: str       # نشانی قرارداد TRC20؛ خالی یعنی ارز بومی
    rate_key: str       # کلید نرخ در AppSetting
    default_margin: int  # حاشیه‌ی اطمینان پیش‌فرض، به درصد
    quantize: str       # دقت مبلغی که به کاربر گفته می‌شود

    @property
    def is_native(self) -> bool:
        """ارز بومی شبکه است یا توکن روی آن.

        مسیر خواندنشان از بلاک‌چین کاملاً فرق دارد: توکن رویداد
        Transfer دارد، ارز بومی یک TransferContract در خودِ تراکنش.
        """
        return not self.contract


# قرارداد رسمی تتر روی ترون. تتر تنها توکنی نیست که TRC20 باشد، پس
# بدون این بررسی یک توکن بی‌ارزشِ خودساخته با همان نام «USDT»
# می‌توانست به‌جای پول واقعی قبول شود.
USDT_CONTRACT = "TR7NHqjeKQxGTCi8q8ZY4pL8otSzgjLj6t"

COINS: dict[str, Coin] = {
    USDT: Coin(
        code=USDT,
        label="₮ تتر (TRC20)",
        symbol="USDT",
        decimals=6,
        market="USDTIRT",
        contract=USDT_CONTRACT,
        rate_key="usdt_rate",
        default_margin=2,
        quantize="0.01",
    ),
    TRX: Coin(
        code=TRX,
        label="🔺 ترون (TRX)",
        symbol="TRX",
        decimals=6,          # ۱ TRX = ۱٬۰۰۰٬۰۰۰ سان
        market="TRXIRT",
        contract="",         # ارز بومی، قرارداد ندارد
        rate_key="trx_rate",
        # بیشتر از تتر، چون قیمتش در فاصله‌ی ساخت درخواست تا پرداخت
        # واقعاً تکان می‌خورد
        default_margin=5,
        # ترون ارزان است؛ با دو رقم اعشار مبلغ‌ها گرد و بی‌دقت می‌شوند
        quantize="0.0001",
    ),
}


def get(code: str) -> Coin | None:
    return COINS.get((code or "").strip().lower())


def all_codes() -> tuple[str, ...]:
    return tuple(COINS)
