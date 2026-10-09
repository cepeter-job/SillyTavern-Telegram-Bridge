"""Subscription-only physical dispatch has no repair/fallback or hidden retries."""

import json
from io import BytesIO

import pytest

from tools.native_context_limits import TrialBudget
from tools.native_context_transport import SubscriptionClient

SAFE = {
    "active": True,
    "state": "active",
    "allowOverage": False,
    "weeklyInputTokens": {"remaining": 1000000},
    "routing": {"subscriptionRequestsPermitted": True},
}


def response(content="Grounded text.", *, usage=True, finish="stop"):
    data = {
        "model": "z-ai/glm-5.2",
        "choices": [{"message": {"role": "assistant", "content": content}, "finish_reason": finish}],
    }
    if usage:
        data["usage"] = {"prompt_tokens": 100, "completion_tokens": 20}
    return data


class Wire:
    def __init__(self, result=None, quota=None):
        self.result = response() if result is None else result
        self.quota = SAFE if quota is None else quota
        self.calls = []

    def __call__(self, request, **_kwargs):
        self.calls.append(request)
        data = self.quota if request.method == "GET" else self.result
        return BytesIO(json.dumps(data).encode())


def client(wire):
    return SubscriptionClient("z-ai/glm-5.2", "not-a-real-test-secret", {}, TrialBudget(), opener=wire)


def body():
    return {
        "model": "z-ai/glm-5.2",
        "messages": [{"role": "user", "content": "Test synthetic source."}],
        "temperature": 0.7,
        "max_tokens": 500,
        "stream": False,
    }


def test_one_admission_one_physical_call_and_complete_logical_usage():
    wire = Wire()
    c = client(wire)
    encoded = c.admit(body())
    assert c.budget.requests == 1 and len(wire.calls) == 1
    result = c.send_reserved(encoded)
    assert result["output"] == "Grounded text."
    assert result["usage"]["input_tokens"] == 100
    assert c.budget.requests == 1 and len(wire.calls) == 2
    assert all("/api/subscription/v1/" in req.full_url for req in wire.calls)
    with pytest.raises(ValueError):
        c.send_reserved(encoded)
    assert len(wire.calls) == 2


def test_paid_overage_denied_before_spending():
    wire = Wire(quota={**SAFE, "allowOverage": True})
    c = client(wire)
    with pytest.raises(ValueError):
        c.admit(body())
    assert c.budget.requests == 0 and all(req.method == "GET" for req in wire.calls)


def test_body_route_or_stream_tampering_is_never_sent():
    for change in ({"model": "other/model"}, {"stream": True}, {"provider": "paid-pin"}, {"n": 2}):
        wire = Wire()
        c = client(wire)
        with pytest.raises(ValueError):
            c.admit({**body(), **change})
        assert wire.calls == []
    wire = Wire()
    c = client(wire)
    encoded = c.admit(body())
    with pytest.raises(ValueError):
        c.send_reserved(encoded + b" ")
    assert len(wire.calls) == 1


def test_unknown_usage_prevents_next_call_and_remains_unknown():
    wire = Wire(result=response(usage=False))
    c = client(wire)
    with pytest.raises(ValueError):
        c.send_reserved(c.admit(body()))
    assert c.budget.unknown_usage and c.last_usage is None
    with pytest.raises(ValueError):
        c.admit(body())
    assert sum(req.method == "POST" for req in wire.calls) == 1


def test_truncated_or_empty_answer_still_counts_usage_without_retry():
    for result in (response(finish="length"), response(content="")):
        wire = Wire(result=result)
        c = client(wire)
        with pytest.raises(ValueError):
            c.send_reserved(c.admit(body()))
        assert c.budget.requests == 1 and c.budget.input_tokens == 100
        assert c.last_usage["input_tokens"] == 100
        assert sum(req.method == "POST" for req in wire.calls) == 1
