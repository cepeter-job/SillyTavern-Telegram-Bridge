"""Low-overhead process memory diagnostics."""

from __future__ import annotations

import json
import os
import stat
import tracemalloc
from pathlib import Path

import pytest

import bridge.memory_diagnostics as memory_diagnostics
from bridge.memory_diagnostics import DiagnosticState, MemoryDiagnostics, _resident_memory_kib, _smaps_rollup_kib


def _diagnostics(tmp_path: Path, *, enabled: bool = True, safe_counters=None) -> MemoryDiagnostics:
    environ = {"SILLYTAVERN_MEMORY_DIAGNOSTICS": "1"} if enabled else {}
    return MemoryDiagnostics(tmp_path / "bridge-home", environ, safe_counters=safe_counters)


def _report_files(diagnostics: MemoryDiagnostics) -> list[Path]:
    return sorted((diagnostics.bridge_home / "diagnostics" / "memory").glob("memory-*.json"))


def test_memory_diagnostics_disabled_by_default(tmp_path):
    diagnostics = _diagnostics(tmp_path, enabled=False)

    assert diagnostics.state is DiagnosticState.DISABLED
    assert diagnostics.start() is False
    assert diagnostics.sample_once() is DiagnosticState.DISABLED


def test_memory_diagnostics_enabled_starts_armed(tmp_path):
    diagnostics = _diagnostics(tmp_path)

    assert diagnostics.state is DiagnosticState.ARMED


def test_rss_reader_parses_vmrss_kib(tmp_path):
    status = tmp_path / "status"
    status.write_text("Name:\tpython\nVmPeak:\t999999 kB\nVmRSS:\t262144 kB\n")

    assert _resident_memory_kib(status) == 262144


@pytest.mark.parametrize(
    "content",
    [
        "Name:\tpython\n",
        "VmRSS:\tgarbage kB\n",
        "VmRSS:\t123 bytes\n",
        "VmRSS:\n",
    ],
)
def test_rss_reader_returns_none_for_malformed_or_missing_vmrss(tmp_path, content):
    status = tmp_path / "status"
    status.write_text(content)

    assert _resident_memory_kib(status) is None


def test_rss_reader_returns_none_when_status_is_unavailable(tmp_path):
    assert _resident_memory_kib(tmp_path / "missing") is None


def test_smaps_rollup_reader_keeps_only_allowlisted_kib_fields(tmp_path):
    smaps = tmp_path / "smaps_rollup"
    smaps.write_text(
        "00400000-7fffffffff ---p 00000000 00:00 0 [rollup]\n"
        "Rss: 400000 kB\n"
        "Pss: 300000 kB\n"
        "Pss_Anon: 250000 kB\n"
        "Private_Clean: 1000 kB\n"
        "Private_Dirty: 200000 kB\n"
        "Anonymous: 240000 kB\n"
        "Swap: 10 kB\n"
        "Locked: 99 kB\n"
        "Shared_Clean: malformed kB\n"
        "Shared_Dirty: 12 MB\n"
    )

    assert _smaps_rollup_kib(smaps) == {
        "Rss": 400000,
        "Pss": 300000,
        "Pss_Anon": 250000,
        "Private_Clean": 1000,
        "Private_Dirty": 200000,
        "Anonymous": 240000,
        "Swap": 10,
    }


def test_smaps_rollup_reader_returns_empty_when_unavailable(tmp_path):
    assert _smaps_rollup_kib(tmp_path / "missing") == {}


def test_threshold_transition_255_mib_stays_armed(tmp_path, monkeypatch):
    diagnostics = _diagnostics(tmp_path)
    monkeypatch.setattr(memory_diagnostics, "_resident_memory_kib", lambda: 255 * 1024)

    assert diagnostics.sample_once() is DiagnosticState.ARMED


def test_threshold_transitions_warn_trace_capture_once(tmp_path, monkeypatch):
    diagnostics = _diagnostics(tmp_path)
    samples = iter([256 * 1024, 320 * 1024, 384 * 1024, 450 * 1024])
    monkeypatch.setattr(memory_diagnostics, "_resident_memory_kib", lambda: next(samples))
    tracemalloc.stop()

    try:
        assert diagnostics.sample_once() is DiagnosticState.WARNED
        assert diagnostics.sample_once() is DiagnosticState.TRACING
        assert tracemalloc.is_tracing()
        assert diagnostics.sample_once() is DiagnosticState.CAPTURED
        assert not tracemalloc.is_tracing()
        assert diagnostics.sample_once() is DiagnosticState.CAPTURED
    finally:
        tracemalloc.stop()


