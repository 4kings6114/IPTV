"""Explicit favorites and bounded, declarative personalized M3U profiles."""

import json
import re
from dataclasses import replace
from pathlib import Path
from urllib.parse import urlencode, urlsplit

from .classification import infer_language, language_code
from .config import Settings, country_code
from .health import ProbeResult, child_request_headers
from .m3u import Channel, render_m3u, res_of, stable_id, validate_output
from .privacy import exportable_channel, sensitive_url
from .reliability import reliability


def source_rank(channel: Channel, preferences: tuple[str, ...]) -> int:
    for rank, preference in enumerate(preferences):
        for value in (channel.source, channel.request_url):
            parsed = urlsplit(value)
            if "://" not in preference:
                matched = preference.casefold() in {
                    parsed.netloc.casefold(),
                    (parsed.hostname or "").casefold(),
                }
            else:
                prefix = urlsplit(preference)
                matched = (parsed.scheme, parsed.netloc) == (
                    prefix.scheme,
                    prefix.netloc,
                ) and parsed.path.startswith(prefix.path)
            if matched:
                return rank
    return len(preferences)


def channel_language(channel: Channel) -> str:
    return infer_language(channel.name, channel.attrs.get("tvg-language", ""))


def _read(path: Path, section: str):
    if not path.exists():
        return [] if section == "channels" else {}
    if path.stat().st_size > 1024 * 1024:
        raise ValueError(f"{path.name} exceeds 1 MiB")
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or set(data) != {section}:
        raise ValueError(f"{path.name} must contain only '{section}'")
    return data[section]


def load_personalization(settings: Settings, input_dir: Path) -> tuple[set[str], dict]:
    options = settings.personalization
    favorites = _read(input_dir / options.favorites_file, "channels")
    if not isinstance(favorites, list) or not all(
        isinstance(value, str) and value.strip() and not any(ord(char) < 32 for char in value)
        for value in favorites
    ):
        raise ValueError(
            "Favorites must be a list of channel IDs or name:country:base-name identities"
        )
    normalized_favorites = [value.strip().casefold() for value in favorites]
    favorites = {
        value if value.startswith("name:") else "id:" + stable_id(value.removeprefix("id:"))
        for value in normalized_favorites
    }
    profiles = _read(input_dir / options.profiles_file, "profiles")
    if not isinstance(profiles, dict) or len(profiles) > 20:
        raise ValueError("profiles must be an object with at most 20 profiles")
    validated = {}
    for name, item in profiles.items():
        if (
            not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,39}", name)
            or not isinstance(item, dict)
            or set(item)
            - {
                "languages",
                "countries",
                "max_resolution",
                "include_unknown_resolution",
                "max_bitrate",
                "include_unknown_bitrate",
                "preferred_sources",
                "favorites_only",
            }
        ):
            raise ValueError(f"Invalid profile: {name}")
        for key in ("languages", "countries", "preferred_sources"):
            values = item.get(key, [])
            if not isinstance(values, list) or not all(
                isinstance(value, str)
                and value.strip()
                and not any(ord(char) < 32 for char in value)
                for value in values
            ):
                raise ValueError(f"Invalid profile {name}.{key}")
        for key in ("include_unknown_resolution", "include_unknown_bitrate", "favorites_only"):
            if key in item and type(item[key]) is not bool:
                raise ValueError(f"{name}.{key} must be boolean")
        height = item.get("max_resolution", 0)
        if type(height) is not int or height < 0 or height > 4320:
            raise ValueError(f"{name}.max_resolution must be an integer from 0 to 4320")
        bitrate = item.get("max_bitrate", 0)
        if type(bitrate) is not int or not 0 <= bitrate <= 1000000000:
            raise ValueError(f"{name}.max_bitrate must be an integer from 0 to 1000000000 bps")
        countries = {country_code(value) for value in item.get("countries", [])}
        if any(not re.fullmatch(r"[a-z]{2}", value) for value in countries):
            raise ValueError(f"{name}.countries must contain two-letter codes")
        validated[name] = {
            **item,
            "countries": sorted(countries),
            "languages": sorted({language_code(value) for value in item.get("languages", [])}),
            "max_resolution": height,
            "max_bitrate": bitrate,
        }
    return favorites, validated


