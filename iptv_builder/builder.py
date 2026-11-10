"""Build orchestration: collect -> probe alternatives -> guard -> publish."""

import re
from collections import Counter, defaultdict
from copy import deepcopy
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import budget
from .alerts import load_watch_ids, watched_failures
from .catalog import ORDER, SPLIT
from .changes import channel_changes, remember_channels
from .classification import load_excludes, load_overrides, prepare_channels
from .config import Settings, Source
from .dashboard import render_dashboard
from .epg import validate_epg
from .health import ProbeResult, check_candidates
from .m3u import Channel, parse, render_m3u, res_of, valid_source, validate_output
from .metadata import load_metadata
from .personalization import (
    channel_language,
    editor_manifest,
    load_personalization,
    personalized_outputs,
    source_rank,
)
from .playback import sample_channels
from .privacy import public_data, public_url
from .reliability import daily_observation, reliability, stability_rank
from .sources import fetch_sources
from .storage import archive_playlist, atomic_write, build_lock, json_text, publish_files, read_json


class BuildError(RuntimeError):
    """An unsafe build was rejected without replacing published outputs."""


@dataclass(frozen=True)
class Selection:
    channels: list[Channel]
    retained_unverified: int


def update_history(results: dict[str, ProbeResult], previous: dict, now: str) -> dict:
    history = {}
    try:
        stamp = datetime.fromisoformat(now)
    except ValueError:
        stamp = None
    for key, old in previous.items():
        if not isinstance(old, dict):
            continue
        try:
            if (
                stamp is not None
                and stamp - timedelta(days=30) <= datetime.fromisoformat(old["checked_at"]) <= stamp
            ):
                history[key] = old
        except (KeyError, TypeError, ValueError):
            pass
    for key, result in results.items():
        old = previous.get(key, {})
        if not isinstance(old, dict):
            old = {}
        if result.status == "unchecked" or result.cached:
            if old:
                history[key] = old
            continue
        failures = old.get("consecutive_failures", 0)
        if type(failures) is not int or failures < 0:
            failures = 0
        successes = old.get("consecutive_successes", 0)
        if type(successes) is not int or successes < 0:
            successes = 0
        checked_at = result.checked_at or now
        history[key] = {
            "status": result.status,
            "reason": result.reason,
            "checked_at": checked_at,
            "last_success": checked_at if result.healthy else old.get("last_success"),
            "resolution": result.resolution,
            "bitrate": result.bitrate,
            "bitrate_source": result.bitrate_source,
            "variants": [asdict(item) for item in result.variants],
            "daily": daily_observation(old, checked_at, result.healthy),
            "consecutive_successes": successes + 1 if result.healthy else 0,
            # Attempts within one build count as one failed observation.
            "consecutive_failures": 0 if result.healthy else failures + 1,
        }
    # Current requests first, then bounded recently absent alternatives.
    return dict(
        sorted(
            history.items(),
            key=lambda item: (item[0] in results, str(item[1].get("checked_at", ""))),
            reverse=True,
        )[:25000]
    )


