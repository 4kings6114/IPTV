"""Safe XMLTV/gzip inspection and explicit, identity-preserving ID aliases."""

import codecs
import io
import json
import re
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import budget
from .classification import override_id
from .config import Settings
from .m3u import Channel, stable_id
from .resources import ResourceValidationError, fetch_resource


def xmltv_time(value: str) -> datetime | None:
    match = re.fullmatch(r"(\d{12}|\d{14})(?:\s*([+-]\d{4}|Z))?", value.strip())
    if not match:
        return None
    stamp, offset = match.groups()
    try:
        result = datetime.strptime(stamp, "%Y%m%d%H%M%S" if len(stamp) == 14 else "%Y%m%d%H%M")
        zone = (
            timezone.utc if not offset or offset == "Z" else datetime.strptime(offset, "%z").tzinfo
        )
        return result.replace(tzinfo=zone).astimezone(timezone.utc)
    except ValueError:
        return None


class _XMLReader:
    def __init__(self, stream, maximum):
        self.stream, self.maximum, self.total = stream, maximum, 0
        self.decoder = codecs.getincrementaldecoder("utf-8-sig")()
        self.tail = ""

    def read(self, size=-1):
        budget.check()
        body = self.stream.read(min(65536, size) if size >= 0 else 65536)
        self.total += len(body)
        if self.total > self.maximum:
            raise ResourceValidationError("Expanded resource exceeds byte limit")
        try:
            text = self.decoder.decode(body, final=not body)
        except UnicodeError as error:
            raise ResourceValidationError("XMLTV must be UTF-8") from error
        # Bounded rolling detector even when whitespace/declarations cross a
        # chunk boundary; fail closed before ElementTree can expand entities.
        scan = self.tail + "".join(text.upper().split())
        if "<!DOCTYPE" in scan or "<!ENTITY" in scan:
            raise ResourceValidationError("DTD/entity declarations are not allowed")
        self.tail = scan[-16:]
        return body


def parse_xmltv(body: bytes) -> dict:
    return parse_xmltv_stream(io.BytesIO(body), max(1, len(body)))


def parse_xmltv_stream(stream, max_expanded_bytes: int) -> dict:
    channels, programmes = {}, {}
    invalid_times = 0
    programme_count, window_count = 0, 0
    try:
        parser = ET.iterparse(_XMLReader(stream, max_expanded_bytes), events=("start", "end"))
        _, root = next(parser)
        if root.tag != "tv":
            raise ResourceValidationError("Expected XMLTV tv root")
        for event, element in parser:
            if event != "end":
                continue
            if element.tag == "channel":
                identifier = element.get("id", "").strip()
                if identifier and not any(char in identifier for char in '\r\n\x00"'):
                    channels[identifier] = {
                        "names": [
                            child.text.strip()[:160]
                            for child in element.findall("display-name")[:3]
                            if child.text and child.text.strip()
                        ]
                    }
                if len(channels) > 50000:
                    raise ResourceValidationError("XMLTV channel count exceeds limit")
                element.clear()
                root.clear()
            elif element.tag == "programme":
                identifier = element.get("channel", "").strip()
                start = xmltv_time(element.get("start", ""))
                stop = xmltv_time(element.get("stop", ""))
                if identifier and start and stop and stop > start:
                    start_text, stop_text = start.isoformat(), stop.isoformat()
                    item = programmes.setdefault(identifier, {"count": 0, "windows": []})
                    item["count"] += 1
                    windows = item["windows"]
                    if windows and windows[-1][0] <= start_text <= windows[-1][1]:
                        windows[-1][1] = max(stop_text, windows[-1][1])
                    else:
                        windows.append([start_text, stop_text])
                        window_count += 1
                    programme_count += 1
                    if (
                        len(programmes) > 50000
                        or window_count > 250000
                        or programme_count > 2000000
                    ):
                        raise ResourceValidationError("XMLTV programme summary exceeds limit")
                else:
                    invalid_times += 1
                element.clear()
                root.clear()
    except (ET.ParseError, StopIteration) as error:
        raise ResourceValidationError("Malformed XMLTV") from error
    if not channels:
        raise ResourceValidationError("XMLTV has no declared channels")
    for identifier in channels:
        item = programmes.get(identifier, {})
        if item:
            windows = []
            for start, stop in sorted(item["windows"]):
                if windows and start <= windows[-1][1]:
                    windows[-1][1] = max(stop, windows[-1][1])
                else:
                    windows.append([start, stop])
            item["windows"] = windows
        channels[identifier].update(item)
    return {
        "schema_version": 1,
        "channels": channels,
        "invalid_programmes": invalid_times,
        "orphan_programmes": sum(
            item["count"] for key, item in programmes.items() if key not in channels
        ),
    }


