"""Atomic publication, rollback and bounded last-known-good snapshots."""

import hashlib
import json
import os
import tempfile
from contextlib import contextmanager
from pathlib import Path

from .m3u import valid_source


def read_json(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def json_text(value: dict) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def atomic_write(path: Path, text: str) -> bool:
    data = text.encode("utf-8")
    if path.exists() and path.read_bytes() == data:
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    return True


@contextmanager
def build_lock(output_dir: Path):
    # Lock the output, not the configurable cache: two configurations using
    # different caches must still not publish into the same directory at once.
    # flock is released by the OS even if a build is killed; no stale PID locks.
    import fcntl

    output_dir.mkdir(parents=True, exist_ok=True)
    with (output_dir / ".iptv-build.lock").open("a") as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise RuntimeError(
                "Another build is already using this cache/output directory"
            ) from error
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


def archive_playlist(backup_dir: Path, text: str, keep: int) -> None:
    if not valid_source(text):
        return
    digest = hashlib.sha256(text.encode()).hexdigest()[:20]
    snapshot = backup_dir / f"{digest}.m3u"
    atomic_write(snapshot, text)
    snapshot.touch()  # An older version reused today is recent again.
    snapshots = sorted(
        backup_dir.glob("*.m3u"),
        key=lambda path: (path.stat().st_mtime_ns, path.name),
        reverse=True,
    )
    for path in snapshots[keep:]:
        path.unlink()


def publish_files(output_dir: Path, files: dict[str, str]) -> list[str]:
    """Stage every file before replacement; roll back on ordinary I/O errors.

    Each replacement is atomic. This is not a multi-file filesystem transaction:
    on a machine/power failure readers may briefly see different generations.
    The main playlist is replaced last and all staged content is validated by
    the caller before entering this function.
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    changed = {}
    for name, text in files.items():
        if Path(name).name != name:
            raise ValueError("Publication filenames must be flat relative filenames")
        path = output_dir / name
        data = text.encode("utf-8")
        if not path.exists() or path.read_bytes() != data:
            changed[name] = data
    if not changed:
        return []
    order = sorted(changed, key=lambda name: (name == "playlist.m3u", name))
    with tempfile.TemporaryDirectory(prefix=".iptv-stage-", dir=output_dir) as temporary:
        stage = Path(temporary)
        previous = {}
        for name, data in changed.items():
            path = output_dir / name
            previous[name] = path.read_bytes() if path.exists() else None
            with (stage / name).open("wb") as handle:
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
            if previous[name] is not None:
                (stage / f"old-{name}").write_bytes(previous[name])
        replaced = []
        try:
            for name in order:
                os.replace(stage / name, output_dir / name)
                replaced.append(name)
        except OSError:
            for name in reversed(replaced):
                if previous[name] is None:
                    (output_dir / name).unlink(missing_ok=True)
                else:
                    os.replace(stage / f"old-{name}", output_dir / name)
            raise
    return order
