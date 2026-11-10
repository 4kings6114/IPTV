"""Channel-identity deltas; URL changes are not channel additions/removals."""

from datetime import datetime, timedelta

from .health import ProbeResult
from .m3u import Channel, res_of


def _height(channel: Channel, links: dict) -> int:
    item = links.get(channel.probe_key, {})
    value = item.get("resolution", 0) if isinstance(item, dict) else 0
    return value if type(value) is int and value > 0 else res_of(channel.name)


def channel_changes(
    previous: list[Channel],
    current: list[Channel],
    results: dict[str, ProbeResult],
    previous_links: dict,
    remembered: dict,
) -> dict:
    old = {channel.identity: channel for channel in previous}
    new = {channel.identity: channel for channel in current}
    added, removed, urls, quality, recovered, unverified_returns = [], [], [], [], [], []
    for identity in sorted(old.keys() - new.keys()):
        removed.append({"identity": identity, "name": old[identity].name, "url": old[identity].url})
    for identity, channel in sorted(new.items()):
        result = results[channel.probe_key]
        prior = remembered.get(identity, {})
        if not isinstance(prior, dict):
            prior = {}
        previous_result = previous_links.get(old[identity].probe_key, {}) if identity in old else {}
        if not isinstance(previous_result, dict):
            previous_result = {}
        was_failed = previous_result.get("status") not in {
            None,
            "healthy",
            "unchecked",
        } or prior.get("status") not in {None, "healthy", "unchecked"}
        returning = identity not in old and bool(prior)
        recovery = result.healthy and not result.cached and (was_failed or returning)
        if recovery:
            recovered.append(
                {
                    "identity": identity,
                    "name": channel.name,
                    "url": channel.url,
                    "kind": "returned" if returning else "health_restored",
                }
            )
        if identity not in old:
            if not returning:
                added.append({"identity": identity, "name": channel.name, "url": channel.url})
            elif not recovery:
                # A returning unchecked/unverified channel is not claimed recovered.
                unverified_returns.append(
                    {"identity": identity, "name": channel.name, "url": channel.url}
                )
            continue
        before = old[identity]
        if before.url != channel.url or before.probe_key != channel.probe_key:
            urls.append(
                {
                    "identity": identity,
                    "name": channel.name,
                    "before": before.url,
                    "after": channel.url,
                    "headers_changed": before.headers != channel.headers,
                }
            )
        before_height = _height(before, previous_links)
        after_height = result.resolution or res_of(channel.name)
        if before_height and after_height and before_height != after_height:
            quality.append(
                {
                    "identity": identity,
                    "name": channel.name,
                    "before": before_height,
                    "after": after_height,
                    "direction": "up" if after_height > before_height else "down",
                }
            )
    return {
        "added_channels": added,
        "removed_channels": removed,
        "url_changes": urls,
        "quality_changes": quality,
        "recovered_channels": recovered,
        "returned_unverified_channels": unverified_returns,
    }


def remember_channels(
    previous: dict, channels: list[Channel], results: dict[str, ProbeResult], now_text: str
) -> dict:
    """Keep bounded 90-day tombstones to recognize returning channels."""
    now = datetime.fromisoformat(now_text)
    cutoff = now - timedelta(days=90)
    remembered = {}
    for key, value in previous.items():
        if not isinstance(value, dict):
            continue
        try:
            if cutoff <= datetime.fromisoformat(value["last_seen"]) <= now:
                remembered[key] = value
        except (KeyError, TypeError, ValueError):
            pass
    for channel in channels:
        result = results[channel.probe_key]
        remembered[channel.identity] = {
            "last_seen": now_text,
            "status": result.status,
            "probe_key": channel.probe_key,
        }
    return dict(
        sorted(remembered.items(), key=lambda item: (item[1]["last_seen"], item[0]), reverse=True)[
            :25000
        ]
    )
