"""Persian menus and plain-text views; no HTML/Markdown interpretation."""

import re
import unicodedata
from urllib.parse import quote

from .config import ConfigurationError

LINKS = (
    ("📥 همهٔ کانال‌ها", "playlist.m3u"),
    ("🔁 اصلی + جایگزین دستی", "playlist-alternatives.m3u"),
    ("🇮🇷 فارسی", "persian.m3u"),
    ("🇮🇳 هندی", "indian/playlist.m3u"),
    ("🇮🇳 هندی + جایگزین دستی", "indian/playlist-alternatives.m3u"),
    ("📰 اخبار", "news.m3u"),
    ("🎵 موسیقی", "music.m3u"),
    ("⚽ ورزشی", "sports.m3u"),
    ("🎬 فیلم", "movies.m3u"),
    ("👶 کودک", "kids.m3u"),
    ("⭐ علاقه‌مندی‌های پروژه", "favorites.m3u"),
)
MENU = {
    "keyboard": [
        ["🇮🇷 فارسی", "📰 اخبار"],
        ["🎵 موسیقی", "⚽ ورزشی"],
        ["🇮🇳 هندی", "🇮🇳 هندی + جایگزین دستی"],
        ["🎬 فیلم", "👶 کودک"],
        ["📥 همه لینک‌ها", "🔎 جستجو"],
        ["📊 آمار", "✅ وضعیت ساخت"],
        ["📶 پروفایل‌ها", "🔄 آخرین تغییرات"],
        ["🔁 اصلی + جایگزین دستی", "🌐 داشبورد"],
        ["⚙️ راهنما"],
    ],
    "resize_keyboard": True,
    "is_persistent": True,
}


def clean(value, length=160):
    return "".join(c for c in str(value) if unicodedata.category(c) != "Cc")[:length]


def normalized(value):
    value = unicodedata.normalize("NFKC", value).casefold().translate(str.maketrans("يك", "یک"))
    return " ".join("".join(c for c in value if not unicodedata.combining(c)).split())


def raw_url(config, filename):
    return f"https://raw.githubusercontent.com/{config.repository}/{quote(config.branch, safe='/')}/{filename}"


def dashboard_url(config):
    owner, repo = config.repository.split("/")
    return f"https://{owner}.github.io/{repo}/"


def links_text(config):
    parts = ["📥 پلی‌لیست‌های پروژه"]
    for title, filename in LINKS:
        parts.extend(("", title, raw_url(config, filename)))
    parts.extend(
        (
            "",
            "نسخهٔ قبلی پلی‌لیست اصلی؛ لینک جایگزین یک شبکه نیست:",
            raw_url(config, "playlist-backup.m3u"),
            "",
            "اگر لینک اصلی پخش نشد، در نسخهٔ دولینکی ورودی [جایگزین] را دستی انتخاب کنید.",
        )
    )
    return "\n".join(parts)


def compact_report(data):
    """Persist only UI data, never the large editor manifest or stream URLs."""
    try:
        return _compact_report(data)
    except (AttributeError, KeyError, TypeError, ValueError):
        raise ConfigurationError(
            "Published report has invalid fields; previous snapshot preserved."
        ) from None


def _compact_report(data):
    if (
        not isinstance(data, dict)
        or type(data.get("channels")) is not int
        or not 0 < data["channels"] <= 50000
        or not isinstance(data.get("channel_catalog"), list)
        or len(data["channel_catalog"]) > 50000
    ):
        raise ConfigurationError(
            "Published report is missing/invalid; previous snapshot preserved."
        )
    result = {"channels": data["channels"], "generated_at": clean(data.get("generated_at", ""))}
    for key in (
        "added",
        "removed",
        "changed_urls",
        "upgraded",
        "downgraded",
        "recovered",
        "retained_unverified",
    ):
        value = data.get(key, 0)
        result[key] = value if type(value) is int and value >= 0 else 0
    result["channel_catalog"] = [
        {
            key: clean(row.get(key, ""), 200)
            for key in ("name", "group", "identity", "status", "language")
        }
        for row in data["channel_catalog"]
        if isinstance(row, dict)
    ]
    result["groups"] = {
        clean(key): value
        for key, value in data.get("groups", {}).items()
        if type(value) is int and value >= 0
    }
    alt = data.get("alternatives", {})
    count = alt.get("channels_with_alternative", 0) if isinstance(alt, dict) else 0
    result["alternatives"] = count if type(count) is int and count >= 0 else 0
    result["profiles"] = {
        name: {"file": item["file"], "channels": item.get("channels", 0)}
        for name, item in data.get("personalization", {}).get("profiles", {}).items()
        if isinstance(item, dict)
        and isinstance(item.get("file"), str)
        and re.fullmatch(r"profile-[a-z0-9][a-z0-9-]{0,39}\.m3u", item["file"])
    }
    result["changes"] = {}
    for key in (
        "added_channels",
        "removed_channels",
        "url_changes",
        "quality_changes",
        "recovered_channels",
    ):
        result["changes"][key] = [
            clean(item.get("name", ""))
            for item in data.get("changes", {}).get(key, [])[:30]
            if isinstance(item, dict)
        ]
    return result


