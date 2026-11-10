"""Atomic cursors and one local poller per bot, on POSIX and Windows."""

import json
import os
import stat
import tempfile
from pathlib import Path


class StateError(RuntimeError):
    pass


class AlreadyRunning(StateError):
    pass


def _offset(value):
    if type(value) is not int or not 0 <= value <= 2**63 - 1:
        raise StateError("شناسهٔ ذخیره‌شدهٔ پیام نامعتبر است؛ وضعیت را بررسی کنید.")
    return value


class StateStore:
    def __init__(self, directory: Path, scope: str):
        self.directory = directory / scope
        self.path = self.directory / "state.json"
        self.lock_path = self.directory / "poller.lock"

    def prepare(self):
        try:
            self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
            if self.directory.is_symlink():
                raise StateError("پوشهٔ وضعیت نباید لینک نمادین باشد.")
        except OSError:
            raise StateError("ساخت پوشهٔ وضعیت ممکن نیست.") from None

    def load(self):
        if self.path.is_symlink():
            raise StateError("فایل وضعیت نباید لینک نمادین باشد.")
        try:
            with self.path.open("rb") as source:
                raw = source.read(4097)
        except FileNotFoundError:
            return 0
        except OSError:
            raise StateError("خواندن وضعیت ممکن نیست.") from None
        try:
            if len(raw) > 4096:
                raise ValueError
            data = json.loads(raw)
            if (
                not isinstance(data, dict)
                or set(data) != {"schema", "next_offset"}
                or type(data["schema"]) is not int
                or data["schema"] != 1
            ):
                raise ValueError
            return _offset(data["next_offset"])
        except (ValueError, KeyError, UnicodeError, RecursionError):
            raise StateError(
                "فایل وضعیت خراب است؛ برای جلوگیری از تکرار پیام‌ها خودکار صفر نمی‌شود."
            ) from None

    def save(self, offset):
        _offset(offset)
        self.prepare()
        if self.path.is_symlink():
            raise StateError("فایل وضعیت نباید لینک نمادین باشد.")
        if offset < self.load():
            raise StateError("بازگرداندن خودکار وضعیت به شناسهٔ قدیمی مجاز نیست.")
        temporary = None
        try:
            descriptor, temporary = tempfile.mkstemp(prefix=".cursor-", dir=self.directory)
            with os.fdopen(descriptor, "w", encoding="utf-8") as target:
                json.dump({"schema": 1, "next_offset": offset}, target)
                target.write("\n")
                target.flush()
                os.fsync(target.fileno())
            os.replace(temporary, self.path)
            temporary = None
        except OSError:
            raise StateError("ثبت اتمی وضعیت ممکن نیست؛ پردازش متوقف شد.") from None
        finally:
            if temporary is not None:
                try:
                    os.unlink(temporary)
                except OSError:
                    pass

    def import_legacy(self, path):
        if self.path.exists():
            raise StateError("وضعیت جدید موجود است؛ برای جلوگیری از بازپخش، بازنویسی نمی‌شود.")
        try:
            with Path(path).open("rb") as source:
                raw = source.read(65)
            text = raw.decode("ascii").strip()
            if len(raw) > 64 or not text.isascii() or not text.isdecimal() or len(text) > 19:
                raise ValueError
            value = _offset(int(text))
        except (OSError, ValueError, UnicodeError):
            raise StateError("فایل offset قدیمی معتبر نیست.") from None
        self.save(value)

    def lock(self):
        return PollerLock(self)


class PollerLock:
    def __init__(self, store):
        self.store, self.handle = store, None

    def __enter__(self):
        self.store.prepare()
        path = self.store.lock_path
        if path.is_symlink():
            raise StateError("فایل قفل نباید لینک نمادین باشد.")
        descriptor = None
        try:
            flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0)
            descriptor = os.open(path, flags, 0o600)
            if not stat.S_ISREG(os.fstat(descriptor).st_mode):
                raise OSError
            self.handle = os.fdopen(descriptor, "r+b")
            descriptor = None
            if os.name == "nt":
                import msvcrt

                if not os.fstat(self.handle.fileno()).st_size:
                    self.handle.write(b"\0")
                    self.handle.flush()
                self.handle.seek(0)
                msvcrt.locking(self.handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(self.handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            if descriptor is not None:
                os.close(descriptor)
            if self.handle is not None:
                self.handle.close()
                self.handle = None
            raise AlreadyRunning(
                "قفل ربات در دسترس نیست؛ نمونهٔ دیگر یا مجوز پوشه را بررسی کنید."
            ) from None
        return self

    def __exit__(self, *args):
        if self.handle is not None:
            self.handle.close()
            self.handle = None
        # Do not unlink: another process may already be waiting on this inode.
