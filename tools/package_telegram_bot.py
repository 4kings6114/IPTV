#!/usr/bin/env python3
"""Build a standalone bot ZIP from an explicit, credential-free allowlist."""

import argparse
import stat
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FILES = (
    "telegram_bot.py",
    "run_bot.sh",
    "run_bot.bat",
    ".env.example",
    "BOT-README.md",
    "docs/telegram-bot-review.md",
    "deploy/iptv-telegram-bot.service",
    "tests/__init__.py",
    "tools/package_telegram_bot.py",
)


def package(output):
    files = [ROOT / name for name in FILES]
    files.extend(sorted((ROOT / "iptv_bot").glob("*.py")))
    files.extend(sorted((ROOT / "tests/bot").glob("*.py")))
    # A missing directory must not silently produce a non-working distribution.
    for required in (ROOT / "iptv_bot/cli.py", ROOT / "tests/bot/helpers.py"):
        if required not in files:
            raise ValueError("Required package source is missing")
    for path in files:
        if not path.is_file() or path.is_symlink():
            raise ValueError("Package source is missing or is a symbolic link")
    output = Path(output)
    if output.resolve() in {path.resolve() for path in files}:
        raise ValueError("Archive must not overwrite source files")
    output.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(files):
            relative = path.relative_to(ROOT)
            info = zipfile.ZipInfo("iptv-telegram-bot/" + relative.as_posix())
            info.compress_type = zipfile.ZIP_DEFLATED
            info.create_system = 3
            permissions = 0o755 if relative.as_posix() == "run_bot.sh" else 0o644
            info.external_attr = (stat.S_IFREG | permissions) << 16
            text = path.read_text(encoding="utf-8")
            if relative.as_posix() == "run_bot.bat":
                text = text.replace("\n", "\r\n")
            archive.writestr(info, text.encode("utf-8"))
    return output


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output", type=Path, default=ROOT / ".cache/downloads/iptv-telegram-bot.zip"
    )
    args = parser.parse_args()
    print(package(args.output))


if __name__ == "__main__":
    main()
