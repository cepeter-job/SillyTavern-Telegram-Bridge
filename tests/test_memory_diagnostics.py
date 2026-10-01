"""Low-overhead process memory diagnostics."""

from __future__ import annotations

import tracemalloc
from pathlib import Path

import pytest

import bridge.memory_diagnostics as memory_diagnostics
from bridge.memory_diagnostics import DiagnosticState, MemoryDiagnostics, _resident_memory_kib, _smaps_rollup_kib


def _diagnostics(tmp_path: Path, *, enabled: bool = True) -> MemoryDiagnostics:
    environ = {"SILLYTAVERN_MEMORY_DIAGNOSTICS": "1"} if enabled else {}
    return MemoryDiagnostics(tmp_path / "bridge-home", environ)


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
