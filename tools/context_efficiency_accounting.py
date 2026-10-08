"""Paired provider observations: estimates never substitute for logical usage."""

from __future__ import annotations

import math
import re

from context_efficiency_replay import json_hash
from story_memory_retrieval_fixture import sha256

VARIANTS = ("baseline", "candidate")
REQUEST_KINDS = {"story", "helper", "repair", "fallback"}


def count(value, name):
    if value is not None and (type(value) is not int or value < 0):
        raise ValueError("invalid_" + name)
    return value


def comparison(baseline, candidate):
    return {
        "baseline": baseline,
        "candidate": candidate,
        "reduction_fraction": (baseline - candidate) / baseline if baseline > 0 else None,
    }


def percentiles(values):
    ordered = sorted(values)
    return {"p50": ordered[math.ceil(0.50 * len(ordered)) - 1], "p95": ordered[math.ceil(0.95 * len(ordered)) - 1]}


def validate_observations(data, rows, plan_hash):
    if not isinstance(data, dict) or data.get("schema_version") != 1 or data.get("replay_plan_sha256") != plan_hash:
        raise ValueError("observations_plan_mismatch")
    seed = data.get("blinding_seed")
    if not isinstance(seed, str) or re.fullmatch(r"[0-9a-fA-F]{64}", seed) is None:
        raise ValueError("invalid_private_blinding_seed")
    supplied = data.get("cases")
    if not isinstance(supplied, list) or len(supplied) != len(rows):
        raise ValueError("observations_cases_mismatch")
    cases = {row.get("case_id"): row for row in supplied if isinstance(row, dict)}
    if len(cases) != len(rows) or set(cases) != {row["case_id"] for row in rows}:
        raise ValueError("observations_cases_mismatch")
    unknown, incomplete, unaccepted = False, False, False
    story_values, total_values = {v: [] for v in VARIANTS}, {v: [] for v in VARIANTS}
    details = []
    for row in rows:
        observed = cases[row["case_id"]].get("variants")
        if not isinstance(observed, dict) or set(observed) != set(VARIANTS):
            raise ValueError("invalid_observation_variants")
        detail = {"case_id": row["case_id"], "variants": {}}
        for name in VARIANTS:
            value = observed[name]
            if not isinstance(value, dict) or value.get("prompt_sha256") != row["variants"][name]["prompt_sha256"]:
                raise ValueError("observations_prompt_mismatch")
            if (
                value.get("model") != row["variants"][name]["model"]
                or value.get("settings") != row["variants"][name]["settings"]
            ):
                raise ValueError("observations_model_settings_mismatch")
            if not isinstance(value.get("output"), str) or not value["output"].strip():
                raise ValueError("invalid_observed_output")
            if type(value.get("accepted")) is not bool or type(value.get("accounting_complete")) is not bool:
                raise ValueError("invalid_accounting_attestation")
            incomplete |= not value["accounting_complete"]
            unaccepted |= not value["accepted"]
            requests = value.get("requests")
            if not isinstance(requests, list) or not requests or len(requests) > 100:
                raise ValueError("invalid_request_inventory")
            inputs, story_inputs = [], []
            for request in requests:
                if not isinstance(request, dict) or request.get("kind") not in REQUEST_KINDS:
                    raise ValueError("invalid_request_kind")
                if request.get("outcome") not in {"success", "accepted", "failed"}:
                    raise ValueError("invalid_request_outcome")
                tokens = count(request.get("input_tokens"), "input_tokens")
                cached = count(request.get("cached_input_tokens"), "cached_input_tokens")
                output_tokens = count(request.get("output_tokens"), "output_tokens")
                if tokens is not None and cached is not None and cached > tokens:
                    raise ValueError("invalid_cached_input_tokens")
                unknown |= tokens is None or output_tokens is None
                inputs.append(tokens)
                if request["kind"] == "story":
                    story_inputs.append(tokens)
            if not story_inputs:
                raise ValueError("invalid_missing_story_request")
            if value["accepted"] and not any(
                request["kind"] in {"story", "repair", "fallback"}
                and request["outcome"] in {"success", "accepted"}
                and request.get("output_sha256") == sha256(value["output"])
                for request in requests
            ):
                raise ValueError("invalid_accepted_output_inventory")
            total = sum(inputs) if all(x is not None for x in inputs) else None
            story = sum(story_inputs) if all(x is not None for x in story_inputs) else None
            total_values[name].append(total)
            story_values[name].append(story)
            detail["variants"][name] = {
                "logical_story_input_tokens": story,
                "logical_total_input_tokens": total,
                "request_count": len(requests),
                "requests": requests,
                "accepted": value["accepted"],
                "accounting_complete": value["accounting_complete"],
                "output_sha256": sha256(value["output"]),
            }
        details.append(detail)
    story_known = all(x is not None for values in story_values.values() for x in values)
    work_known = not unknown and not incomplete and not unaccepted
    metrics = {
        "aggregate_story_input": comparison(*(sum(story_values[v]) for v in VARIANTS)) if story_known else None,
        "weighted_story_input": comparison(
            *(sum(row["weight"] * x for row, x in zip(rows, story_values[v], strict=True)) for v in VARIANTS)
        )
        if story_known
        else None,
        "story_input_percentiles": {v: percentiles(story_values[v]) for v in VARIANTS} if story_known else None,
        "total_accepted_work_input": comparison(*(sum(total_values[v]) for v in VARIANTS)) if work_known else None,
        "accounting_basis": "provider_reported_logical_input_including_cached_input",
        "cases": details,
    }
    blockers = (["unknown_usage"] if unknown else []) + (["incomplete_accounting"] if incomplete else [])
    blockers += ["unaccepted_work"] if unaccepted else []
    packet, mapping = blinded_packet(data, rows, plan_hash)
    return metrics, blockers, packet, mapping


