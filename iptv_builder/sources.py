"""Source downloads with validation and expiring last-success caches."""

import hashlib
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime, timezone
from http.client import HTTPException
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from .config import Settings, Source
from .m3u import valid_source
from .storage import atomic_write, json_text, read_json

MAX_SOURCE_BYTES = 16 * 1024 * 1024


@dataclass(frozen=True)
class SourceResult:
    source: Source
    text: str
    status: str
    error: str = ""


def cache_path(cache_dir: Path, url: str) -> Path:
    return cache_dir / "sources" / (hashlib.sha256(url.encode()).hexdigest() + ".json")


def fetch_source(source: Source, settings: Settings, cache_dir: Path) -> SourceResult:
    error_text = "Download failed"
    for attempt in range(settings.source_attempts):
        retryable = True
        try:
            request = Request(
                source.url, headers={"User-Agent": "playlist-bot/5.0", "Accept": "*/*"}
            )
            with urlopen(request, timeout=settings.source_timeout) as response:
                data = response.read(MAX_SOURCE_BYTES + 1)
            if len(data) > MAX_SOURCE_BYTES:
                raise ValueError("Source exceeds 16 MiB limit")
            text = data.decode("utf-8-sig", "replace")
            if not valid_source(text):
                raise ValueError("Source is empty or not a valid M3U playlist")
            atomic_write(
                cache_path(cache_dir, source.url),
                json_text(
                    {
                        "url": source.url,
                        "fetched_at": datetime.now(timezone.utc).isoformat(),
                        "text": text,
                    }
                ),
            )
            return SourceResult(source, text, "fresh")
        except HTTPError as error:
            error_text = f"HTTP {error.code}"
            retryable = error.code == 429 or error.code >= 500
            error.close()
        except ValueError as error:
            error_text = str(error)
        except (URLError, OSError, HTTPException):
            error_text = "Connection, timeout or cache I/O failure"
        if not retryable or attempt + 1 == settings.source_attempts:
            break
        time.sleep(settings.retry_delay * (2**attempt))

    cached = read_json(cache_path(cache_dir, source.url))
    try:
        fetched_at = datetime.fromisoformat(cached["fetched_at"])
        age = (datetime.now(timezone.utc) - fetched_at).total_seconds()
        if (
            cached.get("url") == source.url
            and 0 <= age <= settings.source_cache_max_age_hours * 3600
            and isinstance(cached.get("text"), str)
            and valid_source(cached["text"])
        ):
            return SourceResult(source, cached["text"], "cached", error_text)
    except (KeyError, TypeError, ValueError):
        pass
    return SourceResult(source, "", "failed", error_text)


def fetch_sources(settings: Settings, cache_dir: Path) -> list[SourceResult]:
    sources = settings.sources()
    with ThreadPoolExecutor(max_workers=settings.source_workers) as pool:
        # executor.map preserves configured order, regardless of completion order.
        return list(pool.map(lambda source: fetch_source(source, settings, cache_dir), sources))
