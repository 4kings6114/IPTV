"""Build a reproducible standalone bot ZIP from an explicit non-secret allowlist."""

import argparse
import hashlib
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FILES = (
    "bot_run.py",
    "bot_run.bat",
    "bot_run.sh",
    "bot_run.command",
    "START_HERE.txt",
    "README.fa.md",
    "config.example.json",
    ".gitignore",
    "data/README.md",
    "iptv_bot/__init__.py",
    "iptv_bot/config.py",
    "iptv_bot/network.py",
    "iptv_bot/views.py",
    "iptv_bot/app.py",
)


def package(output: Path, source: Path = ROOT / "local_bot") -> str:
    output.parent.mkdir(parents=True, exist_ok=True)
    manifest = []
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for name in FILES:
            path = source / name
            if path.is_symlink():
                raise ValueError("Package sources cannot be symlinks")
            # Every allowlisted source is UTF-8 text. Normalize git's Windows
            # checkout line endings, then emit only the batch file as CRLF.
            data = path.read_bytes().replace(b"\r\n", b"\n")
            if name.endswith(".bat"):
                data = data.replace(b"\n", b"\r\n")
            manifest.append(f"{hashlib.sha256(data).hexdigest()}  {name}")
            info = zipfile.ZipInfo(f"IPTV-Local-Bot/{name}", date_time=(2026, 11, 20, 0, 0, 0))
            info.create_system = 3
            mode = 0o755 if name.endswith((".sh", ".command")) else 0o644
            info.external_attr = (0o100000 | mode) << 16
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, data)
        info = zipfile.ZipInfo("IPTV-Local-Bot/MANIFEST.sha256", date_time=(2026, 11, 20, 0, 0, 0))
        info.create_system = 3
        info.external_attr = 0o100644 << 16
        info.compress_type = zipfile.ZIP_DEFLATED
        archive.writestr(info, "\n".join(manifest) + "\n")
    digest = hashlib.sha256(output.read_bytes()).hexdigest()
    output.with_suffix(".sha256").write_text(f"{digest}  {output.name}\n", encoding="ascii")
    return digest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "deliverables/IPTV-Local-Bot.zip")
    args = parser.parse_args()
    digest = package(args.output)
    print(f"Created {args.output.name}\nSHA256: {digest}")


if __name__ == "__main__":
    main()
