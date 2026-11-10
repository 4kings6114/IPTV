"""Validated configuration. Relative input paths belong to the config directory."""

import json
import re
from dataclasses import dataclass, field, fields
from pathlib import Path
from urllib.parse import urlsplit

from .catalog import ALLOWED_COUNTRIES, BLOCK_KEYWORDS, BLOCKED_COUNTRIES


def country_code(value: str) -> str:
    code = value.strip().lower()
    return "uk" if code == "gb" else code


def http_url(value: str) -> bool:
    try:
        if any(char.isspace() or ord(char) < 32 for char in value):
            return False
        parsed = urlsplit(value)
        # Accessing .port also validates malformed/non-numeric port numbers.
        valid_port = parsed.port is None or 0 < parsed.port <= 65535
        return parsed.scheme.lower() in {"http", "https"} and bool(parsed.hostname) and valid_port
    except ValueError:
        return False


@dataclass(frozen=True)
class Source:
    url: str
    country: str = ""
    language: str = ""
    allow_unknown_country: bool = False


@dataclass(frozen=True)
class MetadataOptions:
    urls: tuple[str, ...] = ()
    file: str = ""
    cache_hours: float = 168
    refresh_hours: float = 24
    max_bytes: int = 33554432


@dataclass(frozen=True)
class EPGOptions:
    enabled: bool = False
    cache_hours: float = 24
    freshness_hours: float = 24
    max_bytes: int = 33554432
    max_expanded_bytes: int = 536870912
    aliases_file: str = "epg_aliases.json"
    keep_unverified: bool = True


@dataclass(frozen=True)
class PlaybackOptions:
    enabled: bool = False
    max_channels: int = 10
    seconds: float = 3
    timeout: float = 20
    ffmpeg: str = "ffmpeg"
    ffprobe: str = "ffprobe"


@dataclass(frozen=True)
class HealthCacheOptions:
    hours: float = 0
    max_hours: float = 24
    successes_per_step: int = 3


@dataclass(frozen=True)
class HostLimitOptions:
    concurrent: int = 2
    interval: float = 0
    max_retry_after: float = 10


@dataclass(frozen=True)
class PersonalizationOptions:
    favorites_file: str = "favorites.json"
    profiles_file: str = "profiles.json"


_OPTION_TYPES = {
    "metadata": MetadataOptions,
    "epg": EPGOptions,
    "playback": PlaybackOptions,
    "health_cache": HealthCacheOptions,
    "host_limit": HostLimitOptions,
    "personalization": PersonalizationOptions,
}


def _load_options(name: str, data: dict):
    cls = _OPTION_TYPES[name]
    if not isinstance(data, dict) or set(data) - {item.name for item in fields(cls)}:
        raise ValueError(f"Invalid {name} configuration keys")
    data = dict(data)
    for item in fields(cls):
        if item.name not in data:
            continue
        value = data[item.name]
        label = f"{name}.{item.name}"
        if item.type == tuple[str, ...]:
            if not isinstance(value, list) or not all(
                isinstance(part, str) and http_url(part) for part in value
            ):
                raise ValueError(f"{label} must be a list of HTTP(S) URLs")
            data[item.name] = tuple(value)
        elif item.type is bool:
            if type(value) is not bool:
                raise ValueError(f"{label} must be a boolean")
        elif item.type is str:
            if not isinstance(value, str) or any(char in value for char in "\r\n\x00"):
                raise ValueError(f"{label} must be a string without control characters")
            if not value.strip() and (name, item.name) not in {
                ("metadata", "file"),
                ("playback", "ffprobe"),
            }:
                raise ValueError(f"{label} must not be empty")
        else:
            if type(value) not in (int, float) or not 0 <= value < float("inf"):
                raise ValueError(f"{label} must be a finite non-negative number")
            if item.type is int and type(value) is not int:
                raise ValueError(f"{label} must be an integer")
            if item.name not in {"hours", "interval", "max_retry_after"} and value <= 0:
                raise ValueError(f"{label} must be positive")
    options = cls(**data)
    if name == "health_cache" and options.hours > options.max_hours:
        raise ValueError("health_cache.hours must not exceed max_hours")
    if name == "metadata" and options.refresh_hours > options.cache_hours:
        raise ValueError("metadata.refresh_hours must not exceed cache_hours")
    if name in {"epg", "metadata"} and options.max_bytes < 512:
        raise ValueError(f"{name}.max_bytes must be at least 512")
    if name == "epg" and not options.max_bytes <= options.max_expanded_bytes <= 536870912:
        raise ValueError("epg.max_expanded_bytes must be between max_bytes and 512 MiB")
    if name == "playback" and (
        options.seconds > 30 or options.timeout > 120 or options.max_channels > 100
    ):
        raise ValueError("Playback limits: seconds <= 30, timeout <= 120, max_channels <= 100")
    return options


