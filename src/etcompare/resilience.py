"""Resilient processing: retries, keep-awake, stall detection and checkpoints (guide step 18).

None of these change a result; they decide only whether a run finishes, waits, resumes or
fails. Every setting comes from the ``processing:`` section
(:class:`~etcompare.config.ProcessingSpec`).

- :func:`call_with_retries` retries a read that raised an I/O error (:class:`OSError`,
  which includes ``rasterio.errors.RasterioIOError``) while the file still exists. Each
  retry is an ``io_retry`` log event. A file that no longer exists raises
  :class:`~etcompare.errors.InputMissingError` at once. etcompare's own errors
  (configuration, contract, grid) are never retried, so a retry never masks a
  configuration error.
- :func:`keep_awake` asks Windows not to idle-sleep while a run is active
  (``SetThreadExecutionState``); elsewhere it does nothing and says so in the log.
- :class:`StallWatchdog` logs ``io_stall`` when no raster has finished for
  ``stall_warning_s`` seconds, and ``system_suspended`` when its own clock jumped (the
  machine slept; D-078). With ``stall_timeout_s`` it fails the run.
- :class:`Checkpoint` stores an extraction's progress under the unit's cache key. A
  resume reads it only when the key is identical and the file's SHA-256 matches the
  record written after it, so a half-written checkpoint is never used.
"""

from __future__ import annotations

import ctypes
import hashlib
import logging
import os
import pickle
import shutil
import sys
import threading
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, TypeVar

from etcompare.errors import EtcompareError, InputMissingError
from etcompare.io.atomic import atomic_path, write_json_atomic
from etcompare.log import get_logger, log_event

_log = get_logger("resilience")
T = TypeVar("T")

CHECKPOINT_VERSION = "1"


# --------------------------------------------------------------------------- retries

@dataclass(frozen=True)
class RetryPolicy:
    """``attempts`` tries in all; wait ``backoff_s[i]`` before retry ``i + 1``."""

    attempts: int = 3
    backoff_s: tuple[float, ...] = (1.0, 4.0)

    def wait(self, retry: int) -> float:
        """Seconds to wait before retry number ``retry`` (1-based); the last value repeats."""
        if not self.backoff_s:
            return 0.0
        return self.backoff_s[min(retry, len(self.backoff_s)) - 1]

    @classmethod
    def from_spec(cls, spec: Any) -> RetryPolicy:
        return cls(spec.io_attempts, tuple(spec.io_backoff_s))


def is_transient(exc: BaseException) -> bool:
    """An I/O error that may succeed on retry: an :class:`OSError` not raised by etcompare."""
    return isinstance(exc, OSError) and not isinstance(exc, EtcompareError)


def call_with_retries(fn: Callable[[], T], path: str | os.PathLike[str], *,
                      policy: RetryPolicy | None, what: str,
                      sleep: Callable[[float], None] = time.sleep) -> T:
    """Call ``fn``; on a transient error retry while ``path`` exists (see module docs)."""
    policy = policy or RetryPolicy(1, ())
    attempt = 1
    while True:
        try:
            return fn()
        except Exception as exc:
            if not is_transient(exc):
                raise
            if not os.path.exists(path):
                raise InputMissingError(f"{os.fspath(path)}: file no longer exists "
                                        f"({type(exc).__name__}: {exc})") from exc
            if attempt >= policy.attempts:
                if policy.attempts > 1:
                    log_event(_log, "io_failed", level=logging.WARNING, what=what,
                              path=os.fspath(path), attempts=attempt,
                              error_type=type(exc).__name__, error=str(exc)[:500])
                raise
            wait = policy.wait(attempt)
            log_event(_log, "io_retry", level=logging.WARNING, what=what,
                      path=os.fspath(path), attempt=attempt, attempts=policy.attempts,
                      wait_s=wait, error_type=type(exc).__name__, error=str(exc)[:500])
            sleep(wait)
            attempt += 1


# --------------------------------------------------------------------------- keep awake

_ES_CONTINUOUS = 0x80000000
_ES_SYSTEM_REQUIRED = 0x00000001


