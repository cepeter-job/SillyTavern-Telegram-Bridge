"""Offline full-story replay and paired logical-input accounting contracts."""

import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
CLI = ROOT / "tools/evaluate_context_efficiency.py"


def run_cli(tmp_path, *args):
    output = tmp_path / "report.json"
    result = subprocess.run(
        [sys.executable, str(CLI), "--output", str(output), *map(str, args)],
        capture_output=True,
        text=True,
        timeout=60,
    )
    return result, json.loads(output.read_text()) if output.exists() else None


def observations(plan):
    return {
        "schema_version": 1,
        "replay_plan_sha256": plan["replay_plan_sha256"],
        "blinding_seed": "b" * 64,
        "cases": [
            {
                "case_id": case["case_id"],
                "variants": {
                    name: {
                        "prompt_sha256": variant["prompt_sha256"],
                        "model": variant["model"],
                        "settings": variant["settings"],
                        "output": "Synthetic observed continuation.",
                        "accepted": True,
                        "accounting_complete": True,
                        "requests": [
                            {
                                "kind": "story",
                                "outcome": "accepted",
                                "input_tokens": amount,
                                "cached_input_tokens": 100,
                                "output_tokens": 20,
                                "output_sha256": hashlib.sha256(b"Synthetic observed continuation.").hexdigest(),
                            },
                            {
                                "kind": "helper",
                                "outcome": "success",
                                "input_tokens": 30,
                                "cached_input_tokens": 0,
                                "output_tokens": 10,
                            },
                            {
                                "kind": "repair",
                                "outcome": "failed",
                                "input_tokens": 40,
                                "cached_input_tokens": 0,
                                "output_tokens": 5,
                            },
                            {
                                "kind": "fallback",
                                "outcome": "failed",
                                "input_tokens": 50,
                                "cached_input_tokens": 0,
                                "output_tokens": 5,
                            },
                        ],
                    }
                    for name, variant, amount in (
                        ("baseline", case["variants"]["baseline"], 1000),
                        ("candidate", case["variants"]["candidate"], 650),
                    )
                },
            }
            for case in plan["cases"]
        ],
    }


def test_default_cli_builds_actual_story_prompts_without_measured_claim(tmp_path):
    result, report = run_cli(tmp_path)
    assert result.returncode == 0, result.stderr
    assert report["mode"] == "offline_plan"
    frozen = ROOT / "tests/fixtures/story_memory/context_efficiency_v1.json"
    assert report["fixture_sha256"] == hashlib.sha256(frozen.read_bytes()).hexdigest()
    assert report["network"]["requests"] == 0
    assert report["model_evaluation"]["status"] == "not_executed"
    assert report["narrative_quality"]["status"] == "human_review_required"
    assert report["target"]["reduction_fraction"] == 0.30
    assert report["target"]["status"] == "goal_only"
    assert report["decision"]["approval_ready"] is False
    assert report["measured"]["aggregate_story_input"] is None
    assert sum(case["weight"] for case in report["cases"]) == pytest.approx(1)
    for case in report["cases"]:
        for variant in case["variants"].values():
            assert variant["invariants"]["satisfied"] is True
            prompt = variant["messages"]
            systems = [message["content"] for message in prompt if message["role"] == "system"]
            assert any("Mandatory response language" in content for content in systems)
            assert variant["invariants"]["post_history_placement_preserved"]
            assert variant["invariants"]["native_policy_precedence_preserved"]
            assert any("Light Novel response contract" in content for content in systems)
            assert "required_fact_keys" not in json.dumps(prompt)
            assert variant["estimated_input_tokens"] > 0
            assert all("_context_optional" not in message for message in prompt)
        assert case["variants"]["baseline"]["settings"] == case["variants"]["candidate"]["settings"]


def test_observations_include_cached_and_all_accepted_work_costs(tmp_path):
    _, plan = run_cli(tmp_path)
    source = tmp_path / "observations.json"
    source.write_text(json.dumps(observations(plan)))
    result, report = run_cli(tmp_path, "--observations", source, "--review-output", tmp_path / "blind.json")
    assert result.returncode == 0, result.stderr
    metrics = report["measured"]
    assert metrics["aggregate_story_input"]["reduction_fraction"] == pytest.approx(0.35)
    assert metrics["total_accepted_work_input"]["baseline"] == len(plan["cases"]) * 1120
    assert metrics["total_accepted_work_input"]["candidate"] == len(plan["cases"]) * 770
    assert metrics["story_input_percentiles"]["baseline"] == {"p50": 1000, "p95": 1000}
    assert report["decision"]["approval_ready"] is False
    blind = json.loads((tmp_path / "blind.json").read_text())
    assert "baseline" not in json.dumps(blind)
    assert "candidate" not in json.dumps(blind)
    assert "prompt_sha256" not in json.dumps(blind)


