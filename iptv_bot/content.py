"""Validated, bounded project downloads; never fetch user-supplied stream URLs."""

import re
import time
import unicodedata
from dataclasses import dataclass
from urllib.parse import urlencode, urlsplit

from .transport import NetworkError, decode_json


class DataError(RuntimeError):
    pass


def clean_text(value):
    return "".join(
        char
        for char in value
        if (ord(char) >= 32 or char in "\n\t")
        and char != "\x7f"
        and not 0x202A <= ord(char) <= 0x202E
        and not 0x2066 <= ord(char) <= 0x2069
    )


def normalize_query(value):
    value = unicodedata.normalize("NFKC", value).casefold()
    value = value.translate(str.maketrans("يك٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹", "یک01234567890123456789"))
    value = value.replace("\u200c", " ").replace("\u200d", " ")
    value = "".join(char for char in value if not unicodedata.combining(char))
    return " ".join(clean_text(value).split())


def playlist_names(text):
    if not re.match(r"#EXTM3U(?:\s|$)", text.lstrip("\ufeff \r\n\t"), re.I):
        raise DataError("منبع پلی‌لیست معتبر نیست.")
    result, seen, pending = [], set(), None
    for raw in text.splitlines():
        line = raw.strip()
        if len(line) > 16384:
            raise DataError("خط پلی‌لیست بزرگ‌تر از حد مجاز است.")
        if line.upper().startswith("#EXTINF:"):
            pending, quote = None, ""
            for index, char in enumerate(line):
                if char in {"'", '"'}:
                    if not quote:
                        quote = char
                    elif quote == char:
                        quote = ""
                elif char == "," and not quote:
                    pending = clean_text(line[index + 1 :]).strip()[:512]
                    break
        elif line and not line.startswith("#"):
            try:
                parsed = urlsplit(line.partition("|")[0])
                valid = parsed.scheme in {"http", "https"} and parsed.hostname
            except ValueError:
                valid = False
            if pending and valid:
                key = normalize_query(pending)
                if key and key not in seen:
                    result.append(pending)
                    seen.add(key)
                    if len(result) > 50000:
                        raise DataError("تعداد کانال‌ها بیشتر از حد مجاز است.")
            pending = None
    if not result:
        raise DataError("پلی‌لیست کانال قابل‌خواندن ندارد.")
    return tuple(result)


@dataclass(frozen=True)
class Snapshot:
    value: object
    age: float
    stale: bool = False


class ResourceCache:
    def __init__(self, clock=time.monotonic):
        self.clock, self.entries, self.failed_until = clock, {}, {}

    def get(self, key, ttl, max_age, loader):
        now = self.clock()
        old = self.entries.get(key)
        if old and now - old[1] < ttl:
            return Snapshot(old[0], max(0, now - old[1]))
        if now >= self.failed_until.get(key, 0):
            try:
                value = loader()
            except (NetworkError, DataError) as error:
                now = self.clock()
                self.failed_until[key] = now + max(30, getattr(error, "retry_after", None) or 0)
            else:
                self.entries[key] = (value, self.clock())
                self.failed_until.pop(key, None)
                return Snapshot(value, 0)
        if old and now - old[1] <= max_age:
            return Snapshot(old[0], max(0, now - old[1]), stale=True)
        raise DataError("اطلاعات منبع فعلاً در دسترس نیست؛ کمی بعد دوباره تلاش کنید.")


@dataclass(frozen=True)
class SearchResults:
    names: tuple[str, ...]
    total: int
    age: float
    stale: bool


