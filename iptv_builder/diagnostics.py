"""Opt-in bounded public-network validation in an isolated directory, never main."""

import argparse
import hashlib
import shutil
from dataclasses import replace
from pathlib import Path

from .builder import BuildError, run_build
from .classification import load_excludes, load_overrides, prepare_channels
from .config import MetadataOptions, load_settings
from .m3u import render_m3u
from .metadata import load_metadata
from .sources import fetch_sources
from .storage import atomic_write, json_text


def _hashes(root: Path) -> dict:
    return {
        path.name: hashlib.sha256(path.read_bytes()).hexdigest()
        for path in root.iterdir()
        if path.is_file()
    }


def _sample(candidates, limit):
    # Spread countries and hosts, rather than testing 12 variants of one URL.
    ordered = sorted(
        candidates, key=lambda channel: hashlib.sha256(channel.identity.encode()).hexdigest()
    )
    selected, identities, hosts = [], set(), set()
    for country in ("ir", "us", "uk", "de", ""):
        for channel in ordered:
            if len(selected) == limit:
                return selected
            if channel.identity in identities or channel.request_url.split("/", 3)[2] in hosts:
                continue
            if country and channel.attrs.get("tvg-country") != country:
                continue
            selected.append(channel)
            identities.add(channel.identity)
            hosts.add(channel.request_url.split("/", 3)[2])
            if country:
                break
    # A small source may host every channel on one CDN; fill the remaining
    # budget with distinct identities rather than testing only one bad URL.
    for channel in ordered:
        if len(selected) == limit:
            break
        if channel.identity not in identities:
            selected.append(channel)
            identities.add(channel.identity)
    return selected


