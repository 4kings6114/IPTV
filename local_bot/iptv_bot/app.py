"""Private long-polling Telegram bot and read-only publication monitor."""

import argparse
import getpass
import hmac
import secrets
import sys
import time
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

from .config import Config, ConfigurationError, atomic_json, instance_lock, load_config, read_json
from .network import GitHub, ServiceError, Telegram
from .views import (
    HELP,
    LINKS,
    MENU,
    compact_report,
    dashboard_url,
    links_text,
    profiles,
    raw_url,
    search,
    stats,
    summary,
)


def log(text):
    print(datetime.now(timezone.utc).strftime("%H:%M:%S UTC"), text, flush=True)


def run_text(config, run):
    if not run:
        return "هنوز اجرای Update playlist پیدا نشد."
    status = {"completed": "پایان‌یافته", "in_progress": "در حال اجرا", "queued": "در صف"}.get(
        run.get("status"), "نامشخص"
    )
    result = {
        "success": "موفق",
        "failure": "ناموفق",
        "cancelled": "لغوشده",
        "timed_out": "اتمام مهلت",
    }.get(run.get("conclusion"), "هنوز نتیجه ندارد")
    return f"✅ وضعیت Update playlist\n{status} · {result}\nhttps://github.com/{config.repository}/actions/runs/{run['id']}"


