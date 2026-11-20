#!/usr/bin/env python3
"""Build the separately scoped Indian playlist using the standard IPTV engine."""

import argparse
import sys
from dataclasses import replace
from pathlib import Path

from iptv_builder.builder import run_build
from iptv_builder.config import load_settings
from iptv_builder.m3u import parse, valid_source
from iptv_builder.privacy import exportable_channel

ROOT = Path(__file__).resolve().parent


def build(config_path: Path, output_dir: Path, *, dry_run=False, force_check=False):
    config_path = config_path.resolve()
    settings = load_settings(config_path)
    if settings.stream_template or settings.extra_sources or settings.manual_file != "channels.m3u":
        raise ValueError("Indian build expects the separate local channels.m3u input only")
    input_path = config_path.parent / settings.manual_file
    if (
        input_path.is_symlink()
        or not input_path.is_file()
        or input_path.stat().st_size > 8 * 1024 * 1024
    ):
        raise ValueError("Indian input is missing, unsafe or too large")
    text = input_path.read_text(encoding="utf-8-sig")
    entries = parse(text)
    if not valid_source(text) or len(entries) != sum(
        line.strip().upper().startswith("#EXTINF:") for line in text.splitlines()
    ):
        raise ValueError(
            "Indian input must contain a valid non-empty M3U without malformed entries"
        )
    if any(not exportable_channel(item) for item in entries):
        raise ValueError(
            "Indian input contains potentially private URLs or headers; refusing publication"
        )
    if force_check:
        settings = replace(settings, health_cache=replace(settings.health_cache, hours=0))
    return run_build(settings, output_dir, config_path.parent, dry_run=dry_run)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=ROOT / "indian/config.json")
    parser.add_argument("--output-dir", type=Path, default=ROOT / "indian")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--force-check", action="store_true")
    args = parser.parse_args(argv)
    try:
        build(args.config, args.output_dir, dry_run=args.dry_run, force_check=args.force_check)
    except (OSError, ValueError, RuntimeError) as error:
        print(f"Indian build failed: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
