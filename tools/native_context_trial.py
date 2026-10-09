"""Matched native experiment accounting. Reports never authorize production."""

from __future__ import annotations

import math
import random
import re

from tools.native_context_review import summarize_reviews

VARIANTS = ("baseline", "candidate")


def make_schedule(case_ids: list[str], *, seed: int = 421) -> list[dict]:
    """Freeze all generation orders before dispatch; use both anonymous review orders."""
    if (
        not 1 <= len(case_ids) <= 6
        or len(set(case_ids)) != len(case_ids)
        or any(not isinstance(item, str) or not re.fullmatch(r"[a-z][a-z0-9_]{0,63}", item) for item in case_ids)
        or type(seed) is not int
    ):
        raise ValueError("invalid_native_trial_schedule")
    rng = random.Random(seed)  # noqa: S311 -- reproducible ordering, not cryptography
    order = list(case_ids)
    rng.shuffle(order)
    generations, reviews = [], []
    first_variant_parity = rng.randrange(2)
    for index, case in enumerate(order):
        variants = VARIANTS if index % 2 == first_variant_parity else tuple(reversed(VARIANTS))
        for variant in variants:
            generations.append(
                {"attempt_id": f"{case}:generate:{variant}", "case_id": case, "kind": "generation", "variant": variant}
            )
        left = list(VARIANTS)
        rng.shuffle(left)
        for turn, labels in enumerate((left, list(reversed(left)))):
            reviews.append({"attempt_id": f"{case}:review:{turn}", "case_id": case, "kind": "review", "order": labels})
    return generations + reviews


def _usage(attempt: dict) -> tuple[int, int] | None:
    usage = attempt.get("usage")
    if not isinstance(usage, dict) or usage.get("complete") is not True:
        return None
    inputs, outputs = usage.get("input_tokens"), usage.get("output_tokens")
    if type(inputs) is not int or inputs <= 0 or type(outputs) is not int or outputs < 0:
        return None
    return inputs, outputs


def _percentile(values: list[int], fraction: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    position = (len(values) - 1) * fraction
    lower, upper = math.floor(position), math.ceil(position)
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower)


