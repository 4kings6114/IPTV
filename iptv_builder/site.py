"""Stage an allowlisted Pages snapshot without modifying production files."""

import argparse
import re
from pathlib import Path

from .dashboard import render_dashboard
from .m3u import attributes, parse, render_m3u, validate_output
from .privacy import exportable_channel, public_data, sensitive_url
from .storage import atomic_write, json_text, read_json

BASE_PLAYLISTS = (
    "playlist.m3u",
    "persian.m3u",
    "news.m3u",
    "music.m3u",
    "sports.m3u",
    "movies.m3u",
    "kids.m3u",
    "favorites.m3u",
)


def stage_site(root: Path, runtime_path: Path, destination: Path, preview: bool = False) -> dict:
    root, destination = root.resolve(), destination.resolve()
    if destination == root or destination in root.parents or (destination / ".git").exists():
        raise ValueError("Pages staging must be separate from the source/repository root")
    if destination.exists() and any(destination.iterdir()):
        raise ValueError("Use an empty Pages staging directory")
    snapshot, runtime = read_json(root / "build_report.json"), read_json(runtime_path)
    if not snapshot or runtime.get("status") not in {"published", "unchanged"}:
        raise ValueError(
            "Pages needs a successful build; dry-run, blocked and failed reports cannot deploy"
        )
    original = parse((root / "playlist.m3u").read_text(encoding="utf-8"))
    if len(original) != snapshot.get("channels") or runtime.get("channels") != snapshot.get(
        "channels"
    ):
        raise ValueError("Pages snapshot and successful runtime/playlist counts disagree")
    destination.mkdir(parents=True, exist_ok=True)
    report = public_data(dict(snapshot))
    # Update telemetry every successful build, even when the meaningful static
    # snapshot and its original publication timestamp intentionally did not churn.
    for key in (
        "channel_catalog",
        "checked_links",
        "cached_links",
        "health",
        "metadata",
        "epg",
        "playback",
        "changes",
        "added",
        "removed",
        "changed_urls",
        "upgraded",
        "downgraded",
        "recovered",
        "retained_unverified",
        "editor",
    ):
        if key in runtime:
            report[key] = public_data(runtime[key])
    telemetry = public_data(
        {
            key: runtime[key]
            for key in (
                "generated_at",
                "status",
                "channels",
                "checked_links",
                "cached_links",
                "health",
                "budget",
                "changed_files",
            )
            if key in runtime
        }
    )
    report["last_run"] = telemetry
    report["site"] = {"preview": preview, "omitted_sensitive_streams": 0}
    names = list(BASE_PLAYLISTS)
    for name in snapshot.get("personalization", {}).get("managed_files", []):
        if not isinstance(name, str) or not re.fullmatch(
            r"profile-[a-z0-9][a-z0-9-]{0,39}\.m3u", name
        ):
            raise ValueError("Unsafe generated profile filename")
        names.append(name)
    for name in names:
        path = root / name
        if not path.is_file():
            continue
        if path.is_symlink():
            raise ValueError("Pages source files cannot be symbolic links")
        text = path.read_text(encoding="utf-8")
        channels = parse(text)
        public = [channel for channel in channels if exportable_channel(channel)]
        report["site"]["omitted_sensitive_streams"] += len(channels) - len(public)
        header = next(iter(text.splitlines()), "")
        guides = tuple(
            url
            for url in attributes(header).get("url-tvg", "").split(",")
            if url and not sensitive_url(url)
        )
        rendered = render_m3u(public, guides)
        validate_output(rendered, len(public))
        atomic_write(destination / name, rendered)
    stats = root / "build_stats.txt"
    if stats.is_file() and not stats.is_symlink():
        atomic_write(destination / stats.name, stats.read_text(encoding="utf-8"))
    atomic_write(destination / "build_report.json", json_text(report))
    atomic_write(destination / "last_run.json", json_text(telemetry))
    atomic_write(destination / "index.html", render_dashboard(report))
    atomic_write(destination / ".nojekyll", "")
    atomic_write(
        destination / "robots.txt",
        "User-agent: *\nDisallow: /\n" if preview else "User-agent: *\nAllow: /\n",
    )
    return {
        "status": "staged",
        "preview": preview,
        "channels": snapshot["channels"],
        "last_meaningful_publication": snapshot.get("generated_at"),
        "latest_run": runtime.get("generated_at"),
        "omitted_sensitive_streams": report["site"]["omitted_sensitive_streams"],
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("."))
    parser.add_argument("--runtime", type=Path, default=Path(".cache/iptv/last_run.json"))
    parser.add_argument("--output", type=Path, default=Path(".cache/pages-site"))
    parser.add_argument("--preview", action="store_true")
    args = parser.parse_args(argv)
    try:
        print(json_text(stage_site(args.root, args.runtime, args.output, args.preview)))
        return 0
    except (OSError, ValueError, TypeError) as error:
        print(f"Pages staging failed: {type(error).__name__}: {error}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