@pytest.mark.parametrize("usage_field", ["input_tokens", "output_tokens"])
def test_unknown_helper_usage_stays_unknown_and_blocks_approval(tmp_path, usage_field):
    _, plan = run_cli(tmp_path)
    data = observations(plan)
    data["cases"][0]["variants"]["candidate"]["requests"][1][usage_field] = None
    source = tmp_path / "unknown.json"
    source.write_text(json.dumps(data))
    result, report = run_cli(tmp_path, "--observations", source)
    assert result.returncode == 0, result.stderr
    assert report["measured"]["total_accepted_work_input"] is None
    assert "unknown_usage" in report["decision"]["blocking_reasons"]
    assert report["decision"]["approval_ready"] is False


@pytest.mark.parametrize(
    "mutation",
    [
        "wrong_hash",
        "missing_case",
        "negative_usage",
        "cached_exceeds_input",
        "wrong_model",
        "wrong_output_cap",
        "missing_blinding_seed",
        "accepted_output_mismatch",
    ],
)
def test_cli_rejects_unmatched_or_invalid_accounting(tmp_path, mutation):
    _, plan = run_cli(tmp_path)
    data = observations(plan)
    variant = data["cases"][0]["variants"]["candidate"]
    if mutation == "wrong_hash":
        variant["prompt_sha256"] = "0" * 64
    elif mutation == "missing_case":
        data["cases"].pop()
    elif mutation == "negative_usage":
        variant["requests"][0]["input_tokens"] = -1
    elif mutation == "cached_exceeds_input":
        variant["requests"][0]["cached_input_tokens"] = 651
    elif mutation == "wrong_model":
        variant["model"] = "different-model"
    elif mutation == "wrong_output_cap":
        variant["settings"]["max_tokens"] = 999
    elif mutation == "missing_blinding_seed":
        data.pop("blinding_seed")
    else:
        variant["requests"][0]["output_sha256"] = "0" * 64
    source = tmp_path / "invalid.json"
    source.write_text(json.dumps(data))
    result, _ = run_cli(tmp_path, "--observations", source)
    assert result.returncode == 2
    assert "invalid" in result.stderr.lower() or "mismatch" in result.stderr.lower()


def test_review_requires_all_blinded_axes_and_accounting_before_readiness(tmp_path):
    _, plan = run_cli(tmp_path)
    data = observations(plan)
    source = tmp_path / "observed.json"
    source.write_text(json.dumps(data))
    packet_path = tmp_path / "blind.json"
    result, report = run_cli(tmp_path, "--observations", source, "--review-output", packet_path)
    assert result.returncode == 0, result.stderr
    packet = json.loads(packet_path.read_text())
    review = {
        "schema_version": 1,
        "review_packet_sha256": report["review_packet_sha256"],
        "reviewer": "Synthetic test reviewer",
        "blinded_before_unmasking": True,
        "cases": [
            {
                "case_id": case["case_id"],
                "scores": {label: {axis: 4 for axis in packet["rubric"]} for label in ("A", "B")},
            }
            for case in packet["cases"]
        ],
    }
    review_path = tmp_path / "review.json"
    review_path.write_text(json.dumps(review))
    result, reviewed = run_cli(tmp_path, "--observations", source, "--human-review", review_path)
    assert result.returncode == 0, result.stderr
    assert reviewed["decision"]["approval_ready"] is True
    assert reviewed["model_evaluation"]["status"] == "supplied_observations_only"
    assert reviewed["network"]["requests"] == 0
    first_id = review["cases"][0]["case_id"]
    candidate_label = next(
        label for label, value in report["review_unmasking"][first_id].items() if value == "candidate"
    )
    review["cases"][0]["scores"][candidate_label][packet["rubric"][0]] = 2
    review_path.write_text(json.dumps(review))
    result, regressed = run_cli(tmp_path, "--observations", source, "--human-review", review_path)
    assert result.returncode == 0, result.stderr
    assert regressed["decision"]["approval_ready"] is False
    assert "human_review_quality_regression" in regressed["decision"]["blocking_reasons"]


def test_story_replay_preserves_actual_repetition_negation_and_ending(tmp_path):
    result, report = run_cli(tmp_path)
    assert result.returncode == 0, result.stderr
    cases = {row["case_id"]: row for row in report["cases"]}
    for name in ("baseline", "candidate"):
        repeated = cases["repeated_negated"]["variants"][name]["messages"]
        assert (
            sum(message["role"] == "user" and message["content"] == "Do not open the safe." for message in repeated)
            == 2
        )
        assert "Do not open it. Do not open it." in repeated[-1]["content"]
        assert "I keep my hand on the latch." in repeated[-1]["content"]
        continuation = cases["continuation"]["variants"][name]["messages"]
        assert any(
            message["role"] == "assistant"
            and message["content"] == "Rowan lifts the inspection ledger, but stops when the brass latch clicks."
            for message in continuation
        )
    for case in report["cases"]:
        baseline = case["variants"]["baseline"]["messages"][0]["content"]
        candidate = case["variants"]["candidate"]["messages"][0]["content"]
        for protected_policy in ("Memory policy", "Episodic memory policy", "Character knowledge boundary"):
            if protected_policy in baseline:
                assert protected_policy in candidate


