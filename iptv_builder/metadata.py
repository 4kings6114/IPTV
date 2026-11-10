"""Reference channel/feed/logo metadata, matched by ID, never fuzzy name alone."""

import json
import re
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path

from .config import Settings, country_code, http_url
from .m3u import Channel, base_name, stable_id
from .resources import Resource, fetch_resource


def _text(value) -> str:
    return (
        value.strip()
        if isinstance(value, str) and not any(ord(char) < 32 for char in value)
        else ""
    )


def parse_metadata(body: bytes) -> dict:
    data = json.loads(body.decode("utf-8-sig"))
    if not isinstance(data, list) or not data:
        raise ValueError("Metadata must be a non-empty JSON array")
    records = []
    for item in data:
        if not isinstance(item, dict):
            continue
        if (
            _text(item.get("id"))
            and _text(item.get("name"))
            and re.fullmatch(r"[a-zA-Z]{2}", _text(item.get("country")))
        ):
            records.append(
                {
                    "kind": "channel",
                    "id": item["id"],
                    "name": item["name"],
                    "country": item["country"],
                    "categories": item.get("categories", []),
                    "languages": item.get("languages", []),
                    "logo": item.get("logo", ""),
                }
            )
        elif (
            _text(item.get("channel"))
            and _text(item.get("id"))
            and isinstance(item.get("languages"), list)
        ):
            records.append(
                {
                    "kind": "feed",
                    "channel": item["channel"],
                    "id": item["id"],
                    "languages": item["languages"],
                    "is_main": item.get("is_main") is True,
                }
            )
        elif (
            _text(item.get("channel"))
            and isinstance(item.get("url"), str)
            and http_url(item["url"])
        ):
            records.append(
                {
                    "kind": "logo",
                    "channel": item["channel"],
                    "feed": item.get("feed"),
                    "url": item["url"],
                    "in_use": item.get("in_use") is True,
                }
            )
    if not records:
        raise ValueError("Metadata contains no recognized records")
    return {"schema_version": 1, "records": records}


def _strings(value) -> list[str]:
    return [_text(part) for part in value if _text(part)] if isinstance(value, list) else []


@dataclass
class ReferenceCatalog:
    channels: dict = field(default_factory=dict)
    feeds: dict = field(default_factory=dict)
    logos: dict = field(default_factory=dict)
    sources: list[dict] = field(default_factory=list)

    def add(self, resource: Resource) -> None:
        self.sources.append(
            {"url": resource.url, "status": resource.status, "error": resource.error}
        )
        records = resource.value.get("records", []) if isinstance(resource.value, dict) else []
        for item in records:
            if not isinstance(item, dict):
                continue
            if item.get("kind") == "channel" and isinstance(item.get("id"), str):
                self.channels.setdefault(stable_id(item["id"]), item)
            elif item.get("kind") in {"feed", "logo"} and isinstance(item.get("channel"), str):
                target = self.feeds if item["kind"] == "feed" else self.logos
                target.setdefault(stable_id(item["channel"]), []).append(item)

    def lookup(self, channel: Channel) -> dict:
        identifier = channel.channel_id
        base, _, variant = identifier.partition("@")
        key = stable_id(base)
        record = self.channels.get(key)
        if not record:
            return {}
        feeds = self.feeds.get(key, [])
        exact = [feed for feed in feeds if str(feed.get("id", "")).casefold() == variant.casefold()]
        mains = [feed for feed in feeds if feed.get("is_main")]
        candidates = exact or (mains if not variant or stable_id(identifier) == key else [])
        if not candidates and len(feeds) == 1 and (not variant or stable_id(identifier) == key):
            candidates = feeds
        languages = _strings(record.get("languages"))
        if not languages and candidates:
            distinct = {tuple(_strings(feed.get("languages"))) for feed in candidates}
            if len(distinct) == 1:
                languages = list(next(iter(distinct)))
        languages = [
            value for value in languages if re.fullmatch(r"[a-zA-Z]{2,3}(?:-[a-zA-Z0-9]+)*", value)
        ]
        logos = [
            logo
            for logo in self.logos.get(key, [])
            if not logo.get("feed") or str(logo["feed"]).casefold() == variant.casefold()
        ]
        logos.sort(
            key=lambda logo: (
                not logo.get("in_use"),
                not bool(logo.get("feed")),
                logo.get("url", ""),
            )
        )
        logo = logos[0]["url"] if logos else _text(record.get("logo"))
        if not http_url(logo) or '"' in logo:
            logo = ""
        return {
            "name": _text(record.get("name")),
            "country": country_code(_text(record.get("country"))),
            "language": languages[0] if languages else "",
            "category": ";".join(_strings(record.get("categories"))),
            "logo": logo,
        }

    def summary(self, channels: list[Channel]) -> dict:
        distinct = {channel.identity: channel for channel in channels}
        missing = [
            channel.channel_id
            for channel in distinct.values()
            if channel.channel_id and not self.lookup(channel)
        ]
        without_id = sum(not channel.channel_id for channel in distinct.values())
        name_index = {}
        for key, record in self.channels.items():
            name_index.setdefault(base_name(record.get("name", "")), []).append(key)
        ambiguous = []
        for channel in distinct.values():
            if self.lookup(channel):
                continue
            candidates = name_index.get(base_name(channel.name), [])
            code = country_code(channel.attrs.get("tvg-country", ""))
            if code:
                candidates = [
                    key
                    for key in candidates
                    if country_code(self.channels[key].get("country", "")) == code
                ]
            if len(candidates) > 1:
                ambiguous.append(
                    {
                        "identity": channel.identity,
                        "name": channel.name,
                        "candidates": sorted(candidates)[:10],
                    }
                )
        return {
            "sources": self.sources,
            "ambiguous": len(ambiguous),
            "ambiguous_examples": ambiguous[:50],
            "reference_channels": len(self.channels),
            "matched": len(distinct) - len(missing) - without_id,
            "missing": len(missing),
            "without_id": without_id,
            "missing_examples": sorted(missing)[:50],
        }


def load_metadata(settings: Settings, cache_dir: Path, input_dir: Path) -> ReferenceCatalog:
    options = settings.metadata
    catalog = ReferenceCatalog()

    def fetch(url):
        return fetch_resource(
            url,
            "metadata-v1",
            settings,
            cache_dir,
            parse_metadata,
            options.max_bytes,
            options.cache_hours,
            options.refresh_hours,
        )

    with ThreadPoolExecutor(max_workers=min(settings.source_workers, 3)) as pool:
        for resource in pool.map(fetch, dict.fromkeys(options.urls)):
            catalog.add(resource)
    if options.file:
        path = input_dir / options.file
        if not path.exists() or path.stat().st_size > options.max_bytes:
            raise ValueError("Local metadata file is missing or exceeds its byte limit")
        catalog.add(Resource(path.name, parse_metadata(path.read_bytes()), "local"))
    return catalog
