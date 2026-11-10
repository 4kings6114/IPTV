"""Bounded GET probes: HTTP success alone is not proof of a playable stream."""

import re
import socket
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import contextmanager
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from http.client import HTTPException
from threading import BoundedSemaphore, Lock
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from .config import Settings, http_url
from .m3u import Channel


@dataclass(frozen=True)
class ProbeResult:
    status: str
    reason: str
    resolution: int = 0
    cached: bool = False
    checked_at: str = ""

    @property
    def healthy(self) -> bool:
        return self.status == "healthy"


class ProbeFailure(Exception):
    def __init__(self, status: str, reason: str, retryable: bool = False, retry_after: float = 0):
        super().__init__(reason)
        self.result = ProbeResult(status, reason)
        self.retryable = retryable
        self.retry_after = retry_after


class HostLimiter:
    """Limit in-flight requests, including HLS children and redirect targets."""

    def __init__(self, settings: Settings):
        self.options = settings.host_limit
        self.lock = Lock()
        self.hosts = {}

    def _state(self, url: str):
        parsed = urlsplit(url)
        host = (
            (parsed.hostname or "").casefold(),
            parsed.port or (443 if parsed.scheme == "https" else 80),
        )
        with self.lock:
            return self.hosts.setdefault(host, [BoundedSemaphore(self.options.concurrent), 0.0])

    @contextmanager
    def request(self, url: str):
        state = self._state(url)
        with state[0]:
            # Re-evaluate after sleep: another request may have received 429.
            while True:
                with self.lock:
                    wait = state[1] - time.monotonic()
                    if wait <= 0:
                        state[1] = time.monotonic() + self.options.interval
                        break
                time.sleep(wait)
            yield

    def defer(self, url: str, seconds: float) -> None:
        state = self._state(url)
        with self.lock:
            state[1] = max(state[1], time.monotonic() + min(seconds, self.options.max_retry_after))


def retry_after(value: str, maximum: float) -> float:
    try:
        seconds = float(value)
    except (ValueError, TypeError):
        try:
            seconds = (parsedate_to_datetime(value) - datetime.now(timezone.utc)).total_seconds()
        except (ValueError, TypeError, OverflowError):
            return 0
    return min(maximum, max(0, seconds))


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def urlopen(request, timeout):
    # Follow redirects ourselves so their destination shares the host limiter.
    return build_opener(_NoRedirect()).open(request, timeout=timeout)


def _open_read(url: str, headers: dict, settings: Settings, limiter: HostLimiter):
    visited = set()
    for _ in range(6):
        if not http_url(url) or url in visited:
            raise ProbeFailure("invalid", "Invalid or cyclic HTTP redirect")
        visited.add(url)
        try:
            request = Request(url, headers=headers, method="GET")
            with limiter.request(url):
                with urlopen(request, timeout=settings.timeout) as response:
                    data = response.read(settings.probe_bytes + 1)
                    return (
                        data[: settings.probe_bytes],
                        response.headers.get("Content-Type", ""),
                        response.geturl(),
                        len(data) > settings.probe_bytes,
                    )
        except HTTPError as error:
            if error.code not in {301, 302, 303, 307, 308}:
                raise
            location = (error.headers or {}).get("Location", "")
            error.close()
            if not location:
                raise ProbeFailure("invalid", "HTTP redirect has no destination") from error
            destination = urljoin(url, location)
            if (urlsplit(url).scheme, urlsplit(url).netloc.casefold()) != (
                urlsplit(destination).scheme,
                urlsplit(destination).netloc.casefold(),
            ):
                headers = {
                    key: value
                    for key, value in headers.items()
                    if key.casefold() not in {"authorization", "cookie", "proxy-authorization"}
                }
            url = destination
    raise ProbeFailure("invalid", "Too many HTTP redirects")


