"""Fail-closed, zero-network preflight for budgeted context quality experiments.

A supplied user budget and structural proof are not trusted live source
attestations. This workflow deliberately cannot authorize paid dispatch
or production pruning until a separate native evaluator is implemented.
"""

from __future__ import annotations

import math

_BUDGET_KEYS = frozenset(
    (
        "approved",
        "model",
        "maximum_requests",
        "maximum_logical_input_tokens",
        "maximum_output_tokens",
        "maximum_usd",
        "input_usd_per_million",
        "output_usd_per_million",
    )
)


def _positive_int(x: object) -> bool:
    return type(x) is int and x > 0


def _positive_finite(x: object) -> bool:
    return type(x) in (float, int) and math.isfinite(x) and x > 0


def evaluate_quality_preflight(plan: object, continuity: object, budget: object = None) -> dict[str, object]:
    """Return content-free reason codes; never execute a provider request."""
    reasons: set[str] = set()
    cases: list[dict] = []
    model = ""
    min_requests = 0
    min_input = 0
    min_output = 0
    estimated_gain = None
    if not isinstance(plan, dict) or plan.get("schema_version") != 1 or not isinstance(plan.get("cases"), list):
        reasons.add("invalid_plan")
    else:
        cases = plan["cases"]
        estimate = plan.get("estimated", {}).get("aggregate_story_input", {})
        estimated_gain = estimate.get("reduction_fraction") if isinstance(estimate, dict) else None
        if not (type(estimated_gain) in (float, int) and math.isfinite(estimated_gain) and -1 <= estimated_gain <= 1):
            reasons.add("invalid_plan")
        elif estimated_gain < 0.30:
            reasons.add("candidate_savings_below_target")
        if plan.get("network") != {"requests": 0}:
            reasons.add("invalid_plan")
        if not 1 <= len(cases) <= 12:
            reasons.add("invalid_plan")
        min_requests = len(cases) * 2
        for case in cases:
            variants = case.get("variants") if isinstance(case, dict) else None
            if not isinstance(variants, dict) or set(variants) != {"baseline", "candidate"}:
                reasons.add("invalid_plan")
                continue
            left, right = variants["baseline"], variants["candidate"]
            if not isinstance(left, dict) or not isinstance(right, dict):
                reasons.add("invalid_plan")
                continue
            a, b = left.get("settings"), right.get("settings")
            if a != b or left.get("model") != right.get("model"):
                reasons.add("unmatched_pair")
            if not isinstance(a, dict) or not _positive_int(a.get("max_tokens")):
                reasons.add("invalid_plan")
            else:
                min_output += 2 * a["max_tokens"]
            for variant in (left, right):
                estimated = variant.get("estimated_input_tokens")
                if type(estimated) is int and estimated >= 0:
                    min_input += estimated
            candidate_model = left.get("model")
            if not isinstance(candidate_model, str) or not candidate_model:
                reasons.add("invalid_plan")
            elif not model:
                model = candidate_model
            elif model != candidate_model:
                reasons.add("unmatched_pair")
    # The current repo has no trusted native attestation/semantic closure.
    # Even a caller-supplied native_causal_proof=True cannot mint that authority.
    reasons.add("native_causal_proof_missing")

    if budget is None:
        reasons.add("explicit_budget_missing")
    else:
        valid = isinstance(budget, dict) and set(budget) == _BUDGET_KEYS
        if valid:
            valid = (
                budget["approved"] is True
                and isinstance(budget["model"], str)
                and budget["model"] == model
                and _positive_int(budget["maximum_requests"])
                and min_requests <= budget["maximum_requests"] <= 24
                and _positive_int(budget["maximum_logical_input_tokens"])
                and min_input <= budget["maximum_logical_input_tokens"] <= 1_200_000
                and _positive_int(budget["maximum_output_tokens"])
                and min_output <= budget["maximum_output_tokens"] <= 98_304
                and _positive_finite(budget["maximum_usd"])
                and budget["maximum_usd"] <= 0.10
                and _positive_finite(budget["input_usd_per_million"])
                and _positive_finite(budget["output_usd_per_million"])
            )
            if valid:
                exposure = (
                    budget["maximum_logical_input_tokens"] * budget["input_usd_per_million"]
                    + budget["maximum_output_tokens"] * budget["output_usd_per_million"]
                ) / 1_000_000
                valid = exposure <= budget["maximum_usd"]
        if not valid:
            reasons.add("invalid_budget")
    return {
        "schema_version": 1,
        "trial_ready": False,
        "activation_allowed": False,
        "provider_requests": 0,
        "predeclared_case_count": len(cases),
        "required_request_slots": min_requests,
        "estimated_story_reduction_fraction": estimated_gain,
        "blocking_reasons": sorted(reasons),
        "meaning": "No source-trusted native causal attestation; zero-network quality gate only.",
    }