def summarize_trial(plan: dict, state: dict) -> dict:
    """Count every physical attempt, preserve controls, and separate audit overhead.

    Missing/failed work is never dropped from the denominator. Missing usage is
    unknown rather than zero. Generation input includes logical cache-hit tokens.
    """
    attempts = state.get("attempts", [])
    if not isinstance(attempts, list) or any(not isinstance(item, dict) for item in attempts):
        raise ValueError("invalid_native_trial_attempts")
    ids = [row.get("attempt_id") for row in attempts]
    if any(not isinstance(item, str) or not item for item in ids) or len(ids) != len(set(ids)):
        raise ValueError("duplicate_or_invalid_native_attempt_ids")
    cases = plan.get("cases", [])
    if not cases or len({case["case_id"] for case in cases}) != len(cases):
        raise ValueError("invalid_native_trial_cases")
    allowed = {case["case_id"] for case in cases}
    if any(row.get("case_id") not in allowed or row.get("kind") not in {"generation", "review"} for row in attempts):
        raise ValueError("unknown_native_trial_attempt")
    weights = [case.get("weight") for case in cases]
    if any(type(weight) not in (int, float) or not math.isfinite(weight) or weight <= 0 for weight in weights):
        raise ValueError("invalid_native_trial_weights")
    if not math.isclose(sum(weights), 1.0):
        raise ValueError("native_trial_weights_do_not_sum_to_one")

    known = [_usage(row) for row in attempts]
    all_usage_known = bool(attempts) and all(value is not None for value in known)
    complete = all_usage_known and all(row.get("status") == "complete" for row in attempts)
    totals = {variant: 0 for variant in VARIANTS}
    outputs = {variant: 0 for variant in VARIANTS}
    distribution: dict[str, list[int]] = {variant: [] for variant in VARIANTS}
    per_case = []
    matched = True
    reviews_ok = state.get("reviews_locked") is True
    for case in cases:
        rows = [row for row in attempts if row.get("case_id") == case["case_id"]]
        generations = [row for row in rows if row.get("kind") == "generation"]
        reviews = [row for row in rows if row.get("kind") == "review"]
        valid_pair = len(generations) == 2
        by_variant = {row.get("variant"): row for row in generations}
        valid_pair = valid_pair and set(by_variant) == set(VARIANTS)
        counts: dict[str, int] = {}
        if valid_pair:
            for variant in VARIANTS:
                row = by_variant[variant]
                value = _usage(row)
                if (
                    row.get("status") != "complete"
                    or value is None
                    or not isinstance(row.get("output"), str)
                    or not row["output"].strip()
                ):
                    valid_pair = False
                    break
                counts[variant] = value[0]
            observed = {row.get("response_model") for row in generations if row.get("response_model")}
            if len(observed) > 1:
                valid_pair = False
        if valid_pair:
            for variant in VARIANTS:
                totals[variant] += counts[variant]
                outputs[variant] += _usage(by_variant[variant])[1]
                distribution[variant].append(counts[variant])
        else:
            matched = False
        review_result = {"automated_review_passed": False, "reason": "missing_or_unlocked_reviews"}
        if (
            state.get("reviews_locked") is True
            and len(reviews) == 2
            and all(row.get("status") == "complete" and _usage(row) is not None for row in reviews)
        ):
            try:
                review_result = summarize_reviews(reviews)
            except (ValueError, TypeError, KeyError):
                review_result = {"automated_review_passed": False, "reason": "invalid_review"}
        canaries = case.get("forbidden_canaries", [])
        violations = sorted(
            {
                canary
                for row in generations
                if row.get("variant") == "candidate"
                for canary in canaries
                if canary and canary in str(row.get("output", ""))
            }
        )
        if violations:
            review_result = {**review_result, "automated_review_passed": False, "forbidden_canary_detected": True}
        reviews_ok = reviews_ok and review_result["automated_review_passed"]
        per_case.append(
            {
                "case_id": case["case_id"],
                "category": case.get("category"),
                "weight": case["weight"],
                "matched_complete": valid_pair,
                "logical_input": counts if valid_pair else None,
                "reduction_fraction": 1 - counts["candidate"] / counts["baseline"] if valid_pair else None,
                "review": review_result,
            }
        )
    expected_count = 4 * len(cases)
    complete = complete and len(attempts) == expected_count
    # Review validity is independent of known matched generation usage.
    # Preserve a valid measured contrast even if a judge later fails.
    matched = matched and all_usage_known
    reduction = 1 - totals["candidate"] / totals["baseline"] if matched and totals["baseline"] else None
    review_inputs = sum(
        value[0] for row, value in zip(attempts, known, strict=True) if row["kind"] == "review" and value is not None
    )
    known_inputs = sum(value[0] for value in known if value is not None)
    known_outputs = sum(value[1] for value in known if value is not None)
    shared_review_half = review_inputs / 2
    return {
        "schema_version": 1,
        "plan_sha256": plan.get("plan_sha256"),
        "case_count": len(cases),
        "expected_physical_requests": expected_count,
        "measured": {
            "story_input_tokens": totals if matched else None,
            "story_output_tokens": outputs if matched else None,
            "story_input_reduction_fraction": reduction,
            # Preserve the original frozen 3.11 evidence byte-for-byte.
            # Interpreter versions can differ by 1 ULP when accumulating
            # weighted fractions; 15 decimals gives a stable report without
            # altering raw counts, narrative decisions, or approval flags.
            "case_weighted_reduction_fraction": round(
                sum(row["weight"] * row["reduction_fraction"] for row in per_case), 15
            )
            if matched
            else None,
            "story_input_percentiles": {
                v: {"p50": _percentile(values, 0.5), "p95": _percentile(values, 0.95)}
                for v, values in distribution.items()
            }
            if matched
            else None,
        },
        "accounting": {
            "complete": complete,
            "physical_requests": len(attempts),
            "unknown_usage_requests": sum(value is None for value in known),
            "all_physical_input_tokens": known_inputs if all_usage_known else None,
            "all_physical_output_tokens": known_outputs if all_usage_known else None,
            "known_input_tokens": known_inputs,
            "known_output_tokens": known_outputs,
            "review_input_tokens": review_inputs,
            "candidate_helper_requests": 0,
            "accepted_work_input": totals if matched else None,
            "review_cost_split_sensitivity": {v: totals[v] + shared_review_half for v in VARIANTS} if matched else None,
            "review_overhead_is_offline_evaluation_not_runtime": True,
        },
        "matched_target_met": reduction is not None and reduction >= 0.3 and totals["candidate"] < totals["baseline"],
        "automated_review_passed": bool(reviews_ok and matched and complete),
        "native_source_retention_verified": all(
            case.get("proof", {}).get("all_source_evidence_preserved") is True for case in cases
        ),
        "semantic_equivalence_proven": False,
        "human_approved": False,
        "production_activation_allowed": False,
        "cases": per_case,
        "limitations": [
            "Frozen synthetic workload; no claim of representative production savings.",
            "One stochastic pair per case is not a powered noninferiority trial.",
            "Order-swapped model review is not independent human approval.",
            "Exact source reconstruction proves retention, not model interpretation.",
        ],
    }