@contextmanager
def keep_awake(enabled: bool = True) -> Iterator[bool]:
    """Keep the system from idle-sleeping inside the block; yield whether it is held.

    Windows only (``SetThreadExecutionState``); the display may still turn off. On other
    systems, or when disabled, nothing is requested.
    """
    held = False
    if enabled and sys.platform == "win32":
        try:
            held = bool(ctypes.WinDLL("kernel32").SetThreadExecutionState(
                _ES_CONTINUOUS | _ES_SYSTEM_REQUIRED))
        except (OSError, AttributeError):
            held = False
    log_event(_log, "keep_awake", requested=enabled, held=held, platform=sys.platform)
    try:
        yield held
    finally:
        if held:
            ctypes.WinDLL("kernel32").SetThreadExecutionState(_ES_CONTINUOUS)


# --------------------------------------------------------------------------- watchdog

class StallTimeoutError(EtcompareError):
    """No raster finished within ``processing.stall_timeout_s``."""


_ACTIVE: StallWatchdog | None = None


def beat(what: str | None = None) -> None:
    """Record progress (once per raster) and arm stall detection; no-op without a watchdog."""
    w = _ACTIVE
    if w is not None:
        w.beat(what)


def rest() -> None:
    """Disarm stall detection (raster reading has ended); a no-op without a watchdog."""
    w = _ACTIVE
    if w is not None:
        w.rest()


class StallWatchdog:
    """Background thread that watches :func:`beat` (use as a context manager).

    Stall detection is armed by the first :func:`beat` and disarmed by :func:`rest`, so
    only raster reading is watched; analyses that read no raster are never a stall.
    ``io_stall`` (warning) after ``warning_s`` without a beat, once per stall;
    ``system_suspended`` (warning) when the watchdog's own wall clock jumps by more than
    ``max(60, 4 * interval)`` seconds between ticks, in which case the stall clock is
    reset: a sleeping machine is not a stalled drive. With ``timeout_s`` the run fails:
    :class:`StallTimeoutError` is raised in the main thread when it next runs Python code,
    and ``on_timeout`` is called; if the main thread is still blocked after
    ``grace_s`` the process exits with code 1.
    """

    def __init__(self, warning_s: float = 120.0, timeout_s: float | None = None, *,
                 on_timeout: Callable[[str], None] | None = None, grace_s: float = 30.0,
                 interval_s: float | None = None,
                 clock: Callable[[], float] = time.time) -> None:
        self.clock = clock
        self.warning_s = warning_s
        self.timeout_s = timeout_s
        self.on_timeout = on_timeout
        self.grace_s = grace_s
        self.interval_s = interval_s or max(0.05, min(5.0, warning_s / 4))
        self._last = clock()
        self._what: str | None = None
        self._warned = False
        self._armed = False
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._main = threading.main_thread().ident
        self.events: list[str] = []

    def beat(self, what: str | None = None) -> None:
        self._last = self.clock()
        self._what = what
        self._warned = False
        self._armed = True

    def rest(self) -> None:
        self._armed = False

    def __enter__(self) -> StallWatchdog:
        global _ACTIVE
        self._last = self.clock()
        self._thread = threading.Thread(target=self._run, name="etcompare-watchdog",
                                        daemon=True)
        self._thread.start()
        _ACTIVE = self
        return self

    def __exit__(self, *exc: object) -> None:
        global _ACTIVE
        _ACTIVE = None
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=5)

    def _run(self) -> None:
        tick = self.clock()
        while not self._stop.wait(self.interval_s):
            now = self.clock()
            jump = now - tick - self.interval_s
            tick = now
            if jump > max(60.0, 4 * self.interval_s):
                self.events.append("system_suspended")
                log_event(_log, "system_suspended", level=logging.WARNING,
                          gap_s=round(jump, 1), last_input=self._what,
                          note="the process did not run for this long (sleep or "
                               "hibernation); not counted as an I/O stall")
                self._last = now
                continue
            if not self._armed:
                continue
            idle = now - self._last
            if idle >= self.warning_s and not self._warned:
                self._warned = True
                self.events.append("io_stall")
                log_event(_log, "io_stall", level=logging.WARNING, idle_s=round(idle, 1),
                          last_input=self._what)
            if self.timeout_s is not None and idle >= self.timeout_s:
                self._timeout(idle)
                return

    def _timeout(self, idle: float) -> None:
        msg = (f"no raster finished for {idle:.0f} s (processing.stall_timeout_s = "
               f"{self.timeout_s:.0f}); last input {self._what}")
        self.events.append("io_stall_timeout")
        log_event(_log, "io_stall_timeout", level=logging.ERROR, idle_s=round(idle, 1),
                  last_input=self._what)
        if self.on_timeout is not None:
            try:
                self.on_timeout(msg)
            except Exception as exc:  # noqa: BLE001 - must not kill the watchdog silently
                log_event(_log, "stall_callback_failed", level=logging.ERROR, error=str(exc))
        if self._main is not None:
            ctypes.pythonapi.PyThreadState_SetAsyncExc(ctypes.c_ulong(self._main),
                                                       ctypes.py_object(StallTimeoutError))
        if not self._stop.wait(self.grace_s):
            log_event(_log, "stall_exit", level=logging.ERROR,
                      note="main thread still blocked; exiting with code 1")
            logging.shutdown()
            os._exit(1)


