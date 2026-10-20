"""Exercise the hosted candidate workflow's strict subprocess and outcome contracts."""

from __future__ import annotations

import json
import os
import re
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github/workflows/python314-candidate-pr500.yml"


def workflow():
    return yaml.safe_load(WORKFLOW.read_text())


def job():
    return workflow()["jobs"]["candidate-validation"]


def step(step_id):
    return next(item for item in job()["steps"] if item.get("id") == step_id)


def test_candidate_runs_only_on_hosted_scoped_pull_request_with_immutable_checkout():
    data = workflow()
    triggers = data.get("on", data.get(True))
    assert set(triggers) == {"pull_request"}
    assert triggers["pull_request"]["branches"] == ["main"]
    assert data["permissions"] == {"contents": "read"}
    assert job()["runs-on"] == "ubuntu-24.04"
    condition = job()["if"]
    for required in [
        "github.event.pull_request.number == 500",
        "github.actor == 'cepeter'",
        "github.event.pull_request.head.repo.id == 1369500317",
        "github.event.pull_request.head.ref == 'feat/python314-migration-20261010'",
        "github.event.pull_request.base.ref == 'main'",
    ]:
        assert required in condition
    checkout = job()["steps"][0]
    assert checkout["with"]["ref"] == "${{ github.event.pull_request.head.sha }}"
    assert checkout["with"]["persist-credentials"] is False
    assert "pull_request_target" not in triggers
    assert "secrets." not in WORKFLOW.read_text()
    for item in job()["steps"]:
        if "uses" in item:
            assert re.fullmatch(r"[^@]+@[a-f0-9]{40}", item["uses"])


def test_native_failure_does_not_suppress_independent_evidence_or_final_gate():
    assert step("native_probe")["continue-on-error"] is True
    assert step("native_smoke")["continue-on-error"] is True
    for name in ["native_smoke", "full_regression", "security_coverage", "native_logs"]:
        assert step(name)["if"] == "${{ !cancelled() && steps.candidate_install.outcome == 'success' }}"
    assert step("freshness")["if"] == "${{ always() && steps.source.outcome == 'success' }}"
    final = next(item for item in job()["steps"] if item["name"] == "Require every original gate outcome to succeed")
    assert final["if"] == "${{ always() }}"
    for value in final["env"].values():
        assert value.endswith(".outcome }}")
    assert "--cov-fail-under=76" in step("full_regression")["run"]
    assert "--dist=loadfile" in step("full_regression")["run"]
    assert "-n 0" in step("native_smoke")["run"]


@pytest.mark.skipif(shutil.which("bash") is None, reason="Workflow uses bash")
@pytest.mark.parametrize("step_id", ["native_smoke", "full_regression"])
def test_actual_shell_preamble_makes_unflagged_children_strict(step_id):
    preamble = step(step_id)["run"].split(".venv314-candidate/bin/python -X dev", 1)[0]
    probe = (
        "import json,os,sys,warnings\n"
        "try:\n warnings.warn('probe',ResourceWarning)\n"
        "except ResourceWarning:\n strict=True\n"
        "else:\n strict=False\n"
        "print(json.dumps({'dev':sys.flags.dev_mode,'strict':strict,'overlay':os.getenv('PYTHONPATH')}))\n"
    )
    result = subprocess.run(
        [shutil.which("bash"), "-c", preamble + shlex.quote(sys.executable) + " -c " + shlex.quote(probe)],
        env=dict(os.environ, PYTHONWARNINGS="ignore", PYTHONMALLOC="malloc", PYTHONPATH="/untrusted/overlay"),
        capture_output=True,
        text=True,
        timeout=10,
        check=True,
    )
    assert json.loads(result.stdout) == {"dev": True, "strict": True, "overlay": None}


@pytest.mark.skipif(shutil.which("bash") is None, reason="Workflow uses bash")
def test_actual_final_gate_rejects_any_missing_skipped_or_failed_outcome():
    final = next(item for item in job()["steps"] if item["name"] == "Require every original gate outcome to succeed")
    env = {name: "success" for name in final["env"]}
    result = subprocess.run([shutil.which("bash"), "-c", final["run"]], env=env, capture_output=True, check=False)
    assert result.returncode == 0
    for key in env:
        for bad in ["failure", "cancelled", "skipped", ""]:
            result = subprocess.run(
                [shutil.which("bash"), "-c", final["run"]],
                env=dict(env, **{key: bad}),
                capture_output=True,
                check=False,
            )
            assert result.returncode != 0, (key, bad)
