"""Per-query evidence, honest quality denominators and production selected sets."""

from __future__ import annotations

import math
import statistics
from dataclasses import asdict
from itertools import combinations
from time import perf_counter_ns

from story_memory_retrieval_rank import finalize_ranking, fused_blocks


def latency_summary(values):
    if not values:
        return None
    ordered = sorted(value / 1_000_000 for value in values)
    return {
        "samples": len(ordered),
        "min_ms": ordered[0],
        "median_ms": statistics.median(ordered),
        "p95_ms": ordered[math.ceil(0.95 * len(ordered)) - 1],
        "max_ms": ordered[-1],
    }


def query_base(corpus, case):
    return {
        "query_id": case.query_id,
        "relevant_fact_keys": list(case.relevant_fact_keys),
        "eligible_count": len(corpus.eligible(case)),
        "forbidden_fact_keys": list(case.forbidden_fact_keys),
    }


def observe_component(corpus, case, ranking):
    final = finalize_ranking(corpus.db, corpus.scopes[case.query_id], ranking)
    keys = {stored.memory_id: key for key, stored in corpus.facts.items()}
    admitted = [keys[mid] for mid in final.ordered_memory_ids]
    raw_count = ranking.details.get("candidate_count", len(ranking.ordered_memory_ids))
    result = {
        **query_base(corpus, case),
        "status": ranking.status,
        "reason": ranking.reason,
        "candidate_memory_ids": list(ranking.ordered_memory_ids),
        "candidate_fact_keys": [keys[mid] for mid in ranking.ordered_memory_ids],
        "raw_provider_candidate_ids": list(ranking.raw_candidate_ids),
        "admitted_memory_ids": list(final.ordered_memory_ids),
        "admitted_fact_keys": admitted,
        "canonical_summaries": list(final.summaries),
        "evidence": [asdict(item) for item in final.evidence],
        "raw_candidate_count": raw_count,
        "candidate_count": len(ranking.ordered_memory_ids),
        "admitted_count": len(admitted),
        "late_invalidation_count": len(ranking.ordered_memory_ids) - len(admitted),
        "postfilter_starved": raw_count > 0 and not admitted,
        "forbidden_admitted_count": len(set(admitted) & set(case.forbidden_fact_keys)),
        "timing_ns": {**ranking.timing_ns, "canonical_finalize": final.timing_ns},
        "details": ranking.details,
    }
    if ranking.scores:
        result["candidate_cosine_scores"] = list(ranking.scores)
    return result, final


def observe_fusion(corpus, case, supplemental=None, *, documents=(), channel="recall"):
    start = perf_counter_ns()
    blocks, input_counts = fused_blocks(
        corpus.db, corpus.scopes[case.query_id], case.text, supplemental, documents=documents, channel=channel
    )
    elapsed = perf_counter_ns() - start
    keys = {stored.memory_id: key for key, stored in corpus.facts.items()}
    by_id = {item.memory_id: item for block in blocks for item in block.evidence}
    ids = sorted(by_id)  # Stable display order only: the validator produces channel sets.
    admitted = [keys[mid] for mid in ids]
    return {
        **query_base(corpus, case),
        "status": "available",
        "selection_kind": "production_selected_set",
        "production_block_order": [channel, "episodic", "summary", "scene"],
        "admitted_memory_ids": ids,
        "admitted_fact_keys": admitted,
        "selected_count": len(ids),
        "selected_relevant_count": len(set(admitted) & set(case.relevant_fact_keys)),
        "forbidden_admitted_count": len(set(admitted) & set(case.forbidden_fact_keys)),
        "evidence": [asdict(by_id[mid]) for mid in ids],
        "channel_membership": {block.channel: [keys[item.memory_id] for item in block.evidence] for block in blocks},
        "input_channel_counts": input_counts,
        "canonical_blocks": {block.channel: block.text for block in blocks},
        "timing_ns": {"fts_read_and_production_validation": elapsed},
    }


