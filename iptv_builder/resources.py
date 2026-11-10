"""Bounded optional-resource downloads, including gzip, with expiring caches."""

import gzip
import hashlib
import io
import tempfile
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from http.client import HTTPException
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from .config import Settings, http_url
from .storage import atomic_write, json_text, read_json


class ResourceValidationError(ValueError):
    """A controlled validation reason safe for reports, never response bodies."""


@dataclass(frozen=True)
class Resource:
    url: str
    value: dict | None
    status: str
    error: str = ""


def resource_path(cache_dir: Path, namespace: str, url: str) -> Path:
    return cache_dir / "resources" / namespace / f"{hashlib.sha256(url.encode()).hexdigest()}.json"


def _cached(path: Path, url: str, hours: float) -> tuple[dict | None, float]:
    data = read_json(path)
    try:
        age = (
            datetime.now(timezone.utc) - datetime.fromisoformat(data["fetched_at"])
        ).total_seconds()
        value = data["value"]
        if (
            data.get("url") == url
            and isinstance(value, dict)
            and value.get("schema_version") == 1
            and 0 <= age <= hours * 3600
        ):
            return value, age
    except (KeyError, TypeError, ValueError):
        pass
    return None, float("inf")


def fetch_resource(
    url: str,
    namespace: str,
    settings: Settings,
    cache_dir: Path,
    parser: Callable[[bytes], dict],
    max_bytes: int,
    cache_hours: float,
    refresh_hours: float = 0,
    stream_parser: Callable | None = None,
) -> Resource:
    if not http_url(url):
        return Resource(url, None, "failed", "Non-HTTP resource URL")
    path = resource_path(cache_dir, namespace, url)
    cached, age = _cached(path, url, cache_hours)
    if cached is not None and refresh_hours and age < refresh_hours * 3600:
        return Resource(url, cached, "cached")
    error_text = "Resource download failed"
    for attempt in range(settings.source_attempts):
        retryable = True
        try:
            request = Request(
                url, headers={"User-Agent": "playlist-bot/6.0", "Accept-Encoding": "gzip"}
            )
            if stream_parser is not None:
                # Disk-backed compressed spool, at most 1 MiB in memory. The
                # streaming XML parser enforces its own expanded-byte limit.
                with tempfile.SpooledTemporaryFile(max_size=1048576) as spool:
                    with urlopen(request, timeout=settings.source_timeout) as response:
                        total = 0
                        while chunk := response.read(min(65536, max_bytes - total + 1)):
                            total += len(chunk)
                            if total > max_bytes:
                                raise ResourceValidationError("Resource exceeds byte limit")
                            spool.write(chunk)
                        encoding = response.headers.get("Content-Encoding", "").lower()
                    spool.seek(0)
                    compressed = spool.read(2) == b"\x1f\x8b" or encoding == "gzip"
                    spool.seek(0)
                    if compressed:
                        with gzip.GzipFile(fileobj=spool, mode="rb") as expanded:
                            value = stream_parser(expanded)
                    else:
                        value = stream_parser(spool)
            else:
                with urlopen(request, timeout=settings.source_timeout) as response:
                    body = response.read(max_bytes + 1)
                    encoding = response.headers.get("Content-Encoding", "").lower()
                if len(body) > max_bytes:
                    raise ResourceValidationError("Resource exceeds byte limit")
                if body.startswith(b"\x1f\x8b") or encoding == "gzip":
                    with gzip.GzipFile(fileobj=io.BytesIO(body)) as compressed:
                        body = compressed.read(max_bytes + 1)
                    if len(body) > max_bytes:
                        raise ResourceValidationError("Expanded resource exceeds byte limit")
                value = parser(body)
            if not isinstance(value, dict) or value.get("schema_version") != 1:
                raise ResourceValidationError("Invalid parsed resource")
            atomic_write(
                path,
                json_text(
                    {
                        "url": url,
                        "value": value,
                        "fetched_at": datetime.now(timezone.utc).isoformat(),
                    }
                ),
            )
            return Resource(url, value, "fresh")
        except HTTPError as error:
            error_text = f"HTTP {error.code}"
            retryable = error.code == 429 or error.code >= 500
            error.close()
        except ResourceValidationError as error:
            error_text = str(error)
            retryable = False
        except (ValueError, EOFError):
            error_text = "Malformed, empty or oversized resource"
            retryable = False
        except (OSError, URLError, HTTPException):
            error_text = "Resource connection, timeout or decoding failure"
        if not retryable or attempt + 1 == settings.source_attempts:
            break
        time.sleep(settings.retry_delay * (2**attempt))
    if cached is not None:
        return Resource(url, cached, "cached", error_text)
    return Resource(url, None, "failed", error_text)
