@echo off
chcp 65001 >nul
rem گرفتن آخرین نسخه از گیت‌هاب و نصب وابستگی‌های تازه
cd /d "%~dp0"

echo [*] دریافت آخرین تغییرات...
git fetch origin main
if errorlevel 1 (
    echo [!] دریافت ناموفق بود. اینترنت یا دسترسی گیت‌هاب را بررسی کنید.
    pause
    exit /b 1
)

git reset --hard origin/main
if errorlevel 1 (
    echo [!] به‌روزرسانی ناموفق بود.
    pause
    exit /b 1
)

echo [*] نصب وابستگی‌های احتمالیِ تازه...
".venv\Scripts\python.exe" -m pip install -r requirements.txt

echo.
echo [*] به‌روزرسانی تمام شد. فایل .env و پوشه‌ی data دست‌نخورده مانده‌اند.
echo     اگر ربات را به‌صورت سرویس اجرا می‌کنید، سرویس را ری‌استارت کنید:
echo     nssm restart TelkapBot
pause