def _quality(rows, *, selected_set=False):
    positive = [row for row in rows if row["relevant_fact_keys"]]
    no_answer = [row for row in rows if not row["relevant_fact_keys"]]
    recalls = {}
    for k in (6,) if selected_set else (1, 3, 6):
        values = [
            len(set(row["admitted_fact_keys"][:k]) & set(row["relevant_fact_keys"])) / len(row["relevant_fact_keys"])
            for row in positive
        ]
        recalls[str(k)] = statistics.mean(values) if values else None
    result = {
        "recall_at": recalls,
        "no_answer_candidate_return_rate": statistics.mean(bool(row["admitted_fact_keys"]) for row in no_answer)
        if no_answer
        else None,
    }
    if not selected_set:
        reciprocal = [
            next(
                (
                    1 / index
                    for index, key in enumerate(row["admitted_fact_keys"], 1)
                    if key in row["relevant_fact_keys"]
                ),
                0,
            )
            for row in positive
        ]
        result["mrr"] = statistics.mean(reciprocal) if reciprocal else None
    return result


def aggregate_backend(rows, evidence_kind, *, selected_set=False):
    available = [row for row in rows if row["status"] == "available"]
    failed = [row["query_id"] for row in rows if row["status"] == "failed"]
    skipped = [row["query_id"] for row in rows if row["status"] == "skipped"]
    stages = {}
    for row in rows:
        samples = row.get("timing_samples_ns", {key: [value] for key, value in row.get("timing_ns", {}).items()})
        for stage, values in samples.items():
            stages.setdefault(stage, []).extend(values)
    result = {
        "status": "failed" if failed else "available" if available else "skipped",
        "evidence_kind": evidence_kind,
        "queries": rows,
        "failed_query_ids": failed,
        "skipped_query_ids": skipped,
        "denominators": {
            "expected_queries": len(rows),
            "successful_queries": len(available),
            "expected_positive_queries": sum(bool(row["relevant_fact_keys"]) for row in rows),
            "successful_positive_queries": sum(bool(row["relevant_fact_keys"]) for row in available),
            "expected_no_answer_queries": sum(not row["relevant_fact_keys"] for row in rows),
            "successful_no_answer_queries": sum(not row["relevant_fact_keys"] for row in available),
        },
        "forbidden_admitted_count": sum(row["forbidden_admitted_count"] for row in rows),
        "postfilter_starved_query_ids": [row["query_id"] for row in available if row.get("postfilter_starved")],
        "latency": {stage: latency_summary(values) for stage, values in stages.items()},
    }
    if selected_set:
        result["selection_kind"] = "production_selected_set"
        result["display_order"] = "memory_id ascending; not a global retrieval rank"
    if evidence_kind != "contract_only" and available:
        result["quality"] = _quality(available, selected_set=selected_set)
    return result


def paired_differences(backends, *, selected_set=False):
    results = []
    for (left_name, left), (right_name, right) in combinations(backends.items(), 2):
        if "contract_only" in {left["evidence_kind"], right["evidence_kind"]}:
            continue
        left_rows = {row["query_id"]: row for row in left["queries"] if row["status"] == "available"}
        right_rows = {row["query_id"]: row for row in right["queries"] if row["status"] == "available"}
        joint = sorted(left_rows.keys() & right_rows.keys())
        if not joint:
            continue
        lq = _quality([left_rows[key] for key in joint], selected_set=selected_set)
        rq = _quality([right_rows[key] for key in joint], selected_set=selected_set)
        delta = {
            "recall_at": {
                k: rq["recall_at"][k] - value if value is not None else None for k, value in lq["recall_at"].items()
            }
        }
        for key in ("no_answer_candidate_return_rate",) + (() if selected_set else ("mrr",)):
            delta[key] = rq[key] - lq[key] if lq[key] is not None else None
        results.append(
            {
                "left": left_name,
                "right": right_name,
                "joint_positive_query_ids": [key for key in joint if left_rows[key]["relevant_fact_keys"]],
                "joint_no_answer_query_ids": [key for key in joint if not left_rows[key]["relevant_fact_keys"]],
                "right_minus_left": delta,
            }
        )
    return results
