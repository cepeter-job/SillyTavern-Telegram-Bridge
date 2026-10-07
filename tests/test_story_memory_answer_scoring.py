"""Exact fact checks and prose diagnostics cannot substitute for one another."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))


from story_memory_answer_scoring import score_answer

FACTS = {
    "M01": "The brass compass is beneath the northern observatory staircase.",
    "A01": "The brass compass is beneath the northern observatory staircase.",
    "M13": "The iron safe's access code is orchid-27.",
    "M16": "The iron safe stands beside a green ivy pot in the archive.",
}


def test_required_fact_claim_cannot_hide_missing_answer_or_narrative():
    result = score_answer(
        {"answer": "I do not know.", "narrative": "Mira waits.", "facts": [{"key": "M16", "statement": FACTS["M16"]}]},
        required=("M16",),
        forbidden=("M13",),
        eligible=("M16",),
        facts=FACTS,
    )
    assert result["extraction"]["missing_required"] == []
    assert result["prose"]["missing_required_answer"] == ["M16"]
    assert result["prose"]["missing_required_narrative"] == ["M16"]
    assert not result["machine_checks_satisfied"]


def test_prose_leak_detected_even_without_forbidden_self_label():
    result = score_answer(
        {"answer": FACTS["M16"], "narrative": FACTS["M13"], "facts": [{"key": "M16", "statement": FACTS["M16"]}]},
        required=("M16",),
        forbidden=("M13",),
        eligible=("M16",),
        facts=FACTS,
    )
    assert result["extraction"]["forbidden_claims"] == []
    assert result["prose"]["forbidden_mentions"] == ["M13"]
    assert not result["machine_checks_satisfied"]


def test_wrong_statement_unknown_identity_and_forbidden_clone_are_independent_failures():
    result = score_answer(
        {
            "answer": FACTS["M01"],
            "narrative": FACTS["M01"],
            "facts": [
                {"key": "M01", "statement": "The compass is gone."},
                {"key": "A01", "statement": FACTS["A01"]},
                {"key": "invented", "statement": "Moon cheese."},
            ],
        },
        required=("M01",),
        forbidden=("A01",),
        eligible=("M01",),
        facts=FACTS,
    )
    assert result["extraction"]["missing_required"] == ["M01"]
    assert result["extraction"]["statement_mismatches"] == ["M01"]
    assert result["extraction"]["unknown_claims"] == ["invented"]
    assert result["extraction"]["forbidden_claims"] == ["A01"]
    # Clone text is shared by an eligible fact; prose alone cannot identify its origin.
    assert result["prose"]["forbidden_mentions"] == []
    assert not result["machine_checks_satisfied"]


def test_exact_contract_success_has_no_semantic_quality_pass():
    result = score_answer(
        {"answer": FACTS["M01"], "narrative": FACTS["M01"], "facts": [{"key": "M01", "statement": FACTS["M01"]}]},
        required=("M01",),
        forbidden=("A01",),
        eligible=("M01",),
        facts=FACTS,
    )
    assert result["machine_checks_satisfied"]
    assert result["semantic_truth"] == "not_assessed"


def test_malformed_or_duplicate_claims_are_rejected():
    cases = [
        {"answer": "text"},
        {"answer": 1, "narrative": "", "facts": []},
        {"answer": "", "narrative": "", "facts": [{"key": "M01"}]},
        {"answer": "", "narrative": "", "facts": [{"key": "M01", "statement": "x"}] * 2},
    ]
    for value in cases:
        with pytest.raises(ValueError):
            score_answer(value, required=(), forbidden=(), eligible=(), facts=FACTS)
