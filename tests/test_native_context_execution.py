"""Durable trial journal prevents duplicate network work after interruption."""

import json
from io import BytesIO

import pytest

from tools.evaluate_native_context import build_native_plan
from tools.native_context_fixture import declared_cases
from tools.native_context_limits import TrialBudget
from tools.native_context_review import DIMENSIONS
from tools.native_context_transport import SubscriptionClient
from tools.run_native_context_trial import execute_trial, freeze_schedule, validate_trial_state


@pytest.fixture(scope="module")
def prepared(tmp_path_factory):
    directory = tmp_path_factory.mktemp("native-execution")
    cases = [dict(declared_cases()[-1], weight=1.0)]
    plan = build_native_plan(directory, model="nano-gpt::z-ai/glm-5.2", max_output_tokens=1000, cases=cases)
    return directory, freeze_schedule(plan)


class Socket:
    def __init__(self):
        self.posts = 0
        self.fail = False

    def __call__(self, request, **_kwargs):
        if request.method == "GET":
            result = {
                "active": True,
                "state": "active",
                "allowOverage": False,
                "weeklyInputTokens": {"remaining": 1000000},
            }
        else:
            self.posts += 1
            if self.fail:
                raise TimeoutError("unknown delivery")
            payload = json.loads(request.data)
            if "response_format" in payload:
                answer = {
                    p: {"scores": dict.fromkeys(DIMENSIONS, 4), "violation_codes": [], "rationale": "Grounded."}
                    for p in ("A", "B")
                }
                content = json.dumps(answer)
            else:
                content = "Rowan pauses; the box remains closed. The user must decide what happens next."
            result = {
                "model": "z-ai/glm-5.2",
                "choices": [{"message": {"content": content}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 250, "completion_tokens": 100},
            }
        return BytesIO(json.dumps(result).encode())


def connection(socket, budget=None):
    return SubscriptionClient("z-ai/glm-5.2", "test-only", {}, budget or TrialBudget(), opener=socket)


def test_full_trial_locks_order_swapped_reviews_and_keeps_production_off(prepared, tmp_path):
    directory, plan = prepared
    socket = Socket()
    state = execute_trial(directory, plan, connection(socket), tmp_path / "state.json")
    assert socket.posts == 4 and state["reviews_locked"] is True
    assert len(state["attempts"]) == 4
    assert all(row["status"] == "complete" for row in state["attempts"])
    assert len(state["review_lock_sha256"]) == 64
    assert state["production_activation_allowed"] is False
    assert validate_trial_state(plan, state) is None


def test_resume_never_resends_completed_calls(prepared, tmp_path):
    directory, plan = prepared
    socket = Socket()
    path = tmp_path / "state.json"
    client = connection(socket)
    first = execute_trial(directory, plan, client, path, max_steps=1)
    assert socket.posts == 1 and not first["reviews_locked"]
    resumed_budget = TrialBudget(**first["budget"])
    state = execute_trial(directory, plan, connection(socket, resumed_budget), path)
    assert socket.posts == 4 and len(state["attempts"]) == 4
    execute_trial(directory, plan, connection(socket, TrialBudget(**state["budget"])), path)
    assert socket.posts == 4


def test_ambiguous_delivery_is_recorded_and_cannot_auto_retry(prepared, tmp_path):
    directory, plan = prepared
    socket = Socket()
    socket.fail = True
    path = tmp_path / "state.json"
    state = execute_trial(directory, plan, connection(socket), path)
    assert socket.posts == 1
    assert state["attempts"][0]["status"] == "failed"
    assert state["budget"]["unknown_usage"] is True
    assert not state["reviews_locked"]
    with pytest.raises(ValueError):
        execute_trial(directory, plan, connection(socket, TrialBudget(**state["budget"])), path)
    assert socket.posts == 1


def test_mutated_plan_or_journal_cannot_resume(prepared, tmp_path):
    directory, plan = prepared
    socket = Socket()
    path = tmp_path / "state.json"
    execute_trial(directory, plan, connection(socket), path, max_steps=1)
    changed = json.loads(json.dumps(plan))
    changed["cases"][0]["variants"]["candidate"]["settings"]["max_tokens"] = 999
    with pytest.raises(ValueError):
        execute_trial(directory, changed, connection(socket), path)
    journal = json.loads(path.read_text())
    journal["attempts"][0]["output"] = "Altered after the response was recorded."
    path.write_text(json.dumps(journal))
    with pytest.raises(ValueError):
        execute_trial(directory, plan, connection(socket), path)
    assert socket.posts == 1