def test_hysteresis_requires_three_consecutive_samples_below_warning(tmp_path, monkeypatch):
    diagnostics = _diagnostics(tmp_path)
    samples = iter([384 * 1024, 255 * 1024, 255 * 1024, 255 * 1024])
    monkeypatch.setattr(memory_diagnostics, "_resident_memory_kib", lambda: next(samples))
    tracemalloc.stop()

    try:
        assert diagnostics.sample_once() is DiagnosticState.CAPTURED
        assert diagnostics.sample_once() is DiagnosticState.CAPTURED
        assert diagnostics.sample_once() is DiagnosticState.CAPTURED
        assert diagnostics.sample_once() is DiagnosticState.ARMED
    finally:
        tracemalloc.stop()


def test_hysteresis_resets_when_memory_rises_to_warning_threshold(tmp_path, monkeypatch):
    diagnostics = _diagnostics(tmp_path)
    samples = iter(
        [
            384 * 1024,
            255 * 1024,
            256 * 1024,
            255 * 1024,
            255 * 1024,
            255 * 1024,
        ]
    )
    monkeypatch.setattr(memory_diagnostics, "_resident_memory_kib", lambda: next(samples))
    tracemalloc.stop()

    try:
        assert diagnostics.sample_once() is DiagnosticState.CAPTURED
        assert diagnostics.sample_once() is DiagnosticState.CAPTURED
        assert diagnostics.sample_once() is DiagnosticState.CAPTURED
        assert diagnostics.sample_once() is DiagnosticState.CAPTURED
        assert diagnostics.sample_once() is DiagnosticState.CAPTURED
        assert diagnostics.sample_once() is DiagnosticState.ARMED
    finally:
        tracemalloc.stop()


def test_threshold_recovery_before_capture_stops_owned_tracing(tmp_path, monkeypatch):
    diagnostics = _diagnostics(tmp_path)
    samples = iter([320 * 1024, 255 * 1024, 255 * 1024, 255 * 1024])
    monkeypatch.setattr(memory_diagnostics, "_resident_memory_kib", lambda: next(samples))
    tracemalloc.stop()

    try:
        assert diagnostics.sample_once() is DiagnosticState.TRACING
        assert tracemalloc.is_tracing()
        assert diagnostics.sample_once() is DiagnosticState.TRACING
        assert diagnostics.sample_once() is DiagnosticState.TRACING
        assert diagnostics.sample_once() is DiagnosticState.ARMED
        assert not tracemalloc.is_tracing()
    finally:
        tracemalloc.stop()


def test_preexisting_tracemalloc_is_never_stopped(tmp_path, monkeypatch):
    diagnostics = _diagnostics(tmp_path)
    monkeypatch.setattr(memory_diagnostics, "_resident_memory_kib", lambda: 384 * 1024)
    tracemalloc.stop()
    tracemalloc.start(1)

    try:
        assert diagnostics.sample_once() is DiagnosticState.CAPTURED
        assert tracemalloc.is_tracing()
        assert diagnostics.stop() is True
        assert tracemalloc.is_tracing()
    finally:
        tracemalloc.stop()


class _FakeThread:
    def __init__(self, *, target, name, daemon):
        self.target = target
        self.name = name
        self.daemon = daemon
        self.started = False
        self.join_timeout = None
        self.alive = True

    def start(self):
        self.started = True

    def join(self, timeout=None):
        self.join_timeout = timeout
        self.alive = False

    def is_alive(self):
        return self.alive


def test_start_launches_one_daemon_sampler_and_stop_joins(tmp_path, monkeypatch):
    created = []
    monkeypatch.setattr(
        memory_diagnostics.threading,
        "Thread",
        lambda **kwargs: created.append(_FakeThread(**kwargs)) or created[-1],
    )
    diagnostics = _diagnostics(tmp_path)

    assert diagnostics.start() is True
    assert diagnostics.start() is False
    assert len(created) == 1
    assert created[0].started is True
    assert created[0].daemon is True
    assert created[0].name == "memory-diagnostics"
    assert diagnostics.stop(timeout=0.25) is True
    assert created[0].join_timeout == 0.25


def test_sampler_waits_before_first_sample(tmp_path, monkeypatch):
    diagnostics = _diagnostics(tmp_path)
    waits = []

    class StopAfterFirstWait:
        def wait(self, timeout):
            waits.append(timeout)
            return True

        def set(self):
            pass

    diagnostics._stop_event = StopAfterFirstWait()
    monkeypatch.setattr(
        diagnostics,
        "sample_once",
        lambda: pytest.fail("sampler must wait before the first sample"),
    )

    diagnostics._run_sampler()

    assert waits == [memory_diagnostics.SAMPLE_INTERVAL_SECONDS]