def select_channels(
    channels: list[Channel],
    results: dict[str, ProbeResult],
    history: dict,
    previous: list[Channel],
    settings: Settings,
) -> Selection:
    groups = defaultdict(list)
    for channel in channels:
        groups[channel.identity].append(channel)
    old_keys = {channel.probe_key for channel in previous}
    selected, seen_urls = [], set()
    retained = 0

    def rank(channel: Channel):
        resolution = results[channel.probe_key].resolution or res_of(channel.name)
        return (
            source_rank(channel, settings.preferred_sources),
            -stability_rank(history.get(channel.probe_key, {})),
            -resolution,
            not channel.manual,
            channel.source == "__previous__",
            channel.url,
        )

    for identity in sorted(groups):
        candidates = sorted(groups[identity], key=rank)
        healthy = [channel for channel in candidates if results[channel.probe_key].healthy]
        unchecked = [
            channel for channel in candidates if results[channel.probe_key].status == "unchecked"
        ]
        eligible = healthy or unchecked
        unverified = False
        if not eligible and not settings.drop_dead:
            eligible = candidates
            unverified = True
        if not eligible:
            eligible = [
                channel
                for channel in candidates
                if channel.probe_key in old_keys
                and history[channel.probe_key]["consecutive_failures"] < settings.failure_threshold
            ]
            unverified = bool(eligible)
        if not eligible:
            continue
        # A URL shared by different IDs is emitted once. Try another healthy
        # alternative before dropping a channel because of such a collision.
        chosen = next((channel for channel in eligible if channel.url not in seen_urls), None)
        if chosen is None:
            continue
        metadata = min(
            candidates,
            key=lambda channel: (
                channel.source == "__previous__",
                -channel.metadata_priority,
                not channel.manual,
                channel.url,
            ),
        )
        chosen = chosen.with_group(metadata.group)
        for key in ("tvg-language", "tvg-country", "tvg-logo"):
            if metadata.attrs.get(key):
                chosen = chosen.with_attribute(key, metadata.attrs[key])
        selected.append(chosen)
        seen_urls.add(chosen.url)
        retained += int(unverified)
    order = {group: index for index, group in enumerate(ORDER)}
    selected.sort(
        key=lambda channel: (
            order.get(channel.group, len(order)),
            channel.name.casefold(),
            channel.url,
        )
    )
    return Selection(selected, retained)


def guard_output(settings: Settings, old_count: int, new_count: int) -> None:
    if new_count < settings.min_channels:
        raise BuildError(
            f"Only {new_count} channels; minimum is {settings.min_channels}. Previous output preserved."
        )
    if old_count and (old_count - new_count) / old_count > settings.max_drop_ratio:
        raise BuildError(
            f"Channel count fell from {old_count} to {new_count}, exceeding the "
            f"{settings.max_drop_ratio:.0%} drop limit. Previous output preserved. "
            "Review the report/configuration before using --allow-large-drop."
        )


def _unavailable(
    channels: list[Channel], results: dict[str, ProbeResult], history: dict
) -> list[dict]:
    distinct = {}
    for channel in sorted(channels, key=lambda item: (item.name.casefold(), item.url)):
        distinct.setdefault(channel.probe_key, channel)
    return [
        {
            "name": channel.name,
            "url": channel.url,
            "status": results[key].status,
            "reason": results[key].reason,
            "last_success": history.get(key, {}).get("last_success"),
        }
        for key, channel in distinct.items()
        if results[key].status not in {"healthy", "unchecked"}
    ]


def _dead_text(failures: list[dict]) -> str:
    lines = ["# Unavailable links (not necessarily dead)", f"# count: {len(failures)}", ""]
    for item in failures:
        lines.extend(
            (
                item["name"],
                item["url"],
                f"{item['status']}: {item['reason']}",
                f"last_success: {item['last_success'] or 'unknown'}",
                "",
            )
        )
    return "\n".join(lines) + "\n"


def _stats_text(report: dict) -> str:
    lines = [
        f"OK: {report['channels']} channels",
        f"Added: {report['added']} | Removed: {report['removed']} | Unavailable links: {len(report['unavailable_links'])}",
        f"URL changes: {report.get('changed_urls', 0)} | Quality up/down: {report.get('upgraded', 0)}/{report.get('downgraded', 0)} | Recovered: {report.get('recovered', 0)}",
        f"Checked now: {report['checked_links']} | Cached successes: {report.get('cached_links', 0)}",
        f"Retained unverified: {report['retained_unverified']}",
    ]
    known = [group for group in ORDER if group in report["groups"]]
    extra = sorted(set(report["groups"]) - set(known))
    lines.extend(f"  {group}: {report['groups'][group]}" for group in (*known, *extra))
    lines.append(f"Published: {report['generated_at']}")
    return "\n".join(lines) + "\n"


def _read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8") if path.exists() else ""


