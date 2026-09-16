@echo off
chcp 65001 >nul
rem کلیدهای تازه را به .env موجود اضافه می‌کند، بدون دست زدن به مقادیر شما.
rem
rem   envsync.bat            فقط بگو چه چیزی کم است
rem   envsync.bat --apply    اضافه‌شان کن
cd /d "%~dp0"

if exist ".venv\Scripts\python.exe" (
    ".venv\Scripts\python.exe" tools\envsync.py %*
) else (
    py -3 tools\envsync.py %*
)

echo.
pause
