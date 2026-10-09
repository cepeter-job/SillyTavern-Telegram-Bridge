"""Provider-free execution rehearsal: bounded 32 calls, complete accounting, no ambiguous replay."""

import json
from io import BytesIO

import pytest

from tools.issue468_agency_execute import execute, report
from tools.issue468_agency_trial import MODEL, digest, make_plan
from tools.native_context_transport import SubscriptionClient


class FakeSubscriptionWire:
    def __init__(self, *, fail_at=None):
        self.posts = 0
        self.fail_at = fail_at

    def __call__(self, request, **_kwargs):
        method = request.get_method()
        if method == "GET":
            if request.full_url.endswith("/models"):
                obj = {"data": [{"id": MODEL}]}
            else:
                obj = {
                    "active": True,
                    "state": "active",
                    "allowOverage": False,
                    "weeklyInputTokens": {"remaining": 1000000},
                    "routing": {"subscriptionRequestsPermitted": True},
                }
        else:
            self.posts += 1
            assert method == "POST"
            body = json.loads(request.data)
            assert body["model"] == MODEL and body["stream"] is False
            obj = {
                "model": MODEL,
                "choices": [{"message": {"content": "Rowan observes the station."}, "finish_reason": "stop"}],
            }
            if self.posts != self.fail_at:
                obj["usage"] = {"prompt_tokens": 90, "completion_tokens": 12}
        return BytesIO(json.dumps(obj).encode("utf-8"))


def saved_plan(tmp_path):
    plan = make_plan()
    path = tmp_path / "plan.json"
    path.write_text(json.dumps(plan))
    return path, plan


def inject_wire(monkeypatch, wire):
    def client(_selection, budget):
        return SubscriptionClient(MODEL, "synthetic-test-only-key", {}, budget, opener=wire)

    monkeypatch.setattr("tools.issue468_agency_execute.configured_client", client)


def test_all_32_fake_posts_are_accounted_and_new_trial_cannot_replay(tmp_path, monkeypatch):
    path, _ = saved_plan(tmp_path)
    wire = FakeSubscriptionWire()
    inject_wire(monkeypatch, wire)
    dest = tmp_path / "private"
    execute(path, dest)
    state = json.loads((dest / "state.json").read_text())
    assert wire.posts == len(state["records"]) == 32
    assert state["complete"] is True and state["stopped"] is False and state["pending"] is None
    assert sum(v["requests"] for v in state["budgets"].values()) == 32
    public = tmp_path / "published"
    private_map = tmp_path / "private-unblinding"
    summary = report(path, dest / "state.json", public, private_map)
    assert summary["physical_model_requests"] == 32
    assert summary["all_input_tokens"] == 32 * 90
    assert summary["all_output_tokens"] == 32 * 12
    assert summary["production_approval"] is False
    assert len(json.loads((public / "HUMAN_REVIEW.json").read_text())["pairs"]) == 16
    assert len((public / "HUMAN_SCORECARD.csv").read_text().splitlines()) == 17
    assert "baseline" not in (public / "HUMAN_REVIEW.json").read_text()
    assert "candidate" not in (public / "HUMAN_REVIEW.json").read_text()
    assert len(json.loads((private_map / "unblinding-map.json").read_text())["pairs"]) == 16
    with pytest.raises(ValueError, match="existing_trial_state_refuses_any_replay"):
        execute(path, dest)
    assert wire.posts == 32


def test_unknown_usage_stops_after_second_post_without_replay(tmp_path, monkeypatch):
    path, _ = saved_plan(tmp_path)
    wire = FakeSubscriptionWire(fail_at=2)
    inject_wire(monkeypatch, wire)
    dest = tmp_path / "private"
    with pytest.raises(RuntimeError, match="agency_trial_stopped_no_replay"):
        execute(path, dest)
    state = json.loads((dest / "state.json").read_text())
    assert wire.posts == len(state["records"]) == 2
    assert state["stopped"] and state["pending"] is not None
    assert state["records"][-1]["usage_may_be_unknown"]
    with pytest.raises(ValueError, match="existing_trial_state_refuses_any_replay"):
        execute(path, dest)
    assert wire.posts == 2


def test_tampered_frozen_schedule_fails_before_opening_network(tmp_path, monkeypatch):
    path, plan = saved_plan(tmp_path)
    plan["schedule"][0]["variant"] = "candidate"
    path.write_text(json.dumps(plan))
    monkeypatch.setattr(
        "tools.issue468_agency_execute.configured_client",
        lambda *_args: pytest.fail("No credential or network should be accessed"),
    )
    with pytest.raises(ValueError, match="unapproved_agency_schedule"):
        execute(path, tmp_path / "private")


def test_ledger_and_plan_hashes_are_nonempty(tmp_path):
    _, plan = saved_plan(tmp_path)
    assert len(digest(plan)) == 64
    assert plan["source_sha256"]
