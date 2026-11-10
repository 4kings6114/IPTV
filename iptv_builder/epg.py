"""Safe XMLTV/gzip inspection and explicit, identity-preserving ID aliases."""

import io
import json
import re
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .classification import override_id
from .config import Settings
from .m3u import Channel, stable_id
from .resources import fetch_resource


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


def parse_xmltv(body: bytes) -> dict:
    # ElementTree does not fetch external entities, but reject DTDs/expansion
    # explicitly. Decode first so UTF-16 cannot hide an ENTITY declaration.
    try:
        text = body.decode("utf-8-sig")
    except UnicodeError as error:
        raise ValueError("XMLTV must be UTF-8") from error
    if re.search(r"<!\s*(?:DOCTYPE|ENTITY)\b", text, re.I):
        raise ValueError("DTD/entity declarations are not allowed")
    channels, programmes = {}, {}
    invalid_times = 0
    try:
        parser = ET.iterparse(io.StringIO(text), events=("start", "end"))
        _, root = next(parser)
        if root.tag != "tv":
            raise ValueError("Expected XMLTV tv root")
        for event, element in parser:
            if event != "end":
                continue
            if element.tag == "channel":
                identifier = element.get("id", "").strip()
                if identifier and not any(char in identifier for char in '\r\n\x00"'):
                    channels[identifier] = {}
                element.clear()
                root.clear()
            elif element.tag == "programme":
                identifier = element.get("channel", "").strip()
                start = xmltv_time(element.get("start", ""))
                stop = xmltv_time(element.get("stop", ""))
                if identifier and start and stop and stop > start:
                    start_text, stop_text = start.isoformat(), stop.isoformat()
                    item = programmes.setdefault(
                        identifier, {"count": 0, "start": start_text, "stop": stop_text}
                    )
                    item["count"] += 1
                    item["start"] = min(item["start"], start_text)
                    item["stop"] = max(item["stop"], stop_text)
                else:
                    invalid_times += 1
                element.clear()
                root.clear()
    except (ET.ParseError, StopIteration) as error:
        raise ValueError("Malformed XMLTV") from error
    if not channels:
        raise ValueError("XMLTV has no declared channels")
    for identifier in channels:
        channels[identifier] = programmes.get(identifier, {})
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
        start, stop = datetime.fromisoformat(item["start"]), datetime.fromisoformat(item["stop"])
        if stop <= now:
            return "stale"
        if start > now + timedelta(hours=freshness_hours):
            return "future"
        return "covered"
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
            "xmltv-v1",
            settings,
            cache_dir,
            parse_xmltv,
            options.max_bytes,
            options.cache_hours,
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
