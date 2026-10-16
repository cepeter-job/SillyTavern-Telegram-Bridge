"""A new trial must freeze exact inputs before any provider call."""

import copy

import pytest

from tools.issue421_helper_fixture import KINDS, MODEL, SCENARIOS
from tools.issue421_helper_study import freeze_plan, verify_plan


def captures():
    return {
        s["id"]: {
            k: {
                "status": "complete",
                "source_sha256": "a" * 64,
                "calls": [
                    {
                        "model": MODEL,
                        "messages": [
                            {"role": "system", "content": "Synthetic instruction"},
                            {"role": "user", "content": s["source"]},
                        ],
                        "settings": {"max_tokens": 1000, "temperature": 0.0},
                        "force_non_stream": True,
                    }
                ],
            }
            for k in KINDS
        }
        for s in SCENARIOS
    }


def test_plan_freezes_twenty_requests_and_keeps_identical_story_controls():
    base = captures()
    candidate = copy.deepcopy(base)
    p = freeze_plan(base, candidate)
    verify_plan(p)
    assert len(p["order"]) == 20
    assert p["maximum_requests"] == 24
    assert p["story_reduction_target"] == 0.20
    assert p["human_approved"] is False
    assert p["production_activation_allowed"] is False
    assert all(x["variants"]["baseline"] == x["variants"]["candidate"] for x in p["cases"] if x["kind"] == "story")


def test_source_or_output_allocation_mismatch_refuses_before_dispatch():
    base = captures()
    candidate = copy.deepcopy(base)
    candidate[SCENARIOS[0]["id"]]["npc"]["calls"][0]["settings"]["max_tokens"] = 999
    with pytest.raises(ValueError, match="matched"):
        freeze_plan(base, candidate)


def test_changed_plan_or_protocol_refuses_replay():
    p = freeze_plan(captures(), captures())
    p["maximum_requests"] += 1
    with pytest.raises(ValueError, match="digest"):
        verify_plan(p)
