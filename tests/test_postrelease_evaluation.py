"""Offline safety and blinding contracts for the post-release research driver."""

import json

import pytest

from tools.postrelease_plan import judge_body, schedule
from tools.postrelease_run import load_state


def test_every_pair_has_two_generations_and_both_blind_orders():
    jobs = schedule(8)
    assert len(jobs) == 32
    assert sum(job["kind"] == "story" for job in jobs) == 16
    for case in range(8):
        chosen = [job for job in jobs if job["case"] == case]
        assert {job["variant"] for job in chosen if job["kind"] == "story"} == {"baseline", "candidate"}
        assert {job["swap"] for job in chosen if job["kind"] == "judge"} == {False, True}


def test_blinded_review_keeps_complete_canon_without_profile_labels():
    case = {
        "canon": [{"role": "user", "content": "Only Rowan knows the train changed."}],
        "required": ["Boundary"],
        "forbidden": ["Mara knows"],
        "focus": "Knowledge boundary",
    }
    body = judge_body(case, {"baseline": "First output", "candidate": "Second output"}, False)
    swapped = judge_body(case, {"baseline": "First output", "candidate": "Second output"}, True)
    packet = json.loads(body["messages"][-1]["content"])
    other = json.loads(swapped["messages"][-1]["content"])
    assert packet["canon"] == case["canon"]
    assert packet["A"] == other["B"] == "First output"
    assert packet["B"] == other["A"] == "Second output"
    assert "baseline" not in json.dumps(packet)
    assert "candidate" not in json.dumps(packet)
    assert body["response_format"] == {"type": "json_object"}


def test_new_execution_state_does_not_claim_any_requests(tmp_path):
    state = load_state(tmp_path / "state.json", "a" * 64)
    assert state["records"] == []
    assert state["pending"] is None
    assert state["plan_sha256"] == "a" * 64


@pytest.mark.parametrize("change", [{"pending": {"ordinal": 1}}, {"stopped": True}, {"plan_sha256": "b" * 64}])
def test_ambiguous_or_changed_run_is_never_silently_replayed(tmp_path, change):
    path = tmp_path / "state.json"
    data = {"plan_sha256": "a" * 64, "records": [], "budgets": {}, "pending": None, "stopped": False}
    path.write_text(json.dumps(data | change))
    with pytest.raises(ValueError):
        load_state(path, "a" * 64)