def validate(
    config_path: Path, directory: Path, limit: int = 12, playback_samples: int = 2
) -> dict:
    input_root = config_path.resolve().parent
    directory = directory.resolve()
    # A repeated diagnostic run must not overwrite any real build/input tree.
    if (
        directory == input_root
        or directory in input_root.parents
        or (directory / "manual.m3u").exists()
        or (directory / ".git").exists()
    ):
        raise ValueError(
            "Diagnostics need a separate scratch directory, not the repository/output root"
        )
    directory.mkdir(parents=True, exist_ok=True)
    output, inputs = directory / "output", directory / "inputs"
    output.mkdir(exist_ok=True)
    inputs.mkdir(exist_ok=True)
    if (output / "playlist.m3u").exists():
        raise ValueError("Use a new diagnostic directory for each validation")
    summary = {"production_modified": False, "sample_limit": limit, "status": "running"}
    try:
        settings = load_settings(config_path)
        settings = replace(
            settings,
            source_attempts=2,
            source_timeout=min(settings.source_timeout, 20),
            max_workers=min(settings.max_workers, 6),
            link_attempts=1,
            cache_dir=".cache/iptv",
        )
        cache = output / settings.cache_dir
        sources = fetch_sources(settings, cache)
        summary["sources"] = [
            {"url": item.source.url, "status": item.status, "error": item.error} for item in sources
        ]
        available = sum(item.status != "failed" for item in sources)
        summary["available_sources"] = available
        summary["source_count"] = len(sources)
        summary["source_cache_files"] = len(list((cache / "sources").glob("*.json")))
        if sources and available / len(sources) < settings.min_available_source_ratio:
            raise BuildError("Live source availability guard rejected the diagnostic build")
        overrides = load_overrides(input_root / settings.overrides_file)
        excludes = load_excludes(input_root / settings.exclude_file)
        reference = load_metadata(settings, cache, input_root)
        candidates = []
        for source in sources:
            if source.text:
                candidates.extend(
                    prepare_channels(
                        source.text,
                        source.source,
                        settings,
                        overrides,
                        excludes,
                        reference=reference,
                    )
                )
        selected = _sample(candidates, limit)
        if not selected:
            raise BuildError("No eligible public-source diagnostic candidates")
        summary["eligible_source_identities"] = len({channel.identity for channel in candidates})
        summary["sample"] = [
            {"identity": channel.identity, "name": channel.name} for channel in selected
        ]
        atomic_write(inputs / "manual.m3u", render_m3u(selected))
        for original, name in (
            (settings.overrides_file, "overrides.json"),
            (settings.exclude_file, "exclude.txt"),
            (settings.epg.aliases_file, "epg_aliases.json"),
            (settings.personalization.favorites_file, "favorites.json"),
            (settings.personalization.profiles_file, "profiles.json"),
        ):
            path = input_root / original
            if path.exists():
                shutil.copyfile(path, inputs / name)
        if settings.metadata.file:
            shutil.copyfile(input_root / settings.metadata.file, inputs / "reference.json")
        sampled_settings = replace(
            settings,
            stream_template="",
            extra_sources=(),
            manual_file="manual.m3u",
            overrides_file="overrides.json",
            exclude_file="exclude.txt",
            metadata=replace(
                settings.metadata, file="reference.json" if settings.metadata.file else ""
            ),
            epg=replace(settings.epg, aliases_file="epg_aliases.json"),
            personalization=replace(
                settings.personalization,
                favorites_file="favorites.json",
                profiles_file="profiles.json",
            ),
            playback=replace(
                settings.playback,
                enabled=bool(playback_samples),
                max_channels=max(1, playback_samples),
            ),
        )
        first = run_build(sampled_settings, output, inputs)
        atomic_write(directory / "first_run.json", json_text(first))
        second = run_build(sampled_settings, output, inputs)
        atomic_write(directory / "second_run.json", json_text(second))
        baseline = _hashes(output)
        history = (cache / "link-health.json").read_bytes()
        playback_path = cache / "playback.json"
        playback_history = playback_path.read_bytes() if playback_path.exists() else None
        dry = run_build(
            replace(sampled_settings, health_cache=replace(settings.health_cache, hours=0)),
            output,
            inputs,
            dry_run=True,
        )
        atomic_write(directory / "dry_run.json", json_text(dry))
        if (
            _hashes(output) != baseline
            or (cache / "link-health.json").read_bytes() != history
            or (playback_path.read_bytes() if playback_path.exists() else None) != playback_history
        ):
            raise RuntimeError("Dry-run unexpectedly changed isolated outputs/history")
        blocked = False
        try:
            run_build(
                replace(
                    sampled_settings,
                    min_channels=limit + 1,
                    check_links=False,
                    metadata=MetadataOptions(),
                    epg=replace(settings.epg, enabled=False),
                ),
                output,
                inputs,
            )
        except BuildError:
            blocked = True
        if (
            not blocked
            or _hashes(output) != baseline
            or (cache / "link-health.json").read_bytes() != history
        ):
            raise RuntimeError(
                "Expected minimum-count guard did not preserve isolated outputs/history"
            )
        summary.update(
            status="passed",
            channels=first["channels"],
            checked_links=first["checked_links"],
            second_run_cached_links=second["cached_links"],
            metadata=first["metadata"],
            epg=first["epg"],
            playback=first["playback"],
            dry_run_preserved_outputs_and_history=True,
            synthetic_minimum_guard_preserved_outputs=True,
            note="A bounded sample, not an all-channel playback guarantee. Guard failure is deliberately simulated. No commit, push or Telegram.",
        )
        return summary
    except Exception as error:
        summary.update(status="failed", error=str(error))
        raise
    finally:
        atomic_write(directory / "validation_summary.json", json_text(summary))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=Path("config.json"))
    parser.add_argument("--output-dir", type=Path, default=Path(".cache/live-validation"))
    parser.add_argument("--limit", type=int, default=12)
    parser.add_argument("--playback-samples", type=int, default=2)
    args = parser.parse_args(argv)
    if not 1 <= args.limit <= 50 or not 0 <= args.playback_samples <= 5:
        parser.error("limit must be 1..50; playback-samples must be 0..5")
    try:
        result = validate(args.config, args.output_dir, args.limit, args.playback_samples)
        print(
            json_text(
                {
                    key: result[key]
                    for key in (
                        "status",
                        "source_count",
                        "available_sources",
                        "channels",
                        "second_run_cached_links",
                    )
                }
            )
        )
    except (OSError, ValueError, RuntimeError) as error:
        print(f"Live diagnostic failed: {error}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