def _fetch(
    url: str, headers: dict, settings: Settings, limiter: HostLimiter | None = None
) -> tuple[bytes, str, str, bool]:
    if not http_url(url):
        raise ProbeFailure("invalid", "Non-HTTP stream URL")
    limiter = limiter or HostLimiter(settings)
    request_headers = {
        "user-agent": "Mozilla/5.0 (compatible; playlist-bot/6.0)",
        "accept": "*/*",
        **headers,
        "range": f"bytes=0-{settings.probe_bytes - 1}",
    }
    for use_range in (True, False):
        if not use_range:
            request_headers.pop("range", None)
        try:
            return _open_read(url, request_headers, settings, limiter)
        except HTTPError as error:
            code = error.code
            delay = retry_after(
                (error.headers or {}).get("Retry-After", ""), settings.host_limit.max_retry_after
            )
            error.close()
            if code == 416 and use_range:
                continue
            if code in {429, 503}:
                limiter.defer(error.url, delay)
            status = "restricted" if code in {401, 403} else "http_error"
            raise ProbeFailure(status, f"HTTP {code}", code == 429 or code >= 500, delay) from error
        except TimeoutError as error:
            raise ProbeFailure("timeout", "Request timed out", True) from error
        except URLError as error:
            if isinstance(error.reason, (socket.timeout, TimeoutError)):
                raise ProbeFailure("timeout", "Request timed out", True) from error
            raise ProbeFailure("network_error", "Connection or DNS/TLS failure", True) from error
        except (OSError, HTTPException) as error:
            raise ProbeFailure("network_error", "Connection interrupted", True) from error
        except ValueError as error:
            raise ProbeFailure("invalid", "Invalid stream URL or request headers") from error
    raise ProbeFailure("http_error", "HTTP 416")


def _binary_stream(data: bytes, content_type: str, segment: bool = False) -> bool:
    lower = data.lstrip(b"\xef\xbb\xbf \r\n\t").lower()
    if (
        not data
        or lower.startswith((b"<!doctype", b"<html", b"{", b"["))
        or b"<html" in lower[:100]
    ):
        return False
    content_type = content_type.lower().split(";", 1)[0].strip()
    if (
        (data[:1] == b"G" and len(data) >= 188)
        or data.startswith(b"ID3")
        or data[4:8] in {b"ftyp", b"styp", b"moof"}
    ):
        return True
    if content_type.startswith(("video/", "audio/")):
        return True
    if content_type in {"application/octet-stream", "binary/octet-stream"}:
        # Encrypted HLS fragments have no recognizable plaintext magic.
        return segment or len(data) >= 188
    return False


def _inspect(
    url: str,
    headers: dict,
    settings: Settings,
    seen: frozenset[str] = frozenset(),
    segment: bool = False,
    limiter: HostLimiter | None = None,
) -> ProbeResult:
    if url in seen or len(seen) >= 4:
        raise ProbeFailure("invalid", "Cyclic or excessively nested HLS playlist")
    data, content_type, final_url, truncated = _fetch(url, headers, settings, limiter)
    stripped = data.lstrip(b"\xef\xbb\xbf \r\n\t")
    if not segment and stripped.startswith(b"#EXTM3U"):
        if truncated:
            raise ProbeFailure("invalid", "HLS manifest exceeds probe_bytes")
        lines = [
            line.strip()
            for line in stripped.decode("utf-8", "replace").splitlines()
            if line.strip()
        ]
        variants = []
        height = 0
        awaiting_variant = False
        for line in lines:
            if line.startswith("#EXT-X-STREAM-INF:"):
                awaiting_variant = True
                match = re.search(r"RESOLUTION=\d+x(\d+)", line, re.I)
                height = int(match[1]) if match else 0
            elif not line.startswith("#") and awaiting_variant:
                variants.append((height, urljoin(final_url, line)))
                awaiting_variant = False
        if variants:
            last_error = None
            for height, child in sorted(variants, reverse=True)[:3]:
                try:
                    result = _inspect(child, headers, settings, seen | {url}, limiter=limiter)
                    return ProbeResult(
                        "healthy",
                        "Valid HLS master and media playlist",
                        result.resolution or height,
                    )
                except ProbeFailure as error:
                    last_error = error
            if last_error:
                raise last_error
        segments = [urljoin(final_url, line) for line in lines if not line.startswith("#")]
        if not any(line.startswith("#EXTINF:") for line in lines) or not segments:
            raise ProbeFailure("invalid", "HLS playlist has no playable variants or segments")
        if any(not http_url(child) for child in segments):
            raise ProbeFailure("invalid", "Non-HTTP HLS segment URL")
        if settings.check_segments:
            _inspect(segments[0], headers, settings, seen | {url}, segment=True, limiter=limiter)
            return ProbeResult("healthy", "Valid HLS playlist and sampled segment")
        return ProbeResult("healthy", "Valid HLS media playlist (segments not sampled)")
    if not segment and (b"<MPD" in stripped[:500] or "dash+xml" in content_type.lower()):
        # DASH validation remains structural; no DRM/decoder claim.
        import xml.etree.ElementTree as ET

        if truncated:
            raise ProbeFailure("invalid", "DASH manifest exceeds probe_bytes")
        try:
            root = ET.fromstring(data)
        except ET.ParseError as error:
            raise ProbeFailure("invalid", "Malformed DASH manifest") from error
        tags = {element.tag.rsplit("}", 1)[-1] for element in root.iter()}
        if root.tag.rsplit("}", 1)[-1] != "MPD" or not {"Period", "Representation"} <= tags:
            raise ProbeFailure("invalid", "DASH manifest has no playable representations")
        return ProbeResult("healthy", "Valid DASH manifest (segments not sampled)")
    if _binary_stream(data, content_type, segment):
        return ProbeResult("healthy", "Non-empty media response")
    raise ProbeFailure("invalid", "Empty, HTML/JSON or unrecognized stream response")


