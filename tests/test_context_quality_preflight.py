"""No paid A/B request is authorized by estimates or model-output assertions."""

import pytest

from tools.context_quality_preflight import evaluate_quality_preflight


def report():
    return {
        "schema_version": 1,
        "mode": "offline_plan",
        "network": {"requests": 0},
        "target": {"reduction_fraction": 0.30},
        "estimated": {
            "aggregate_story_input": {"baseline": 6000, "candidate": 6000, "reduction_fraction": 0.0},
        },
        "cases": [
            {
                "case_id": "story",
                "weight": 1.0,
                "variants": {
                    "baseline": {"model": "nano-gpt::z-ai/glm-5.2", "settings": {"max_tokens": 600}},
                    "candidate": {"model": "nano-gpt::z-ai/glm-5.2", "settings": {"max_tokens": 600}},
                },
            }
        ],
    }


def budget():
    return {
        "approved": True,
        "model": "nano-gpt::z-ai/glm-5.2",
        "maximum_requests": 2,
        "maximum_logical_input_tokens": 16000,
        "maximum_output_tokens": 1200,
        "maximum_usd": 0.01,
        "input_usd_per_million": 0.42,
        "output_usd_per_million": 1.32,
    }


def closure():
    return {"structural_ok": True, "native_causal_proof": False, "activation_allowed": False}


def test_native_zero_savings_refuses_paid_trials_even_if_budget_is_marked_approved():
    result = evaluate_quality_preflight(report(), closure(), budget())
    assert result["trial_ready"] is False
    assert result["activation_allowed"] is False
    assert result["provider_requests"] == 0
    assert "candidate_savings_below_target" in result["blocking_reasons"]
    assert "native_causal_proof_missing" in result["blocking_reasons"]


def test_missing_numeric_authorization_fails_without_implicit_budget():
    result = evaluate_quality_preflight(report(), closure(), None)
    assert result["trial_ready"] is False
    assert "explicit_budget_missing" in result["blocking_reasons"]


@pytest.mark.parametrize(
    "mutate",
    [
        lambda x: x.update(maximum_requests=100),
        lambda x: x.update(maximum_usd=5.0),
        lambda x: x.update(maximum_logical_input_tokens=None),
        lambda x: x.update(model="other-provider::other-model"),
        lambda x: x.update(approved=False),
    ],
)
def test_excessive_unknown_and_mismatched_budget_is_rejected(mutate):
    b = budget()
    mutate(b)
    result = evaluate_quality_preflight(report(), closure(), b)
    assert result["trial_ready"] is False
    assert "invalid_budget" in result["blocking_reasons"]


def test_proposed_model_and_generation_settings_must_match_in_both_variants():
    p = report()
    p["cases"][0]["variants"]["candidate"]["settings"]["max_tokens"] = 512
    result = evaluate_quality_preflight(p, closure(), budget())
    assert result["trial_ready"] is False
    assert "unmatched_pair" in result["blocking_reasons"]


def test_user_supplied_trusted_native_claim_cannot_self_authorize():
    c = closure()
    c["native_causal_proof"] = True
    c["activation_allowed"] = True
    result = evaluate_quality_preflight(report(), c, budget())
    assert result["trial_ready"] is False
    assert result["activation_allowed"] is False
    assert "native_causal_proof_missing" in result["blocking_reasons"]


def test_offline_preflight_output_contains_no_prompts_or_story_data():
    p = report()
    p["cases"][0]["SECRET_PROMPT_TEXT"] = "PRIVATE CANARY"
    result = evaluate_quality_preflight(p, closure(), budget())
    assert "PRIVATE CANARY" not in str(result)
    assert result["provider_requests"] == 0
    assert result["trial_ready"] is False