@dataclass(frozen=True)
class Settings:
    allowed_countries: tuple[str, ...] = tuple(sorted(ALLOWED_COUNTRIES))
    blocked_countries: tuple[str, ...] = tuple(sorted(BLOCKED_COUNTRIES))
    blocked_keywords: tuple[str, ...] = tuple(BLOCK_KEYWORDS)
    stream_template: str = (
        "https://raw.githubusercontent.com/iptv-org/iptv/master/streams/{country}.m3u"
    )
    extra_sources: tuple[Source, ...] = ()
    epg_urls: tuple[str, ...] = ()
    check_links: bool = True
    drop_dead: bool = True
    max_workers: int = 20
    timeout: float = 7
    link_attempts: int = 2
    retry_delay: float = 0.5
    probe_bytes: int = 65536
    check_segments: bool = False
    failure_threshold: int = 2
    source_timeout: float = 30
    source_attempts: int = 3
    source_workers: int = 4
    source_cache_max_age_hours: float = 168
    min_available_source_ratio: float = 0.8
    min_channels: int = 1
    max_drop_ratio: float = 0.3
    save_backup: bool = True
    backup_keep: int = 7
    cache_dir: str = ".cache/iptv"
    manual_file: str = "manual.m3u"
    exclude_file: str = "exclude.txt"
    overrides_file: str = "overrides.json"
    metadata: MetadataOptions = field(default_factory=MetadataOptions)
    epg: EPGOptions = field(default_factory=EPGOptions)
    playback: PlaybackOptions = field(default_factory=PlaybackOptions)
    health_cache: HealthCacheOptions = field(default_factory=HealthCacheOptions)
    host_limit: HostLimitOptions = field(default_factory=HostLimitOptions)
    personalization: PersonalizationOptions = field(default_factory=PersonalizationOptions)
    preferred_sources: tuple[str, ...] = ()
    dashboard: bool = True

    def sources(self) -> tuple[Source, ...]:
        countries = ()
        if self.stream_template:
            countries = tuple(
                Source(self.stream_template.format(country=code), country=code)
                for code in sorted(set(self.allowed_countries) - set(self.blocked_countries))
            )
        # The same URL must not be counted twice by the availability guard/cache.
        unique = {source.url: source for source in (*countries, *self.extra_sources)}
        return tuple(unique.values())


