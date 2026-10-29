@echo off
REM اجرای ربات تلگرام در ویندوز
REM 1. توکن را در خط پایین وارد کن
REM 2. روی این فایل دوبار کلیک کن

set TELEGRAM_BOT_TOKEN=9136318818:AAHBAjYkoO7CUCjf9KvYqqjb2KKAnmBTL64
set TELEGRAM_CHAT_ID=247772508
set GITHUB_REPO=4kings6114/IPTV

cd /d "%~dp0"
python telegram_bot.py
pause
