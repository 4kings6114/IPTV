"""Local-only configuration, atomic state, and a cross-platform process lock."""

import json
import os
import re
import tempfile
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_REPOSITORY = "4kings6114/IPTV"


class ConfigurationError(ValueError):
    pass


@dataclass(frozen=True)
class Config:
    token: str = field(repr=False)
    repository: str = DEFAULT_REPOSITORY
    branch: str = "main"
    owner_chat_id: int | None = None
    poll_seconds: int = 300

    def __post_init__(self):
        if not re.fullmatch(r"\d{5,20}:[A-Za-z0-9_-]{20,200}", self.token):
            raise ConfigurationError(
                "Bot token format is invalid; enter it locally, never in chat."
            )
        if not re.fullmatch(r"[A-Za-z0-9-]+/[A-Za-z0-9_.-]+", self.repository):
            raise ConfigurationError("Repository must be owner/repository.")
        if self.repository.split("/")[1] in {".", ".."}:
            raise ConfigurationError("Invalid repository name.")
        if (
            not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._/-]{0,99}", self.branch)
            or ".." in self.branch
            or "//" in self.branch
            or self.branch.endswith("/")
        ):
            raise ConfigurationError("Invalid branch name.")
        if self.owner_chat_id is not None and (
            type(self.owner_chat_id) is not int or self.owner_chat_id <= 0
        ):
            raise ConfigurationError("Owner must be a positive private-chat ID.")
        if type(self.poll_seconds) is not int or not 120 <= self.poll_seconds <= 3600:
            raise ConfigurationError("poll_seconds must be between 120 and 3600 (default 300).")

    def data(self):
        return {
            "token": self.token,
            "repository": self.repository,
            "branch": self.branch,
            "owner_chat_id": self.owner_chat_id,
            "poll_seconds": self.poll_seconds,
        }


def read_json(path: Path, default=None, maximum=8 * 1024 * 1024):
    if not path.exists():
        return {} if default is None else default
    if path.stat().st_size > maximum:
        raise ConfigurationError("Local data file is too large.")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise ConfigurationError(
            "Cannot read local JSON; keep a backup before resetting data."
        ) from error
    if not isinstance(value, dict):
        raise ConfigurationError("Local JSON must be an object.")
    return value


def atomic_json(path: Path, data: dict):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    descriptor, temporary = tempfile.mkstemp(prefix=".write-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(data, handle, ensure_ascii=False, separators=(",", ":"))
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        if os.name != "nt":
            path.chmod(0o600)
    finally:
        Path(temporary).unlink(missing_ok=True)


def load_config(path: Path) -> Config:
    data = read_json(path, maximum=16384)
    if set(data) - {"token", "repository", "branch", "owner_chat_id", "poll_seconds"}:
        raise ConfigurationError("Unknown configuration key.")
    try:
        return Config(**data)
    except TypeError as error:
        raise ConfigurationError(
            "Configuration is missing a required field or has invalid types."
        ) from error


@contextmanager
def instance_lock(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with path.open("a+b") as handle:
        if path.stat().st_size == 0:
            handle.write(b"0")
            handle.flush()
        handle.seek(0)
        try:
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as error:
            raise ConfigurationError(
                "Another local bot is already using this data folder."
            ) from error
        try:
            yield
        finally:
            handle.seek(0)
            if os.name == "nt":
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle, fcntl.LOCK_UN)
