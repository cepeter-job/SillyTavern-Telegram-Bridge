#!/usr/bin/env python3
"""Replay paired synthetic full-story prompts offline; never dispatch a provider request."""

from __future__ import annotations

import argparse
import json
import math
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from context_efficiency_accounting import validate_observations, validate_review  # noqa: E402
from context_efficiency_replay import FIXTURE, build_replay, json_hash  # noqa: E402
from story_memory_retrieval_fixture import sha256  # noqa: E402
from story_memory_retrieval_identity import source_identity  # noqa: E402

LIMITATIONS = [
    "Synthetic captures and weights do not establish deployment savings or representative production traffic.",
    "Estimated tokens use production character ratios; only supplied provider observations measure logical input.",
    "Cached input remains logical input. Monetary savings and provider billing are not assessed.",
    "Request inventories and accounting assertions are supplied evidence and cannot be independently audited offline.",
    "Attribute helpers, failed repairs and fallbacks to accepted story work. Unknown usage blocks readiness.",
    "Blinded review is a supplied human attestation, not automatic semantic scoring or proof of general quality.",
    "Replay uses production scoped memory and full story prompts but does not execute a real model or conversation.",
]


def load_json(path):
    if path.stat().st_size > 2_000_000:
        raise ValueError("invalid_observation_file_size")

    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("invalid_duplicate_json_key")
            result[key] = value
        return result

    return json.loads(
        path.read_text(encoding="utf-8"),
        object_pairs_hook=unique,
        parse_constant=lambda value: (_ for _ in ()).throw(ValueError("invalid_json_number")),
    )


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--model")
    parser.add_argument("--max-output-tokens", type=int)
    parser.add_argument("--observations", type=Path)
    parser.add_argument("--review-output", type=Path)
    parser.add_argument("--human-review", type=Path)
    args = parser.parse_args(argv)
    if args.model is not None and not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:/-]{0,199}", args.model):
        parser.error("invalid explicit model")
    if args.max_output_tokens is not None and not 1 <= args.max_output_tokens <= 4096:
        parser.error("max-output-tokens must be 1..4096")
    if (args.review_output or args.human_review) and not args.observations:
        parser.error("review arguments require supplied observations")
    try:
        fixture, rows, plan_hash = build_replay(model=args.model, max_output_tokens=args.max_output_tokens)
        if not math.isclose(sum(row["weight"] for row in rows), 1):
            raise ValueError("invalid_predeclared_weights")
        blockers = []
        if not all(value["invariants"]["satisfied"] for row in rows for value in row["variants"].values()):
            blockers.append("protected_prompt_invariant_failed")
        estimated_values = {
            name: [row["variants"][name]["estimated_input_tokens"] for row in rows]
            for name in ("baseline", "candidate")
        }
        from context_efficiency_accounting import comparison, percentiles

        report = {
            "schema_version": 1,
            "mode": "offline_plan",
            "replay_plan_sha256": plan_hash,
            "fixture_sha256": sha256(FIXTURE.read_bytes()),
            "source": source_identity(),
            "context_pipeline": "native_memory_service_and_full_story_builder_dispatch_final_budget",
            "candidate_scope": "source_aware_memory_deduplication_only; history_and_summary_conservatively_retained",
            "predeclared_workload": {"weight_origin": "frozen_synthetic_capture_author", "weights_normalized": True},
            "cases": rows,
            "network": {"requests": 0},
            "model_evaluation": {"status": "not_executed"},
            "narrative_quality": {"status": "human_review_required", "rubric": fixture["review_rubric"]},
            "target": {"reduction_fraction": fixture["target_reduction_fraction"], "status": "goal_only"},
            "estimated": {
                "aggregate_story_input": comparison(*(sum(estimated_values[v]) for v in estimated_values)),
                "story_input_percentiles": {v: percentiles(values) for v, values in estimated_values.items()},
                "basis": "production_character_ratio_estimate_not_provider_usage",
            },
            "measured": {
                "aggregate_story_input": None,
                "weighted_story_input": None,
                "story_input_percentiles": None,
                "total_accepted_work_input": None,
            },
            "limitations": LIMITATIONS,
        }
        if args.observations:
            report["mode"] = "offline_observation_validation"
            report["measured"], observation_blockers, packet, mapping = validate_observations(
                load_json(args.observations),
                rows,
                plan_hash,
            )
            blockers.extend(observation_blockers)
            report["model_evaluation"] = {"status": "supplied_observations_only", "execution_verified": False}
            packet["rubric"] = fixture["review_rubric"]
            packet["rating_scale"] = {"1": "poor", "2": "weak", "3": "adequate", "4": "good", "5": "excellent"}
            report["review_packet_sha256"] = json_hash(packet)
            report["review_unmasking"] = mapping
            if args.human_review:
                report["narrative_quality"] = validate_review(
                    load_json(args.human_review),
                    packet,
                    mapping,
                    fixture["review_rubric"],
                )
                if report["narrative_quality"]["quality_regression_cases"]:
                    blockers.append("human_review_quality_regression")
            else:
                blockers.append("blinded_human_review_required")
            for metric in ("aggregate_story_input", "weighted_story_input", "total_accepted_work_input"):
                measured = report["measured"][metric]
                if measured is None or measured["reduction_fraction"] is None:
                    blockers.append("measured_" + metric + "_required")
                elif measured["reduction_fraction"] < fixture["target_reduction_fraction"]:
                    blockers.append(metric + "_target_not_met")
            if args.review_output:
                write_json(args.review_output, packet)
        else:
            blockers.extend(("provider_observations_required", "blinded_human_review_required"))
        report["decision"] = {
            "approval_ready": not blockers,
            "blocking_reasons": blockers,
            "meaning": "supplied evidence satisfies this synthetic checkpoint only; deployment needs human approval",
        }
        write_json(args.output, report)
    except (ValueError, TypeError, KeyError, OSError) as exc:
        parser.error("invalid evaluation input: " + str(exc))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
