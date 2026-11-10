"""Telegram summaries with real newlines and checked API responses."""

import json
import os
import sys
from pathlib import Path
from urllib.request import Request, urlopen

from .storage import read_json


def message_text(
    report: dict, repository: str, run_id: str, failed: bool = False, changed: bool = False
) -> str:
    run_url = f"https://github.com/{repository}/actions/runs/{run_id}"
    if failed:
        title = "❌ به‌روزرسانی یا انتشار ناموفق بود"
        if report.get("status") == "blocked":
            title += "؛ خروجی قبلی حفظ شد"
        return "\n".join((title, report.get("error", "جزئیات در گزارش اجرا موجود است."), run_url))
    title = "✅ پلی‌لیست به‌روزرسانی شد" if changed else "ℹ️ تغییری برای انتشار نبود"
    lines = [
        title,
        f"کانال‌ها: {report.get('channels', '?')}",
        f"اضافه: {report.get('added', 0)} | حذف: {report.get('removed', 0)}",
        f"لینک‌های ناموفق: {len(report.get('unavailable_links', []))}",
        f"نگه‌داری موقت بدون تأیید سلامت: {report.get('retained_unverified', 0)}",
    ]
    cached = sum(item.get("status") == "cached" for item in report.get("sources", []))
    if cached:
        lines.append(f"منابع بازیابی‌شده از کش: {cached}")
    if changed:
        base = f"https://raw.githubusercontent.com/{repository}/main"
        for label, name in (
            ("همهٔ کانال‌ها", "playlist"),
            ("فارسی", "persian"),
            ("اخبار", "news"),
            ("موزیک", "music"),
            ("ورزشی", "sports"),
            ("فیلم", "movies"),
            ("کودک", "kids"),
        ):
            lines.extend(("", f"{label}:", f"{base}/{name}.m3u"))
    return "\n".join(lines)


def send_message(token: str, chat_id: str, text: str) -> None:
    payload = json.dumps(
        {"chat_id": chat_id, "text": text[:4096], "disable_web_page_preview": True}
    ).encode("utf-8")
    request = Request(
        f"https://api.telegram.org/bot{token}/sendMessage",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urlopen(request, timeout=20) as response:
        result = json.loads(response.read(65536))
    if result.get("ok") is not True:
        raise RuntimeError("Telegram API did not accept the message")


def main() -> int:
    token, chat = os.environ.get("TG_TOKEN"), os.environ.get("TG_CHAT")
    if not token or not chat:
        print("Telegram secrets are not configured; skipping notification.")
        return 0
    report = read_json(Path(".cache/iptv/last_run.json"))
    text = message_text(
        report,
        os.environ["GITHUB_REPOSITORY"],
        os.environ["GITHUB_RUN_ID"],
        os.environ.get("BUILD_JOB_STATUS") != "success",
        os.environ.get("PLAYLIST_CHANGED") == "true",
    )
    try:
        send_message(token, chat, text)
    except Exception as error:
        # Do not print Request/HTTPError URLs: they contain the bot token.
        print(f"Telegram notification failed ({type(error).__name__}).", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