def blinded_packet(data, rows, plan_hash):
    cases = {row["case_id"]: row for row in data["cases"]}
    packet, mapping = {"schema_version": 1, "cases": []}, {}
    shuffled = sorted(rows, key=lambda row: json_hash({"seed": data["blinding_seed"], "case": row["case_id"]}))
    first_label = {row["case_id"] for row in shuffled[: len(rows) // 2]}
    for row in rows:
        # A private randomized seed makes counterbalanced assignment reproducible
        # without exposing labels to a reviewer who knows the implementation.
        order = VARIANTS if row["case_id"] in first_label else tuple(reversed(VARIANTS))
        mapping[row["case_id"]] = dict(zip(("A", "B"), order, strict=True))
        packet["cases"].append(
            {
                "case_id": row["case_id"],
                "category": row["category"],
                "story": row["captured_story"],
                "outputs": {
                    label: cases[row["case_id"]]["variants"][name]["output"]
                    for label, name in mapping[row["case_id"]].items()
                },
            }
        )
    packet["comparison_id"] = json_hash({"plan": plan_hash, "outputs": packet["cases"]})
    return packet, mapping


def validate_review(review, packet, mapping, rubric):
    if (
        not isinstance(review, dict)
        or review.get("schema_version") != 1
        or review.get("review_packet_sha256") != json_hash(packet)
        or review.get("blinded_before_unmasking") is not True
        or not isinstance(review.get("reviewer"), str)
        or not review["reviewer"].strip()
    ):
        raise ValueError("invalid_blinded_review_identity")
    cases = review.get("cases")
    if not isinstance(cases, list) or len(cases) != len(mapping):
        raise ValueError("invalid_blinded_review_cases")
    indexed = {row.get("case_id"): row for row in cases if isinstance(row, dict)}
    if set(indexed) != set(mapping) or len(indexed) != len(cases):
        raise ValueError("invalid_blinded_review_cases")
    regressions = []
    for case_id, labels in mapping.items():
        scores = indexed[case_id].get("scores")
        if not isinstance(scores, dict) or set(scores) != {"A", "B"}:
            raise ValueError("invalid_blinded_review_scores")
        for score in scores.values():
            if not isinstance(score, dict) or set(score) != set(rubric):
                raise ValueError("invalid_blinded_review_scores")
            if any(type(value) is not int or not 1 <= value <= 5 for value in score.values()):
                raise ValueError("invalid_blinded_review_scores")
        reverse = {value: key for key, value in labels.items()}
        if any(scores[reverse["candidate"]][axis] < scores[reverse["baseline"]][axis] for axis in rubric):
            regressions.append(case_id)
    return {
        "status": "supplied_blinded_review",
        "reviewer": review["reviewer"],
        "quality_regression_cases": regressions,
        "semantic_truth": "human_assessment_only",
    }