class Bot:
    def __init__(self, config, data_dir, telegram=None, github=None, pairing_code=None):
        self.config, self.data_dir = config, data_dir
        self.telegram = telegram or Telegram(config.token)
        self.github = github or GitHub(config)
        self.state = read_json(data_dir / "state.json")
        self.report = read_json(data_dir / "report.json", maximum=32 * 1024 * 1024)
        self.pin = pairing_code or secrets.token_urlsafe(9)
        self.run_cache = {}
        self.run_checked = -float("inf")
        self.next_check = 0.0
        self._validate_state()
        self._validate_report_cache()

    def _validate_report_cache(self):
        if not self.report:
            return
        report = self.report
        catalog = report.get("channel_catalog")
        profiles = report.get("profiles")
        changes = report.get("changes")
        if (
            type(report.get("channels")) is not int
            or not isinstance(catalog, list)
            or any(
                not isinstance(row, dict)
                or any(
                    not isinstance(row.get(key), str)
                    for key in ("name", "group", "identity", "status")
                )
                for row in catalog
            )
            or not isinstance(report.get("groups"), dict)
            or not isinstance(profiles, dict)
            or any(
                not isinstance(item, dict) or not isinstance(item.get("file"), str)
                for item in profiles.values()
            )
            or not isinstance(changes, dict)
            or any(
                not isinstance(value, list) or any(not isinstance(name, str) for name in value)
                for value in changes.values()
            )
        ):
            raise ConfigurationError(
                "Invalid report cache. Back up and remove data/report.json; it will be restored without replaying notifications."
            )

    def _validate_state(self):
        import re

        if type(self.state.get("offset", 0)) is not int or self.state.get("offset", 0) < 0:
            raise ConfigurationError("Invalid Telegram offset in local state.")
        pending = self.state.get("pending", [])
        if (
            not isinstance(pending, list)
            or len(pending) > 20
            or any(
                not isinstance(sha, str) or not re.fullmatch(r"[0-9a-f]{40}", sha)
                for sha in pending
            )
        ):
            raise ConfigurationError("Invalid publication queue in local state.")
        seen = self.state.get("last_seen")
        if seen is not None and (
            not isinstance(seen, str) or not re.fullmatch(r"[0-9a-f]{40}", seen)
        ):
            raise ConfigurationError("Invalid publication cursor in local state.")

    def save(self):
        atomic_json(self.data_dir / "state.json", self.state)

    def send(self, text):
        if self.config.owner_chat_id:
            self.telegram.send(self.config.owner_chat_id, text, MENU)

    def latest_run(self, now):
        if now - self.run_checked >= 60:
            self.run_cache = self.github.latest_run()
            self.run_checked = now
        return self.run_cache

    def handle_update(self, update):
        """Ignore every non-owner chat after pairing; never trust usernames."""
        message = update.get("message", {})
        if not isinstance(message, dict):
            return
        chat = message.get("chat", {})
        sender = message.get("from", {})
        if not isinstance(chat, dict) or not isinstance(sender, dict):
            return
        chat_id, user_id = chat.get("id"), sender.get("id")
        if (
            chat.get("type") != "private"
            or type(chat_id) is not int
            or chat_id <= 0
            or user_id != chat_id
        ):
            return
        text = message.get("text", "")
        if not isinstance(text, str) or len(text) > 4096:
            return
        if self.config.owner_chat_id is None:
            # Do not let the first stranger sending /start claim the bot.
            parts = text.strip().split()
            if (
                len(parts) != 2
                or parts[0] != "/start"
                or not hmac.compare_digest(parts[1].encode(), self.pin.encode())
            ):
                return
            candidate = replace(self.config, owner_chat_id=chat_id)
            # A failed welcome leaves pairing retryable; unauthorized users
            # never receive an acknowledgement or any project data.
            self.telegram.send(chat_id, "✅ اتصال امن انجام شد.\n" + HELP, MENU)
            atomic_json(self.data_dir / "config.json", candidate.data())
            self.config = candidate
            self.pin = ""
            log("Owner paired. Monitoring is active.")
            return
        if chat_id != self.config.owner_chat_id:
            return
        text = text.strip()
        command, _, argument = text.partition(" ")
        command = command.split("@", 1)[0].casefold()
        if command in {"/start", "/help"} or text == "⚙️ راهنما":
            self.send(HELP)
        elif command == "/links" or text == "📥 همه لینک‌ها":
            self.send(links_text(self.config))
        elif text == "🌐 داشبورد" or command == "/dashboard":
            self.send("🌐 داشبورد (در صورت فعال بودن Pages):\n" + dashboard_url(self.config))
        elif text in dict(LINKS):
            self.send(text + "\n" + raw_url(self.config, dict(LINKS)[text]))
        elif command == "/status" or text == "✅ وضعیت ساخت":
            try:
                text = run_text(self.config, self.latest_run(time.monotonic()))
            except ServiceError:
                text = "وضعیت GitHub فعلاً در دسترس نیست؛ کمی بعد دوباره تلاش کنید. گزارش قبلی حفظ شده است."
            self.send(text)
        elif text == "🔎 جستجو":
            self.send("نام شبکه را بفرستید؛ مثال: bbc\nیا از /search bbc استفاده کنید.")
        elif not self.report:
            self.send(
                "گزارش هنوز دریافت نشده است؛ لینک‌ها آماده‌اند، دریافت گزارش به‌صورت خودکار تلاش می‌شود."
            )
        elif command == "/stats" or text == "📊 آمار":
            self.send(stats(self.report))
        elif command == "/profiles" or text == "📶 پروفایل‌ها":
            self.send(profiles(self.config, self.report))
        elif command == "/changes" or text == "🔄 آخرین تغییرات":
            self.send(
                summary(
                    self.config,
                    self.report,
                    self.report.get("publication_sha", self.state.get("report_sha", "")),
                )
            )
        elif command == "/search":
            self.send(search(self.config, self.report, argument))
        elif not text.startswith("/"):
            self.send(search(self.config, self.report, text))
        else:
            self.send("دستور ناشناخته است. /help")

    def process_updates(self):
        updates = self.telegram.updates(self.state.get("offset", 0))
        for update in updates:
            if not isinstance(update, dict) or type(update.get("update_id")) is not int:
                continue
            if update["update_id"] < self.state.get("offset", 0):
                continue
            self.handle_update(update)
            # Advance only after handling/sending succeeds. A failed API send
            # can be retried on the next poll instead of silently losing input.
            self.state["offset"] = update["update_id"] + 1
            self.save()

    def check_publications(self, now):
        if self.config.owner_chat_id is None or now < self.next_check:
            return
        self.next_check = now + self.config.poll_seconds
        run = self.latest_run(now)
        if run.get("status") == "completed" and run.get("conclusion") in {
            "failure",
            "timed_out",
            "cancelled",
        }:
            fingerprint = f"{run['id']}/{run.get('run_attempt', 1)}"
            if self.state.get("last_failed_run") != fingerprint:
                self.send("⚠️ آخرین اجرای به‌روزرسانی موفق نبوده است.\n" + run_text(self.config, run))
                self.state["last_failed_run"] = fingerprint
                self.save()
        pending = self.state.get("pending", [])
        if not pending:
            commits = self.github.commits()
            if not commits:
                return
            seen = self.state.get("last_seen")
            if seen is None:
                pending = commits[:1]
                self.state["initial"] = True
            elif seen in commits:
                pending = list(reversed(commits[: commits.index(seen)]))
            else:
                pending = list(reversed(commits))
                self.state["history_gap"] = True
            self.state["pending"] = pending
            self.save()
            if not pending and not self.report and seen:
                # Losing the optional search cache must not require deleting
                # delivery state or replaying an already delivered notification.
                cached = compact_report(self.github.report(seen))
                cached["publication_sha"] = seen
                atomic_json(self.data_dir / "report.json", cached)
                self.report = cached
        if self.state.get("history_gap"):
            self.send(
                "ℹ️ مرجع قبلی در ۲۰ انتشار اخیر پیدا نشد؛ حداکثر همین ۲۰ انتشار گزارش می‌شود و تاریخچهٔ قدیمی‌تر ممکن است جا مانده باشد."
            )
            self.state.pop("history_gap", None)
            self.save()
        if not pending:
            return
        sha = pending[0]
        report = compact_report(self.github.report(sha))
        report["publication_sha"] = sha
        self.send(summary(self.config, report, sha, initial=self.state.get("initial", False)))
        # Keep the old report in memory/on disk until a new one is validated
        # and delivered. No stream URLs or Telegram credentials enter this cache.
        atomic_json(self.data_dir / "report.json", report)
        self.report = report
        self.state.update(last_seen=sha, report_sha=sha, pending=pending[1:])
        self.state.pop("initial", None)
        self.save()
        log("Publication report delivered.")
        if self.state["pending"]:
            self.next_check = now + 10

    def run(self):
        self.telegram.call("getMe", {})
        webhook = self.telegram.call("getWebhookInfo", {})
        if not isinstance(webhook, dict):
            raise ServiceError("Telegram")
        if webhook.get("url"):
            raise ConfigurationError(
                "This bot has an active webhook. Stop its hosted service or use a NEW bot token; no webhook was deleted."
            )
        self.telegram.call(
            "setMyCommands",
            {
                "commands": [
                    {"command": command, "description": description}
                    for command, description in (
                        ("start", "منوی اصلی"),
                        ("links", "لینک‌های M3U"),
                        ("search", "جستجوی شبکه"),
                        ("stats", "آمار"),
                        ("status", "وضعیت ساخت"),
                        ("changes", "آخرین تغییرات"),
                        ("profiles", "پروفایل‌ها"),
                        ("dashboard", "داشبورد"),
                        ("help", "راهنما"),
                    )
                ]
            },
        )
        if self.config.owner_chat_id is None:
            print(f"\nSend this PRIVATE message to your bot: /start {self.pin}\n", flush=True)
            print(
                "کد بالا را فقط در گفت‌وگوی خصوصی با بات خودتان بفرستید؛ در چت/عکس عمومی منتشر نکنید."
            )
        log("Local bot is running. Stop with Ctrl+C. No repository writes are performed.")
        while True:
            try:
                self.check_publications(time.monotonic())
            except (ServiceError, ConfigurationError) as error:
                log(f"Publication check unavailable ({error}); previous report kept.")
                self.next_check = time.monotonic() + (
                    max(self.config.poll_seconds, error.retry_after)
                    if isinstance(error, ServiceError)
                    else self.config.poll_seconds
                )
            try:
                self.process_updates()
            except ServiceError as error:
                if error.code in {401, 404, 409}:
                    # 409 generally means another getUpdates consumer/webhook.
                    raise ConfigurationError(
                        "Telegram rejected polling (401/404/409). Check the LOCAL token and stop other instances/hosted bots."
                    ) from None
                log(
                    f"Telegram unavailable (HTTP {error.code}); retrying without discarding pending updates."
                )
                time.sleep(error.retry_after)