def summary(config, report, sha, initial=False):
    title = "📌 آخرین تصویر منتشرشده (شروع پایش محلی)" if initial else "✅ انتشار جدید پلی‌لیست"
    lines = [
        title,
        f"زمان گزارش: {report.get('generated_at', 'نامشخص')}",
        f"شبکه‌ها: {report['channels']} | دارای جایگزین: {report.get('alternatives', 0)}",
        f"اضافه: {report.get('added', 0)} | حذف: {report.get('removed', 0)}",
        f"تغییر آدرس: {report.get('changed_urls', 0)} | بازیابی: {report.get('recovered', 0)}",
        f"کیفیت بهتر/کمتر: {report.get('upgraded', 0)}/{report.get('downgraded', 0)}",
        f"نگه‌داری موقت بدون تأیید سلامت: {report.get('retained_unverified', 0)}",
    ]
    for key, title in (("added_channels", "جدید"), ("removed_channels", "حذف‌شده")):
        names = report.get("changes", {}).get(key, [])[:5]
        if names:
            lines.append(title + ": " + "، ".join(names))
    lines.extend(
        (
            "",
            "اصلی:",
            raw_url(config, "playlist.m3u"),
            "",
            "اصلی + جایگزین دستی:",
            raw_url(config, "playlist-alternatives.m3u"),
            "",
            "مرجع همین انتشار:",
            f"https://github.com/{config.repository}/commit/{sha}",
            "این آمار همان انتشار است؛ تضمین پخش لحظه‌ای در شبکهٔ شما نیست.",
        )
    )
    return "\n".join(lines)


def stats(report):
    lines = [
        "📊 آمار آخرین تصویر منتشرشده",
        f"زمان: {report.get('generated_at', 'نامشخص')}",
        f"شبکه‌ها: {report['channels']} | دارای جایگزین: {report.get('alternatives', 0)}",
    ]
    lines.extend(f"{name}: {count}" for name, count in list(report.get("groups", {}).items())[:60])
    return "\n".join(lines)


def profiles(config, report):
    lines = ["📶 پروفایل‌های منتشرشده"]
    for name, item in report.get("profiles", {}).items():
        lines.extend(
            ("", f"{clean(name)} · {item.get('channels', '?')} شبکه", raw_url(config, item["file"]))
        )
    if len(lines) == 1:
        lines.append("هنوز پروفایلی در گزارش موجود نیست.")
    return "\n".join(lines)


def search(config, report, query):
    if not query.strip() or len(query) > 128:
        return "استفاده: /search bbc\nعبارت باید ۱ تا ۱۲۸ نویسه باشد."
    term = normalized(query)
    matches = [
        row
        for row in report.get("channel_catalog", [])
        if term in normalized(row.get("name", "") + " " + row.get("identity", ""))
    ]
    if not matches:
        return "شبکه‌ای با این عبارت در آخرین گزارش پیدا نشد."
    lines = [f"🔎 {len(matches)} نتیجه؛ نمایش حداکثر ۱۰ مورد:"]
    for row in matches[:10]:
        lines.extend(("", row["name"], row["group"], "وضعیت زمان بررسی: " + row["status"]))
    lines.extend(
        (
            "",
            "نام شبکه را در فهرست پلیر جستجو کنید:",
            raw_url(config, "playlist.m3u"),
            "نسخهٔ دارای جایگزین دستی:",
            raw_url(config, "playlist-alternatives.m3u"),
        )
    )
    return "\n".join(lines)


HELP = (
    "راهنمای بات محلی IPTV\n"
    "/start — منو\n/links — لینک‌های M3U\n/search bbc — جستجوی شبکه\n"
    "/stats — آمار\n/status — آخرین وضعیت Update playlist\n"
    "/changes — آخرین تغییرات\n/profiles — پروفایل‌ها\n/dashboard — داشبورد\n"
    "پایش انتشارها خودکار است؛ بات باید روی کامپیوتر روشن بماند.\n"
    "این برنامه پلی‌لیست را نمی‌سازد و اجازهٔ تغییر مخزن ندارد.\n"
    "داشبورد فقط در صورت فعال بودن GitHub Pages در دسترس است."
)
