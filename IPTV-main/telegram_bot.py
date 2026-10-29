#!/usr/bin/env python3
"""
ربات تلگرام مدیریت پلی‌لیست IPTV
================================
دستورها:
  /start   - راهنما
  /link    - همه لینک‌های پلی‌لیست
  /stats   - آمار آخرین آپدیت
  /persian - فقط کانال‌های فارسی
  /news    - اخبار
  /music   - موزیک
  /sports  - ورزشی
  /movies  - فیلم
  /kids    - کودک
  /backup  - لینک نسخه پشتیبان
  /status  - وضعیت ربات

نحوه اجرا:
  export TELEGRAM_BOT_TOKEN="توکن-ربات"
  export TELEGRAM_CHAT_ID="آیدی-چت"          # اختیاری (اگر بگذاری فقط تو جواب می‌گیری)
  export GITHUB_REPO="4kings6114/IPTV"       # اختیاری
  python telegram_bot.py
"""

import os
import sys
import json
import time
import urllib.request
import urllib.parse
import urllib.error
from datetime import datetime, timezone

# ===================== تنظیمات =====================
TOKEN   = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "").strip()   # اگر خالی باشد همه می‌توانند استفاده کنند
REPO    = os.environ.get("GITHUB_REPO", "4kings6114/IPTV").strip()

BASE = f"https://raw.githubusercontent.com/{REPO}/main"
API  = f"https://api.telegram.org/bot{TOKEN}"

# ===================== توابع کمکی =====================
def log(msg: str):
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    print(f"[{ts}] {msg}", flush=True)

def api_call(method: str, **params):
    """فراخوانی API تلگرام"""
    url = f"{API}/{method}"
    data = urllib.parse.urlencode(params).encode("utf-8")
    req = urllib.request.Request(url, data=data, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=45) as r:
            return json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", "replace")
        log(f"HTTP Error {e.code}: {body[:200]}")
        return None
    except Exception as e:
        log(f"API error: {e}")
        return None

def send_message(chat_id, text: str, parse_mode: str = None):
    """ارسال پیام به کاربر"""
    params = {
        "chat_id": chat_id,
        "text": text,
        "disable_web_page_preview": "true",
    }
    if parse_mode:
        params["parse_mode"] = parse_mode
    return api_call("sendMessage", **params)

def fetch_text(url: str, timeout: int = 15) -> str:
    """دانلود متن از یک URL"""
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "IPTV-Bot/1.0"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.read().decode("utf-8", "replace")
    except Exception as e:
        log(f"Fetch failed ({url}): {e}")
        return ""

def get_stats() -> str:
    raw = fetch_text(f"{BASE}/build_stats.txt")
    if not raw.strip():
        return "آمار در دسترس نیست.\nشاید هنوز آپدیتی انجام نشده باشد."
    return raw.strip()

# ===================== متن دستورها =====================
HELP_TEXT = """📺 ربات مدیریت پلی‌لیست IPTV

دستورهای موجود:

/link     → همه لینک‌های پلی‌لیست
/stats    → آمار آخرین آپدیت
/persian  → فقط کانال‌های فارسی
/news     → اخبار
/music    → موزیک
/sports   → ورزشی
/movies   → فیلم و سریال
/kids     → کودک و کارتون
/backup   → نسخه پشتیبان
/status   → وضعیت ربات

هر روز پلی‌لیست به صورت خودکار آپدیت می‌شود.
"""

def links_text() -> str:
    return f"""📥 لینک‌های پلی‌لیست

🔹 همه کانال‌ها:
{BASE}/playlist.m3u

🇮🇷 فارسی:
{BASE}/persian.m3u

📰 اخبار:
{BASE}/news.m3u

🎵 موزیک:
{BASE}/music.m3u

⚽ ورزشی:
{BASE}/sports.m3u

🎬 فیلم:
{BASE}/movies.m3u

👶 کودک:
{BASE}/kids.m3u

💾 پشتیبان:
{BASE}/playlist-backup.m3u
"""

