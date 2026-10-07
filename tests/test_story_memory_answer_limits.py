"""Budget reservation must cover every planned call before dispatch."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))


def limits(**changes):
    from story_memory_answer_limits import AnswerLimits

    values = dict(
        request_budget=2,
        input_token_budget=1000,
        output_token_budget=200,
        max_output_tokens=100,
        context_token_cap=1000,
        input_usd_per_million="1",
        output_usd_per_million="2",
        max_cost_usd="0.01",
    )
    return AnswerLimits(**dict(values, **changes))


def test_all_calls_reserve_full_output_and_conservative_input():
    # UTF-8 JSON wire bodies are 13 and 14 bytes; each reserves 256 envelope tokens.
    result = limits().reserve([{"text": "a"}, {"text": "bb"}])
    assert result["input_upper_bounds"] == [269, 270]
    assert result["reserved_input_tokens"] == 539
    assert result["reserved_output_tokens"] == 200
    assert result["reserved_cost_usd"] == "0.000939"


def test_caps_and_unpriceable_values_fail_before_dispatch():
    invalid = [
        {"request_budget": 1},
        {"input_token_budget": 538},
        {"output_token_budget": 199},
        {"context_token_cap": 369},
        {"max_cost_usd": "0.000938"},
        {"max_cost_usd": "NaN"},
        {"input_usd_per_million": "Infinity"},
        {"output_usd_per_million": "-1"},
        {"input_usd_per_million": "1e999999999"},
        {"request_budget": None},
        {"max_output_tokens": 0},
        {"context_token_cap": True},
    ]
    for changes in invalid:
        with pytest.raises(ValueError):
            limits(**changes).reserve([{"text": "a"}, {"text": "bb"}])


def test_exact_caps_allow_planned_work_without_spare_dispatch():
    result = limits(
        input_token_budget=539, output_token_budget=200, context_token_cap=370, max_cost_usd="0.000939"
    ).reserve([{"text": "a"}, {"text": "bb"}])
    assert result["planned_requests"] == 2


def test_provider_usage_must_fit_reserved_bounds_or_stop():
    budget = limits()
    assert budget.validate_usage(None, 269) is None
    assert budget.validate_usage({"prompt_tokens": 20, "completion_tokens": 10, "total_tokens": 30}, 269) == {
        "prompt_tokens": 20,
        "completion_tokens": 10,
        "total_tokens": 30,
    }
    invalid = [
        {"prompt_tokens": 270, "completion_tokens": 10, "total_tokens": 280},
        {"prompt_tokens": 20, "completion_tokens": 101, "total_tokens": 121},
        {"prompt_tokens": 20, "completion_tokens": 10, "total_tokens": 31},
        {"prompt_tokens": True, "completion_tokens": 10, "total_tokens": 11},
        {},
    ]
    for value in invalid:
        with pytest.raises(ValueError):
            budget.validate_usage(value, 269)
