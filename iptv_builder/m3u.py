"""Small, lossless-enough M3U parser and stable channel identities."""

import hashlib
import json
import re
from dataclasses import dataclass, replace
from urllib.parse import parse_qsl

from .config import country_code, http_url

_ATTRIBUTE = re.compile(r"([\w-]+)\s*=\s*([\"'])(.*?)\2")
_QUALITY_SUFFIX = re.compile(r"(?:sd|hd|fhd|uhd|4k|\d{3,4}p)", re.I)


def attributes(line: str) -> dict[str, str]:
    separator = display_separator(line)
    header = line[:separator] if separator >= 0 else line
    return {match[1].lower(): match[3] for match in _ATTRIBUTE.finditer(header)}


def display_separator(line: str) -> int:
    quote = ""
    for index, char in enumerate(line):
        if char in {'"', "'"}:
            if not quote:
                quote = char
            elif quote == char:
                quote = ""
        elif char == "," and not quote:
            return index
    return -1


def name_of(ext: tuple[str, ...] | list[str]) -> str:
    separator = display_separator(ext[0])
    return ext[0][separator + 1 :].strip() if separator >= 0 else ""


def base_name(name: str) -> str:
    name = re.sub(r"\s*\(?\d{3,4}\s*p\)?", "", name, flags=re.I)
    name = re.sub(r"\s*\[.*?\]", "", name)
    name = re.sub(r"\b(?:FHD|UHD|HD|4K|SD)\b", "", name, flags=re.I)
    return re.sub(r"\s+", " ", name).strip().casefold()


def res_of(name: str) -> int:
    match = re.search(r"\b(\d{3,4})\s*p\b", name, re.I)
    if match:
        return int(match[1])
    for pattern, height in (
        (r"\b(?:4k|uhd|2160)\b", 2160),
        (r"\b(?:fhd|full\s*hd)\b", 1080),
        (r"\bhd\b", 720),
        (r"\bsd\b", 480),
    ):
        if re.search(pattern, name, re.I):
            return height
    return 0


def country_from_tvg(tvg_id: str) -> str:
    match = re.search(r"\.([a-z]{2})(?:@|$)", tvg_id.lower())
    return country_code(match[1]) if match else ""


def stable_id(tvg_id: str) -> str:
    value = tvg_id.strip().casefold()
    stem, separator, variant = value.partition("@")
    stem = re.sub(r"\.gb$", ".uk", stem)
    # Resolution variants are alternatives; regional/time-shift feeds are not.
    return stem if not separator or _QUALITY_SUFFIX.fullmatch(variant) else f"{stem}@{variant}"


def set_attribute(line: str, key: str, value: str) -> str:
    if any(char in value for char in '\r\n"'):
        raise ValueError(f"Invalid value for {key}")
    pattern = re.compile(rf"(?<![\w-]){re.escape(key)}\s*=\s*([\"']).*?\1", re.I)
    separator = display_separator(line)
    if separator < 0:
        raise ValueError("EXTINF is missing its display-name separator")
    header, display = line[:separator], line[separator:]
    if pattern.search(header):
        return pattern.sub(lambda _: f'{key}="{value}"', header, count=1) + display
    return header + f' {key}="{value}"' + display


