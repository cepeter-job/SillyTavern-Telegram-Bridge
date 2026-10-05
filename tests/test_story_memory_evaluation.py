"""Standalone evaluation accepts real turns and detects broken contracts."""

import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
CLI = ROOT / "tools/evaluate_story_memory.py"


def run_cli(cwd, *args):
    return subprocess.run(
        [sys.executable, str(CLI), "--repeats", "3", *args],
        cwd=cwd,
        text=True,
        capture_output=True,
        timeout=180,
        check=False,
    )


@pytest.fixture(scope="module")
def successful_report(tmp_path_factory):
    isolated = tmp_path_factory.mktemp("standalone-eval")
    output = tmp_path_factory.mktemp("evaluation-report") / "measured.json"
    result = run_cli(isolated, "--repeats", "10", "--output", str(output))
    report = json.loads(result.stdout)
    failures = [case["case_id"] for case in report.get("cases", []) if not case["passed"]]
    assert result.returncode == 0, {
        "failed_cases": failures,
        "fatal_error": report.get("fatal_error"),
        "stderr": result.stderr[:3000],
    }
    assert list(isolated.iterdir()) == []
    assert json.loads(output.read_text()) == report
    print("MEASURED_REPORT_PATH=" + str(output), flush=True)
    return report


def test_standalone_evaluation_contract(successful_report):
    report = successful_report
    assert report["schema_version"] == 1
    assert report["failed_case_count"] == 0
    assert report["counts"]["target_later_message_count"] > 100
    assert report["counts"]["target_later_episode_count"] > 200
    assert report["counts"]["distinct_accepted_episode_count"] >= 300
    assert report["unexpected_network_attempt_count"] == 0
    assert report["passed_case_count"] == report["executed_case_count"]
    assert (
        report["fixture_sha256"]
        == hashlib.sha256((ROOT / "tests/fixtures/story_memory/v1.json").read_bytes()).hexdigest()
    )


def test_actual_canonical_evidence_and_measurement_labels(successful_report):
    report = successful_report
    mapping = report["fact_mapping"]["f0005"]
    assert len(mapping) == 1
    pointer = mapping[0]["evidence"]
    assert pointer["source_document_id"]
    assert pointer["source_start_rowid"] == report["message_mapping"]["main:m0005"]["row_id"]
    assert pointer["start_offset"] <= mapping[0]["annotated_start_char"]
    assert pointer["end_offset"] >= mapping[0]["annotated_end_char"]
    cases = {case["case_id"]: case for case in report["cases"]}
    assert cases["acceptance.primary"]["derived_rows_seeded"] == 0
    assert cases["coverage.complete-long-source"]["covered_chars"] > 150000
    assert cases["retry.remote-success-lost-ack"]["passed"]
    assert cases["purge.floor"]["passed"]
    assert report["metrics"]["query_sample_count"] == 10
    assert report["metrics"]["query_warmups"] == 1
    assert report["metrics"]["python_tracemalloc_peak_bytes"] > 0
    assert report["metrics"]["process_rss_bytes"] is None
    assert report["metrics"]["sqlite_page_bytes"] > 0


def test_injected_missing_tail_fails_real_runner_and_serializes(tmp_path):
    output = tmp_path / "failure.json"
    result = run_cli(tmp_path, "--inject-failure", "drop-tail", "--output", str(output))
    assert result.returncode == 1, result.stderr + result.stdout
    report = json.loads(result.stdout)
    assert json.loads(output.read_text()) == report
    assert "fatal_error" not in report, report
    failed = {case["case_id"] for case in report["cases"] if not case["passed"]}
    assert failed == {"coverage.complete-long-source"}
    case = next(case for case in report["cases"] if case["case_id"] in failed)
    assertions = {item["name"]: item["passed"] for item in case["assertions"]}
    assert assertions["head_middle_tail_extracted"] is False
    assert assertions["all_source_characters_covered"] is True
    assert report["unexpected_network_attempt_count"] == 0


def test_cli_rejects_unbounded_repetitions(tmp_path):
    result = run_cli(tmp_path, "--repeats", "101")
    assert result.returncode == 2
    assert "between 3 and 100" in result.stderr


def test_runtime_guards_consumed_native_and_socket_paths(tmp_path):
    sys.path.insert(0, str(ROOT / "tools"))
    try:
        from story_memory_eval_support import EvaluationRuntime
    finally:
        sys.path.remove(str(ROOT / "tools"))
    import socket

    from bridge import codex_transport, persona_sync, provider_transport

    fixture = json.loads((ROOT / "tests/fixtures/story_memory/v1.json").read_text())
    with EvaluationRuntime(tmp_path, fixture) as runtime:
        for call in [
            lambda: persona_sync._native_settings(app_settings=runtime.settings),
            lambda: provider_transport.strict_urlopen("https://example.invalid"),
            lambda: codex_transport.resolve_access_token(app_settings=runtime.settings),
            lambda: socket.create_connection(("127.0.0.1", 1)),
        ]:
            with pytest.raises(RuntimeError, match="blocked unconfigured"):
                call()
        assert len(runtime.unexpected_io) == 4
        assert runtime.db.execute("SELECT count(*) FROM sessions").fetchone()[0] == 1


def test_empty_memory_channels_render_no_evidence(monkeypatch):
    monkeypatch.syspath_prepend(str(ROOT / "tools"))
    from evaluate_story_memory import text_of

    from bridge.memory_contracts import MemoryPromptContext

    assert text_of(MemoryPromptContext()) == ""
