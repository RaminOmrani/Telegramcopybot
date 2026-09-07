@echo off
chcp 65001 >nul
rem پشتیبان‌گیری از پوشه‌ی data و فایل .env در یک zip تاریخ‌دار.
rem پیش از هر به‌روزرسانی یا جابه‌جایی سرور این را بزنید.
cd /d "%~dp0"

if not exist "data" (
    echo [!] پوشه‌ی data پیدا نشد.
    echo     یعنی یا ربات هنوز یک بار هم اجرا نشده، یا این پوشه‌ی پروژه نیست.
    pause
    exit /b 1
)

for /f %%i in ('powershell -NoProfile -Command "Get-Date -Format yyyyMMdd-HHmmss"') do set STAMP=%%i
set TARGET=telkap-backup-%STAMP%.zip

echo [*] در حال ساخت %TARGET% ...

if exist ".env" (
    powershell -NoProfile -Command "Compress-Archive -Path 'data','.env' -DestinationPath '%TARGET%' -Force"
) else (
    echo [!] فایل .env نبود؛ فقط پوشه‌ی data پشتیبان گرفته می‌شود.
    powershell -NoProfile -Command "Compress-Archive -Path 'data' -DestinationPath '%TARGET%' -Force"
)

if errorlevel 1 (
    echo [!] پشتیبان‌گیری ناموفق بود.
    pause
    exit /b 1
)

echo.
echo [*] پشتیبان ساخته شد: %TARGET%
echo.
echo  ==================== هشدار ====================
echo  این فایل شامل .env است، یعنی BOT_TOKEN و API_HASH و
echo  FERNET_KEY داخلش هستند. با این کلیدها می‌شود به اکانت
echo  دسترسی گرفت. جای امن نگهش دارید، در تلگرام عمومی یا
echo  جای اشتراکی نگذارید.
echo  ===============================================
echo.
pause