def setup(data_dir):
    print("تنظیم بات محلی — توکن را فقط همین‌جا وارد کنید، نه در چت.")
    print("If the token input is invisible, that is intentional.")
    token = getpass.getpass("Telegram bot token (hidden): ").strip()
    repository = input("Repository [4kings6114/IPTV]: ").strip() or "4kings6114/IPTV"
    config = Config(token, repository)
    atomic_json(data_dir / "config.json", config.data())
    # A new token may belong to another bot; its update IDs must start fresh.
    atomic_json(data_dir / "state.json", {})
    (data_dir / "report.json").unlink(missing_ok=True)
    print("تنظیمات فقط در data/config.json ذخیره شد. این پوشه را به کسی ندهید.")
    return config


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Private local IPTV Telegram bot; Python 3.10+, no external packages."
    )
    parser.add_argument(
        "--setup", action="store_true", help="Replace local configuration and pair again"
    )
    parser.add_argument(
        "--data-dir", type=Path, default=Path(__file__).resolve().parents[1] / "data"
    )
    args = parser.parse_args(argv)
    try:
        with instance_lock(args.data_dir / "bot.lock"):
            path = args.data_dir / "config.json"
            config = setup(args.data_dir) if args.setup or not path.exists() else load_config(path)
            Bot(config, args.data_dir).run()
    except KeyboardInterrupt:
        print("\nبات متوقف شد؛ وضعیت برای اجرای بعد ذخیره است.")
        return 0
    except (ConfigurationError, ServiceError) as error:
        # These are controlled messages only, never raw urllib exceptions.
        print(f"Stopped: {error}", file=sys.stderr)
        return 1
    except (OSError, EOFError):
        print(
            "Stopped: cannot read input/save local data. Check folder permissions and run in a terminal.",
            file=sys.stderr,
        )
        return 1
    return 0
