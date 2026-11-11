"""Install the pinned official Linux age tools for CI, after a SHA-256 check."""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
import platform
import tarfile
import urllib.request

VERSION = "v1.3.2"
SHA256 = "cbe24006683f8eb669266162894b9a522a1af52f2665fbc63a4bb032ed26ac10"
URL = f"https://github.com/FiloSottile/age/releases/download/{VERSION}/age-{VERSION}-linux-amd64.tar.gz"


def main() -> None:
    if platform.system() != "Linux" or platform.machine() != "x86_64":
        raise RuntimeError("This pinned CI installer supports only Linux amd64.")
    root = Path(os.environ["RUNNER_TEMP"]) / "iptv-age-tools"
    root.mkdir(mode=0o700)
    archive_path = root / "age.tar.gz"
    digest = hashlib.sha256()
    size = 0
    with urllib.request.urlopen(URL, timeout=60) as response, archive_path.open("xb") as output:
        while block := response.read(64 * 1024):
            size += len(block)
            if size > 30 * 1024 * 1024:
                raise RuntimeError("Age download exceeded the pinned package size bound.")
            digest.update(block)
            output.write(block)
    if digest.hexdigest() != SHA256:
        archive_path.unlink()
        raise RuntimeError("Official age archive checksum did not match the pinned digest.")
    found = set()
    with tarfile.open(archive_path) as archive:
        for member in archive:
            if member.name not in {"age/age", "age/age-keygen"}:
                continue
            if not member.isfile() or member.size > 30 * 1024 * 1024:
                raise RuntimeError("Unexpected tool archive entry.")
            data = archive.extractfile(member)
            assert data is not None
            with data:
                destination = root / Path(member.name).name
                destination.write_bytes(data.read())
                destination.chmod(0o700)
            found.add(destination.name)
    if found != {"age", "age-keygen"}:
        raise RuntimeError("Official age archive is missing a required tool.")
    archive_path.unlink()
    with open(os.environ["GITHUB_PATH"], "a", encoding="utf-8") as output:
        output.write(str(root) + "\n")
    print("Verified official age", VERSION, "archive SHA-256:", SHA256)


if __name__ == "__main__":
    main()
