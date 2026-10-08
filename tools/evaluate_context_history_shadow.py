#!/usr/bin/env python3
"""Offline, non-authoritative structural/token estimates for shadow history framing.

No model dispatch, no live SQLite and no release activation. Source-row identities
are synthetic test identities, never evidence that native sources were authorized.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from bridge.context_compaction import estimate_message_tokens  # noqa: E402
from bridge.context_history_preview import preview_packed_history  # noqa: E402
from bridge.memory_contracts import MemoryReadScope  # noqa: E402
from bridge.user_dialogue import format_user_dialogue_action  # noqa: E402

FIXTURE = ROOT / "tests/fixtures/story_memory/context_history_shadow_v1.json"


def _recover_original_turns(messages: list[dict]) -> list[tuple[str, str]]:
    restored: list[tuple[str, str]] = []
    for message in messages:
        if "_context_history_index" in message:
            restored.append((message["role"], message["content"]))
        elif message.get("_history_preview_packet") is True:
            _, separator, json_rows = message["content"].partition("\n")
            if not separator:
                raise ValueError("history preview packet is missing an encoded payload")
            rows = json.loads(json_rows)
            if not isinstance(rows, list) or any(
                not isinstance(row, list)
                or len(row) != 2
                or row[0] not in {"user", "assistant"}
                or not isinstance(row[1], str)
                for row in rows
            ):
                raise ValueError("history preview packet has an invalid shape")
            restored.extend((role, text) for role, text in rows)
    return restored


def _evaluate_case(case: dict) -> dict:
    seed = case["history_seed"]
    repeat = case["repeat"]
    if not isinstance(repeat, int) or not 1 <= repeat <= 32:
        raise ValueError("invalid frozen repeat count")
    if not 1 <= len(seed) <= 16 or not all(
        isinstance(row, list) and len(row) == 2 and row[0] in {"user", "assistant"} and isinstance(row[1], str)
        for row in seed
    ):
        raise ValueError("invalid frozen story dialogue")
    raw_history = [tuple(row) for row in seed] * repeat
    history = [(role, format_user_dialogue_action(text) if role == "user" else text) for role, text in raw_history]
    rows = tuple((index + 1, role, text) for index, (role, text) in enumerate(raw_history))
    source_rows = rows
    if case.get("source_mutation"):
        source_rows = (
            (*rows[0][0:2], rows[0][2] + " [outdated source]"),
            *rows[1:],
        )

    scope = MemoryReadScope(
        chat_id="synthetic",
        session_id="synthetic:" + case["id"],
        session_created_at=1.0,
        through_rowid=len(rows),
        rewrite_revision=0,
        principals=("rowan",),
        historical=bool(case.get("historical", False)),
    )
    policy = "Preserve character voice, narrator bounds, reader knowledge and user agency."
    current_user = case["query"]
    baseline = [
        {"role": "system", "content": policy},
        *(
            {"role": role, "content": text, "_context_history_index": index}
            for index, (role, text) in enumerate(history)
        ),
        {"role": "user", "content": current_user},
    ]
    candidate, reframed_turns, reason = preview_packed_history(
        baseline,
        source_rows,
        scope,
        coverage_valid=case.get("coverage_valid", True) is True,
        query=current_user,
    )
    reconstructed = _recover_original_turns(candidate)
    direct_messages = [message for message in candidate if "_context_history_index" in message]
    expected = list(history)
    anchors_preserved = all(
        sum(anchor in text for _role, text in history)
        == sum(anchor in str(message["content"]) for message in direct_messages)
        for anchor in case.get("protected_anchors", [])
    )
    last_assistant_index = next(
        (i for i in range(len(history) - 1, -1, -1) if history[i][0] == "assistant"),
        None,
    )
    continuation_preserved = last_assistant_index is None or any(
        item.get("_context_history_index") == last_assistant_index
        and item["role"] == "assistant"
        and item["content"] == history[last_assistant_index][1]
        for item in direct_messages
    )
    baseline_tokens = estimate_message_tokens(baseline)
    candidate_tokens = estimate_message_tokens(candidate)
    mandatory = candidate[0]["content"] == policy and candidate[0]["role"] == "system"
    current = candidate[-1]["content"] == current_user and candidate[-1]["role"] == "user"
    safe = bool(
        reconstructed == expected
        and anchors_preserved
        and continuation_preserved
        and mandatory
        and current
        and candidate_tokens <= baseline_tokens
        and (reason != "selected" or reframed_turns > 0)
        and (reason == "selected" or candidate == baseline)
    )
    if not safe:
        raise ValueError("synthetic history invariants failed for " + case["id"])
    return {
        "id": case["id"],
        "weight": case["weight"],
        "reason": reason,
        "history_turns": len(rows),
        "reframed_turns": reframed_turns,
        "baseline_estimated_tokens": baseline_tokens,
        "candidate_estimated_tokens": candidate_tokens,
        "exact_source_reconstruction": reconstructed == expected,
        "protected_anchors_preserved": anchors_preserved,
        "mandatory_preserved": mandatory,
        "current_user_preserved": current,
        "continuation_ending_preserved": continuation_preserved,
    }


def run_study() -> dict:
    fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))
    if fixture["schema_version"] != 1:
        raise ValueError("invalid frozen fixture schema")
    cases = fixture["cases"]
    if len(cases) > 24 or len({item["id"] for item in cases}) != len(cases):
        raise ValueError("invalid frozen case inventory")
    results = [_evaluate_case(case) for case in cases]
    if not math.isclose(sum(row["weight"] for row in results), 1.0):
        raise ValueError("frozen weights must sum to one")
    baseline = sum(row["baseline_estimated_tokens"] for row in results)
    candidate = sum(row["candidate_estimated_tokens"] for row in results)
    weighted_baseline = sum(row["weight"] * row["baseline_estimated_tokens"] for row in results)
    weighted_candidate = sum(row["weight"] * row["candidate_estimated_tokens"] for row in results)
    return {
        "schema_version": 1,
        "source_authority": "synthetic_rows_not_live_database",
        "provider_requests": 0,
        "model_evaluation": "not_executed",
        "human_review": "required",
        "provider_reported_input_tokens": None,
        "total_accepted_work_input_tokens": None,
        "cases": results,
        "estimated": {
            "aggregate_reduction_fraction": (baseline - candidate) / baseline if baseline else None,
            "weighted_reduction_fraction": (
                (weighted_baseline - weighted_candidate) / weighted_baseline if weighted_baseline else None
            ),
            "basis": "synthetic_character_ratio_not_provider_usage",
        },
        "decision": {
            "approval_ready": False,
            "blocking_reasons": [
                "no_native_database_coverage_or_causal_proof",
                "provider_reported_matched_input_required",
                "helper_repair_fallback_accounting_required",
                "blinded_narrative_review_required",
                "separately_approved_model_and_cost_cap_required",
                "signed_staged_rollout_approval_required",
            ],
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = run_study()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, sort_keys=True, allow_nan=False) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
