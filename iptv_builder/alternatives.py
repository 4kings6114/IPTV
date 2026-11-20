"""An opt-in M3U with adjacent primary/alternate entries for manual switching."""

from collections import defaultdict

from .config import Settings
from .health import ProbeResult
from .m3u import Channel

ALTERNATIVES_FILE = "playlist-alternatives.m3u"


def manual_alternatives(
    channels: list[Channel],
    primary: list[Channel],
    results: dict[str, ProbeResult],
    history: dict,
    settings: Settings,
    select_channels,
) -> tuple[list[Channel], dict]:
    """Keep canonical selection untouched; add at most one healthy, distinct URL.

    Reuse the canonical ranker (source preference, stability, quality), but never
    use unchecked, failed or grace-retained requests as new alternates. URL pipe
    headers do not make a second endpoint. Primary endpoints are reserved before
    selecting alternates so one network cannot steal another's only entry.
    """
    primary_urls = {item.request_url for item in primary}
    candidates = defaultdict(list)
    for item in channels:
        if item.request_url not in primary_urls and results[item.probe_key].healthy:
            candidates[item.identity].append(item)
    entries, used = [], set(primary_urls)
    count = 0
    for item in primary:
        entries.append(item)
        if not results[item.probe_key].healthy:
            continue
        available = [
            candidate
            for candidate in candidates[item.identity]
            if candidate.request_url not in used
        ]
        selection = select_channels(available, results, history, [], settings)
        if not selection.channels:
            continue
        alternate = selection.channels[0]
        alternate = alternate.with_group(item.group)
        for key in ("tvg-language", "tvg-country", "tvg-logo"):
            if item.attrs.get(key):
                alternate = alternate.with_attribute(key, item.attrs[key])
        name = f"{alternate.name} [جایگزین]"
        alternate = alternate.renamed(name)
        # Some players display tvg-name instead of EXTINF's display name.
        # Quoted M3U attributes cannot contain literal double quotes.
        alternate = alternate.with_attribute("tvg-name", name.replace('"', "'"))
        entries.append(alternate)
        used.add(alternate.request_url)
        count += 1
    return entries, {
        "file": ALTERNATIVES_FILE,
        "channels_with_alternative": count,
        "entries": len(entries),
        "max_links_per_channel": 2,
        "switching": "manual",
    }
