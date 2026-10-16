"""Physical trial attempts are admitted and durably counted before socket I/O."""

import io
import json
import urllib.request

import pytest

from tools.issue421_helper_wire import TrialWire


class Reply(io.BytesIO):
    status = 200

    def __init__(self, data):
        super().__init__(data)
        self.headers = {}


def request(cap=1000):
    return urllib.request.Request(
        "https://nano-gpt.com/api/subscription/v1/chat/completions",
        data=json.dumps(
            {
                "model": "z-ai/glm-5.2",
                "messages": [{"role": "user", "content": "Synthetic fact"}],
                "max_tokens": cap,
                "stream": False,
            }
        ).encode(),
        method="POST",
    )


def response(usage=None):
    return Reply(
        json.dumps({"usage": usage, "choices": [{"message": {"content": "{}"}, "finish_reason": "stop"}]}).encode()
    )


def test_pending_state_exists_before_send_and_cached_tokens_are_counted(tmp_path):
    path = tmp_path / "attempts.json"

    def opener(*args, **kwargs):
        assert json.loads(path.read_text())["attempts"][-1]["status"] == "pending"
        return response({"prompt_tokens": 30, "completion_tokens": 8, "prompt_tokens_details": {"cached_tokens": 20}})

    wire = TrialWire(path, quota=lambda size: None, opener=opener)
    wire.set_context("scene", "baseline")
    with wire(request()) as output:
        assert json.load(output)["usage"]["prompt_tokens"] == 30
    assert wire.state["attempts"][0]["usage"]["input_tokens"] == 30
    assert wire.state["attempts"][0]["usage"]["cached_tokens"] == 20
    assert wire.state["attempts"][0]["status"] == "complete"
    assert path.stat().st_mode & 0o777 == 0o600


def test_missing_usage_halts_without_automatic_retry(tmp_path):
    calls = []

    def opener(*args, **kwargs):
        calls.append(1)
        return response()

    wire = TrialWire(tmp_path / "state.json", quota=lambda size: None, opener=opener)
    wire.set_context("story", "candidate")
    with pytest.raises(ValueError, match="usage"):
        wire(request())
    with pytest.raises(ValueError, match="stopped"):
        wire(request())
    assert len(calls) == 1
    assert wire.state["attempts"][0]["usage"] is None


def test_output_reservation_and_route_cannot_be_bypassed(tmp_path):
    wire = TrialWire(tmp_path / "state.json", quota=lambda size: None, opener=lambda *a, **k: pytest.fail("no network"))
    wire.set_context("npc", "candidate")
    with pytest.raises(ValueError, match="body"):
        wire(request(1401))
    req = urllib.request.Request("https://example.com/chat/completions", data=b"{}")
    with pytest.raises(ValueError, match="route"):
        wire(req)
    assert wire.state["attempts"] == []


def test_existing_ledger_refuses_reexecution(tmp_path):
    path = tmp_path / "state.json"
    path.write_text('{"attempts": [{"status": "pending"}]}')
    with pytest.raises(ValueError, match="exists"):
        TrialWire(path, quota=lambda size: None, opener=lambda *a, **k: None)


def test_per_arm_budget_is_smaller_than_global_study_budget(tmp_path):
    calls = []

    def opener(*args, **kwargs):
        calls.append(1)
        return response({"prompt_tokens": 30, "completion_tokens": 8})

    wire = TrialWire(
        tmp_path / "arm.json",
        quota=lambda size: None,
        opener=opener,
        maximum_requests=1,
        maximum_input=150000,
        maximum_output=16000,
    )
    wire.set_context("story", "baseline")
    with wire(request()):
        pass
    with pytest.raises(ValueError, match="budget"):
        wire(request())
    assert len(calls) == 1


def test_cannot_raise_study_budget_ceiling(tmp_path):
    with pytest.raises(ValueError, match="limit"):
        TrialWire(tmp_path / "arm.json", quota=lambda size: None, opener=lambda *a, **k: None, maximum_requests=25)
