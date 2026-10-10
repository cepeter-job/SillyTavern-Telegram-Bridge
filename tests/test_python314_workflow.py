"""The Python 3.14 migration keeps strict full-suite and focused diagnostic paths separate."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
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


def test_full_regression_environment_reaches_xdist_workers_and_children(tmp_path):
    full, _ = _steps()
    worker_test = tmp_path / "test_strict_worker.py"
    worker_test.write_text(
        "import subprocess, sys, warnings\n"
        "import pytest\n"
        "def test_strict_runtime_is_inherited():\n"
        "    assert sys.flags.dev_mode\n"
        "    with pytest.raises(ResourceWarning):\n"
        "        warnings.warn('strict-warning-probe', ResourceWarning)\n"
        "    child = subprocess.run([sys.executable, '-c', "
        '"import sys, warnings; assert sys.flags.dev_mode; '
        "warnings.warn('strict-warning-probe', ResourceWarning)\"], "
        "capture_output=True, text=True, timeout=10, check=False)\n"
        "    assert child.returncode != 0\n"
        "    assert 'ResourceWarning: strict-warning-probe' in child.stderr\n"
        "    assert 'AssertionError' not in child.stderr\n",
        encoding="utf-8",
    )
    environment = {
        key: value
        for key, value in os.environ.items()
        if key not in {"PYTHONDEVMODE", "PYTHONWARNINGS", "PYTEST_ADDOPTS"}
    }
    environment.update(full.get("env", {}))
    environment["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] = "1"
    # CLI flags reach the coordinator; the job environment must reach workers
    # and their own subprocesses without depending on inherited host settings.
    result = subprocess.run(
        [
            sys.executable,
            "-X",
            "dev",
            "-W",
            "error::ResourceWarning",
            "-m",
            "pytest",
            "-p",
            "xdist.plugin",
            "-p",
            "no:cacheprovider",
            "-q",
            "-n",
            "1",
            str(worker_test),
        ],
        cwd=tmp_path,
        env=environment,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "1 passed" in result.stdout


@pytest.mark.skipif(shutil.which("bash") is None, reason="GitHub Actions uses bash")
@pytest.mark.parametrize(
    ("pytest_output", "pytest_exit", "expected_exit"),
    [
        pytest.param("5208 passed", 0, 0, id="normal"),
        pytest.param("DeprecationWarning: process is multi-threaded", 0, 0, id="ordinary-warning"),
        pytest.param("Fatal Python error: Aborted", 0, 1, id="fatal-python"),
        pytest.param("_PyMem_DebugRawFree: bad ID", 0, 1, id="allocator-mismatch"),
        pytest.param("Segmentation fault", 0, 1, id="segfault"),
        pytest.param("Aborted (core dumped)", 0, 1, id="native-abort"),
        pytest.param("no tests ran", 5, 5, id="pytest-failure"),
    ],
)
def test_full_regression_rejects_hidden_native_crashes(tmp_path, pytest_output, pytest_exit, expected_exit):
    full, _ = _steps()
    command = next(s["run"] for s in full["steps"] if "full Python 3.14 strict regression" in s["name"])
    fake_python = tmp_path / "python"
    fake_python.write_text(
        "#!/bin/sh\n"
        'if [ "$1" = "-X" ]; then\n'
        '  printf "%s\\n" "$FAKE_PYTEST_OUTPUT"\n'
        '  exit "$FAKE_PYTEST_EXIT"\n'
        "fi\n"
        ': > "$COVERAGE_MARKER"\n',
        encoding="utf-8",
    )
    fake_python.chmod(0o755)
    coverage_marker = tmp_path / "coverage_checked"
    result = subprocess.run(
        [shutil.which("bash"), "-c", command],
        cwd=tmp_path,
        env={
            **os.environ,
            "PATH": f"{tmp_path}:{os.environ.get('PATH', '')}",
            "FAKE_PYTEST_OUTPUT": pytest_output,
            "FAKE_PYTEST_EXIT": str(pytest_exit),
            "COVERAGE_MARKER": str(coverage_marker),
        },
        capture_output=True,
        text=True,
        timeout=12,
        check=False,
    )
    assert result.returncode == expected_exit, result.stdout + result.stderr
    assert (tmp_path / "python314-pytest.log").read_text() == pytest_output + "\n"
    assert coverage_marker.exists() == (expected_exit == 0)


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
    assert "github.event_name" in data["concurrency"]["group"]
    assert "github.ref" in data["concurrency"]["group"]
    debug = jobs["native-debugger"]
    assert debug["if"] == "${{ inputs.native_debug_backtrace }}"
    assert jobs["voice-enabled-native-preflight"]["if"].startswith("${{ !inputs.native_debug_backtrace")
    assert "gdb" in str(debug["steps"])
    assert "native-finalizer-backtrace" in str(debug["steps"])
    assert "pybind11==3.0.1" not in str(debug["steps"])  # Version is passed as a bound loop variable.
    assert "for version in 2.11.1 2.13.6 3.0.1" in str(debug["steps"])
    assert "ctranslate2-upstream" in str(debug["steps"])
    assert "d44d2d069eb88c7b7804da864c10c201501cb4a9" in str(debug["steps"])
    assert "LD_LIBRARY_PATH" in str(debug["steps"])
    assert "requirements-dev-py314.lock" in str(debug["steps"])
    assert '"ctranslate2-upstream/python" in ext.__file__' in str(debug["steps"])
    assert "continue-on-error" not in str(jobs["full-regression"])