def load_settings(path: Path) -> Settings:
    with path.open(encoding="utf-8") as handle:
        data = json.load(handle)
    if not isinstance(data, dict):
        raise ValueError("Configuration must be a JSON object")
    unknown = set(data) - {field.name for field in fields(Settings)}
    if unknown:
        raise ValueError(f"Unknown configuration keys: {', '.join(sorted(unknown))}")

    for key in ("allowed_countries", "blocked_countries", "blocked_keywords", "epg_urls"):
        if key not in data:
            continue
        if not isinstance(data[key], list) or not all(isinstance(item, str) for item in data[key]):
            raise ValueError(f"{key} must be a list of strings")
        data[key] = tuple(data[key])
    for key in ("allowed_countries", "blocked_countries"):
        if key in data:
            data[key] = tuple(sorted({country_code(code) for code in data[key]}))
            if any(not re.fullmatch(r"[a-z]{2}", code) for code in data[key]):
                raise ValueError(f"{key} must contain two-letter country codes")

    if "extra_sources" in data:
        if not isinstance(data["extra_sources"], list):
            raise ValueError("extra_sources must be a list")
        sources = []
        for item in data["extra_sources"]:
            if isinstance(item, str):
                item = {"url": item}
            if not isinstance(item, dict) or set(item) - {field.name for field in fields(Source)}:
                raise ValueError("Invalid extra_sources entry")
            if not isinstance(item.get("url"), str) or not http_url(item["url"]):
                raise ValueError("Source URLs must use HTTP or HTTPS")
            for key in ("country", "language"):
                if key in item and not isinstance(item[key], str):
                    raise ValueError(f"Source {key} must be a string")
            if item.get("country"):
                item["country"] = country_code(item["country"])
                if not re.fullmatch(r"[a-z]{2}", item["country"]):
                    raise ValueError("Source country must be a two-letter code")
            if "allow_unknown_country" in item and type(item["allow_unknown_country"]) is not bool:
                raise ValueError("allow_unknown_country must be a boolean")
            sources.append(Source(**item))
        data["extra_sources"] = tuple(sources)

    for name in _OPTION_TYPES:
        if name in data:
            data[name] = _load_options(name, data[name])
    if "preferred_sources" in data:
        if not isinstance(data["preferred_sources"], list) or not all(
            isinstance(item, str) and item.strip() and not any(char.isspace() for char in item)
            for item in data["preferred_sources"]
        ):
            raise ValueError("preferred_sources must be a list of URL prefixes or hostnames")
        data["preferred_sources"] = tuple(data["preferred_sources"])
    settings = Settings(**data)
    for key in ("check_links", "drop_dead", "check_segments", "save_backup", "dashboard"):
        if type(getattr(settings, key)) is not bool:
            raise ValueError(f"{key} must be a boolean")
    for key in (
        "max_workers",
        "link_attempts",
        "probe_bytes",
        "failure_threshold",
        "source_attempts",
        "source_workers",
        "min_channels",
        "backup_keep",
    ):
        value = getattr(settings, key)
        if type(value) is not int or value < 1:
            raise ValueError(f"{key} must be a positive integer")
    if settings.probe_bytes < 512:
        raise ValueError("probe_bytes must be at least 512")
    for key in ("timeout", "source_timeout", "source_cache_max_age_hours"):
        value = getattr(settings, key)
        if type(value) not in (int, float) or not 0 < value < float("inf"):
            raise ValueError(f"{key} must be a finite positive number")
    for key in ("min_available_source_ratio", "max_drop_ratio"):
        value = getattr(settings, key)
        if type(value) not in (int, float) or not 0 <= value <= 1:
            raise ValueError(f"{key} must be between 0 and 1")
    if type(settings.retry_delay) not in (int, float) or not 0 <= settings.retry_delay < float(
        "inf"
    ):
        raise ValueError("retry_delay must be a finite non-negative number")
    for key in ("stream_template", "cache_dir", "manual_file", "exclude_file", "overrides_file"):
        value = getattr(settings, key)
        if not isinstance(value, str) or (key != "stream_template" and not value.strip()):
            raise ValueError(f"{key} must be a non-empty string")
    if settings.stream_template:
        if "{country}" not in settings.stream_template:
            raise ValueError(
                "stream_template must contain {country}, or be empty for manual-only builds"
            )
        try:
            valid = http_url(settings.stream_template.format(country="us"))
        except (KeyError, ValueError, IndexError, AttributeError) as error:
            raise ValueError("Invalid stream_template") from error
        if not valid:
            raise ValueError("stream_template must use HTTP or HTTPS")
    if any(not http_url(url) or any(char in url for char in '",') for url in settings.epg_urls):
        raise ValueError("EPG URLs must be HTTP(S) URLs without quotes, commas or whitespace")
    return settings
