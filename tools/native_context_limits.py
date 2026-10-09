"""Finite subscription-only experiment admission; unknown usage is never zero."""

from __future__ import annotations

from dataclasses import dataclass


def _integer(value: object, *, minimum: int = 0) -> bool:
    return type(value) is int and value >= minimum


@dataclass
class TrialBudget:
    maximum_requests: int = 24
    maximum_input_tokens: int = 300000
    maximum_output_tokens: int = 24000
    maximum_request_bytes: int = 150000
    requests: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    reserved_output_tokens: int = 0
    unknown_usage: bool = False
    pending_input_reserve: int = 0
    pending_output_cap: int = 0

    def __post_init__(self) -> None:
        ceilings = {
            "maximum_requests": 24,
            "maximum_input_tokens": 300000,
            "maximum_output_tokens": 24000,
            "maximum_request_bytes": 150000,
        }
        if any(
            not _integer(getattr(self, key), minimum=1) or getattr(self, key) > limit for key, limit in ceilings.items()
        ):
            raise ValueError("native_trial_protocol_ceiling_exceeded")
        counters = (
            self.requests,
            self.input_tokens,
            self.output_tokens,
            self.reserved_output_tokens,
            self.pending_input_reserve,
            self.pending_output_cap,
        )
        if any(not _integer(value) for value in counters) or (
            self.requests > self.maximum_requests
            or self.input_tokens > self.maximum_input_tokens
            or self.output_tokens > self.maximum_output_tokens
            or self.reserved_output_tokens > self.maximum_output_tokens
            or self.pending_input_reserve > self.maximum_request_bytes + 512
            or self.pending_output_cap > 1000
            or type(self.unknown_usage) is not bool
        ):
            raise ValueError("native_trial_counters_invalid")

    def reserve(self, body_bytes: int, output_cap: int) -> None:
        if (
            not _integer(body_bytes, minimum=1)
            or body_bytes > self.maximum_request_bytes
            or not _integer(output_cap, minimum=1)
            or output_cap > 1000
            or self.unknown_usage
            or self.pending_input_reserve
            or self.requests >= self.maximum_requests
            or self.input_tokens + body_bytes + 512 > self.maximum_input_tokens
            or self.reserved_output_tokens + output_cap > self.maximum_output_tokens
        ):
            raise ValueError("native_trial_budget_exhausted")
        self.requests += 1
        self.reserved_output_tokens += output_cap
        self.pending_input_reserve = body_bytes + 512
        self.pending_output_cap = output_cap

    def complete(self, usage: object) -> dict:
        if not self.pending_input_reserve:
            raise ValueError("no_pending_native_trial_request")
        valid = isinstance(usage, dict)
        if valid:
            inputs = usage.get("prompt_tokens")
            outputs = usage.get("completion_tokens")
            detail = usage.get("prompt_tokens_details", {})
            valid = _integer(inputs, minimum=1) and _integer(outputs) and isinstance(detail, dict)
        if valid:
            cached = detail.get("cached_tokens")
            valid = (
                (cached is None or (_integer(cached) and cached <= inputs))
                and inputs <= self.pending_input_reserve
                and outputs <= self.pending_output_cap
                and self.input_tokens + inputs <= self.maximum_input_tokens
            )
        if not valid:
            self.unknown_usage = True
            raise ValueError("native_trial_usage_incomplete_or_out_of_bounds")
        self.input_tokens += inputs
        self.output_tokens += outputs
        self.pending_input_reserve = 0
        self.pending_output_cap = 0
        return {
            "input_tokens": inputs,
            "output_tokens": outputs,
            "cached_tokens": cached,
            "complete": True,
            "reported": True,
        }


def verify_subscription(data: object, *, minimum_reserve: int, request_bytes: int) -> int:
    if (
        not isinstance(data, dict)
        or data.get("active") is not True
        or data.get("state") != "active"
        or data.get("allowOverage") is not False
        or not isinstance(data.get("weeklyInputTokens"), dict)
    ):
        raise ValueError("subscription_not_safe")
    routing = data.get("routing")
    if routing is not None and (
        not isinstance(routing, dict) or routing.get("subscriptionRequestsPermitted") is not True
    ):
        raise ValueError("subscription_requests_not_permitted")
    remaining = data["weeklyInputTokens"].get("remaining")
    if not _integer(remaining) or remaining - minimum_reserve < request_bytes * 2 + 512:
        raise ValueError("subscription_allowance_guard")
    return remaining
