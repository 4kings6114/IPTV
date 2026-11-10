"""Telegram summaries with real newlines and checked API responses."""

import json
import os
import sys
from pathlib import Path
from urllib.request import Request, urlopen

from .alerts import alert_plan
from .storage import atomic_write, json_text, read_json


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
        f"تغییر آدرس: {report.get('changed_urls', 0)} | کیفیت بهتر/کمتر: {report.get('upgraded', 0)}/{report.get('downgraded', 0)}",
        f"بازیابی‌شده: {report.get('recovered', 0)} | نتیجهٔ سلامت از کش: {report.get('cached_links', 0)}",
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
    state_path = Path(".cache/iptv/alerts.json")
    events, state = alert_plan(
        report, read_json(state_path), os.environ.get("BUILD_JOB_STATUS") != "success"
    )
    if not events:
        atomic_write(state_path, json_text(state))
        print("No new targeted incident; Telegram notification suppressed.")
        return 0
    text = "⚠️ هشدار هدفمند IPTV\n" + "\n".join(event["title"][:250] for event in events[:10])
    if len(events) > 10:
        text += f"\nو {len(events) - 10} مورد دیگر؛ گزارش اجرا را ببینید."
    text += f"\nhttps://github.com/{os.environ['GITHUB_REPOSITORY']}/actions/runs/{os.environ['GITHUB_RUN_ID']}"
    try:
        send_message(token, chat, text)
        # Record delivery only after Telegram accepted it; failed delivery can retry.
        atomic_write(state_path, json_text(state))
    except Exception as error:
        # Do not print Request/HTTPError URLs: they contain the bot token.
        print(f"Telegram notification failed ({type(error).__name__}).", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
