"""Shared receive-byte and cooperative wall-time budgets across HTTP workers."""

import time
from contextlib import contextmanager
from threading import Lock


class BudgetExceeded(RuntimeError):
    pass


class BuildBudget:
    def __init__(self, seconds: float, maximum: int):
        self.started = time.monotonic()
        self.seconds, self.maximum = seconds, maximum
        self.used, self.received = 0, 0
        self.lock = Lock()

    def remaining(self) -> float:
        remaining = self.seconds - (time.monotonic() - self.started)
        if remaining <= 0:
            raise BudgetExceeded(
                "Whole-build wall-time budget exhausted; previous output preserved"
            )
        return remaining

    def read(self, response, size: int) -> bytes:
        self.remaining()
        with self.lock:
            grant = min(size, self.maximum - self.used)
            if grant <= 0:
                raise BudgetExceeded(
                    "Whole-build HTTP receive-byte budget exhausted; previous output preserved"
                )
            self.used += grant
        try:
            # HTTPResponse.read1 performs at most one buffered/raw read; check
            # the deadline again between chunks, even for slow drip responses.
            body = getattr(response, "read1", response.read)(grant)
        except Exception:
            # Conservatively charge the reservation on an interrupted body.
            raise
        else:
            with self.lock:
                self.used -= grant - len(body)
                self.received += len(body)
            self.remaining()
            return body

    def summary(self) -> dict:
        return {
            "max_seconds": self.seconds,
            "elapsed_seconds": round(time.monotonic() - self.started, 2),
            "max_http_bytes": self.maximum,
            "http_bytes_received": self.received,
            "http_bytes_charged": self.used,
            "scope": "Python HTTP response payloads, including retries, redirects, source/metadata/guide downloads and HLS children. Not TCP/TLS overhead or external ffmpeg traffic; decoder calls have separate fixed count/time limits. The workflow adds an outer hard timeout.",
        }


_ACTIVE = None
_BUILD_LOCK = Lock()


@contextmanager
def build_budget(options):
    global _ACTIVE
    # Workers in one process share this budget; serialize simultaneous builds
    # in the same interpreter so one build cannot reset another's counters.
    with _BUILD_LOCK:
        budget = BuildBudget(options.max_seconds, options.max_http_bytes)
        _ACTIVE = budget
        try:
            yield budget
        finally:
            _ACTIVE = None


def check() -> None:
    if _ACTIVE is not None:
        _ACTIVE.remaining()


def timeout(seconds: float) -> float:
    return min(seconds, _ACTIVE.remaining()) if _ACTIVE is not None else seconds


def pause(seconds: float) -> None:
    if _ACTIVE is not None and seconds >= _ACTIVE.remaining():
        raise BudgetExceeded("Whole-build wall-time budget would be exceeded by retry/host delay")
    time.sleep(seconds)
    check()


def read_chunk(response, size: int) -> bytes:
    return _ACTIVE.read(response, size) if _ACTIVE is not None else response.read(size)


def read_response(response, maximum: int) -> bytes:
    chunks, count = [], 0
    while count < maximum:
        body = read_chunk(response, min(65536, maximum - count))
        if not body:
            break
        chunks.append(body)
        count += len(body)
    return b"".join(chunks)
