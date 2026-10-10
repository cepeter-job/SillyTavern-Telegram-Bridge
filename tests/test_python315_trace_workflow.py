"""Keep manual Python 3.15 leak tracing targeted and distinct from the full gate."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

_WORKFLOW = Path(__file__).resolve().parents[1] / ".github/workflows/python315-experimental.yml"


def test_python315_experiment_runs_only_on_manual_dispatch():
    workflow = yaml.safe_load(_WORKFLOW.read_text(encoding="utf-8"))
    # PyYAML's YAML 1.1 loader resolves the bare GitHub Actions `on` key as True.
    assert set(workflow[True]) == {"workflow_dispatch"}


def _test_steps() -> tuple[dict, dict]:
    workflow = yaml.safe_load(_WORKFLOW.read_text(encoding="utf-8"))
    steps = workflow["jobs"]["stable-final-core-regression"]["steps"]
    by_id = {step["id"]: step for step in steps if "id" in step}
    return by_id["core-suite"], by_id["resource-trace"]


def test_full_core_suite_does_not_inherit_allocation_tracing():
    full, traced = _test_steps()
    assert full["if"] == "${{ !inputs.trace_resource_allocations }}"
    assert traced["if"] == "${{ inputs.trace_resource_allocations }}"
    assert "-n auto --maxprocesses=4" in full["run"]
    assert "--cov=bridge" in full["run"]
    assert "PYTHONTRACEMALLOC" not in full["run"]
    assert "ResourceWarning" in full["run"]
    assert "-n 0" in traced["run"]
    assert "PYTHONTRACEMALLOC=8" in traced["run"]
    assert "ResourceWarning" in traced["run"]
    assert "TRACE_TEST_FILES" in traced["env"]


@pytest.mark.skipif(shutil.which("bash") is None, reason="GitHub Actions tracing runs under bash")
@pytest.mark.parametrize(
    ("selection", "accepted"),
    [
        ("tests/test_expressions.py", True),
        ("tests/test_expressions.py tests/test_npc_generation_wiring.py", True),
        ("tests/test_expressions.py;touch evil", False),
        ("../../tests/test_expressions.py", False),
        ("--collect-only", False),
        ("tests/test_expressions.py\necho evil", False),
        ("", False),
        ("tests/test_expressions.py " * 5, False),
    ],
)
def test_trace_workflow_rejects_unsafe_test_selectors(tmp_path, selection, accepted):
    _full, traced = _test_steps()
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests/test_expressions.py").touch()
    (tmp_path / "tests/test_npc_generation_wiring.py").touch()
    runner = tmp_path / ".venv315-stable-core/bin/python"
    runner.parent.mkdir(parents=True)
    runner.write_text(
        '#!/bin/sh\nprintf "TRACE=%s ARGS=%s\\n" "$PYTHONTRACEMALLOC" "$*" > "$TRACE_CAPTURE_FILE"\n',
        encoding="utf-8",
    )
    runner.chmod(0o755)
    marker = tmp_path / "called"
    bash = shutil.which("bash")
    assert bash is not None
    result = subprocess.run(
        [bash, "-c", traced["run"]],
        cwd=tmp_path,
        env={**os.environ, "TRACE_TEST_FILES": selection, "TRACE_CAPTURE_FILE": str(marker)},
        check=False,
        capture_output=True,
        text=True,
        timeout=12,
    )
    if accepted:
        assert result.returncode == 0, result.stdout + result.stderr
        output = marker.read_text(encoding="utf-8")
        assert output.startswith("TRACE=8 ARGS=")
        assert " -n 0 " in output
    else:
        assert result.returncode != 0
        assert not marker.exists()
    assert not (tmp_path / "evil").exists()
