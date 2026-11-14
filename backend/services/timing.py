"""Per-request stage timings for the click path (POST /analyze).

decide() calls start() once. Any code it runs, in this thread or in a pool thread started with submit(),
adds its stage with `with stage("embed_ms"):` or count("gemini_attempts"). Outside a request (scripts,
tests) there is no current dict and every call is a no-op.
"""
import contextvars
import time
from concurrent.futures import Executor, Future
from contextlib import contextmanager

_current: contextvars.ContextVar[dict | None] = contextvars.ContextVar("secondlook_timings", default=None)


def start() -> dict:
    """A fresh timings dict for this request. Returns it so the caller can store it."""
    timings = {}
    _current.set(timings)
    return timings


def current() -> dict | None:
    return _current.get()


@contextmanager
def stage(name: str):
    """Adds the block's wall time in ms to timings[name]. Repeated stages add up (for example two Gemini reads)."""
    started = time.monotonic()
    try:
        yield
    finally:
        timings = _current.get()
        if timings is not None:
            timings[name] = timings.get(name, 0) + int((time.monotonic() - started) * 1000)


def count(name: str, n: int = 1) -> None:
    timings = _current.get()
    if timings is not None:
        timings[name] = timings.get(name, 0) + n


def submit(pool: Executor, fn, *args, **kwargs) -> Future:
    """pool.submit that keeps the current timings dict, so stages inside fn are recorded too."""
    return pool.submit(contextvars.copy_context().run, fn, *args, **kwargs)
