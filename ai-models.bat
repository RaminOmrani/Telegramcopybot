@echo off
chcp 65001 >nul
rem فهرست کامل مدل‌های سرویس هوش مصنوعی را از خودِ API می‌گیرد.
rem کلید از این دستگاه بیرون نمی‌رود.
rem
rem   ai-models.bat            همه‌ی مدل‌ها
rem   ai-models.bat qwen       فقط آن‌هایی که «qwen» دارند
cd /d "%~dp0"

if exist ".venv\Scripts\python.exe" (
    ".venv\Scripts\python.exe" tools\ai_models.py %*
) else (
    py -3 tools\ai_models.py %*
)

echo.
echo [*] خروجی خام در ai-models.json ذخیره شد.
echo     کلید داخلش نیست، پس می‌شود همان فایل را فرستاد.
pause
