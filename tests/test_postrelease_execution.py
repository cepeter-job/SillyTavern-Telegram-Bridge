"""Finite fake-wire execution proves journaling and no replay without network."""

import json
from io import BytesIO

import pytest

from tools.native_context_transport import SubscriptionClient
from tools.postrelease_plan import AXES, MODEL, PROFILES, schedule, story_body
from tools.postrelease_report import build_report
from tools.postrelease_run import execute


class FakeWire:
    def __init__(self, unknown_at=None):
        self.posts = 0
        self.unknown_at = unknown_at

    def __call__(self, request, **_kwargs):
        if request.get_method() == "GET":
            value = (
                {"data": [{"id": MODEL}]}
                if request.full_url.endswith("/models")
                else {
                    "active": True,
                    "state": "active",
                    "allowOverage": False,
                    "weeklyInputTokens": {"remaining": 10000000},
                    "routing": {"subscriptionRequestsPermitted": True},
                }
            )
        else:
            self.posts += 1
            body = json.loads(request.data)
            text = "Grounded text."
            if "response_format" in body:
                labels = {
                    label: {
                        "hard_failures": [],
                        "scores": dict.fromkeys(AXES, 4),
                        "reason": "No mismatch.",
                        "evidence": text,
                    }
                    for label in ("A", "B")
                }
                text = json.dumps({**labels, "preference": "tie"})
            value = {"model": MODEL, "choices": [{"message": {"content": text}, "finish_reason": "stop"}]}
            if self.posts != self.unknown_at:
                value["usage"] = {"prompt_tokens": 100, "completion_tokens": 20}
        return BytesIO(json.dumps(value).encode())


def fixture(tmp_path):
    body = story_body([{"role": "user", "content": "Continue without choosing my actions."}])
    trials = []
    for name in (*PROFILES, "hybrid_history"):
        count = 6 if name == "hybrid_history" else 8
        cases = [
            {
                "id": str(index),
                "canon": body["messages"],
                "required": [],
                "forbidden": [],
                "focus": "Grounded scene.",
                "baseline": body,
                "candidate": body,
                "weight": 1 / count,
            }
            for index in range(count)
        ]
        trials.append({"id": name, "kind": "synthetic_test", "cases": cases, "schedule": schedule(count)})
    plan = {
        "code_sha256": {},
        "trials": trials,
        "model_post_requests_ceiling": 152,
        "production_activation_allowed": False,
        "total_input_ceiling": 3000000,
        "total_output_ceiling": 152000,
        "minimum_initial_quota": 3100000,
        "model_selection": "nano-gpt::" + MODEL,
        "runtime_base_commit": "a" * 40,
    }
    path = tmp_path / "plan.json"
    path.write_text(json.dumps(plan))
    return path, plan


def install_wire(monkeypatch, wire):
    def client(_selection, budget):
        return SubscriptionClient(MODEL, "not-a-real-test-secret", {}, budget, opener=wire)

    monkeypatch.setattr("tools.postrelease_run.configured_client", client)


def test_complete_fake_trial_is_finite_replay_safe_and_has_complete_accounting(tmp_path, monkeypatch):
    path, plan = fixture(tmp_path)
    wire = FakeWire()
    install_wire(monkeypatch, wire)
    destination = tmp_path / "evidence"
    execute(path, destination)
    state = json.loads((destination / "state.json").read_text())
    assert wire.posts == state["provider_model_posts"] == len(state["records"]) == 152
    assert state["completed"] and state["pending"] is None
    assert not state["production_activation_allowed"] and not state["human_review_approved"]
    assert len(list((destination / "requests").glob("*.json"))) == 152
    report, human, mapping = build_report(plan, state)
    assert report["known_input_tokens"] == 15200
    assert report["known_output_tokens"] == 3040
    assert not report["unknown_usage"]
    assert len(human["pairs"]) == len(mapping["pairs"]) == 38
    assert all(trial["all_cases_automated_quality_eligible"] for trial in report["trials"])
    execute(path, destination)
    assert wire.posts == 152


def test_unknown_usage_stops_every_later_call_and_refuses_replay(tmp_path, monkeypatch):
    path, _ = fixture(tmp_path)
    wire = FakeWire(unknown_at=2)
    install_wire(monkeypatch, wire)
    destination = tmp_path / "evidence"
    with pytest.raises(ValueError, match="trial_stopped"):
        execute(path, destination)
    state = json.loads((destination / "state.json").read_text())
    assert wire.posts == 2
    assert state["stopped"] and state["records"][-1]["unknown_usage"]
    assert sum(b["input_tokens"] for b in state["budgets"].values()) == 100
    with pytest.raises(ValueError, match="cannot_resume"):
        execute(path, destination)
    assert wire.posts == 2


def test_enlarged_schedule_fails_before_even_quota_access(tmp_path, monkeypatch):
    path, plan = fixture(tmp_path)
    plan["trials"][0]["schedule"].append(plan["trials"][0]["schedule"][0])
    path.write_text(json.dumps(plan))
    monkeypatch.setattr("tools.postrelease_run.configured_client", lambda *_args: pytest.fail("No client permitted"))
    with pytest.raises(ValueError, match="unapproved_inventory"):
        execute(path, tmp_path / "evidence")
