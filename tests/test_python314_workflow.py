"""The Python 3.14 migration keeps strict full-suite and focused diagnostic paths separate."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).parents[1]
WORKFLOW = ROOT / ".github/workflows/python314-compatibility.yml"


def _steps():
    data = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    return data["jobs"]["full-regression"], data["jobs"]["resource-owner-trace"]


def test_python314_compatibility_has_real_full_suite_without_warning_suppression():
    full, trace = _steps()
    assert full["if"] == "${{ !inputs.trace_resource_allocations && !inputs.native_debug_backtrace }}"
    assert trace["if"] == "${{ inputs.trace_resource_allocations && !inputs.native_debug_backtrace }}"
    full_run = next(s["run"] for s in full["steps"] if "full Python 3.14 strict regression" in s["name"])
    trace_run = next(s["run"] for s in trace["steps"] if s.get("id") == "trace")
    assert "-W error::ResourceWarning" in full_run
    assert "--cov-fail-under=76" in full_run
    assert "--maxprocesses=4" in full_run
    assert "PYTHONTRACEMALLOC" not in full_run
    assert "-W error::ResourceWarning" in trace_run
    assert "PYTHONTRACEMALLOC=6" in trace_run
    assert " -n 0 " in trace_run


@pytest.mark.skipif(shutil.which("bash") is None, reason="GitHub Actions uses bash")
@pytest.mark.parametrize(
    ("selection", "accepted"),
    [
        ("tests/test_transcript_delivery_recovery.py", True),
        ("tests/test_transcript_delivery_recovery.py tests/test_sync_ui.py", True),
        ("tests/test_transcript_delivery_recovery.py;touch unsafe", False),
        ("--help", False),
        ("../../tests/test_sync_ui.py", False),
        ("tests/test_sync_ui.py\necho unsafe", False),
        ("tests/test_sync_ui.py " * 5, False),
        ("", False),
    ],
)
def test_trace_selector_rejects_untrusted_or_excessive_inputs(tmp_path, selection, accepted):
    _, trace = _steps()
    bash = shutil.which("bash")
    assert bash
    (tmp_path / "tests").mkdir()
    for name in ("test_transcript_delivery_recovery.py", "test_sync_ui.py"):
        (tmp_path / "tests" / name).touch()
    fake_python = tmp_path / "python"
    fake_python.write_text(
        '#!/bin/sh\nprintf "TRACE=%s ARGS=%s\\n" "$PYTHONTRACEMALLOC" "$*" > "$TRACE_RESULT"\n',
        encoding="utf-8",
    )
    fake_python.chmod(0o755)
    marker = tmp_path / "trace_result"
    step = next(s for s in trace["steps"] if s.get("id") == "trace")
    proc = subprocess.run(
        [bash, "-c", step["run"]],
        cwd=tmp_path,
        env={
            **os.environ,
            "PATH": f"{tmp_path}:{os.environ.get('PATH', '')}",
            "TRACE_TEST_FILES": selection,
            "TRACE_RESULT": str(marker),
            "GITHUB_STEP_SUMMARY": str(tmp_path / "summary"),
        },
        capture_output=True,
        text=True,
        timeout=12,
        check=False,
    )
    if accepted:
        assert proc.returncode == 0, proc.stdout + proc.stderr
        observed = marker.read_text(encoding="utf-8")
        assert observed.startswith("TRACE=6 ARGS=")
        assert " -n 0 " in observed
    else:
        assert proc.returncode != 0
        assert not marker.exists()
    assert not (tmp_path / "unsafe").exists()


def test_manual_gdb_job_requires_explicit_debug_dispatch():
    data = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    jobs = data["jobs"]
    debug = jobs["native-debugger"]
    assert debug["if"] == "${{ inputs.native_debug_backtrace }}"
    assert jobs["voice-enabled-native-preflight"]["if"].startswith("${{ !inputs.native_debug_backtrace")
    assert "gdb" in str(debug["steps"])
    assert "native-finalizer-backtrace" in str(debug["steps"])
    assert "pybind11==3.0.1" not in str(debug["steps"])  # Version is passed as a bound loop variable.
    assert "for version in 2.11.1 2.13.6 3.0.1" in str(debug["steps"])
    assert "continue-on-error" not in str(jobs["full-regression"])