def _catalog(channels, results, epg_status, now, history=None):
    return [
        {
            "identity": channel.identity,
            "name": channel.name,
            "url": channel.url,
            "source": channel.source,
            "group": channel.group,
            "country": channel.attrs.get("tvg-country", ""),
            "language": channel_language(channel),
            "logo": channel.attrs.get("tvg-logo", ""),
            "resolution": results[channel.probe_key].resolution or res_of(channel.name),
            "bitrate": results[channel.probe_key].bitrate,
            "bitrate_source": results[channel.probe_key].bitrate_source,
            "reliability": reliability((history or {}).get(channel.probe_key, {}), now),
            "status": results[channel.probe_key].status,
            "reason": results[channel.probe_key].reason,
            "cached": results[channel.probe_key].cached,
            "checked_at": (results[channel.probe_key].checked_at or now)
            if results[channel.probe_key].status != "unchecked"
            else None,
            "epg": epg_status.get(channel.identity, "unchecked"),
        }
        for channel in channels
    ]


def _stable_report(report: dict) -> dict:
    # Publication timestamps, per-run observation times and deltas must not
    # force daily Git commits when the underlying result is identical.
    excluded = {
        "generated_at",
        "added",
        "removed",
        "changed_urls",
        "upgraded",
        "downgraded",
        "recovered",
        "returned_unverified",
        "changes",
        "previous_channels",
        "previous_unique_identities",
        "watched_failures",
        "fresh_healthy_watched",
        "budget",
        "checked_links",
        "cached_links",
    }
    stable = deepcopy({key: value for key, value in report.items() if key not in excluded})
    for resources in (
        stable.get("sources", []),
        stable.get("metadata", {}).get("sources", []),
        stable.get("epg", {}).get("guides", []),
    ):
        for resource in resources:
            if resource.get("status") in {"fresh", "cached"}:
                resource["status"] = "available"
    for item in stable.get("unavailable_links", []):
        item.pop("last_success", None)
    stable["channel_catalog"] = [
        {
            key: (
                {k: v for k, v in value.items() if k != "observations"}
                if key == "reliability"
                else value
            )
            for key, value in item.items()
            if key not in {"checked_at", "cached"}
        }
        for item in report.get("channel_catalog", [])
    ]
    if isinstance(report.get("playback"), dict):
        stable["playback"] = {**report["playback"]}
        if "samples" in report["playback"]:
            stable["playback"]["samples"] = [
                {key: value for key, value in item.items() if key != "checked_at"}
                for item in report["playback"]["samples"]
            ]
    return stable


