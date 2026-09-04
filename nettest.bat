@echo off
chcp 65001 >nul
rem بررسی می‌کند این سرور به تلگرام راه دارد یا نه.
rem وقتی ربات روی «Cannot connect to host api.telegram.org» گیر کرد، این را بزنید.
cd /d "%~dp0"

if exist ".venv\Scripts\python.exe" (
    ".venv\Scripts\python.exe" tools\nettest.py
) else (
    py -3 tools\nettest.py
)

echo.
pause