def probe(channel: Channel, settings: Settings, limiter: HostLimiter | None = None) -> ProbeResult:
    if not settings.check_links:
        return ProbeResult("unchecked", "Link checking disabled")
    limiter = limiter or HostLimiter(settings)
    for attempt in range(settings.link_attempts):
        try:
            return _inspect(channel.request_url, channel.headers, settings, limiter=limiter)
        except ProbeFailure as error:
            if not error.retryable or attempt + 1 == settings.link_attempts:
                return error.result
            time.sleep(max(error.retry_after, settings.retry_delay * (2**attempt)))
    raise AssertionError("link_attempts must be positive")


def _cached_result(old: dict, settings: Settings, now: datetime) -> ProbeResult | None:
    options = settings.health_cache
    if not options.hours or not isinstance(old, dict) or old.get("status") != "healthy":
        return None
    try:
        successes = old.get("consecutive_successes", 0)
        if type(successes) is not int or successes < 0:
            return None
        hours = min(
            options.max_hours, options.hours * 2 ** min(8, successes // options.successes_per_step)
        )
        age = (now - datetime.fromisoformat(old["checked_at"])).total_seconds()
        resolution = old.get("resolution", 0)
        if type(resolution) is not int or resolution < 0 or not isinstance(old.get("reason"), str):
            return None
        if 0 <= age < hours * 3600:
            return ProbeResult("healthy", old["reason"], resolution, True, old["checked_at"])
    except (KeyError, TypeError, ValueError, OverflowError):
        pass
    return None


def check_candidates(
    channels: list[Channel], settings: Settings, previous: dict | None = None
) -> dict[str, ProbeResult]:
    unique = {channel.probe_key: channel for channel in channels}
    if not settings.check_links:
        return {key: ProbeResult("unchecked", "Link checking disabled") for key in unique}
    now = datetime.now(timezone.utc)
    results, pending_channels = {}, {}
    for key, channel in unique.items():
        cached = _cached_result((previous or {}).get(key, {}), settings, now)
        if cached:
            results[key] = cached
        else:
            pending_channels[key] = channel
    print(
        f"Checking {len(pending_channels)} distinct requests; {len(results)} cached successes ({settings.max_workers} workers)..."
    )
    limiter = HostLimiter(settings)
    # Round-robin hosts so one busy origin is less likely to occupy every worker.
    hosts = {}
    for key, channel in pending_channels.items():
        hosts.setdefault(urlsplit(channel.request_url).netloc.casefold(), []).append((key, channel))
    ordered = []
    while hosts:
        for host in list(hosts):
            ordered.append(hosts[host].pop())
            if not hosts[host]:
                del hosts[host]
    with ThreadPoolExecutor(max_workers=settings.max_workers) as pool:
        pending = {pool.submit(probe, channel, settings, limiter): key for key, channel in ordered}
        healthy = 0
        for checked, future in enumerate(as_completed(pending), start=1):
            result = future.result()
            results[pending[future]] = replace(
                result, checked_at=datetime.now(timezone.utc).isoformat()
            )
            healthy += int(result.healthy)
            if checked % 100 == 0 or checked == len(pending):
                print(
                    f"  {checked}/{len(pending)} checked | healthy: {healthy} | unavailable: {checked - healthy}",
                    flush=True,
                )
    return results