def personalized_outputs(
    settings: Settings,
    candidates: list[Channel],
    final: list[Channel],
    results: dict[str, ProbeResult],
    history: dict,
    previous: list[Channel],
    favorites: set[str],
    profiles: dict,
    epg_urls: tuple[str, ...],
    select,
) -> tuple[dict[str, str], dict]:
    candidates, results, history = expand_variants(candidates, results, history)
    chosen_favorites = [channel for channel in final if channel.identity in favorites]
    outputs = {"favorites.m3u": render_m3u(chosen_favorites, epg_urls)}
    report = {
        "favorites": len(chosen_favorites),
        "missing_favorites": sorted(favorites - {channel.identity for channel in chosen_favorites}),
        "profiles": {},
    }
    for name, options in sorted(profiles.items()):
        maximum = options["max_resolution"]
        max_bitrate = options.get("max_bitrate", 0)
        filtered, unknown, too_high = [], set(), set()
        unknown_bitrate, high_bitrate = set(), set()
        for channel in candidates:
            bitrate = results[channel.probe_key].bitrate
            if max_bitrate and not bitrate and not options.get("include_unknown_bitrate", False):
                unknown_bitrate.add(channel.identity)
                continue
            if max_bitrate and bitrate > max_bitrate:
                high_bitrate.add(channel.identity)
                continue
            height = results[channel.probe_key].resolution or res_of(channel.name)
            if maximum and not height and not options.get("include_unknown_resolution", False):
                unknown.add(channel.identity)
                continue
            if maximum and height > maximum:
                too_high.add(channel.identity)
                continue
            filtered.append(channel)
        profile_settings = replace(
            settings,
            preferred_sources=tuple(options.get("preferred_sources", settings.preferred_sources)),
        )
        selected = select(filtered, results, history, previous, profile_settings).channels
        subset = [
            channel
            for channel in selected
            if (not options.get("favorites_only") or channel.identity in favorites)
            and (
                not options["countries"]
                or country_code(channel.attrs.get("tvg-country", "")) in options["countries"]
            )
            and (not options["languages"] or channel_language(channel) in options["languages"])
        ]
        filename = f"profile-{name}.m3u"
        outputs[filename] = render_m3u(subset, epg_urls)
        validate_output(outputs[filename], len(subset))
        included = {channel.identity for channel in subset}
        report["profiles"][name] = {
            "file": filename,
            "channels": len(subset),
            "options": options,
            "unknown_quality_omitted": len(unknown - included),
            "over_limit_omitted": len(too_high - included),
            "unknown_bitrate_omitted": len(unknown_bitrate - included),
            "over_bitrate_omitted": len(high_bitrate - included),
            "unknown_bitrate_included": sum(
                not results[channel.probe_key].bitrate for channel in subset
            ),
        }
    validate_output(outputs["favorites.m3u"], len(chosen_favorites))
    return outputs, report


def expand_variants(candidates: list[Channel], results: dict, history: dict):
    expanded, observations, records = list(candidates), dict(results), dict(history)
    seen = {(channel.identity, channel.probe_key) for channel in candidates}
    for channel in candidates:
        result = results[channel.probe_key]
        if not result.healthy:
            continue
        for variant in result.variants:
            headers = child_request_headers(channel.headers, channel.request_url, variant.url)
            url = variant.url + ("|" + urlencode(headers) if headers else "")
            # Re-encode supported request headers exactly once and do not
            # forward sensitive headers across origins.
            child = replace(
                channel,
                url=url,
                ext=tuple(
                    line
                    for line in channel.ext
                    if not line.lower().startswith(
                        (
                            "#extvlcopt:http-user-agent=",
                            "#extvlcopt:http-referrer=",
                            "#extvlcopt:http-origin=",
                            "#kodiprop:inputstream.adaptive.stream_headers=",
                            "#kodiprop:inputstream.adaptive.manifest_headers=",
                        )
                    )
                ),
            )
            if variant.resolution:
                name = re.sub(r"\s*\(?\d{3,4}\s*p\)?", "", channel.name, flags=re.I)
                name = re.sub(r"\b(?:FHD|UHD|HD|4K|SD)\b", "", name, flags=re.I)
                name = re.sub(r"\s+", " ", name).strip()
                child = child.renamed(f"{name} ({variant.resolution}p)")
            if (child.identity, child.probe_key) in seen:
                continue
            seen.add((child.identity, child.probe_key))
            expanded.append(child)
            if child.probe_key in observations:
                continue
            observations[child.probe_key] = ProbeResult(
                "healthy",
                "Verified HLS media variant",
                variant.resolution,
                result.cached,
                result.checked_at,
                variant.bitrate,
                variant.bitrate_source,
            )
            records[child.probe_key] = history.get(channel.probe_key, {})
    return expanded, observations, records


def editor_manifest(candidates, results, history, epg_urls, favorites, profiles):
    candidates, results, history = expand_variants(candidates, results, history)
    rows, seen, private = [], set(), set()
    for channel in sorted(candidates, key=lambda item: (item.identity, item.url)):
        result = results[channel.probe_key]
        if (
            result.status not in {"healthy", "unchecked"}
            or (channel.identity, channel.probe_key) in seen
        ):
            continue
        seen.add((channel.identity, channel.probe_key))
        if not exportable_channel(channel):
            private.add(channel.identity)
            continue
        # An allowlist avoids disclosing unrelated custom attributes, DRM
        # license properties or private extension directives in the public UI.
        safe_url = channel.request_url + (
            "|" + urlencode(channel.headers) if channel.headers else ""
        )
        safe = Channel((f"#EXTINF:-1,{channel.name}",), safe_url)
        for key in (
            "tvg-id",
            "x-original-tvg-id",
            "tvg-country",
            "tvg-language",
            "group-title",
            "tvg-logo",
        ):
            value = channel.attrs.get(key, "")
            if value and not (key == "tvg-logo" and sensitive_url(value)):
                safe = safe.with_attribute(key, value)
        quality = reliability(history.get(channel.probe_key, {}))
        rows.append(
            {
                "identity": channel.identity,
                "name": channel.name,
                "source": channel.source,
                "request_url": channel.request_url,
                "country": channel.attrs.get("tvg-country", ""),
                "language": channel_language(channel),
                "group": channel.group,
                "resolution": result.resolution or res_of(channel.name),
                "bitrate": result.bitrate,
                "bitrate_source": result.bitrate_source,
                "status": result.status,
                "stability": quality["score"] // 5 if quality["observed_days"] >= 3 else 10,
                "m3u": "\n".join([*safe.ext, safe.url]) + "\n",
            }
        )
    return {
        "schema_version": 1,
        "channels": rows,
        "epg_urls": list(epg_urls),
        "favorites": sorted(favorites),
        "profiles": profiles,
        "private_identities_omitted": len(private),
    }
