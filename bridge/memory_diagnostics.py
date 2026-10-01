"""Opt-in, bounded process memory diagnostics."""

from __future__ import annotations

import logging
import threading
import tracemalloc
from collections.abc import Callable, Mapping
from enum import Enum
from pathlib import Path

SAMPLE_INTERVAL_SECONDS = 20
WARNING_THRESHOLD_KIB = 256 * 1024
TRACING_THRESHOLD_KIB = 320 * 1024
CAPTURE_THRESHOLD_KIB = 384 * 1024
REPORT_RETENTION = 3
RECOVERY_SAMPLES = 3

_SMAPS_KEYS = frozenset(
    {
        "Rss",
        "Pss",
        "Pss_Anon",
        "Pss_File",
        "Private_Clean",
        "Private_Dirty",
        "Shared_Clean",
        "Shared_Dirty",
        "Anonymous",
        "Swap",
    }
)


class DiagnosticState(str, Enum):
    DISABLED = "disabled"
    ARMED = "armed"
    WARNED = "warned"
    TRACING = "tracing"
    CAPTURED = "captured"


def _resident_memory_kib(status_file: Path = Path("/proc/self/status")) -> int | None:
    try:
        lines = status_file.read_text(encoding="utf-8").splitlines()
    except OSError:
        return None
    for line in lines:
        key, separator, value = line.partition(":")
        if key != "VmRSS" or not separator:
            continue
        parts = value.split()
        if len(parts) != 2 or parts[1] != "kB":
            return None
        try:
            amount = int(parts[0])
        except ValueError:
            return None
        return amount if amount >= 0 else None
    return None


def _smaps_rollup_kib(smaps_file: Path = Path("/proc/self/smaps_rollup")) -> dict[str, int]:
    try:
        lines = smaps_file.read_text(encoding="utf-8").splitlines()
    except OSError:
        return {}
    result: dict[str, int] = {}
    for line in lines:
        key, separator, value = line.partition(":")
        if not separator or key not in _SMAPS_KEYS:
            continue
        parts = value.split()
        if len(parts) != 2 or parts[1] != "kB":
            continue
        try:
            amount = int(parts[0])
        except ValueError:
            continue
        if amount >= 0:
            result[key] = amount
    return result


class MemoryDiagnostics:
    def __init__(
        self,
        bridge_home: Path,
        environ: Mapping[str, str],
        safe_counters: Callable[[], Mapping[str, object]] | None = None,
    ) -> None:
        self.bridge_home = Path(bridge_home)
        self.environ = environ
        self.safe_counters = safe_counters
        self._enabled = environ.get("SILLYTAVERN_MEMORY_DIAGNOSTICS") == "1"
        self._state = DiagnosticState.ARMED if self._enabled else DiagnosticState.DISABLED
        self._lock = threading.RLock()
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._owns_tracemalloc = False
        self._recovery_samples = 0

    @property
    def state(self) -> DiagnosticState:
        with self._lock:
            return self._state

    def start(self) -> bool:
        with self._lock:
            if not self._enabled:
                return False
            if self._thread is not None and self._thread.is_alive():
                return False
            self._stop_event.clear()
            self._thread = threading.Thread(
                target=self._run_sampler,
                name="memory-diagnostics",
                daemon=True,
            )
            self._thread.start()
            return True

    def _run_sampler(self) -> None:
        while not self._stop_event.wait(SAMPLE_INTERVAL_SECONDS):
            try:
                self.sample_once()
            except Exception:
                logging.warning("Memory diagnostics sample failed")

    def _start_tracing_if_needed(self) -> None:
        if tracemalloc.is_tracing():
            return
        tracemalloc.start(1)
        self._owns_tracemalloc = True

    def _stop_owned_tracing(self) -> None:
        if self._owns_tracemalloc:
            if tracemalloc.is_tracing():
                tracemalloc.stop()
            self._owns_tracemalloc = False

    def sample_once(self) -> DiagnosticState:
        with self._lock:
            if not self._enabled:
                return self._state
            rss_kib = _resident_memory_kib()
            if rss_kib is None:
                return self._state

            if self._state is not DiagnosticState.ARMED and rss_kib < WARNING_THRESHOLD_KIB:
                self._recovery_samples += 1
                if self._recovery_samples >= RECOVERY_SAMPLES:
                    self._state = DiagnosticState.ARMED
                    self._recovery_samples = 0
                    self._stop_owned_tracing()
                return self._state
            self._recovery_samples = 0
            if self._state is DiagnosticState.CAPTURED:
                return self._state

            if rss_kib >= CAPTURE_THRESHOLD_KIB:
                self._start_tracing_if_needed()
                self._state = DiagnosticState.CAPTURED
                self._stop_owned_tracing()
            elif rss_kib >= TRACING_THRESHOLD_KIB:
                self._start_tracing_if_needed()
                self._state = DiagnosticState.TRACING
            elif rss_kib >= WARNING_THRESHOLD_KIB and self._state is DiagnosticState.ARMED:
                self._state = DiagnosticState.WARNED
            return self._state

    def stop(self, timeout: float = 1.0) -> bool:
        self._stop_event.set()
        with self._lock:
            thread = self._thread
        if thread is not None:
            thread.join(timeout=max(0.0, timeout))
        stopped = thread is None or not thread.is_alive()
        with self._lock:
            if stopped:
                self._thread = None
            self._stop_owned_tracing()
        return stopped
