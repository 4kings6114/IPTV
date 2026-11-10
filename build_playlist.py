#!/usr/bin/env python3
"""CLI for the safety-first IPTV builder (Python 3.10+, Linux/macOS)."""

import argparse
import sys
from dataclasses import replace
from pathlib import Path

from iptv_builder.builder import run_build
from iptv_builder.config import load_settings

ROOT = Path(__file__).resolve().parent


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build and safely publish IPTV playlists")
    parser.add_argument("--config", type=Path, default=ROOT / "config.json")
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=ROOT,
        help="Output/cache directory; input paths stay relative to the config file",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Collect, check and validate without changing published outputs or health history",
    )
    parser.add_argument(
        "--allow-large-drop",
        action="store_true",
        help="Explicitly bypass only the percentage-drop guard (never the minimum/source guards)",
    )
    args = parser.parse_args(argv)
    try:
        settings = load_settings(args.config)
        if args.allow_large_drop:
            print("WARNING: percentage-drop guard disabled for this run.", file=sys.stderr)
            settings = replace(settings, max_drop_ratio=1)
        run_build(settings, args.output_dir, args.config.resolve().parent, args.dry_run)
    except (OSError, ValueError, RuntimeError) as error:
        print(f"Build failed: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
