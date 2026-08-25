@echo off
chcp 65001 >nul
rem نصب اولیه: ساخت محیط مجازی و نصب وابستگی‌ها
cd /d "%~dp0"

where py >nul 2>&1
if errorlevel 1 (
    echo [!] پایتون پیدا نشد. از python.org نسخه‌ی 3.11 یا بالاتر نصب کنید
    echo     و هنگام نصب تیک "Add python.exe to PATH" را بزنید.
    pause
    exit /b 1
)

if not exist ".venv\Scripts\python.exe" (
    echo [*] ساخت محیط مجازی...
    py -3 -m venv .venv
    if errorlevel 1 (
        echo [!] ساخت محیط مجازی ناموفق بود.
        pause
        exit /b 1
    )
)

echo [*] نصب وابستگی‌ها...
".venv\Scripts\python.exe" -m pip install --upgrade pip
".venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 (
    echo [!] نصب وابستگی‌ها ناموفق بود. اگر شبکه‌تان محدود است، از پروکسی
    echo     یا آینه‌ی pip استفاده کنید.
    pause
    exit /b 1
)

if not exist ".env" (
    copy ".env.example" ".env" >nul
    echo [*] فایل .env ساخته شد. حالا آن را باز کنید و مقادیر را پر کنید:
    echo     BOT_TOKEN, API_ID, API_HASH, FERNET_KEY, ADMIN_IDS
)

echo.
echo [*] برای ساخت FERNET_KEY این را اجرا کنید و خروجی را در .env بگذارید:
echo     .venv\Scripts\python.exe -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
echo.
echo [*] نصب تمام شد. بعد از پر کردن .env، start.bat را اجرا کنید.
pause
