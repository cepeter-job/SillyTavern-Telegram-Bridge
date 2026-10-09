"""Reporting retains failed judgments and all physical usage denominators."""

from tools.postrelease_report import normalize_review, review_verdict


def judgment(preference="A", failure=False):
    from tools.postrelease_plan import AXES

    items = {
        label: {
            "hard_failures": ["unsupported knowledge"] if failure and label == "B" else [],
            "scores": dict.fromkeys(AXES, 4),
            "reason": "Reason.",
            "evidence": "quote",
        }
        for label in ("A", "B")
    }
    return {**items, "preference": preference}


def record(*, swap=False, preference="A", failure=False):
    return {
        "kind": "judge",
        "status": "completed",
        "swap": swap,
        "judgment": judgment(preference, failure),
        "judge_evidence_quotes_valid": True,
    }


def test_reversed_order_maps_preferences_back_to_actual_variants():
    assert normalize_review(record())["preference"] == "baseline"
    assert normalize_review(record(swap=True))["preference"] == "candidate"


def test_disagreement_is_retained_not_replaced_by_a_preferred_judge():
    verdict = review_verdict([record(), record(swap=True)])
    assert verdict["consensus"] == "disagreement"
    assert verdict["human_approved"] is False


def test_candidate_hard_failure_in_either_order_blocks_quality_gate():
    verdict = review_verdict([record(failure=True), record(swap=True, preference="B")])
    assert verdict["candidate_hard_failure"] is True
    assert verdict["automated_quality_eligible"] is False


def test_missing_or_invalid_evidence_stays_unreviewed():
    invalid = record() | {"judge_evidence_quotes_valid": False}
    verdict = review_verdict([invalid, record(swap=True)])
    assert verdict["valid_reviews"] == 1
    assert verdict["automated_quality_eligible"] is False
    assert verdict["consensus"] == "unavailable"
