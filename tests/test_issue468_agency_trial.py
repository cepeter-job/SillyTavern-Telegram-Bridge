"""Contracts for the frozen, subscription-only old/new agency prompt comparison."""

import json

import pytest

from tools.issue468_agency_trial import PLAN_CASE_COUNT, make_plan, validate_pair, verify_frozen_sources


def test_plan_has_only_one_prompt_text_difference_with_controls():
    plan = make_plan()
    assert len(plan["cases"]) == PLAN_CASE_COUNT == 16
    assert len(plan["schedule"]) == 32
    assert [job["variant"] for job in plan["schedule"][:4]] == [
        "baseline",
        "candidate",
        "candidate",
        "baseline",
    ]
    classes = {case["scenario_class"] for case in plan["cases"]}
    assert classes == {"unquoted", "quoted", "no_speech"}
    assert sum(case["scenario_class"] == "unquoted" for case in plan["cases"]) == 10
    assert sum(case["scenario_class"] == "quoted" for case in plan["cases"]) == 3
    assert sum(case["scenario_class"] == "no_speech" for case in plan["cases"]) == 3
    for case in plan["cases"]:
        validate_pair(case)
        assert case["baseline"]["messages"][-1] == case["candidate"]["messages"][-1]
    assert plan["paid_overage"] is False
    assert plan["production_activation_allowed"] is False
    assert plan["provider_request_ceiling"] == 32
    assert plan["retry_or_repair_or_fallback"] is False


def test_trial_requires_source_checksum_and_exact_pinned_runtime(tmp_path):
    plan = make_plan()
    verify_frozen_sources(plan)
    first = next(iter(plan["source_sha256"]))
    plan["source_sha256"][first] = "0" * 64
    with pytest.raises(ValueError, match="frozen_source_changed"):
        verify_frozen_sources(plan)


def test_prior_source_cannot_be_recreated_using_candidate_text():
    plan = make_plan()
    case = next(v for v in plan["cases"] if v["id"] == "unquoted_thanks")
    broken = json.loads(json.dumps(case))
    broken["baseline"]["messages"] = broken["candidate"]["messages"]
    with pytest.raises(ValueError, match="only_one_agency_clause"):
        validate_pair(broken)


def test_blinded_human_sheet_is_unfilled_with_no_assignment_leak(tmp_path):
    from tools.issue468_agency_trial import render_human_review

    plan = make_plan()
    observed = {}
    for case in plan["cases"]:
        observed[case["id"]] = {
            "baseline": {"output": "Rowan waits for Ari to respond."},
            "candidate": {"output": "Rowan says the morning is calm."},
        }
    packet, sheet, mapping = render_human_review(plan, observed)
    assert len(packet["pairs"]) == len(sheet) == len(mapping["pairs"]) == 16
    assert len({entry["id"] for entry in packet["pairs"]}) == 16
    assert all(
        labels["A"] in {"baseline", "candidate"}
        and labels["B"] in {"baseline", "candidate"}
        and labels["A"] != labels["B"]
        for labels in mapping["pairs"].values()
    )
    assert all("A" in entry and "B" in entry for entry in packet["pairs"])
    assert all(not value for row in sheet for key, value in row.items() if key != "pair_id")
    assert "baseline" not in json.dumps(packet)
    assert "candidate" not in json.dumps(packet)
    assert "baseline" not in json.dumps(sheet)
    assert "candidate" not in json.dumps(sheet)
