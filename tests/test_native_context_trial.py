"""Matched accounting and review scheduling cannot hide failed or unpaired work."""

import pytest

from tools.native_context_review import DIMENSIONS
from tools.native_context_trial import make_schedule, summarize_trial


def test_schedule_predeclares_all_twenty_four_physical_requests_with_swapped_reviews():
    schedule = make_schedule([f"case_{i}" for i in range(6)])
    assert len(schedule) == 24
    assert len({row["attempt_id"] for row in schedule}) == 24
    for i in range(6):
        rows = [row for row in schedule if row["case_id"] == f"case_{i}"]
        assert {row["variant"] for row in rows if row["kind"] == "generation"} == {"baseline", "candidate"}
        assert {tuple(row["order"]) for row in rows if row["kind"] == "review"} == {
            ("baseline", "candidate"),
            ("candidate", "baseline"),
        }
    assert all(row["kind"] == "generation" for row in schedule[:12])


def sample():
    plan = {"cases": [{"case_id": "one", "weight": 1.0}], "plan_sha256": "f" * 64}
    judgment = {
        p: {"scores": dict.fromkeys(DIMENSIONS, 4), "violation_codes": [], "rationale": "Same quality."}
        for p in ("A", "B")
    }
    attempts = [
        {
            "attempt_id": "g1",
            "kind": "generation",
            "case_id": "one",
            "variant": "baseline",
            "status": "complete",
            "usage": {"input_tokens": 1000, "output_tokens": 100, "complete": True},
            "output": "A grounded story.",
        },
        {
            "attempt_id": "g2",
            "kind": "generation",
            "case_id": "one",
            "variant": "candidate",
            "status": "complete",
            "usage": {"input_tokens": 600, "output_tokens": 100, "complete": True},
            "output": "A grounded story.",
        },
        *[
            {
                "attempt_id": f"r{i}",
                "kind": "review",
                "case_id": "one",
                "order": order,
                "status": "complete",
                "usage": {"input_tokens": 300, "output_tokens": 100, "complete": True},
                "judgment": judgment,
            }
            for i, order in enumerate((["baseline", "candidate"], ["candidate", "baseline"]))
        ],
    ]
    return plan, {"attempts": attempts, "reviews_locked": True}


def test_matched_provider_totals_and_review_overhead_are_separate():
    plan, state = sample()
    report = summarize_trial(plan, state)
    assert report["measured"]["story_input_reduction_fraction"] == pytest.approx(0.4)
    assert report["accounting"]["review_input_tokens"] == 600
    assert report["accounting"]["all_physical_input_tokens"] == 2200
    assert report["matched_target_met"] and report["automated_review_passed"]
    assert report["human_approved"] is False and report["production_activation_allowed"] is False


def test_pending_or_missing_usage_never_counts_as_zero_or_as_a_pass():
    plan, state = sample()
    state["attempts"][1]["status"] = "pending"
    state["attempts"][1].pop("usage")
    report = summarize_trial(plan, state)
    assert not report["matched_target_met"]
    assert not report["accounting"]["complete"]
    assert report["measured"]["story_input_reduction_fraction"] is None


def test_unlocked_reviews_do_not_grant_blinded_review_success():
    plan, state = sample()
    state["reviews_locked"] = False
    report = summarize_trial(plan, state)
    assert not report["automated_review_passed"]


def test_duplicate_physical_attempt_ids_are_rejected():
    plan, state = sample()
    state["attempts"].append(state["attempts"][0])
    with pytest.raises(ValueError):
        summarize_trial(plan, state)


@pytest.mark.parametrize("seed", [0, 1, 2, 17])
def test_generation_order_is_counterbalanced_across_six_cases(seed):
    schedule = make_schedule([f"case_{i}" for i in range(6)], seed=seed)
    first = {}
    for item in schedule:
        if item["kind"] == "generation":
            first.setdefault(item["case_id"], item["variant"])
    assert list(first.values()).count("baseline") == 3
    assert list(first.values()).count("candidate") == 3


def test_failed_judgment_does_not_erase_known_matched_story_measurements():
    plan, state = sample()
    state["attempts"][2]["status"] = "failed"
    state["reviews_locked"] = False
    report = summarize_trial(plan, state)
    assert report["measured"]["story_input_reduction_fraction"] == pytest.approx(0.4)
    assert not report["automated_review_passed"]
    assert not report["accounting"]["complete"]
    assert report["accounting"]["all_physical_input_tokens"] == 2200
    assert report["production_activation_allowed"] is False


def test_new_plan_20_percent_target_is_distinct_from_frozen_30_percent_default():
    plan, state = sample()
    state["attempts"][1]["usage"]["input_tokens"] = 750
    assert summarize_trial(plan, state)["matched_target_met"] is False
    plan["target_reduction_fraction"] = 0.20
    report = summarize_trial(plan, state)
    assert report["matched_target_met"] is True
    assert report["human_approved"] is False
    assert report["production_activation_allowed"] is False


def test_new_native_target_below_20_percent_is_not_accepted():
    plan, state = sample()
    plan["target_reduction_fraction"] = 0.05
    with pytest.raises(ValueError, match="target"):
        summarize_trial(plan, state)
