"""The required CI check must reject incomplete or unsuccessful dependency reports."""

from __future__ import annotations

import ast
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[1]
JOBS = ("python-tests", "miniapp-smoke", "secret-scan", "dependency-audit", "static-analysis")


def _run_gate(tmp_path, payload):
    summary = tmp_path / "summary.md"
    summary.write_text("Existing summary\n", encoding="utf-8")
    env = {**os.environ, "GITHUB_STEP_SUMMARY": str(summary)}
    if payload is None:
        env.pop("CI_NEEDS", None)
    else:
        env["CI_NEEDS"] = payload
    completed = subprocess.run(
        [sys.executable, str(ROOT / "tools/ci_gate.py")],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    return completed, summary.read_text(encoding="utf-8")


def test_all_checks_must_succeed_and_append_a_readable_summary(tmp_path):
    completed, summary = _run_gate(tmp_path, json.dumps({job: {"result": "success"} for job in JOBS}))
    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert summary.startswith("Existing summary\n")
    for job in JOBS:
        assert f"| {job} | success |" in summary
    assert "::error::" not in completed.stdout


@pytest.mark.parametrize("job", JOBS)
def test_missing_check_cannot_make_the_required_gate_green(tmp_path, job):
    needs = {name: {"result": "success"} for name in JOBS if name != job}
    completed, summary = _run_gate(tmp_path, json.dumps(needs))
    assert completed.returncode == 1
    assert f"| {job} | missing |" in summary
    assert "::error::" in completed.stdout


@pytest.mark.parametrize("result", ["failure", "cancelled", "skipped", "unknown", None])
def test_unsuccessful_or_invalid_result_cannot_make_the_required_gate_green(tmp_path, result):
    needs = {job: {"result": "success"} for job in JOBS}
    needs["miniapp-smoke"] = {"result": result}
    completed, summary = _run_gate(tmp_path, json.dumps(needs))
    assert completed.returncode == 1
    assert "| miniapp-smoke | success |" not in summary
    assert "::error::" in completed.stdout


@pytest.mark.parametrize("payload", [None, "{", "[]", "null", "{}"])
def test_absent_or_malformed_report_fails_closed(tmp_path, payload):
    completed, _summary = _run_gate(tmp_path, payload)
    assert completed.returncode == 1
    assert "::error::" in completed.stdout


def test_unexpected_or_incomplete_job_report_fails_closed(tmp_path):
    needs = {job: {"result": "success"} for job in JOBS}
    needs["unreviewed-check"] = {"result": "success"}
    completed, _summary = _run_gate(tmp_path, json.dumps(needs))
    assert completed.returncode == 1
    needs.pop("unreviewed-check")
    needs["python-tests"] = {}
    completed, summary = _run_gate(tmp_path, json.dumps(needs))
    assert completed.returncode == 1
    assert "| python-tests | missing |" in summary


def test_reviewed_test_consolidation_does_not_fragment_again():
    retired = {
        "test_final_budget_routes.py",
        "test_late_budget_delivery.py",
        "test_memory_failure_backoff.py",
        "test_native_message_edit.py",
        "test_optional_numeric_acceleration.py",
        "test_pdf_worker.py",
        "test_pr169_followup.py",
        "test_codex_review_regressions.py",
        "test_session_command_routing.py",
        "test_simulation_review_aliases.py",
        "test_simulation_review_budget.py",
        "test_simulation_review_history.py",
        "test_simulation_review_types.py",
        "test_swipe_panels.py",
        "test_telegram_preview.py",
        "test_voice_conversation_boundary.py",
    }
    assert not {path.name for path in (ROOT / "tests").glob("test_*.py")} & retired

    forbidden = {
        ("test_character_mutation_safety", "_card_png"),
        ("test_character_upload_confirmation", "_card_png"),
        ("test_codex_auth", "_jwt"),
        ("test_codex_transport", "_StreamingResponse"),
        ("test_npc_branch_safety", "_fields"),
        ("test_npc_branch_safety", "_session"),
        ("test_provider_attempt_budget", "setup_route"),
    }
    observed = set()
    for path in (ROOT / "tests").glob("test_*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                observed.update((node.module, alias.name) for alias in node.names)
    assert not observed & forbidden
