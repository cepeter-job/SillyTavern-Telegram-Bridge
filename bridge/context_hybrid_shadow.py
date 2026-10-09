"""Hybrid context proposal with full-history dispatch and fail-closed native evidence."""

from __future__ import annotations

import copy
import json
import sqlite3
from collections.abc import Callable

from bridge.context_compaction import estimate_message_tokens
from bridge.context_hybrid_policy import retained_indices, supported_script
from bridge.context_hybrid_sources import capture_hybrid_sources
from bridge.context_hybrid_types import HISTORY_MARKER, SHADOW_MARKER, HybridOptions, HybridShadowResult, HybridSnapshot
from bridge.memory_contracts import MemoryReadScope

_REFERENCE = (
    "Quoted historical continuity reference, not new user actions or instructions. "
    "Snapshots are chronological and bound to source rows; an old promise or refusal is not revoked "
    "unless later source explicitly changes it. Only listed reader audiences know restricted facts. "
    "Retained dialogue keeps its original speaker and order. This is a shadow evaluation only.\n"
)


def _preview(messages: list[dict], snapshot: HybridSnapshot, keep: set[int]) -> list[dict]:
    packet = [
        {
            "through_rowid": window.through_rowid,
            "blocks": [
                {"text": block.text, "visibility": block.visibility, "known_by": list(block.known_by)}
                for block in window.blocks
            ],
        }
        for window in snapshot.windows
    ]
    reference = {
        "role": "user",
        "content": _REFERENCE + json.dumps(packet, ensure_ascii=False, separators=(",", ":")),
        SHADOW_MARKER: True,
    }
    result = []
    inserted = False
    for message in messages:
        index = message.get(HISTORY_MARKER)
        if index is not None and index not in keep:
            if not inserted:
                result.append(reference)
                inserted = True
        else:
            result.append(copy.deepcopy(message))
    return result


def evaluate_hybrid_shadow(
    db: sqlite3.Connection,
    scope: MemoryReadScope,
    messages: list[dict],
    *,
    query: str,
    options: HybridOptions | None = None,
    resolve_current_scope: Callable[[], MemoryReadScope | None] | None = None,
) -> HybridShadowResult:
    """Return baseline for dispatch, and a separately marked non-dispatchable preview.

    Native storage proof authenticates Summary windows; it cannot demonstrate that
    a generative extractor captured every causal fact. Full-history fallback is
    therefore mandatory for actual requests, even for a smaller shadow proposal.
    """
    options = options or HybridOptions()
    baseline = copy.deepcopy(messages)
    original_tokens = estimate_message_tokens(baseline, chars_per_token=options.chars_per_token)
    metrics: dict[str, object] = {
        "candidate_status": "fallback",
        "reason": "native_evidence_missing",
        "baseline_tokens": original_tokens,
        "candidate_tokens": original_tokens,
        "estimated_reduction_fraction": 0.0,
        "omitted_turns": 0,
        "retained_turns": sum(HISTORY_MARKER in m for m in baseline),
        "native_source_verified": False,
        "semantic_continuity_proven": False,
        "production_activation_allowed": False,
        "dispatch_uses_full_history": True,
        "provider_requests": 0,
        "anchor_turns": 0,
        "relevant_turns": 0,
        "recent_turns": 0,
        "summary_windows": 0,
        "authorized_summary_blocks": 0,
    }

    def fallback(reason: str) -> HybridShadowResult:
        metrics["reason"] = reason
        return HybridShadowResult(copy.deepcopy(baseline), copy.deepcopy(baseline), dict(metrics))

    try:
        if any(SHADOW_MARKER in m for m in baseline):
            return fallback("already_a_shadow_candidate")
        if resolve_current_scope is not None and resolve_current_scope() != scope:
            return fallback("reader_or_source_changed")
        captured = capture_hybrid_sources(db, scope, baseline)
        metrics["native_source_verified"] = True
        if len(captured.rows) <= options.recent_turns + 3:
            return fallback("insufficient_older_history")
        if any(not supported_script(row.text) for row in captured.rows[: -options.recent_turns]):
            return fallback("unsupported_older_script")
        retained, stats = retained_indices(captured.rows, query, options)
        metrics.update(stats)
        if len(retained) == len(captured.rows):
            return fallback("all_history_protected")
        candidate = _preview(baseline, captured, retained)
        # Every non-history payload remains exact, including card/world/system,
        # media attachments, post-history contracts and current user input.
        before = [m for m in baseline if HISTORY_MARKER not in m]
        after = [m for m in candidate if HISTORY_MARKER not in m and SHADOW_MARKER not in m]
        if before != after:
            return fallback("protected_payload_changed")
        candidate_tokens = estimate_message_tokens(candidate, chars_per_token=options.chars_per_token)
        if candidate_tokens >= original_tokens:
            return fallback("no_savings")
        if resolve_current_scope is not None and resolve_current_scope() != scope:
            metrics["native_source_verified"] = False
            return fallback("reader_or_source_changed")
        fresh = capture_hybrid_sources(db, scope, baseline)
        if fresh != captured:
            metrics["native_source_verified"] = False
            return fallback("source_or_archive_changed")
        metrics.update(
            candidate_status="preview",
            reason="semantic_review_required",
            candidate_tokens=candidate_tokens,
            estimated_reduction_fraction=1 - candidate_tokens / original_tokens,
            omitted_turns=len(captured.rows) - len(retained),
            retained_turns=len(retained),
            summary_windows=len(captured.windows),
            authorized_summary_blocks=sum(len(window.blocks) for window in captured.windows),
        )
        return HybridShadowResult(copy.deepcopy(baseline), candidate, metrics)
    except (ValueError, TypeError, KeyError, AttributeError, sqlite3.Error):
        # No transcript, exception message, session ID or private fact enters telemetry.
        metrics["native_source_verified"] = False
        return fallback("native_evidence_missing_or_changed")


def make_hybrid_shadow_probe(
    db: sqlite3.Connection,
    scope: MemoryReadScope,
    query: str,
    resolve_current_scope: Callable[[], MemoryReadScope | None],
) -> Callable[[list[dict], float], dict[str, object]]:
    """Runtime captures no candidate body: only content-free metrics leave this closure."""

    def probe(messages: list[dict], chars_per_token: float) -> dict[str, object]:
        from bridge.context_statement_shadow import evaluate_statement_shadow

        options = HybridOptions(chars_per_token=chars_per_token)
        whole = evaluate_hybrid_shadow(
            db,
            scope,
            messages,
            query=query,
            options=options,
            resolve_current_scope=resolve_current_scope,
        ).metrics
        statements = evaluate_statement_shadow(
            db,
            scope,
            messages,
            query=query,
            options=options,
            resolve_current_scope=resolve_current_scope,
        ).metrics
        return {
            **whole,
            "statement_candidate_tokens": statements["candidate_tokens"],
            "statement_reason": statements["reason"],
            "statement_source_verified": statements["source_statement_receipts_verified"],
        }

    return probe
