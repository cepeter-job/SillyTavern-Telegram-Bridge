"""Offline role-preserving source excerpts, never an authorized runtime prompt.

Native receipts authenticate the full baseline and selected substrings. They do
not prove that omitted descriptions are semantically redundant. The experiment
therefore returns the full baseline for dispatch and only a marked preview.
"""

from __future__ import annotations

import copy
import hashlib
import sqlite3
from collections.abc import Callable

from bridge.context_compaction import estimate_message_tokens
from bridge.context_hybrid_sources import capture_hybrid_sources
from bridge.context_hybrid_types import HISTORY_MARKER, SHADOW_MARKER, HybridOptions, HybridShadowResult
from bridge.context_native_receipt import capture_native_history
from bridge.context_statement_policy import (
    StatementSelection,
    StatementSpan,
    select_statement_spans,
    split_complete_sentences,
    validate_statement_receipts,
)
from bridge.memory_contracts import MemoryReadScope

OMISSION = "[... historical description omitted ...]"


def _protected_spans(snapshot, query: str, options: HybridOptions) -> dict[int, tuple[StatementSpan, ...]]:
    plan = select_statement_spans(snapshot.rows, query, options)
    selected = dict(plan.selected)
    for i, row in enumerate(snapshot.rows[: -options.recent_turns]):
        parts = split_complete_sentences(row.text)
        if row.role == "user" or i == 0 or parts is None:
            intervals = [(0, len(row.text))]
        else:
            intervals = [(span.start, span.end) for span in selected.get(i, ())]
            # Preserve whole quoted paragraphs plus their local referents.
            for ordinal, (start, end) in enumerate(parts):
                if any(char in row.text[start:end] for char in ('"', "“", "”", "‘")):
                    intervals.extend(parts[max(0, ordinal - 1) : ordinal + 2])
            intervals = sorted(set(intervals))
        merged: list[tuple[int, int]] = []
        for start, end in intervals:
            if merged and (start <= merged[-1][1] or not row.text[merged[-1][1] : start].strip()):
                merged[-1] = (merged[-1][0], max(end, merged[-1][1]))
            else:
                merged.append((start, end))
        selected[i] = tuple(
            StatementSpan(a, b, row.text[a:b], hashlib.sha256(row.text[a:b].encode("utf-8")).hexdigest())
            for a, b in merged
        )
    receipts = StatementSelection(tuple((i, s) for i, s in sorted(selected.items()) if s), {})
    validate_statement_receipts(snapshot.rows, receipts)
    return selected


def _excerpt(text: str, spans: tuple[StatementSpan, ...]) -> str:
    if len(spans) == 1 and spans[0].start == 0 and spans[0].end == len(text):
        return text
    pieces = []
    cursor = 0
    for span in spans:
        gap = text[cursor : span.start]
        pieces.append("\n" + OMISSION + "\n" if gap.strip() else gap)
        pieces.append(span.text)
        cursor = span.end
    gap = text[cursor:]
    pieces.append("\n" + OMISSION if gap.strip() else gap)
    return "".join(pieces).strip() or OMISSION


def evaluate_extractive_context(
    db: sqlite3.Connection,
    scope: MemoryReadScope,
    messages: list[dict],
    *,
    query: str,
    options: HybridOptions | None = None,
    resolve_current_scope: Callable[[], MemoryReadScope | None] | None = None,
) -> HybridShadowResult:
    """Preserve all user turns, role/order and fixed inputs; dispatch stays unchanged."""
    options = options or HybridOptions()
    baseline = copy.deepcopy(messages)
    original = estimate_message_tokens(baseline, chars_per_token=options.chars_per_token)
    metrics: dict[str, object] = {
        "candidate_status": "fallback",
        "reason": "native_evidence_unavailable",
        "baseline_tokens": original,
        "candidate_tokens": original,
        "estimated_reduction_fraction": 0.0,
        "changed_assistant_turns": 0,
        "native_source_verified": False,
        "selected_source_spans_verified": False,
        "all_source_evidence_preserved": False,
        "semantic_continuity_proven": False,
        "production_activation_allowed": False,
        "dispatch_uses_full_history": True,
        "provider_requests": 0,
        "archive_blocks_added": 0,
    }

    def fallback(reason: str) -> HybridShadowResult:
        metrics.update(
            candidate_status="fallback",
            reason=reason,
            candidate_tokens=original,
            estimated_reduction_fraction=0.0,
            changed_assistant_turns=0,
        )
        return HybridShadowResult(copy.deepcopy(baseline), copy.deepcopy(baseline), dict(metrics))

    try:
        if any(SHADOW_MARKER in m or "_history_codec" in m for m in baseline):
            return fallback("existing_evaluation_candidate")
        if resolve_current_scope is not None and resolve_current_scope() != scope:
            return fallback("reader_or_source_changed")
        native = capture_native_history(db, scope, baseline)
        snapshot = capture_hybrid_sources(db, scope, baseline)
        if len(snapshot.rows) <= options.recent_turns + 3:
            return fallback("insufficient_older_history")
        selected = _protected_spans(snapshot, query, options)
        candidate = copy.deepcopy(baseline)
        changed = 0
        for before, after in zip(baseline, candidate, strict=True):
            i = before.get(HISTORY_MARKER)
            if i is None or i == 0 or i >= len(snapshot.rows) - options.recent_turns or before["role"] == "user":
                continue
            row = snapshot.rows[i]
            excerpt = _excerpt(row.text, selected.get(i, ()))
            if len(excerpt) < len(before["content"]):
                after["content"] = excerpt
                after[SHADOW_MARKER] = True
                changed += 1
        # No archive text is added: native archives validate provenance only.
        # Retained source is NOT a complete semantic substitute for omitted text.
        if [m["role"] for m in candidate] != [m["role"] for m in baseline]:
            return fallback("protected_payload_changed")
        if [m for m in baseline if HISTORY_MARKER not in m] != [m for m in candidate if HISTORY_MARKER not in m]:
            return fallback("protected_payload_changed")
        current = estimate_message_tokens(candidate, chars_per_token=options.chars_per_token)
        if not changed or current >= original:
            return fallback("no_savings")
        if resolve_current_scope is not None and resolve_current_scope() != scope:
            return fallback("reader_or_source_changed")
        if (
            capture_native_history(db, scope, baseline) != native
            or capture_hybrid_sources(db, scope, baseline) != snapshot
        ):
            return fallback("reader_or_source_changed")
        metrics.update(
            candidate_status="preview",
            reason="independent_continuity_review_required",
            candidate_tokens=current,
            estimated_reduction_fraction=1 - current / original,
            changed_assistant_turns=changed,
            native_source_verified=True,
            selected_source_spans_verified=True,
        )
        return HybridShadowResult(copy.deepcopy(baseline), candidate, metrics)
    except (ValueError, TypeError, KeyError, AttributeError, sqlite3.Error):
        # Exceptions may contain private text. Export only this fixed reason.
        return fallback("native_or_required_evidence_unavailable")