def test_explicit_model_and_output_allocation_are_offline_and_matched(tmp_path):
    result, report = run_cli(tmp_path, "--model", "future-paired-model", "--max-output-tokens", "1200")
    assert result.returncode == 0, result.stderr
    assert report["network"]["requests"] == 0
    assert report["model_evaluation"]["status"] == "not_executed"
    for case in report["cases"]:
        for variant in case["variants"].values():
            assert variant["model"] == "future-paired-model"
            assert variant["settings"]["max_tokens"] == 1200
            assert variant["context_stats"]["output_reserve_tokens"] == 1200


@pytest.mark.parametrize("value", ["0", "4097", "-1"])
def test_output_predeclaration_rejects_unbounded_allocations(tmp_path, value):
    result, _ = run_cli(tmp_path, "--max-output-tokens", value)
    assert result.returncode == 2
    assert "1..4096" in result.stderr


def test_native_memory_service_is_the_baseline_without_postvalidation_duplicates(tmp_path, monkeypatch):
    import re

    monkeypatch.syspath_prepend(str(ROOT / "tools"))
    from story_memory_eval_support import CHAT
    from story_memory_retrieval_corpus import RetrievalCorpus

    result, report = run_cli(tmp_path)
    assert result.returncode == 0, result.stderr
    fixture = json.loads((ROOT / "tests/fixtures/story_memory/context_efficiency_v1.json").read_text())
    captures = {case["case_id"]: case for case in fixture["cases"]}
    with RetrievalCorpus(tmp_path / "native-reference") as corpus:
        for row in report["cases"]:
            scope = corpus.scopes[row["retrieval_query_id"]]
            native = corpus.runtime.memory.prompt_context(
                corpus.db,
                CHAT,
                corpus.sessions["main"],
                fixture["fields"],
                captures[row["case_id"]]["user_text"],
                through_rowid=scope.through_rowid if scope.historical else None,
                principals=scope.principals,
                historical=scope.historical,
            )
            messages = row["variants"]["baseline"]["messages"]
            user = messages[-1]["content"]
            for tag, expected in (("untrusted_memory", native.recall), ("untrusted_episodic_memory", native.episodic)):
                match = re.search(r"<" + tag + r">\n([\s\S]*?)\n</" + tag + r">", user)
                assert (match.group(1) if match else "") == expected
    assert report["estimated"]["aggregate_story_input"]["reduction_fraction"] == 0


def test_audience_capture_matches_rowan_knowledge_and_excludes_private_canary(tmp_path):
    result, report = run_cli(tmp_path)
    assert result.returncode == 0, result.stderr
    audience = next(case for case in report["cases"] if case["case_id"] == "audience")
    for variant in audience["variants"].values():
        assert "Rowan says the recognition phrase is unknown to him." in json.dumps(variant["messages"])
        assert "safe's access code?" not in variant["messages"][-1]["content"]
    for case in report["cases"]:
        for variant in case["variants"].values():
            assert "night heron" not in json.dumps(variant["messages"]).casefold()


@pytest.mark.parametrize("kind", ["story", "repair", "fallback"])
def test_accepted_observations_require_a_successful_answer_producing_attempt(tmp_path, kind):
    _, plan = run_cli(tmp_path)
    data = observations(plan)
    value = data["cases"][0]["variants"]["candidate"]
    for request in value["requests"]:
        request["outcome"] = "failed"
    source = tmp_path / "all-failed.json"
    source.write_text(json.dumps(data))
    result, _ = run_cli(tmp_path, "--observations", source)
    assert result.returncode == 2
    assert "accepted_output" in result.stderr
    value["requests"].append(
        {
            "kind": kind,
            "outcome": "accepted",
            "input_tokens": 60,
            "cached_input_tokens": 0,
            "output_tokens": 20,
            "output_sha256": hashlib.sha256(b"Synthetic observed continuation.").hexdigest(),
        }
    )
    source.write_text(json.dumps(data))
    result, report = run_cli(tmp_path, "--observations", source)
    assert result.returncode == 0, result.stderr
    first = report["measured"]["cases"][0]["variants"]["candidate"]
    assert first["logical_total_input_tokens"] == 830
    assert first["request_count"] == 5
