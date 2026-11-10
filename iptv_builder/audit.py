"""Full, isolated dry-run audit: retain production guards, never publish or notify."""

import argparse
import hashlib
import json
import os
import shutil
from collections import Counter
from dataclasses import replace
from pathlib import Path

from .builder import BuildError, run_build
from .config import load_settings
from .diagnostics import _actions_notices
from .resources import resource_path
from .storage import atomic_write, json_text, read_json


def full_audit(config: Path, directory: Path) -> dict:
    inputs, directory = config.resolve().parent, directory.resolve()
    if directory == inputs or directory in inputs.parents or (directory / ".git").exists():
        raise ValueError("Audit output must be a separate scratch directory")
    output = directory / "output"
    if output.exists():
        raise ValueError("Use a new audit directory for each run")
    output.mkdir(parents=True)
    tracked = [path for path in inputs.glob("*.m3u")]
    tracked += [
        inputs / name
        for name in (
            "config.json",
            "manual.m3u",
            "exclude.txt",
            "overrides.json",
            "favorites.json",
            "profiles.json",
            "epg_aliases.json",
        )
    ]
    before = {
        path: hashlib.sha256(path.read_bytes()).hexdigest() for path in tracked if path.is_file()
    }
    baseline = inputs / "playlist.m3u"
    if baseline.is_file():
        shutil.copyfile(baseline, output / "playlist.m3u")
    old = (output / "playlist.m3u").read_bytes() if (output / "playlist.m3u").exists() else None
    settings = load_settings(config)
    settings = replace(
        settings,
        cache_dir=".cache/iptv",
        health_cache=replace(settings.health_cache, hours=0),
        playback=replace(settings.playback, enabled=False),
    )
    error = ""
    try:
        run_build(settings, output, inputs, dry_run=True)
    except BuildError as blocked:
        error = str(blocked)
    runtime = read_json(output / ".cache/iptv/last_run.json")
    preserved = all(
        path.exists() and hashlib.sha256(path.read_bytes()).hexdigest() == digest
        for path, digest in before.items()
    )
    preserved = (
        preserved
        and ((output / "playlist.m3u").read_bytes() if (output / "playlist.m3u").exists() else None)
        == old
    )
    no_history = (
        not (output / ".cache/iptv/link-health.json").exists()
        and not (output / ".cache/iptv/playback.json").exists()
    )
    guide_evidence = []
    for url in settings.epg_urls:
        value = read_json(resource_path(output / settings.cache_dir, "xmltv-v3", url)).get(
            "value", {}
        )
        for identifier, item in value.get("channels", {}).items():
            if any(
                name.casefold()
                in {
                    "cnn",
                    "cnn international",
                    "bbc world news",
                    "bbc news",
                    "das erste",
                    "zdf",
                    "dw english",
                    "iran international",
                    "iranintl",
                }
                for name in item.get("names", [])
            ):
                guide_evidence.append(
                    {"url": url, "id": identifier, "names": item.get("names", [])}
                )
    failures = runtime.get("unavailable_links", [])
    reasons = Counter((item.get("status", "unknown"), item.get("reason", "")) for item in failures)
    summary = {
        "status": runtime.get("status", "failed"),
        "analysis_completed": "channels" in runtime,
        "publication_allowed": runtime.get("status") == "dry_run",
        "production_modified": not preserved,
        "history_preserved": no_history,
        "generated_at": runtime.get("generated_at"),
        "channels": runtime.get("channels", 0),
        "checked_links": runtime.get("checked_links", 0),
        "cached_links": runtime.get("cached_links", 0),
        "health": runtime.get("health", {}),
        "retained_unverified": runtime.get("retained_unverified", 0),
        "added": runtime.get("added", 0),
        "removed": runtime.get("removed", 0),
        "changed_urls": runtime.get("changed_urls", 0),
        "failure_reasons": [
            {"status": key[0], "reason": key[1], "count": value}
            for key, value in reasons.most_common(12)
        ],
        "source_count": len(runtime.get("sources", [])),
        "available_sources": sum(
            item.get("status") != "failed" for item in runtime.get("sources", [])
        ),
        "sources": runtime.get("sources", []),
        "metadata": runtime.get("metadata", {}),
        "epg": runtime.get("epg", {}),
        "guard_error": error,
        "note": "Full candidate-link audit, not all-channel decoding. A blocked result is NOT publication approval. No commit, push, Pages deployment or Telegram.",
    }
    summary["guide_evidence"] = guide_evidence[:20]
    atomic_write(directory / "audit_summary.json", json_text(summary))
    if not preserved or not no_history:
        raise RuntimeError("Audit changed a protected file or health/playback history")
    if runtime.get("status") not in {"dry_run", "blocked"}:
        raise RuntimeError("Full audit did not complete; inspect the diagnostics")
    return summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=Path("config.json"))
    parser.add_argument("--output-dir", type=Path, default=Path(".cache/full-audit"))
    args = parser.parse_args(argv)
    try:
        result = full_audit(args.config, args.output_dir)
        if os.environ.get("GITHUB_ACTIONS") == "true":
            _actions_notices(
                {key: value for key, value in result.items() if key != "guide_evidence"}
            )
            for index, item in enumerate(result["guide_evidence"]):
                payload = (
                    json.dumps(item, ensure_ascii=False)
                    .replace("%", "%25")
                    .replace("\r", "%0D")
                    .replace("\n", "%0A")
                )
                print(f"::notice title=EPG identity evidence {index}::{payload}")
        print(
            json.dumps(
                {
                    key: result[key]
                    for key in (
                        "status",
                        "analysis_completed",
                        "publication_allowed",
                        "channels",
                        "checked_links",
                        "health",
                        "guard_error",
                    )
                },
                ensure_ascii=False,
            )
        )
        return 0
    except (OSError, ValueError, RuntimeError) as error:
        print(f"Full audit failed ({type(error).__name__}): {error}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