# ===================== پردازش پیام =====================
def handle_message(chat_id, text: str, user_name: str = ""):
    text = (text or "").strip()
    if not text:
        return

    # فقط دستورها (با / شروع می‌شوند)
    if not text.startswith("/"):
        send_message(chat_id, "لطفاً از دستورها استفاده کن.\n/start را بزن.")
        return

    # جدا کردن دستور از آرگومان و حذف @botusername
    cmd = text.split()[0].split("@")[0].lower()

    log(f"Command {cmd} from {user_name or chat_id}")

    if cmd in ("/start", "/help"):
        send_message(chat_id, HELP_TEXT)

    elif cmd == "/link":
        send_message(chat_id, links_text())

    elif cmd == "/stats":
        send_message(chat_id, "📊 آمار آخرین آپدیت:\n\n" + get_stats())

    elif cmd == "/persian":
        send_message(chat_id, f"🇮🇷 کانال‌های فارسی:\n{BASE}/persian.m3u")

    elif cmd == "/news":
        send_message(chat_id, f"📰 اخبار:\n{BASE}/news.m3u")

    elif cmd == "/music":
        send_message(chat_id, f"🎵 موزیک:\n{BASE}/music.m3u")

    elif cmd == "/sports":
        send_message(chat_id, f"⚽ ورزشی:\n{BASE}/sports.m3u")

    elif cmd == "/movies":
        send_message(chat_id, f"🎬 فیلم و سریال:\n{BASE}/movies.m3u")

    elif cmd == "/kids":
        send_message(chat_id, f"👶 کودک و کارتون:\n{BASE}/kids.m3u")

    elif cmd == "/backup":
        send_message(chat_id, f"💾 نسخه پشتیبان:\n{BASE}/playlist-backup.m3u")

    elif cmd == "/status":
        now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        send_message(chat_id, f"✅ ربات آنلاین است\n⏰ {now}\n📦 ریپو: {REPO}")

    else:
        send_message(chat_id, "دستور ناشناخته است.\n/start را بزن تا لیست دستورها را ببینی.")

# ===================== حلقه اصلی =====================
def main():
    if not TOKEN:
        print("❌ خطا: متغیر TELEGRAM_BOT_TOKEN تنظیم نشده است.")
        print("مثال:")
        print('  export TELEGRAM_BOT_TOKEN="123456:ABC-DEF..."')
        print('  python telegram_bot.py')
        sys.exit(1)

    # تست توکن
    me = api_call("getMe")
    if not me or not me.get("ok"):
        print("❌ توکن ربات نامعتبر است. دوباره چک کن.")
        sys.exit(1)

    bot_name = me["result"].get("username", "?")
    log(f"Bot started: @{bot_name}")
    if CHAT_ID:
        log(f"Restricted to chat_id: {CHAT_ID}")
    else:
        log("Open to all users (no CHAT_ID restriction)")

    offset = 0
    while True:
        try:
            data = api_call("getUpdates", offset=offset, timeout=30, allowed_updates='["message"]')
            if not data or not data.get("ok"):
                time.sleep(3)
                continue

            for upd in data.get("result", []):
                offset = upd["update_id"] + 1
                msg = upd.get("message")
                if not msg:
                    continue

                chat = msg.get("chat", {})
                chat_id = chat.get("id")
                if chat_id is None:
                    continue

                # محدودیت دسترسی (اختیاری)
                if CHAT_ID and str(chat_id) != str(CHAT_ID):
                    log(f"Ignored message from {chat_id}")
                    continue

                text = msg.get("text") or ""
                user = msg.get("from", {})
                user_name = user.get("username") or user.get("first_name") or str(chat_id)

                handle_message(chat_id, text, user_name)

        except KeyboardInterrupt:
            log("Stopped by user.")
            break
        except Exception as e:
            log(f"Loop error: {e}")
            time.sleep(5)

if __name__ == "__main__":
    main()