# --------------------------------------------------------------------------- checkpoints

def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


class Checkpoint:
    """Progress of one unit under one cache key: ``<root>/<unit>/<key>/state.{pkl,json}``.

    :meth:`save` writes the state, then a JSON record with its SHA-256 (both atomic), so
    the record is the commit marker. :meth:`load` returns the state only when the record
    names the same key and unit and the hash matches. :meth:`prune` removes checkpoints
    of other keys of the unit (they can never be reused); :meth:`clear` removes the unit's
    checkpoints after its outputs are written.

    The state is a pickle written by this process for this process; it is never read
    from anywhere else.
    """

    def __init__(self, root: str | os.PathLike[str], unit: str, key: str) -> None:
        import re

        self.unit = unit
        self.key = key
        self.unit_dir = Path(root) / re.sub(r"[^A-Za-z0-9_.@=-]+", "_", unit)
        self.dir = self.unit_dir / key
        self.state_path = self.dir / "state.pkl"
        self.record_path = self.dir / "state.json"

    def load(self) -> dict[str, Any] | None:
        import json

        if not (self.state_path.is_file() and self.record_path.is_file()):
            return None
        try:
            rec = json.loads(self.record_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        if (rec.get("version") != CHECKPOINT_VERSION or rec.get("cache_key") != self.key
                or rec.get("unit") != self.unit or rec.get("sha256") != _sha256(
                    self.state_path)):
            log_event(_log, "checkpoint_rejected", level=logging.WARNING, unit=self.unit,
                      path=str(self.dir))
            return None
        with self.state_path.open("rb") as fh:
            state: dict[str, Any] = pickle.load(fh)  # noqa: S301 - written by save()
        log_event(_log, "checkpoint_loaded", unit=self.unit, through=rec.get("through"),
                  n_rows=rec.get("n_rows"))
        return state

    def save(self, state: dict[str, Any], *, through: str, n_rows: int) -> None:
        from etcompare.runrecord import utc_now

        self.record_path.unlink(missing_ok=True)
        with atomic_path(self.state_path) as tmp, tmp.open("wb") as fh:
            pickle.dump(state, fh, protocol=pickle.HIGHEST_PROTOCOL)
        write_json_atomic({"version": CHECKPOINT_VERSION, "unit": self.unit,
                           "cache_key": self.key, "through": through, "n_rows": n_rows,
                           "sha256": _sha256(self.state_path), "written_utc": utc_now()},
                          self.record_path)
        log_event(_log, "checkpoint_saved", unit=self.unit, through=through, n_rows=n_rows)

    def prune(self) -> None:
        if self.unit_dir.is_dir():
            for d in self.unit_dir.iterdir():
                if d.name != self.key:
                    shutil.rmtree(d, ignore_errors=True)

    def clear(self) -> None:
        shutil.rmtree(self.unit_dir, ignore_errors=True)