def _build(
    settings: Settings,
    output_dir: Path,
    input_dir: Path,
    cache_dir: Path,
    dry_run: bool,
    runtime: dict,
) -> dict:
    overrides = load_overrides(input_dir / settings.overrides_file)
    excludes = load_excludes(input_dir / settings.exclude_file)
    favorites, profiles = load_personalization(settings, input_dir)
    watched = favorites | load_watch_ids(input_dir / settings.personalization.important_file)
    old_text = _read_text(output_dir / "playlist.m3u")
    old_channels = parse(old_text)
    old_report = read_json(output_dir / "build_report.json")
    state = read_json(cache_dir / "link-health.json")
    previous_history = state.get("links", {})
    previous_channels = state.get("channels", {})
    if not isinstance(previous_history, dict):
        previous_history = {}
    if not isinstance(previous_channels, dict):
        previous_channels = {}
    # Seed identities from legacy published M3Us as well as the new cache.
    # Otherwise a channel removed on the very first v2 build would later be
    # mislabeled new when it returned, despite having been published before.
    for channel in old_channels:
        prior_link = previous_history.get(channel.probe_key, {})
        old_status = (
            prior_link.get("status", "unchecked") if isinstance(prior_link, dict) else "unchecked"
        )
        previous_channels[channel.identity] = {
            "last_seen": runtime["generated_at"],
            "status": old_status,
            "probe_key": channel.probe_key,
        }
    sources = fetch_sources(settings, cache_dir)
    runtime["sources"] = [
        dict(url=item.source.url, status=item.status, error=item.error) for item in sources
    ]
    for item in sources:
        print(
            f"Source [{item.status}]: {public_url(item.source.url)}"
            + (f" ({public_data(item.error)})" if item.error else "")
        )
    available = sum(item.status != "failed" for item in sources)
    if sources and available / len(sources) < settings.min_available_source_ratio:
        raise BuildError(
            f"Only {available}/{len(sources)} sources available (including valid caches). Previous output preserved."
        )

    reference = load_metadata(settings, cache_dir, input_dir)
    channels = []
    for item in sources:
        if item.text:
            channels.extend(
                prepare_channels(
                    item.text, item.source, settings, overrides, excludes, reference=reference
                )
            )
    manual_path = input_dir / settings.manual_file
    if manual_path.exists():
        manual = prepare_channels(
            manual_path.read_text(encoding="utf-8-sig"),
            Source(str(manual_path), allow_unknown_country=True),
            settings,
            overrides,
            excludes,
            manual=True,
            reference=reference,
        )
        print(f"Manual: {len(manual)} candidates")
        channels.extend(manual)
    metadata_report = reference.summary(channels)
    runtime["metadata"] = metadata_report
    current_ids = {channel.identity for channel in channels}
    previous = prepare_channels(
        old_text,
        Source("__previous__", allow_unknown_country=True),
        settings,
        overrides,
        excludes,
        reference=reference,
    )
    channels.extend(channel for channel in previous if channel.identity in current_ids)
    if not channels:
        raise BuildError("No eligible channel candidates. Previous output preserved.")
    channels, epg_urls, epg_report, epg_status = validate_epg(
        settings, channels, cache_dir, input_dir
    )
    runtime["epg"] = epg_report
    results = check_candidates(channels, settings, previous_history)
    history = update_history(results, previous_history, runtime["generated_at"])
    selection = select_channels(channels, results, history, previous, settings)
    final = selection.channels
    failures = _unavailable(channels, results, history)
    changes = channel_changes(old_channels, final, results, previous_history, previous_channels)
    if epg_report["enabled"]:
        selected_ids = {channel.identity for channel in final}
        epg_report["missing_channels"] = list(
            {
                item["identity"]: item
                for item in epg_report["missing_channels"]
                if item["identity"] in selected_ids
            }.values()
        )
        epg_report["missing"] = len(epg_report["missing_channels"])
        epg_report["covered"] = sum(
            epg_status.get(channel.identity) == "covered" for channel in final
        )
        epg_report["rewritten_ids"] = sum(
            bool(channel.attrs.get("x-original-tvg-id")) for channel in final
        )
    report = {
        "schema_version": 2,
        "generated_at": runtime["generated_at"],
        "channels": len(final),
        "previous_channels": len(old_channels),
        "previous_unique_identities": len({channel.identity for channel in old_channels}),
        "watched_failures": watched_failures(
            channels,
            results,
            history,
            watched,
            None,
            max(2, settings.failure_threshold),
        ),
        "fresh_healthy_watched": sorted(
            {
                channel.identity
                for channel in channels
                if channel.identity in watched
                and results[channel.probe_key].healthy
                and not results[channel.probe_key].cached
            }
        ),
        "groups": dict(Counter(channel.group for channel in final)),
        "checked_links": sum(
            result.status != "unchecked" and not result.cached for result in results.values()
        ),
        "cached_links": sum(result.cached for result in results.values()),
        "health": dict(sorted(Counter(result.status for result in results.values()).items())),
        "retained_unverified": selection.retained_unverified,
        "sources": runtime["sources"],
        "unavailable_links": failures,
        "changes": changes,
        "added": len(changes["added_channels"]),
        "removed": len(changes["removed_channels"]),
        "changed_urls": len(changes["url_changes"]),
        "recovered": len(changes["recovered_channels"]),
        "returned_unverified": len(changes["returned_unverified_channels"]),
        "upgraded": sum(item["direction"] == "up" for item in changes["quality_changes"]),
        "downgraded": sum(item["direction"] == "down" for item in changes["quality_changes"]),
        "metadata": metadata_report,
        "epg": epg_report,
        "channel_catalog": _catalog(final, results, epg_status, runtime["generated_at"], history),
    }
    report = public_data(report)
    runtime.update(report)
    guard_output(settings, len(old_channels), len(final))
    playback_state = read_json(cache_dir / "playback.json")
    playback, next_playback_state = sample_channels(final, settings, playback_state)
    report["playback"] = playback
    personal_outputs, personal_report = personalized_outputs(
        settings,
        channels,
        final,
        results,
        history,
        previous,
        favorites,
        profiles,
        epg_urls,
        select_channels,
    )
    old_personalization = old_report.get("personalization", {})
    old_managed = (
        old_personalization.get("managed_files", [])
        if isinstance(old_personalization, dict)
        else []
    )
    if not isinstance(old_managed, list):
        old_managed = []
    managed = {
        name
        for name in old_managed
        if isinstance(name, str) and re.fullmatch(r"profile-[a-z0-9][a-z0-9-]{0,39}\.m3u", name)
    }
    managed.update(name for name in personal_outputs if name.startswith("profile-"))
    personal_report["managed_files"] = sorted(managed)
    for name in managed:
        personal_outputs.setdefault(name, render_m3u([], epg_urls))
    report["personalization"] = personal_report
    report["editor"] = editor_manifest(channels, results, history, epg_urls, favorites, profiles)
    report = public_data(report)
    runtime.update(report)
    outputs = {"playlist.m3u": render_m3u(final, epg_urls), **personal_outputs}
    validate_output(outputs["playlist.m3u"], len(final))
    for filename, groups in SPLIT.items():
        subset = [channel for channel in final if channel.group in groups]
        outputs[filename] = render_m3u(subset, epg_urls)
        validate_output(outputs[filename], len(subset))
    outputs["dead_links.txt"] = _dead_text(report["unavailable_links"])
    content_changed = any(_read_text(output_dir / name) != text for name, text in outputs.items())
    if (
        content_changed
        or _stable_report(old_report) != _stable_report(report)
        or not (output_dir / "build_stats.txt").exists()
        or settings.dashboard
        and not (output_dir / "index.html").exists()
    ):
        outputs["build_report.json"] = json_text(report)
        outputs["build_stats.txt"] = _stats_text(report)
        if settings.dashboard:
            outputs["index.html"] = render_dashboard(report)
    main_changed = outputs["playlist.m3u"] != old_text
    if settings.save_backup and main_changed and valid_source(old_text):
        outputs["playlist-backup.m3u"] = old_text
    budget.check()
    if dry_run:
        runtime["status"] = "dry_run"
        runtime["changed_files"] = [
            name for name, text in outputs.items() if _read_text(output_dir / name) != text
        ]
    else:
        if settings.save_backup and main_changed and valid_source(old_text):
            archive_playlist(cache_dir / "backups", old_text, settings.backup_keep)
        changed = publish_files(output_dir, outputs)
        runtime["changed_files"] = changed
        runtime["status"] = "published" if changed else "unchanged"
        atomic_write(
            cache_dir / "link-health.json",
            json_text(
                {
                    "schema_version": 3,
                    "links": history,
                    "channels": remember_channels(
                        previous_channels, final, results, runtime["generated_at"]
                    ),
                }
            ),
        )
        if settings.playback.enabled:
            atomic_write(cache_dir / "playback.json", json_text(next_playback_state))
    print(_stats_text(report), end="")
    print(f"Build status: {runtime['status']}")
    return runtime


def run_build(
    settings: Settings, output_dir: Path, input_dir: Path | None = None, dry_run: bool = False
) -> dict:
    output_dir = output_dir.resolve()
    input_dir = (input_dir or output_dir).resolve()
    cache_dir = output_dir / settings.cache_dir
    runtime = {"generated_at": datetime.now(timezone.utc).isoformat(), "status": "running"}
    with build_lock(output_dir), budget.build_budget(settings.budget) as meter:
        try:
            return _build(settings, output_dir, input_dir, cache_dir, dry_run, runtime)
        except Exception as error:
            runtime.update(
                status="blocked"
                if isinstance(error, (BuildError, budget.BudgetExceeded))
                else "failed",
                error=str(error),
            )
            if isinstance(error, budget.BudgetExceeded):
                raise BuildError(str(error)) from error
            raise
        finally:
            runtime["budget"] = meter.summary()
            atomic_write(cache_dir / "last_run.json", json_text(public_data(runtime)))
