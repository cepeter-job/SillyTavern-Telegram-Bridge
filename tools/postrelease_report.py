#!/usr/bin/env python3
"""Describe all frozen experiment observations without inventing human approval."""

from __future__ import annotations

import argparse
import json
import secrets
import statistics
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.postrelease_plan import AXES, digest  # noqa: E402


def normalize_review(record: dict) -> dict | None:
    if (
        record.get("status") != "completed"
        or "judgment" not in record
        or not record.get("judge_evidence_quotes_valid")
        or not all(record["judgment"][label]["evidence"] for label in ("A", "B"))
    ):
        return None
    value = record["judgment"]
    a, b = ("candidate", "baseline") if record.get("swap") else ("baseline", "candidate")
    return {
        a: value["A"],
        b: value["B"],
        "preference": {"A": a, "B": b, "tie": "tie"}[value["preference"]],
        "presentation_reversed": bool(record.get("swap")),
    }


def review_verdict(records: list[dict]) -> dict:
    reviews = [value for record in records if (value := normalize_review(record)) is not None]
    complete = len(reviews) == 2 and {v["presentation_reversed"] for v in reviews} == {False, True}
    hard = {variant: any(r[variant]["hard_failures"] for r in reviews) for variant in ("baseline", "candidate")}
    deltas = [
        statistics.mean(r["candidate"]["scores"].values()) - statistics.mean(r["baseline"]["scores"].values())
        for r in reviews
    ]
    critical_regression = any(
        r["candidate"]["scores"][axis] < r["baseline"]["scores"][axis]
        for r in reviews
        for axis in ("facts", "causality", "agency", "knowledge")
    )
    consensus = "unavailable"
    if complete:
        consensus = reviews[0]["preference"] if reviews[0]["preference"] == reviews[1]["preference"] else "disagreement"
    return {
        "valid_reviews": len(reviews),
        "both_orders_valid": complete,
        "consensus": consensus,
        "baseline_hard_failure": hard["baseline"],
        "candidate_hard_failure": hard["candidate"],
        "critical_score_regression": critical_regression,
        "mean_score_delta": statistics.mean(deltas) if deltas else None,
        "automated_quality_eligible": complete
        and not hard["candidate"]
        and not critical_regression
        and min(deltas, default=-1) >= 0,
        "human_approved": False,
        "normalized_reviews": reviews,
    }


def usage(record: dict) -> dict | None:
    return record.get("response", {}).get("usage") or record.get("usage")