def test_report_privacy_contains_only_allocation_site_aggregates(tmp_path, monkeypatch):
    diagnostics = _diagnostics(tmp_path)
    samples = iter([320 * 1024, 384 * 1024])
    monkeypatch.setattr(memory_diagnostics, "_resident_memory_kib", lambda: next(samples))
    monkeypatch.setattr(
        memory_diagnostics,
        "_smaps_rollup_kib",
        lambda: {"Rss": 384 * 1024, "Private_Dirty": 300 * 1024},
    )
    tracemalloc.stop()

    try:
        assert diagnostics.sample_once() is DiagnosticState.TRACING
        private_value = "memory-diagnostics-must-not-serialize-object-values-7d9f"
        retained = [f"{private_value}-{index}" for index in range(2000)]
        assert diagnostics.sample_once() is DiagnosticState.CAPTURED

        reports = _report_files(diagnostics)
        assert len(reports) == 1
        raw = reports[0].read_text()
        report = json.loads(raw)
        assert private_value not in raw
        assert report["schema_version"] == 1
        assert report["rss_kib"] == 384 * 1024
        assert report["smaps_kib"]["Private_Dirty"] == 300 * 1024
        assert report["top_sites"]
        assert all(set(site) == {"file", "line", "size_bytes", "blocks"} for site in report["top_sites"])
        assert retained[0].endswith("-0")
    finally:
        tracemalloc.stop()


def test_safe_counters_allow_only_bounded_scalars(tmp_path, monkeypatch):
    counters = {
        "flag": True,
        "signed_min": -(2**63),
        "signed_max": 2**63 - 1,
        "secret_text": "never serialize",
        "float_value": 1.5,
        "list_value": [1],
        "bad key": 7,
        "too_large": 2**63,
        "too_small": -(2**63) - 1,
    }
    counters.update({f"counter_{index}": index for index in range(40)})
    diagnostics = _diagnostics(tmp_path, safe_counters=lambda: counters)
    monkeypatch.setattr(memory_diagnostics, "_resident_memory_kib", lambda: 384 * 1024)
    tracemalloc.stop()

    try:
        assert diagnostics.sample_once() is DiagnosticState.CAPTURED
        report = json.loads(_report_files(diagnostics)[0].read_text())
        safe = report["safe_counters"]
        assert len(safe) == 32
        assert safe["flag"] is True
        assert safe["signed_min"] == -(2**63)
        assert safe["signed_max"] == 2**63 - 1
        assert "secret_text" not in safe
        assert "float_value" not in safe
        assert "list_value" not in safe
        assert "bad key" not in safe
        assert "too_large" not in safe
        assert "too_small" not in safe
    finally:
        tracemalloc.stop()


def test_safe_counter_failure_is_redacted_and_nonfatal(tmp_path, monkeypatch, caplog):
    calls = []

    def broken_counters():
        calls.append(True)
        raise RuntimeError("PRIVATE_COUNTER_SECRET")

    diagnostics = _diagnostics(tmp_path, safe_counters=broken_counters)
    monkeypatch.setattr(memory_diagnostics, "_resident_memory_kib", lambda: 384 * 1024)
    tracemalloc.stop()

    try:
        with caplog.at_level("WARNING"):
            assert diagnostics.sample_once() is DiagnosticState.CAPTURED
        report = json.loads(_report_files(diagnostics)[0].read_text())
        assert calls == [True]
        assert report["safe_counters"] == {}
        assert "Memory diagnostics safe counters unavailable" in caplog.text
        assert "PRIVATE_COUNTER_SECRET" not in caplog.text
    finally:
        tracemalloc.stop()


def test_report_write_is_private_atomic_and_cleans_temp(tmp_path, monkeypatch):
    diagnostics = _diagnostics(tmp_path)
    path = diagnostics.bridge_home / "diagnostics" / "memory" / "memory-test.json"
    real_replace = os.replace
    replacements = []

    def record_replace(source, destination):
        replacements.append((Path(source), Path(destination)))
        real_replace(source, destination)

    monkeypatch.setattr(memory_diagnostics.os, "replace", record_replace)
    diagnostics._write_report(path, {"schema_version": 1})

    assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert json.loads(path.read_text()) == {"schema_version": 1}
    assert replacements == [(path.with_name(path.name + ".tmp"), path)]
    assert not path.with_name(path.name + ".tmp").exists()


def test_report_write_rejects_symlink_destination(tmp_path):
    diagnostics = _diagnostics(tmp_path)
    directory = diagnostics.bridge_home / "diagnostics" / "memory"
    directory.mkdir(parents=True)
    real = directory / "real.json"
    real.write_text("original")
    path = directory / "memory-test.json"
    path.symlink_to(real)

    with pytest.raises(RuntimeError, match=r"symlink|regular"):
        diagnostics._write_report(path, {"schema_version": 1})

    assert real.read_text() == "original"


