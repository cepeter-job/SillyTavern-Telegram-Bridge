"""Opt-in, bounded process memory diagnostics."""

from __future__ import annotations

import json
import logging
import os
import re
import stat
import threading
import tracemalloc
from collections.abc import Callable, Mapping
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path, PureWindowsPath

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
        self._incident_index = 0
        self._last_rss_kib: int | None = None
        self._report_index = self._load_report_index()

    @staticmethod
    def _report_metadata(payload: object) -> dict[str, object] | None:
        if not isinstance(payload, dict):
            return None
        timestamp = payload.get("timestamp_utc")
        rss_kib = payload.get("rss_kib")
        if not isinstance(timestamp, str) or not timestamp or type(rss_kib) is not int or rss_kib < 0:
            return None
        return {"timestamp_utc": timestamp, "rss_kib": rss_kib}

    def _load_report_index(self) -> tuple[tuple[Path, dict[str, object]], ...]:
        diagnostics = self.bridge_home / "diagnostics"
        directory = diagnostics / "memory"
        if diagnostics.is_symlink() or directory.is_symlink() or not directory.is_dir():
            return ()
        indexed: list[tuple[Path, dict[str, object]]] = []
        for path in sorted(directory.glob("memory-*.json"), reverse=True)[:REPORT_RETENTION]:
            if path.is_symlink() or not path.is_file():
                continue
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError, TypeError):
                continue
            metadata = self._report_metadata(payload)
            if metadata is not None:
                indexed.append((path, metadata))
        return tuple(indexed)

    def summary(self) -> dict[str, object]:
        with self._lock:
            latest = dict(self._report_index[0][1]) if self._report_index else None
            return {
                "enabled": self._enabled,
                "state": self._state.value,
                "rss_kib": self._last_rss_kib,
                "thresholds_kib": {
                    "warning": WARNING_THRESHOLD_KIB,
                    "tracing": TRACING_THRESHOLD_KIB,
                    "capture": CAPTURE_THRESHOLD_KIB,
                },
                "report_count": len(self._report_index),
                "latest_incident": latest,
            }

    @staticmethod
    def _sanitize_site_path(raw: object) -> str:
        if not isinstance(raw, str) or not raw:
            return "external:unknown"
        if "\\" in raw:
            name = PureWindowsPath(raw).name or "unknown"
            return f"external:{name[:128]}"
        candidate = Path(raw)
        bridge_root = Path(__file__).resolve().parents[1]
        if candidate.is_absolute():
            try:
                relative = candidate.resolve(strict=False).relative_to(bridge_root)
            except (OSError, ValueError):
                return f"external:{(candidate.name or 'unknown')[:128]}"
            return relative.as_posix()[:256]
        if ".." not in candidate.parts:
            normalized = candidate.as_posix().lstrip("./")
            if normalized:
                return normalized[:256]
        return f"external:{(candidate.name or 'unknown')[:128]}"

    @staticmethod
    def _safe_report_counters(raw: object) -> dict[str, bool | int]:
        if not isinstance(raw, Mapping):
            return {}
        result: dict[str, bool | int] = {}
        for key, value in raw.items():
            if len(result) >= 32:
                break
            if not isinstance(key, str) or re.fullmatch(r"[A-Za-z0-9_.:-]{1,64}", key) is None:
                continue
            if type(value) is bool:
                result[key] = value
            elif type(value) is int and -(2**63) <= value <= 2**63 - 1:
                result[key] = value
        return result

    @classmethod
    def _sanitize_report(cls, payload: object) -> dict[str, object] | None:
        metadata = cls._report_metadata(payload)
        if metadata is None or not isinstance(payload, dict):
            return None
        smaps: dict[str, int] = {}
        raw_smaps = payload.get("smaps_kib")
        if isinstance(raw_smaps, Mapping):
            for key, value in raw_smaps.items():
                if key in _SMAPS_KEYS and type(value) is int and value >= 0:
                    smaps[str(key)] = value
        top_sites: list[dict[str, object]] = []
        raw_sites = payload.get("top_sites")
        if isinstance(raw_sites, list):
            for site in raw_sites[:10]:
                if not isinstance(site, Mapping):
                    continue
                line = site.get("line")
                size = site.get("size_bytes")
                blocks = site.get("blocks")
                if not all(type(value) is int and value >= 0 for value in (line, size, blocks)):
                    continue
                top_sites.append(
                    {
                        "file": cls._sanitize_site_path(site.get("file")),
                        "line": line,
                        "size_bytes": size,
                        "blocks": blocks,
                    }
                )

        def optional_nonnegative_int(key: str) -> int | None:
            value = payload.get(key)
            return value if type(value) is int and value >= 0 else None

        return {
            **metadata,
            "smaps_kib": smaps,
            "traced_current_bytes": optional_nonnegative_int("traced_current_bytes"),
            "traced_peak_bytes": optional_nonnegative_int("traced_peak_bytes"),
            "thread_count": optional_nonnegative_int("thread_count"),
            "safe_counters": cls._safe_report_counters(payload.get("safe_counters")),
            "top_sites": top_sites,
        }

    def recent_reports(self) -> tuple[dict[str, object], ...]:
        with self._lock:
            paths = tuple(path for path, _metadata in self._report_index)
        reports: list[dict[str, object]] = []
        for path in paths:
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError, TypeError):
                logging.warning("Memory diagnostics retained report unavailable")
                continue
            sanitized = self._sanitize_report(payload)
            if sanitized is not None:
                reports.append(sanitized)
        return tuple(reports[:REPORT_RETENTION])

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

    def _safe_counter_values(self) -> dict[str, bool | int]:
        if self.safe_counters is None:
            return {}
        try:
            values = self.safe_counters()
        except Exception:
            logging.warning("Memory diagnostics safe counters unavailable")
            return {}
        if not isinstance(values, Mapping):
            return {}
        result: dict[str, bool | int] = {}
        for key, value in values.items():
            if len(result) >= 32:
                break
            if not isinstance(key, str) or re.fullmatch(r"[A-Za-z0-9_.:-]{1,64}", key) is None:
                continue
            if type(value) is bool:
                result[key] = value
            elif type(value) is int and -(2**63) <= value <= 2**63 - 1:
                result[key] = value
        return result

    def _build_report(self, rss_kib: int, now: datetime) -> dict[str, object]:
        traced_current: int | None = None
        traced_peak: int | None = None
        top_sites: list[dict[str, object]] = []
        if tracemalloc.is_tracing():
            traced_current, traced_peak = tracemalloc.get_traced_memory()
            for statistic in tracemalloc.take_snapshot().statistics("lineno")[:30]:
                frame = statistic.traceback[0]
                top_sites.append(
                    {
                        "file": str(frame.filename),
                        "line": int(frame.lineno),
                        "size_bytes": int(statistic.size),
                        "blocks": int(statistic.count),
                    }
                )
        return {
            "schema_version": 1,
            "timestamp_utc": now.isoformat(timespec="seconds"),
            "pid": os.getpid(),
            "state": self._state.value,
            "rss_kib": int(rss_kib),
            "smaps_kib": _smaps_rollup_kib(),
            "traced_current_bytes": traced_current,
            "traced_peak_bytes": traced_peak,
            "top_sites": top_sites,
            "thread_count": threading.active_count(),
            "safe_counters": self._safe_counter_values(),
        }

    @staticmethod
    def _validate_regular_or_missing(path: Path) -> None:
        if path.is_symlink():
            raise RuntimeError("memory diagnostics report path must not be a symlink")
        if path.exists() and not stat.S_ISREG(path.stat().st_mode):
            raise RuntimeError("memory diagnostics report path must be a regular file")

    def _ensure_report_directory(self, directory: Path) -> None:
        diagnostics = self.bridge_home / "diagnostics"
        for current in (diagnostics, directory):
            if current.is_symlink():
                raise RuntimeError("memory diagnostics report directory must not be a symlink")
            if current.exists() and not current.is_dir():
                raise RuntimeError("memory diagnostics report directory must be a regular directory")
            current.mkdir(parents=True, exist_ok=True, mode=0o700)
            if current.is_symlink() or not current.is_dir():
                raise RuntimeError("memory diagnostics report directory must be a regular directory")
            current.chmod(0o700)

    def _write_report(self, path: Path, report: Mapping[str, object]) -> None:
        directory = path.parent
        self._ensure_report_directory(directory)

        temporary = path.with_name(path.name + ".tmp")
        self._validate_regular_or_missing(path)
        self._validate_regular_or_missing(temporary)
        descriptor = os.open(
            temporary,
            os.O_WRONLY | os.O_CREAT | os.O_TRUNC | getattr(os, "O_NOFOLLOW", 0),
            0o600,
        )
        created = True
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as output:
                json.dump(report, output, separators=(",", ":"), sort_keys=True)
                output.write("\n")
            os.replace(temporary, path)
            created = False
            path.chmod(0o600)
        finally:
            if created and temporary.exists() and not temporary.is_symlink():
                try:
                    temporary.unlink()
                except OSError:
                    pass

    @staticmethod
    def _rotate_reports(directory: Path) -> None:
        reports = sorted(path for path in directory.glob("memory-*.json") if path.is_file() and not path.is_symlink())
        for path in reports[: max(0, len(reports) - REPORT_RETENTION)]:
            try:
                path.unlink()
            except OSError:
                logging.warning("Memory diagnostics report rotation failed")

    def _capture_report(self, rss_kib: int) -> Path:
        self._incident_index += 1
        now = datetime.now(timezone.utc)
        stamp = now.strftime("%Y%m%dT%H%M%SZ")
        directory = self.bridge_home / "diagnostics" / "memory"
        path = directory / f"memory-{stamp}-{os.getpid()}-{self._incident_index:04d}.json"
        report = self._build_report(rss_kib, now)
        self._write_report(path, report)
        self._rotate_reports(directory)
        metadata = self._report_metadata(report)
        if metadata is not None:
            with self._lock:
                previous = tuple(item for item in self._report_index if item[0] != path)
                self._report_index = ((path, metadata), *previous)[:REPORT_RETENTION]
        return path

    def sample_once(self) -> DiagnosticState:
        with self._lock:
            if not self._enabled:
                return self._state
            rss_kib = _resident_memory_kib()
            if rss_kib is None:
                return self._state
            self._last_rss_kib = rss_kib

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
                try:
                    self._capture_report(rss_kib)
                except Exception:
                    logging.warning("Memory diagnostics capture failed")
                finally:
                    self._stop_owned_tracing()
            elif rss_kib >= TRACING_THRESHOLD_KIB:
                self._start_tracing_if_needed()
                self._state = DiagnosticState.TRACING
            elif rss_kib >= WARNING_THRESHOLD_KIB and self._state is DiagnosticState.ARMED:
                self._state = DiagnosticState.WARNED
            return self._state

    def stop(self, timeout: float = 1.0) -> bool:
        self._stop_event.set()
        thread = self._thread
        if thread is not None:
            thread.join(timeout=max(0.0, timeout))
            if thread.is_alive():
                return False
        with self._lock:
            self._thread = None
            self._stop_owned_tracing()
        return True
