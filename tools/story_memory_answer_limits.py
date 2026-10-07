"""Conservative text-token reservations; never infer a provider's price or budget."""

from __future__ import annotations

import json
from dataclasses import dataclass
from decimal import ROUND_CEILING, Decimal, DecimalException, InvalidOperation, Underflow, localcontext


def bounded_integer(value, maximum):
    if type(value) is not int or not 1 <= value <= maximum:
        raise ValueError("invalid_explicit_token_or_request_limit")
    return value


def price(value, *, positive=False):
    try:
        result = Decimal(str(value))
    except InvalidOperation as exc:
        raise ValueError("invalid_explicit_price_or_cost") from exc
    if not result.is_finite() or result < 0 or (positive and not 0 < result <= 10):
        raise ValueError("invalid_explicit_price_or_cost")
    return result


@dataclass(frozen=True)
class AnswerLimits:
    request_budget: int
    input_token_budget: int
    output_token_budget: int
    max_output_tokens: int
    context_token_cap: int
    input_usd_per_million: str
    output_usd_per_million: str
    max_cost_usd: str

    def __post_init__(self):
        for name, maximum in (
            ("request_budget", 24),
            ("input_token_budget", 1000000),
            ("output_token_budget", 100000),
            ("max_output_tokens", 4096),
            ("context_token_cap", 100000),
        ):
            bounded_integer(getattr(self, name), maximum)
        price(self.input_usd_per_million)
        price(self.output_usd_per_million)
        price(self.max_cost_usd, positive=True)

    def reserve(self, payloads):
        # Count the entire UTF-8 JSON request, then add a chat framing allowance.
        # This deliberately overestimates ordinary byte-tokenized text; provider
        # hidden prompts/reasoning and nonstandard tokenizers remain out of scope.
        upper = [len(json.dumps(payload, ensure_ascii=False, allow_nan=False).encode()) + 256 for payload in payloads]
        count, inputs, outputs = len(upper), sum(upper), len(upper) * self.max_output_tokens
        try:
            with localcontext() as context:
                context.rounding = ROUND_CEILING
                context.traps[Underflow] = True
                cost = (
                    Decimal(inputs) * price(self.input_usd_per_million)
                    + Decimal(outputs) * price(self.output_usd_per_million)
                ) / Decimal(1000000)
        except DecimalException as exc:
            raise ValueError("unpriceable_explicit_reservation") from exc
        if (
            not 0 < count <= self.request_budget
            or inputs > self.input_token_budget
            or outputs > self.output_token_budget
            or any(value + self.max_output_tokens > self.context_token_cap for value in upper)
            or cost > price(self.max_cost_usd, positive=True)
        ):
            raise ValueError("planned_work_exceeds_explicit_budget")
        return {
            "planned_requests": count,
            "input_upper_bounds": upper,
            "reserved_input_tokens": inputs,
            "reserved_output_tokens": outputs,
            "reserved_cost_usd": str(cost),
            "input_bound_method": "full_utf8_request_bytes_plus_256_chat_framing_tokens",
            "billing_verified": False,
        }

    def validate_usage(self, usage, input_upper):
        if usage is None:
            return None
        keys = ("prompt_tokens", "completion_tokens", "total_tokens")
        if (
            not isinstance(usage, dict)
            or any(type(usage.get(key)) is not int or usage[key] < 0 for key in keys)
            or usage["prompt_tokens"] > input_upper
            or usage["completion_tokens"] > self.max_output_tokens
            or usage["total_tokens"] != usage["prompt_tokens"] + usage["completion_tokens"]
        ):
            raise ValueError("provider_usage_exceeds_reservation_or_is_invalid")
        return {key: usage[key] for key in keys}