def test_report_write_rejects_symlink_temp_path(tmp_path):
    diagnostics = _diagnostics(tmp_path)
    directory = diagnostics.bridge_home / "diagnostics" / "memory"
    directory.mkdir(parents=True)
    path = directory / "memory-test.json"
    temp = path.with_name(path.name + ".tmp")
    real = directory / "real.tmp"
    real.write_text("original")
    temp.symlink_to(real)

    with pytest.raises(RuntimeError, match=r"symlink|regular"):
        diagnostics._write_report(path, {"schema_version": 1})

    assert real.read_text() == "original"


def test_report_write_rejects_non_regular_destination(tmp_path):
    diagnostics = _diagnostics(tmp_path)
    path = diagnostics.bridge_home / "diagnostics" / "memory" / "memory-test.json"
    path.mkdir(parents=True)

    with pytest.raises(RuntimeError, match="regular"):
        diagnostics._write_report(path, {"schema_version": 1})


def test_report_rotation_retains_newest_three(tmp_path):
    diagnostics = _diagnostics(tmp_path)

    paths = [diagnostics._capture_report(384 * 1024) for _ in range(4)]

    reports = _report_files(diagnostics)
    assert len(reports) == memory_diagnostics.REPORT_RETENTION
    assert paths[0] not in reports
    assert reports == paths[1:]


def test_report_rotation_failure_preserves_new_capture(tmp_path, monkeypatch, caplog):
    diagnostics = _diagnostics(tmp_path)
    directory = diagnostics.bridge_home / "diagnostics" / "memory"
    directory.mkdir(parents=True)
    old_paths = []
    for index in range(3):
        path = directory / f"memory-20000101T00000{index}Z-1-000{index}.json"
        diagnostics._write_report(path, {"schema_version": 1})
        old_paths.append(path)

    real_unlink = Path.unlink

    def fail_oldest(path, *args, **kwargs):
        if path == old_paths[0]:
            raise OSError("PRIVATE_ROTATION_DETAIL")
        return real_unlink(path, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", fail_oldest)
    with caplog.at_level("WARNING"):
        newest = diagnostics._capture_report(384 * 1024)

    assert newest.exists()
    assert "Memory diagnostics report rotation failed" in caplog.text
    assert "PRIVATE_ROTATION_DETAIL" not in caplog.text


def test_capture_failure_is_nonfatal_and_stops_owned_tracing(tmp_path, monkeypatch, caplog):
    diagnostics = _diagnostics(tmp_path)
    monkeypatch.setattr(memory_diagnostics, "_resident_memory_kib", lambda: 384 * 1024)

    def fail_capture(_rss_kib):
        raise RuntimeError("PRIVATE_CAPTURE_DETAIL")

    monkeypatch.setattr(diagnostics, "_capture_report", fail_capture)
    tracemalloc.stop()
    try:
        with caplog.at_level("WARNING"):
            assert diagnostics.sample_once() is DiagnosticState.CAPTURED
        assert not tracemalloc.is_tracing()
        assert "Memory diagnostics capture failed" in caplog.text
        assert "PRIVATE_CAPTURE_DETAIL" not in caplog.text
    finally:
        tracemalloc.stop()


def test_report_write_rejects_symlinked_diagnostics_ancestor(tmp_path):
    diagnostics = _diagnostics(tmp_path)
    diagnostics.bridge_home.mkdir(parents=True)
    outside = tmp_path / "outside"
    outside.mkdir()
    diagnostics_dir = diagnostics.bridge_home / "diagnostics"
    diagnostics_dir.symlink_to(outside, target_is_directory=True)
    path = diagnostics_dir / "memory" / "memory-test.json"

    with pytest.raises(RuntimeError, match=r"symlink|directory"):
        diagnostics._write_report(path, {"schema_version": 1})

    assert not (outside / "memory" / "memory-test.json").exists()


def test_stop_timeout_does_not_wait_for_sampler_lock(tmp_path):
    diagnostics = _diagnostics(tmp_path)

    class AliveThread:
        def __init__(self):
            self.join_timeout = None

        def join(self, timeout=None):
            self.join_timeout = timeout

        def is_alive(self):
            return True

    class ForbiddenLock:
        def __enter__(self):
            raise AssertionError("stop must not wait for sampler lock after timeout")

        def __exit__(self, *_args):
            return False

    thread = AliveThread()
    diagnostics._thread = thread
    diagnostics._lock = ForbiddenLock()

    assert diagnostics.stop(timeout=0.125) is False
    assert thread.join_timeout == 0.125