class ContentService:
    def __init__(self, settings, transport, clock=time.monotonic):
        self.settings, self.transport = settings, transport
        self.cache = ResourceCache(clock)
        self.links = {
            key: f"{settings.raw_base}/{filename}"
            for key, filename in {
                "playlist": "playlist.m3u",
                "persian": "persian.m3u",
                "news": "news.m3u",
                "music": "music.m3u",
                "sports": "sports.m3u",
                "movies": "movies.m3u",
                "kids": "kids.m3u",
                "favorites": "favorites.m3u",
                "lowbandwidth": "profile-low-bandwidth.m3u",
                "fa-en": "profile-fa-en.m3u",
                "backup": "playlist-backup.m3u",
            }.items()
        }
        # Tests/PR previews are NOT evidence of a successful production update.
        query = urlencode({"branch": settings.branch, "per_page": 1})
        self.status_url = (
            f"https://api.github.com/repos/{settings.repo}/actions/workflows/main.yml/runs?{query}"
        )

    def _text(self, url, maximum):
        body = self.transport.request(url, timeout=self.settings.http_timeout, max_bytes=maximum)
        try:
            return body.decode("utf-8-sig")
        except UnicodeError:
            raise DataError("کدگذاری منبع نامعتبر است.") from None

    def stats(self):
        def load():
            text = clean_text(
                self._text(f"{self.settings.raw_base}/build_stats.txt", 65536)
            ).strip()
            if not text or text.startswith(("<", "{", "[")):
                raise DataError("گزارش آمار معتبر نیست.")
            return text

        return self.cache.get("stats", 300, 3600, load)

    def status(self):
        def load():
            body = self.transport.request(
                self.status_url,
                timeout=self.settings.http_timeout,
                max_bytes=512 * 1024,
                headers={"Accept": "application/vnd.github+json"},
            )
            data = decode_json(body)
            if not isinstance(data, dict) or not isinstance(data.get("workflow_runs"), list):
                raise DataError("پاسخ وضعیت معتبر نیست.")
            runs = data["workflow_runs"]
            if not runs:
                return "اجرایی برای workflow ساخت پلی‌لیست در این شاخه پیدا نشد."
            run = runs[0]
            if not isinstance(run, dict) or run.get("head_branch") != self.settings.branch:
                raise DataError("شاخهٔ گزارش‌شده با تنظیمات یکی نیست.")
            identifier = run.get("id")
            if type(identifier) is not int or not 0 < identifier <= 2**63 - 1:
                raise DataError("شناسهٔ اجرای ساخت معتبر نیست.")
            status, conclusion = run.get("status"), run.get("conclusion")
            if conclusion is not None and not isinstance(conclusion, str):
                raise DataError("نتیجهٔ اجرای ساخت معتبر نیست.")
            states = {
                "queued": "در صف",
                "in_progress": "در حال اجرا",
                "completed": "پایان‌یافته",
                "waiting": "منتظر تأیید",
                "pending": "در انتظار",
                "requested": "درخواست‌شده",
            }
            endings = {
                "success": "موفق",
                "failure": "ناموفق",
                "cancelled": "لغوشده",
                "timed_out": "پایان مهلت",
                "skipped": "اجرانشده",
                "neutral": "خنثی",
                "action_required": "نیازمند اقدام",
                "stale": "قدیمی",
                "startup_failure": "خطای شروع",
            }
            if not isinstance(status, str) or status not in states:
                raise DataError("وضعیت اجرای ساخت معتبر نیست.")
            icon = "⏳"
            if status == "completed":
                icon = {"success": "✅", "failure": "❌"}.get(conclusion, "⚠️")
            created = run.get("created_at", "")
            if not isinstance(created, str) or not re.fullmatch(r"[0-9T:Z+.-]{10,40}", created):
                created = "نامشخص"
            return (
                f"{icon} آخرین اجرای ساخت و انتشار پلی‌لیست\n"
                f"شاخه: {self.settings.branch}\nوضعیت: {states[status]}\n"
                f"نتیجه: {endings.get(conclusion, 'هنوز مشخص نیست')}\n"
                f"زمان شروع: {created}\n"
                f"https://github.com/{self.settings.repo}/actions/runs/{identifier}\n\n"
                "این وضعیت workflow است، نه تضمین پخش کانال‌ها یا مانیتور زنده."
            )

        return self.cache.get("status", 120, 600, load)

    def search(self, query):
        normalized = normalize_query(query)
        if not normalized or len(query) > 128:
            raise DataError("عبارت جستجو باید بین ۱ تا ۱۲۸ نویسه باشد.")
        snapshot = self.cache.get(
            "playlist",
            600,
            3600,
            lambda: playlist_names(self._text(self.links["playlist"], 16 * 1024 * 1024)),
        )
        found = [name for name in snapshot.value if normalized in normalize_query(name)]
        found.sort(
            key=lambda name: (
                normalize_query(name) != normalized,
                not normalize_query(name).startswith(normalized),
                normalize_query(name),
            )
        )
        return SearchResults(tuple(found[:200]), len(found), snapshot.age, snapshot.stale)
