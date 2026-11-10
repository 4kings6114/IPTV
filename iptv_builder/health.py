"""Bounded GET probes: HTTP success alone is not proof of a playable stream."""

import re
import socket
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from http.client import HTTPException
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin
from urllib.request import Request, urlopen

from .config import Settings, http_url
from .m3u import Channel


@dataclass(frozen=True)
class ProbeResult:
    status: str
    reason: str
    resolution: int = 0

    @property
    def healthy(self) -> bool:
        return self.status == "healthy"


class ProbeFailure(Exception):
    def __init__(self, status: str, reason: str, retryable: bool = False):
        super().__init__(reason)
        self.result = ProbeResult(status, reason)
        self.retryable = retryable


def _fetch(url: str, headers: dict, settings: Settings) -> tuple[bytes, str, str, bool]:
    if not http_url(url):
        raise ProbeFailure("invalid", "Non-HTTP stream URL")
    request_headers = {
        "user-agent": "Mozilla/5.0 (compatible; playlist-bot/5.0)",
        "accept": "*/*",
        **headers,
        "range": f"bytes=0-{settings.probe_bytes - 1}",
    }
    for use_range in (True, False):
        if not use_range:
            request_headers.pop("range", None)
        try:
            request = Request(url, headers=request_headers, method="GET")
            with urlopen(request, timeout=settings.timeout) as response:
                data = response.read(settings.probe_bytes + 1)
                return (
                    data[: settings.probe_bytes],
                    response.headers.get("Content-Type", ""),
                    response.geturl(),
                    len(data) > settings.probe_bytes,
                )
        except HTTPError as error:
            code = error.code
            error.close()
            if code == 416 and use_range:
                continue
            status = "restricted" if code in {401, 403} else "http_error"
            raise ProbeFailure(status, f"HTTP {code}", code == 429 or code >= 500) from error
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
) -> ProbeResult:
    if url in seen or len(seen) >= 4:
        raise ProbeFailure("invalid", "Cyclic or excessively nested HLS playlist")
    data, content_type, final_url, truncated = _fetch(url, headers, settings)
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
                    result = _inspect(child, headers, settings, seen | {url})
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
            _inspect(segments[0], headers, settings, seen | {url}, segment=True)
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


def probe(channel: Channel, settings: Settings) -> ProbeResult:
    if not settings.check_links:
        return ProbeResult("unchecked", "Link checking disabled")
    for attempt in range(settings.link_attempts):
        try:
            return _inspect(channel.request_url, channel.headers, settings)
        except ProbeFailure as error:
            if not error.retryable or attempt + 1 == settings.link_attempts:
                return error.result
            time.sleep(settings.retry_delay * (2**attempt))
    raise AssertionError("link_attempts must be positive")


def check_candidates(channels: list[Channel], settings: Settings) -> dict[str, ProbeResult]:
    unique = {channel.probe_key: channel for channel in channels}
    if not settings.check_links:
        return {key: ProbeResult("unchecked", "Link checking disabled") for key in unique}
    print(f"Checking {len(unique)} distinct requests ({settings.max_workers} workers)...")
    results = {}
    with ThreadPoolExecutor(max_workers=settings.max_workers) as pool:
        pending = {pool.submit(probe, channel, settings): key for key, channel in unique.items()}
        healthy = 0
        for future in as_completed(pending):
            result = future.result()
            results[pending[future]] = result
            healthy += int(result.healthy)
            checked = len(results)
            if checked % 100 == 0 or checked == len(unique):
                print(
                    f"  {checked}/{len(unique)} checked | healthy: {healthy} | unavailable: {checked - healthy}",
                    flush=True,
                )
    return results
