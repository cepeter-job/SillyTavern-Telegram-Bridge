"""Physical request, subscription and complete-usage admission are independent gates."""

import pytest

from tools.native_context_limits import TrialBudget, verify_subscription


def usage(input_tokens=100, output_tokens=50):
    return {
        "prompt_tokens": input_tokens,
        "completion_tokens": output_tokens,
        "prompt_tokens_details": {"cached_tokens": 25},
    }


def test_usage_uses_full_logical_input_not_cache_discount_and_counts_each_call():
    budget = TrialBudget(maximum_requests=2, maximum_input_tokens=10000, maximum_output_tokens=2000)
    budget.reserve(500, 1000)
    reading = budget.complete(usage())
    assert reading["input_tokens"] == 100 and reading["cached_tokens"] == 25
    assert budget.requests == 1 and budget.input_tokens == 100
    budget.reserve(500, 1000)
    budget.complete(usage())
    with pytest.raises(ValueError):
        budget.reserve(500, 1000)
    assert budget.requests == 2


def test_unknown_usage_keeps_attempt_reserved_and_prevents_more_spending():
    budget = TrialBudget()
    budget.reserve(500, 1000)
    with pytest.raises(ValueError):
        budget.complete({"completion_tokens": 10})
    assert budget.requests == 1 and budget.unknown_usage
    with pytest.raises(ValueError):
        budget.reserve(10, 1000)


def test_pending_request_cannot_be_retried_or_admitted_twice():
    budget = TrialBudget()
    budget.reserve(500, 1000)
    with pytest.raises(ValueError):
        budget.reserve(500, 1000)


def test_output_allocation_and_conservative_byte_input_reserve_are_bounded():
    with pytest.raises(ValueError):
        TrialBudget(maximum_input_tokens=100).reserve(1000, 500)
    with pytest.raises(ValueError):
        TrialBudget(maximum_output_tokens=100).reserve(100, 500)


@pytest.mark.parametrize("field,value", [("active", False), ("allowOverage", True), ("state", "expired")])
def test_subscription_only_rejects_paid_overage_and_inactive_plans(field, value):
    data = {
        "active": True,
        "state": "active",
        "allowOverage": False,
        "weeklyInputTokens": {"remaining": 1000000},
        "routing": {"subscriptionRequestsPermitted": True},
    }
    data[field] = value
    with pytest.raises(ValueError):
        verify_subscription(data, minimum_reserve=100000, request_bytes=1000)


def test_quota_insufficient_and_key_route_denied_fail_before_dispatch():
    data = {
        "active": True,
        "state": "active",
        "allowOverage": False,
        "weeklyInputTokens": {"remaining": 100001},
        "routing": {"subscriptionRequestsPermitted": True},
    }
    with pytest.raises(ValueError):
        verify_subscription(data, minimum_reserve=100000, request_bytes=1000)
    data["weeklyInputTokens"]["remaining"] = 1000000
    assert verify_subscription(data, minimum_reserve=100000, request_bytes=1000) == 1000000
    data["routing"]["subscriptionRequestsPermitted"] = False
    with pytest.raises(ValueError):
        verify_subscription(data, minimum_reserve=100000, request_bytes=1000)


def test_unreported_cache_usage_is_unknown_not_a_fabricated_zero():
    budget = TrialBudget()
    budget.reserve(500, 1000)
    measured = budget.complete({"prompt_tokens": 100, "completion_tokens": 25})
    assert measured["input_tokens"] == 100
    assert measured["cached_tokens"] is None


@pytest.mark.parametrize(
    "field,value",
    [
        ("maximum_requests", 25),
        ("maximum_input_tokens", 300001),
        ("maximum_output_tokens", 24001),
        ("maximum_request_bytes", 150001),
    ],
)
def test_global_protocol_limits_cannot_be_raised_by_callers(field, value):
    with pytest.raises(ValueError):
        TrialBudget(**{field: value})
