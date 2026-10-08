"""Synthetic role-framing preview: evidence never authorizes provider activation."""

import json
import subprocess
import sys

import pytest


@pytest.fixture(scope="module")
def report(tmp_path_factory):
    path = tmp_path_factory.mktemp("history-shadow") / "study.json"
    result = subprocess.run(
        [sys.executable, "tools/evaluate_context_history_shadow.py", "--output", str(path)],
        check=False,
        text=True,
        capture_output=True,
        timeout=45,
    )
    assert result.returncode == 0, result.stderr
    return json.loads(path.read_text(encoding="utf-8"))


def test_shadow_study_requires_no_model_and_never_enables_history(report):
    assert report["schema_version"] == 1
    assert report["provider_requests"] == 0
    assert report["model_evaluation"] == "not_executed"
    assert report["human_review"] == "required"
    assert report["decision"]["approval_ready"] is False
    assert report["decision"]["blocking_reasons"]
    assert report["source_authority"] == "synthetic_rows_not_live_database"
    assert len(report["cases"]) >= 8


def test_case_weights_and_byte_exact_history_invariants(report):
    assert sum(case["weight"] for case in report["cases"]) == pytest.approx(1.0)
    assert all(case["exact_source_reconstruction"] for case in report["cases"])
    assert all(case["mandatory_preserved"] and case["current_user_preserved"] for case in report["cases"])
    assert all(case["continuation_ending_preserved"] for case in report["cases"])
    assert all(case["candidate_estimated_tokens"] <= case["baseline_estimated_tokens"] for case in report["cases"])


def test_long_range_callback_and_repetition_cannot_be_removed(report):
    cases = {case["id"]: case for case in report["cases"]}
    assert cases["callback"]["protected_anchors_preserved"]
    assert cases["negation"]["protected_anchors_preserved"]
    assert cases["continuation"]["continuation_ending_preserved"]
    assert cases["long_transcript"]["reason"] == "selected"
    assert cases["long_transcript"]["reframed_turns"] >= 9


def test_unsafe_or_unauthorized_contexts_keep_baseline(report):
    cases = {case["id"]: case for case in report["cases"]}
    for name, reason in (
        ("historical", "historical"),
        ("missing_coverage", "incomplete_coverage"),
        ("source_rewrite", "ambiguous"),
        ("multilingual", "ambiguous"),
    ):
        assert cases[name]["reason"] == reason
        assert cases[name]["reframed_turns"] == 0
        assert cases[name]["candidate_estimated_tokens"] == cases[name]["baseline_estimated_tokens"]


def test_estimates_are_not_mislabeled_provider_usage(report):
    assert report["estimated"]["aggregate_reduction_fraction"] >= 0
    assert report["estimated"]["weighted_reduction_fraction"] >= 0
    assert "provider_reported_input_tokens" not in report["estimated"]
    assert report["provider_reported_input_tokens"] is None
    assert report["total_accepted_work_input_tokens"] is None
    assert not report["decision"]["approval_ready"]


def test_report_persists_no_prompt_or_source_story_text(report):
    payload = json.dumps(report, sort_keys=True)
    for secret in ("The ring was never given", "The last sentence is a cliffhanger", "Doorway description"):
        assert secret not in payload
