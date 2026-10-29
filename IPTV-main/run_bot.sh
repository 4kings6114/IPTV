#!/bin/bash
# اجرای ربات تلگرام
# استفاده:
#   1. توکن و chat id را پایین وارد کن
#   2. دستور بزن:  bash run_bot.sh

export TELEGRAM_BOT_TOKEN="توکن-ربات-را-اینجا-بگذار"
export TELEGRAM_CHAT_ID="247772508"
export GITHUB_REPO="4kings6114/IPTV"

cd "$(dirname "$0")"
python3 telegram_bot.py