def load_aliases(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    if (
        not isinstance(data, dict)
        or set(data) != {"channels"}
        or not isinstance(data["channels"], dict)
    ):
        raise ValueError("EPG aliases must contain a channels object")
    result = {}
    for key, value in data["channels"].items():
        if (
            not isinstance(key, str)
            or not key.strip()
            or not isinstance(value, str)
            or not value.strip()
            or any(char in key + value for char in '\r\n\x00"')
        ):
            raise ValueError("EPG aliases must map channel IDs to non-empty XMLTV IDs")
        normalized = override_id(key)
        if normalized in result:
            raise ValueError("Duplicate normalized EPG alias")
        result[normalized] = value.strip()
    return result


def _coverage(item: dict, now: datetime, freshness_hours: float) -> str:
    if not item or not item.get("count"):
        return "no_programmes"
    try:
        windows = [
            (datetime.fromisoformat(start), datetime.fromisoformat(stop))
            for start, stop in item["windows"]
        ]
        if not windows:
            return "no_programmes"
        if any(
            stop > now and start <= now + timedelta(hours=freshness_hours)
            for start, stop in windows
        ):
            return "covered"
        return "stale" if all(stop <= now for _, stop in windows) else "future"
    except (KeyError, TypeError, ValueError):
        return "invalid"


def validate_epg(
    settings: Settings,
    channels: list[Channel],
    cache_dir: Path,
    input_dir: Path,
    now: datetime | None = None,
) -> tuple[list[Channel], tuple[str, ...], dict, dict]:
    options = settings.epg
    if not options.enabled:
        return (
            channels,
            settings.epg_urls,
            {"enabled": False},
            {channel.identity: "unchecked" for channel in channels},
        )
    now = now or datetime.now(timezone.utc)
    aliases = load_aliases(input_dir / options.aliases_file)

    def fetch(url):
        return fetch_resource(
            url,
            "xmltv-v3",
            settings,
            cache_dir,
            parse_xmltv,
            options.max_bytes,
            options.cache_hours,
            refresh_hours=options.refresh_hours,
            stream_parser=lambda stream: parse_xmltv_stream(stream, options.max_expanded_bytes),
        )

    with ThreadPoolExecutor(max_workers=min(settings.source_workers, 4)) as pool:
        resources = list(pool.map(fetch, dict.fromkeys(settings.epg_urls)))
    guides, mapping, usable_urls = [], {}, []
    for resource in resources:
        value = resource.value or {}
        records = value.get("channels", {})
        if not isinstance(records, dict):
            records = {}
        coverage = {
            key: _coverage(item, now, options.freshness_hours)
            for key, item in records.items()
            if isinstance(item, dict)
        }
        counts = {
            status: sum(value == status for value in coverage.values())
            for status in sorted(set(coverage.values()))
        }
        guides.append(
            {
                "url": resource.url,
                "status": resource.status,
                "error": resource.error,
                "channels": len(records),
                "coverage": counts,
                "invalid_programmes": value.get("invalid_programmes", 0),
                "orphan_programmes": value.get("orphan_programmes", 0),
            }
        )
        if "covered" in coverage.values():
            usable_urls.append(resource.url)
        for key, status in coverage.items():
            if key not in mapping or status == "covered":
                mapping[key] = status
    rewritten, channel_status, missing, changed = [], {}, [], 0
    for channel in channels:
        original_id = channel.channel_id
        alias = aliases.get(override_id(original_id)) or aliases.get(stable_id(original_id))
        base_id = original_id.split("@", 1)[0]
        candidates = [alias] if alias else [channel.attrs.get("tvg-id", ""), original_id]
        if not alias and stable_id(original_id) == stable_id(base_id):
            candidates.append(base_id)
        matched = next((key for key in candidates if key and key in mapping), "")
        status = mapping.get(matched, "missing_id" if original_id else "no_id")
        channel_status[channel.identity] = status
        if status != "covered":
            missing.append(
                {
                    "identity": channel.identity,
                    "name": channel.name,
                    "id": original_id,
                    "target": alias or "",
                    "status": status,
                }
            )
        if matched and matched != channel.attrs.get("tvg-id"):
            if original_id:
                channel = channel.with_attribute("x-original-tvg-id", original_id)
            channel = channel.with_attribute("tvg-id", matched)
            changed += 1
        rewritten.append(channel)
    report = {
        "enabled": True,
        "guides": guides,
        "covered": sum(status == "covered" for status in channel_status.values()),
        "missing": len(missing),
        "missing_channels": missing,
        "rewritten_ids": changed,
        "header_policy": "keep_unverified" if options.keep_unverified else "verified_only",
    }
    header_urls = settings.epg_urls if options.keep_unverified else tuple(usable_urls)
    return rewritten, header_urls, report, channel_status