@dataclass(frozen=True)
class Channel:
    ext: tuple[str, ...]
    url: str
    source: str = ""
    manual: bool = False
    metadata_priority: int = 0

    @property
    def name(self) -> str:
        return name_of(self.ext)

    @property
    def attrs(self) -> dict[str, str]:
        return attributes(self.ext[0])

    @property
    def group(self) -> str:
        return self.attrs.get("group-title", "")

    @property
    def channel_id(self) -> str:
        # A guide-specific ID must not change the channel's stable identity.
        return self.attrs.get("x-original-tvg-id") or self.attrs.get("tvg-id", "")

    @property
    def identity(self) -> str:
        tvg_id = self.channel_id
        if tvg_id:
            return "id:" + stable_id(tvg_id)
        code = country_code(self.attrs.get("tvg-country", ""))
        return f"name:{code}:{base_name(self.name)}"

    @property
    def headers(self) -> dict[str, str]:
        headers = {}
        options = {
            "http-user-agent": "User-Agent",
            "http-referrer": "Referer",
            "http-origin": "Origin",
        }
        for line in self.ext[1:]:
            if line.upper().startswith("#EXTVLCOPT:"):
                key, _, value = line.split(":", 1)[1].partition("=")
                if key.lower() in options and value and not any(char in value for char in "\r\n"):
                    headers[options[key.lower()]] = value
            elif line.upper().startswith("#KODIPROP:"):
                key, _, value = line.split(":", 1)[1].partition("=")
                if key in {
                    "inputstream.adaptive.stream_headers",
                    "inputstream.adaptive.manifest_headers",
                }:
                    headers.update(parse_qsl(value))
        if "|" in self.url:
            headers.update(parse_qsl(self.url.split("|", 1)[1]))
        # Normalize names so equivalent directives share one probe/history key.
        return {
            key.lower(): value
            for key, value in headers.items()
            if re.fullmatch(r"[A-Za-z0-9-]+", key) and not any(char in value for char in "\r\n")
        }

    @property
    def request_url(self) -> str:
        return self.url.split("|", 1)[0]

    @property
    def probe_key(self) -> str:
        value = json.dumps([self.request_url, sorted(self.headers.items())], ensure_ascii=False)
        return hashlib.sha256(value.encode()).hexdigest()

    def with_group(self, group: str) -> "Channel":
        ext = (set_attribute(self.ext[0], "group-title", group),) + tuple(
            f"#EXTGRP:{group}" if line.upper().startswith("#EXTGRP:") else line
            for line in self.ext[1:]
        )
        return replace(self, ext=ext)

    def with_attribute(self, key: str, value: str) -> "Channel":
        return replace(self, ext=(set_attribute(self.ext[0], key, value), *self.ext[1:]))

    def renamed(self, name: str) -> "Channel":
        if not name.strip() or any(char in name for char in "\r\n"):
            raise ValueError("Invalid channel name")
        separator = display_separator(self.ext[0])
        return replace(self, ext=(self.ext[0][: separator + 1] + name, *self.ext[1:]))


def parse(text: str, source: str = "", manual: bool = False) -> list[Channel]:
    result = []
    pending: list[str] = []
    for raw_line in text.lstrip("\ufeff").splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if line.upper().startswith("#EXTINF:"):
            pending = [line]
        elif line.startswith("#"):
            if pending and not line.upper().startswith("#EXTM3U"):
                pending.append(line)
        elif pending:
            if http_url(line.split("|", 1)[0]) and name_of(pending):
                result.append(Channel(tuple(pending), line, source, manual))
            pending = []
    return result


def valid_source(text: str) -> bool:
    header = text.lstrip("\ufeff \r\n\t")
    return bool(re.match(r"#EXTM3U(?:\s|$)", header, re.I)) and bool(parse(text))


def render_m3u(channels: list[Channel], epg_urls: tuple[str, ...] = ()) -> str:
    header = '#EXTM3U url-tvg="' + ",".join(epg_urls) + '"' if epg_urls else "#EXTM3U"
    lines = [header]
    for channel in channels:
        lines.extend((*channel.ext, channel.url))
    return "\n".join(lines) + "\n"


def validate_output(text: str, expected_count: int) -> None:
    channels = parse(text)
    if not text.startswith("#EXTM3U") or len(channels) != expected_count:
        raise ValueError("Generated playlist failed its round-trip validation")
    if sum(line.upper().startswith("#EXTINF:") for line in text.splitlines()) != expected_count:
        raise ValueError("Generated playlist contains malformed entries")
    if len({channel.url for channel in channels}) != len(channels):
        raise ValueError("Generated playlist contains duplicate URLs")
