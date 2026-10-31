@echo off
chcp 65001 >nul
rem اجرای ربات؛ محیط مجازی خودش فعال می‌شود
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
    echo [!] محیط مجازی ساخته نشده. اول setup.bat را اجرا کنید.
    pause
    exit /b 1
)

if not exist ".env" (
    echo [!] فایل .env پیدا نشد. .env.example را کپی و مقادیر را پر کنید.
    pause
    exit /b 1
)

echo [*] در حال اجرای ربات... برای توقف Ctrl+C بزنید.
".venv\Scripts\python.exe" -m telkap.main
pause
