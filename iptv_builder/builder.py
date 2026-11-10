"""Build orchestration: collect -> probe alternatives -> guard -> publish."""

from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from .catalog import ORDER, SPLIT
from .classification import load_excludes, load_overrides, prepare_channels
from .config import Settings, Source
from .health import ProbeResult, check_candidates
from .m3u import Channel, parse, render_m3u, res_of, valid_source, validate_output
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
    for key, result in results.items():
        old = previous.get(key, {})
        if not isinstance(old, dict):
            old = {}
        if result.status == "unchecked":
            if old:
                history[key] = old
            continue
        failures = old.get("consecutive_failures", 0)
        if type(failures) is not int or failures < 0:
            failures = 0
        history[key] = {
            "status": result.status,
            "reason": result.reason,
            "checked_at": now,
            "last_success": now if result.healthy else old.get("last_success"),
            # Attempts within one build count as one failed observation.
            "consecutive_failures": 0 if result.healthy else failures + 1,
        }
    return history


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
        return (-resolution, not channel.manual, channel.source == "__previous__", channel.url)

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
        if metadata.attrs.get("tvg-language"):
            chosen = chosen.with_attribute("tvg-language", metadata.attrs["tvg-language"])
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
        f"Retained unverified: {report['retained_unverified']}",
    ]
    known = [group for group in ORDER if group in report["groups"]]
    extra = sorted(set(report["groups"]) - set(known))
    lines.extend(f"  {group}: {report['groups'][group]}" for group in (*known, *extra))
    lines.append(f"Published: {report['generated_at']}")
    return "\n".join(lines) + "\n"


def _read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8") if path.exists() else ""


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
    old_text = _read_text(output_dir / "playlist.m3u")
    old_channels = parse(old_text)
    old_urls = {channel.url for channel in old_channels}
    sources = fetch_sources(settings, cache_dir)
    runtime["sources"] = [
        dict(url=item.source.url, status=item.status, error=item.error) for item in sources
    ]
    for item in sources:
        print(
            f"Source [{item.status}]: {item.source.url}"
            + (f" ({item.error})" if item.error else "")
        )
    available = sum(item.status != "failed" for item in sources)
    if sources and available / len(sources) < settings.min_available_source_ratio:
        raise BuildError(
            f"Only {available}/{len(sources)} sources available (including valid caches). Previous output preserved."
        )

    channels = []
    for item in sources:
        if item.text:
            channels.extend(prepare_channels(item.text, item.source, settings, overrides, excludes))
    manual_path = input_dir / settings.manual_file
    if manual_path.exists():
        manual = prepare_channels(
            manual_path.read_text(encoding="utf-8-sig"),
            Source(str(manual_path), allow_unknown_country=True),
            settings,
            overrides,
            excludes,
            manual=True,
        )
        print(f"Manual: {len(manual)} candidates")
        channels.extend(manual)

    # Reuse previously published alternatives only for channels still present
    # in the current (or cached) inputs, and reapply excludes/disabled policy.
    current_ids = {channel.identity for channel in channels}
    previous = prepare_channels(
        old_text, Source("__previous__", allow_unknown_country=True), settings, overrides, excludes
    )
    channels.extend(channel for channel in previous if channel.identity in current_ids)
    if not channels:
        raise BuildError("No eligible channel candidates. Previous output preserved.")
    results = check_candidates(channels, settings)
    state = read_json(cache_dir / "link-health.json")
    previous_history = state.get("links", {})
    if not isinstance(previous_history, dict):
        previous_history = {}
    history = update_history(results, previous_history, runtime["generated_at"])
    selection = select_channels(channels, results, history, previous, settings)
    final = selection.channels
    failures = _unavailable(channels, results, history)
    new_urls = {channel.url for channel in final}
    semantic = {
        "schema_version": 1,
        "channels": len(final),
        "groups": dict(Counter(channel.group for channel in final)),
        "checked_links": sum(result.status != "unchecked" for result in results.values()),
        "health": dict(sorted(Counter(result.status for result in results.values()).items())),
        "retained_unverified": selection.retained_unverified,
        "sources": runtime["sources"],
        "unavailable_links": failures,
    }
    report = {
        **semantic,
        "generated_at": runtime["generated_at"],
        "added": len(new_urls - old_urls),
        "removed": len(old_urls - new_urls),
    }
    runtime.update(report)
    guard_output(settings, len(old_channels), len(final))

    outputs = {"playlist.m3u": render_m3u(final, settings.epg_urls)}
    validate_output(outputs["playlist.m3u"], len(final))
    for filename, groups in SPLIT.items():
        subset = [channel for channel in final if channel.group in groups]
        outputs[filename] = render_m3u(subset, settings.epg_urls)
        validate_output(outputs[filename], len(subset))
    outputs["dead_links.txt"] = _dead_text(failures)
    old_report = read_json(output_dir / "build_report.json")
    old_semantic = {
        key: value
        for key, value in old_report.items()
        if key not in {"generated_at", "added", "removed"}
    }
    content_changed = any(_read_text(output_dir / name) != text for name, text in outputs.items())
    if content_changed or old_semantic != semantic or not (output_dir / "build_stats.txt").exists():
        outputs["build_report.json"] = json_text(report)
        outputs["build_stats.txt"] = _stats_text(report)
    # Otherwise keep the last published summary, including its publication
    # time/delta. Current per-run numbers live in ignored last_run.json.
    main_changed = outputs["playlist.m3u"] != old_text
    if settings.save_backup and main_changed and valid_source(old_text):
        outputs["playlist-backup.m3u"] = old_text

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
            cache_dir / "link-health.json", json_text({"schema_version": 1, "links": history})
        )
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
    with build_lock(output_dir):
        try:
            return _build(settings, output_dir, input_dir, cache_dir, dry_run, runtime)
        except Exception as error:
            runtime.update(
                status="blocked" if isinstance(error, BuildError) else "failed", error=str(error)
            )
            raise
        finally:
            atomic_write(cache_dir / "last_run.json", json_text(runtime))
