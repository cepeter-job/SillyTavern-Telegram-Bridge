"""Local pre-dispatch proofs distinguish uncertain selection from revoked sources."""

from __future__ import annotations

import sqlite3
from collections.abc import Callable
from dataclasses import replace

from bridge.context_selection import context_selection_mode, context_slice_enabled, select_memory_blocks
from bridge.memory_contracts import ContextBlockSelection, MemoryBlock, MemoryPromptContext, MemoryReadScope
from bridge.port_contracts import ValidateMemoryBlocks
from bridge.settings import AppSettings


def _summary_coverage_valid(db: sqlite3.Connection, context: MemoryPromptContext) -> bool:
    scope = context.scope
    summaries = [block for block in context.baseline_blocks if block.channel == "summary" and block.text]
    if scope is None or len(summaries) != 1 or not summaries[0].evidence:
        return False
    boundaries = {pointer.source_end_rowid for pointer in summaries[0].evidence}
    if len(boundaries) != 1:
        return False
    through = next(iter(boundaries))
    if through <= 0 or through > scope.through_rowid:
        return False
    state = db.execute(
        "SELECT covered_id,purge_epoch,invalidated_from_id FROM memory_layer_state "
        "WHERE chat_id=? AND session_id=? AND session_created_at=? AND layer='summary'",
        (scope.chat_id, scope.session_id, scope.session_created_at),
    ).fetchone()
    if state is None or state[0] < through or state[2] is not None:
        return False
    rows = db.execute(
        "SELECT m.id,length(m.content),g.start_offset,g.end_offset FROM messages m "
        "LEFT JOIN memory_segments g ON g.chat_id=m.chat_id AND g.session_id=m.session_id "
        "AND g.session_created_at=? AND g.layer='summary' AND g.valid=1 AND g.purge_epoch=? "
        "AND g.start_id=m.id AND g.end_id=m.id "
        "WHERE m.chat_id=? AND m.session_id=? AND m.id<=? ORDER BY m.id,g.start_offset,g.end_offset",
        (scope.session_created_at, state[1], scope.chat_id, scope.session_id, through),
    )
    previous = None
    offset = length = 0
    try:
        for rowid, row_length, start, end in rows:
            if rowid != previous:
                if offset != length:
                    return False
                previous, offset, length = rowid, 0, row_length
            if start is None or start != offset or end < start or end > length:
                return False
            offset = end
    finally:
        rows.close()
    return previous is not None and offset == length


def _guard_reason(
    db: sqlite3.Connection,
    scope: MemoryReadScope,
    blocks: tuple[MemoryBlock, ...],
    resolve_current_scope: Callable[[], MemoryReadScope | None],
    validate_blocks: ValidateMemoryBlocks,
) -> str:
    try:
        current = resolve_current_scope()
        if current is None or current.session_created_at != scope.session_created_at:
            return "invalid_scope"
        if current != scope:
            return "source_changed"
        if validate_blocks(db, scope, blocks) != blocks:
            return "source_changed"
        pending = db.execute(
            "SELECT 1 FROM memory_layer_state WHERE chat_id=? AND session_id=? AND session_created_at=? "
            "AND layer IN ('episodes','hindsight','summary','scene') AND invalidated_from_id IS NOT NULL LIMIT 1",
            (scope.chat_id, scope.session_id, scope.session_created_at),
        ).fetchone()
        if pending:
            return "pending_invalidation"
        return ""
    except Exception:
        # Selection is optional. A failed local proof cannot authorize a smaller prompt.
        return "ambiguous"


def prepare_context_selection(
    db: sqlite3.Connection,
    context: MemoryPromptContext,
    resolve_current_scope: Callable[[], MemoryReadScope | None],
    *,
    app_settings: AppSettings | None,
    validate_blocks: ValidateMemoryBlocks,
) -> MemoryPromptContext:
    mode = context_selection_mode(app_settings) if app_settings is not None else "off"
    if mode == "off":
        return context
    context = replace(context, selection_mode=mode)
    dedup = mode == "shadow" or (app_settings is not None and context_slice_enabled(app_settings, "dedup"))
    history = mode == "shadow" or (app_settings is not None and context_slice_enabled(app_settings, "history"))
    if not dedup and not history:
        return replace(context, selection_reason="not_approved")
    scope = context.scope
    if scope is None:
        return replace(context, selection_reason="invalid_scope")
    baseline_blocks = context.baseline_blocks

    def guard() -> str:
        return _guard_reason(db, scope, baseline_blocks, resolve_current_scope, validate_blocks)

    context = replace(context, selection_guard=guard)
    reason = guard()
    if reason:
        return replace(context, selection_reason=reason)
    candidate = select_memory_blocks(scope, context.baseline_blocks)
    if candidate.reason in {"invalid_scope", "historical"}:
        return replace(context, selection_reason=candidate.reason)
    if not dedup:
        candidate = ContextBlockSelection(
            context.baseline_blocks, candidate.selected_blocks + candidate.deduplicated_blocks, reason="no_savings"
        )
    coverage_valid = False
    if history:
        try:
            coverage_valid = _summary_coverage_valid(db, context)
            history_reason = "ambiguous" if coverage_valid else "incomplete_coverage"
        except Exception:
            history_reason = "ambiguous"
        # Accepted source coverage and an audience label do not establish which facts,
        # negations, callbacks or causal supports a smaller history must preserve.
        # The current canonical contracts contain no such closure proof: retain all history.
        if not candidate.deduplicated_blocks:
            candidate = replace(candidate, reason=history_reason)
    return replace(
        context,
        selection=candidate,
        selection_reason=candidate.reason,
        selection_coverage_valid=coverage_valid,
        selection_guard=guard,
    )