def write_new(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.write("\n")


def build_report(plan: dict, state: dict) -> tuple[dict, dict, dict]:
    if state["plan_sha256"] != digest(plan) or state.get("pending") is not None:
        raise ValueError("cannot_finalize_a_changed_or_pending_experiment")
    records = state["records"]
    if len({r["id"] for r in records}) != len(records):
        raise ValueError("duplicate_physical_record")
    all_usage = [u for r in records if (u := usage(r)) is not None]
    summaries = []
    human = {"schema_version": 1, "review_type": "independent_human_review_pending", "axes": list(AXES), "pairs": []}
    mapping = {"schema_version": 1, "pairs": {}}
    for trial in plan["trials"]:
        selected = [r for r in records if r["trial"] == trial["id"]]
        cases = []
        for case in trial["cases"]:
            items = [r for r in selected if r["case"] == case["id"]]
            stories = {r["variant"]: r for r in items if r["kind"] == "story"}
            pair_complete = set(stories) == {"baseline", "candidate"} and all(
                r["status"] == "completed" for r in stories.values()
            )
            measured = {
                variant: usage(stories[variant])["input_tokens"]
                if variant in stories and usage(stories[variant])
                else None
                for variant in ("baseline", "candidate")
            }
            verdict = review_verdict([r for r in items if r["kind"] == "judge"])
            reduction = 1 - measured["candidate"] / measured["baseline"] if all(measured.values()) else None
            cases.append(
                {
                    "case_id": case["id"],
                    "weight": case["weight"],
                    "pair_complete": pair_complete,
                    "input_tokens": measured,
                    "measured_input_reduction_fraction": reduction,
                    "shadow_metrics": case.get("shadow_metrics"),
                    "automated_review": verdict,
                }
            )
            if pair_complete:
                pair_id = "pair-" + secrets.token_hex(6)
                a, b = ("candidate", "baseline") if secrets.randbelow(2) else ("baseline", "candidate")
                human["pairs"].append(
                    {
                        "id": pair_id,
                        "canon": case["canon"],
                        "required_facts": case["required"],
                        "forbidden_inferences": case["forbidden"],
                        "focus": case["focus"],
                        "A": stories[a]["response"]["output"],
                        "B": stories[b]["response"]["output"],
                        "human_review": None,
                    }
                )
                mapping["pairs"][pair_id] = {"trial": trial["id"], "case": case["id"], "A": a, "B": b}
        complete = all(c["pair_complete"] and all(c["input_tokens"].values()) for c in cases)
        totals = {variant: sum(c["input_tokens"][variant] or 0 for c in cases) for variant in ("baseline", "candidate")}
        trial_usage = [u for r in selected if (u := usage(r)) is not None]
        consensus = {
            key: sum(c["automated_review"]["consensus"] == key for c in cases)
            for key in ("baseline", "candidate", "tie", "disagreement", "unavailable")
        }
        latency = {
            variant: [
                r["latency_seconds"]
                for r in selected
                if r["kind"] == "story" and r["variant"] == variant and r["status"] == "completed"
            ]
            for variant in ("baseline", "candidate")
        }
        summaries.append(
            {
                "trial": trial["id"],
                "kind": trial["kind"],
                "planned_cases": len(cases),
                "planned_requests": len(trial["schedule"]),
                "recorded_attempts": len(selected),
                "successful_requests": sum(r["status"] == "completed" for r in selected),
                "failed_requests": sum(r["status"] != "completed" for r in selected),
                "all_story_pairs_complete": complete,
                "story_input_tokens": totals,
                "aggregate_story_reduction_fraction": 1 - totals["candidate"] / totals["baseline"]
                if complete
                else None,
                "case_weighted_story_reduction_fraction": sum(
                    c["weight"] * c["measured_input_reduction_fraction"] for c in cases
                )
                if complete
                else None,
                "all_experiment_input_tokens": sum(u["input_tokens"] for u in trial_usage),
                "all_experiment_output_tokens": sum(u["output_tokens"] for u in trial_usage),
                "judge_input_tokens": sum(
                    usage(r)["input_tokens"] for r in selected if r["kind"] == "judge" and usage(r)
                ),
                "median_observed_latency_seconds": {
                    variant: statistics.median(values) if values else None for variant, values in latency.items()
                },
                "consensus": consensus,
                "candidate_hard_failure_cases": [
                    c["case_id"] for c in cases if c["automated_review"]["candidate_hard_failure"]
                ],
                "baseline_hard_failure_cases": [
                    c["case_id"] for c in cases if c["automated_review"]["baseline_hard_failure"]
                ],
                "all_cases_automated_quality_eligible": all(
                    c["automated_review"]["automated_quality_eligible"] for c in cases
                ),
                "cases": cases,
            }
        )
    report = {
        "schema_version": 1,
        "plan_sha256": digest(plan),
        "source_commit": plan["runtime_base_commit"],
        "selected_model": plan["model_selection"],
        "provider_requests": state.get("provider_model_posts", 0),
        "planned_provider_requests": plan["model_post_requests_ceiling"],
        "complete": state.get("completed", False),
        "stopped": state.get("stopped", False),
        "known_input_tokens": sum(u["input_tokens"] for u in all_usage),
        "known_output_tokens": sum(u["output_tokens"] for u in all_usage),
        "unknown_usage": any(b["unknown_usage"] for b in state["budgets"].values()),
        "cached_usage_unknown_requests": sum(u.get("cached_tokens") is None for u in all_usage),
        "response_models": sorted(
            {r["response"].get("response_model") for r in records if r.get("response", {}).get("response_model")}
        ),
        "trials": summaries,
        "human_review_approved": False,
        "production_activation_allowed": False,
        "limitations": [
            (
                "Single-sample synthetic pilot, not a representative production benchmark or "
                "proof of semantic equivalence."
            ),
            "Automated reviewer uses the same selected model; it is not an independent human review.",
            (
                "Quality-eligible is a descriptive conservative summary: two valid orders, no "
                "hard failure, no critical-score regression and nonnegative mean-score delta in "
                "each order; never a production approval."
            ),
            "Native fixture Summary receipts authenticate source processing, not complete causal extraction.",
            "Median latency includes quota admission and local persistence, not isolated provider inference time.",
            (
                "Reasoning-specific token details are not retained by the reused normalized "
                "transport and remain unknown; logical input/output totals are provider-reported."
            ),
            "No production transcript exported, optional default changed or live history-pruning switch enabled.",
            (
                "Earlier interrupted acceptance logs and the failed wrong-policy Telegram probe "
                "are preserved; the recovered native transport and 179 passing regressions "
                "supersede them without erasing the failed attempt."
            ),
        ],
    }
    return report, human, mapping


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--state", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    plan = json.loads(args.plan.read_text())
    state = json.loads(args.state.read_text())
    report, human, mapping = build_report(plan, state)
    write_new(args.output / "RESULTS.json", report)
    write_new(args.output / "human-review-packet.json", human)
    write_new(args.output / "anonymous-pair-map.json", mapping)
    print(
        json.dumps(
            {
                k: report[k]
                for k in (
                    "complete",
                    "provider_requests",
                    "known_input_tokens",
                    "known_output_tokens",
                    "unknown_usage",
                    "human_review_approved",
                    "production_activation_allowed",
                )
            }
        )
    )
    for trial in report["trials"]:
        print(
            json.dumps(
                {
                    k: trial[k]
                    for k in (
                        "trial",
                        "successful_requests",
                        "failed_requests",
                        "aggregate_story_reduction_fraction",
                        "consensus",
                        "candidate_hard_failure_cases",
                        "all_cases_automated_quality_eligible",
                    )
                }
            )
        )


if __name__ == "__main__":
    main()
