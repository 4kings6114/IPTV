"""CLI actions are offline unless the owner explicitly starts a Telegram action."""

import argparse
import json
import logging
import re
import signal
import sys
from pathlib import Path

from . import __version__
from .bot import COMMANDS, Bot
from .config import ConfigError, Settings, load_environment
from .content import ContentService
from .state import AlreadyRunning, StateError, StateStore
from .transport import HTTPTransport, NetworkError, TelegramClient, TelegramError

ROOT = Path(__file__).resolve().parent.parent


def parser():
    result = argparse.ArgumentParser(
        description="ربات تعاملی IPTV؛ Python 3.10+، بدون وابستگی خارجی"
    )
    result.add_argument("--version", action="version", version=__version__)
    result.add_argument("--state-dir", type=Path, help="پوشهٔ وضعیت؛ بر BOT_STATE_DIR اولویت دارد")
    result.add_argument(
        "--env-file", type=Path, help="فایل تنظیمات UTF-8؛ پیش‌فرض .env کنار اسکریپت"
    )
    actions = result.add_mutually_exclusive_group()
    actions.add_argument(
        "--check-config", action="store_true", help="فقط اعتبارسنجی محلی؛ بدون شبکه یا نوشتن وضعیت"
    )
    actions.add_argument(
        "--discover-chats",
        action="store_true",
        help="نمایش محلی شناسهٔ چت‌های معوق، بدون پاسخ یا تأیید offset",
    )
    actions.add_argument(
        "--import-offset", type=Path, help="انتقال محلی .tg_offset قدیمی و خروج؛ بدون اتصال تلگرام"
    )
    actions.add_argument(
        "--register-commands", action="store_true", help="ثبت صریح فهرست دستورات در تلگرام و خروج"
    )
    actions.add_argument(
        "--delete-webhook",
        action="store_true",
        help="حذف صریح webhook، بدون حذف پیام‌های معوق، و خروج",
    )
    return result


def main(argv=None):
    # Persian diagnostics must also work in redirected Windows/legacy consoles.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            try:
                stream.reconfigure(encoding="utf-8", errors="backslashreplace")
            except (OSError, ValueError):
                pass
    args = parser().parse_args(argv)
    try:
        env = load_environment(args.env_file or ROOT / ".env", required=args.env_file is not None)
        if args.state_dir is not None:
            env["BOT_STATE_DIR"] = str(args.state_dir)
        settings = Settings.from_env(env, ROOT, allow_unconfigured_chats=args.discover_chats)
    except ConfigError as error:
        print(f"خطای تنظیمات: {error}", file=sys.stderr)
        return 2
    if args.check_config:
        print(
            json.dumps(
                {
                    "configuration": "valid",
                    "mode": "public" if settings.public else "private",
                    "allowed_chats": len(settings.chat_ids),
                    "allowed_users": len(settings.user_ids),
                    "repository": settings.repo,
                    "branch": settings.branch,
                    "poll_timeout": settings.poll_timeout,
                },
                ensure_ascii=False,
            )
        )
        return 0
    logging.basicConfig(level=settings.log_level, format="%(levelname)s %(name)s: %(message)s")
    store = StateStore(settings.state_dir, settings.scope)
    try:
        with store.lock():
            if args.import_offset is not None:
                store.import_legacy(args.import_offset)
                print("وضعیت قدیمی منتقل شد؛ هیچ اتصال شبکه‌ای انجام نشد.")
                return 0
            transport = HTTPTransport()
            client = TelegramClient(settings.token, transport, settings.http_timeout)
            me = client.call("getMe")
            if (
                not isinstance(me, dict)
                or me.get("is_bot") is not True
                or type(me.get("id")) is not int
                or me["id"] != int(settings.token.partition(":")[0])
                or not isinstance(me.get("username"), str)
                or not re.fullmatch(r"[A-Za-z0-9_]{5,32}", me["username"])
            ):
                raise NetworkError("Unexpected bot identity response")
            if args.discover_chats:
                updates = client.call("getUpdates", {"timeout": 0, "limit": 50})
                if not isinstance(updates, list) or len(updates) > 50:
                    raise NetworkError("Invalid discovery response")
                chats = set()
                for update in updates:
                    if not isinstance(update, dict):
                        continue
                    message = update.get("message")
                    if not isinstance(message, dict) or not isinstance(message.get("chat"), dict):
                        continue
                    chat = message["chat"]
                    identifier, kind = chat.get("id"), chat.get("type")
                    if (
                        type(identifier) is int
                        and 0 < abs(identifier) < 2**63
                        and isinstance(kind, str)
                        and kind in {"private", "group", "supergroup"}
                    ):
                        chats.add((identifier, kind))
                print(
                    json.dumps(
                        [
                            {"chat_id": identifier, "type": kind}
                            for identifier, kind in sorted(chats)
                        ],
                        ensure_ascii=False,
                    )
                )
                print("فقط چت خودتان را انتخاب کنید. پاسخی ارسال نشد و offset تأیید نشد.")
                return 0
            if args.register_commands:
                client.call(
                    "setMyCommands",
                    {
                        "commands": [
                            {"command": name, "description": text} for name, text in COMMANDS
                        ]
                    },
                )
                print("فهرست دستورات ثبت شد؛ polling شروع نشد.")
                return 0
            if args.delete_webhook:
                client.call("deleteWebhook", {"drop_pending_updates": False})
                print("webhook حذف شد؛ پیام‌های معوق حذف نشدند. اکنون می‌توانید ربات را اجرا کنید.")
                return 0
            webhook = client.call("getWebhookInfo")
            if not isinstance(webhook, dict) or not isinstance(webhook.get("url"), str):
                raise NetworkError("Invalid webhook response")
            if webhook["url"]:
                print(
                    "webhook فعال است؛ خودکار حذف نمی‌شود. فقط برای مهاجرت به polling از --delete-webhook استفاده کنید.",
                    file=sys.stderr,
                )
                return 2
            bot = Bot(
                settings,
                client,
                ContentService(settings, transport),
                store,
                username=me["username"],
            )
            old_handlers = {}
            try:
                for signum in (signal.SIGINT, signal.SIGTERM):
                    old_handlers[signum] = signal.signal(signum, lambda *_: bot.stop.set())
                logging.getLogger("iptv_bot").info(
                    "Bot ready; mode=%s, allowed_chats=%s",
                    "public" if settings.public else "private",
                    len(settings.chat_ids),
                )
                return bot.run()
            finally:
                for signum, previous in old_handlers.items():
                    signal.signal(signum, previous)
    except (AlreadyRunning, StateError) as error:
        print(f"خطای وضعیت: {error}", file=sys.stderr)
        return 2
    except TelegramError as error:
        print(
            f"اتصال یا مجوز تلگرام ناموفق است (کد {error.code})؛ توکن، webhook و نمونه‌های هم‌زمان را بررسی کنید.",
            file=sys.stderr,
        )
        if error.retry_after:
            print(f"قبل از اجرای مجدد حداقل {error.retry_after} ثانیه صبر کنید.", file=sys.stderr)
            return 2
        return 1 if error.retryable else 2
    except NetworkError:
        print("اتصال امن به تلگرام برقرار نشد؛ شبکه و دسترسی سرور را بررسی کنید.", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 0
    except Exception as error:
        print(f"خطای داخلی؛ اجرا متوقف شد ({type(error).__name__}).", file=sys.stderr)
        return 2
