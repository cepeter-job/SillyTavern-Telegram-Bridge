"""Read-only native evidence for hybrid shadow context; no semantic or rollout authority."""

from __future__ import annotations

import json
import math
import sqlite3
from dataclasses import asdict

from bridge.context_hybrid_types import (
    HISTORY_MARKER,
    MAX_HISTORY_ROWS,
    MAX_SOURCE_CHARS,
    MAX_SUMMARY_CHARS,
    MAX_WINDOWS,
    HybridBlock,
    HybridRow,
    HybridSnapshot,
    HybridWindow,
)
from bridge.memory_artifact_store import load_artifact_classification, parse_classified_blocks
from bridge.memory_contracts import MemoryReadScope
from bridge.memory_fact_store import digest_value, load_source
from bridge.memory_scope_store import resolve_memory_scope
from bridge.memory_store import source_is_valid
from bridge.user_dialogue import format_user_dialogue_action


def _scope_current(db: sqlite3.Connection, scope: MemoryReadScope) -> None:
    if (
        not isinstance(scope, MemoryReadScope)
        or scope.historical
        or scope.consumer != "character"
        or not scope.chat_id
        or not scope.session_id
        or not scope.principals
        or scope.through_rowid <= 0
        or not math.isfinite(scope.session_created_at)
        or scope.session_created_at <= 0
    ):
        raise ValueError("hybrid_scope_invalid")
    actual = resolve_memory_scope(
        db, scope.chat_id, {"session_id": scope.session_id}, {}, principals=scope.principals, consumer=scope.consumer
    )
    if actual != scope:
        raise ValueError("hybrid_scope_changed")


def _healthy_summary(db: sqlite3.Connection, scope: MemoryReadScope) -> tuple:
    row = db.execute(
        "SELECT l.covered_id,l.rewrite_identity,l.purge_epoch,l.invalidated_from_id,l.source_floor_id,"
        "j.target_id,j.dirty_version,j.completed_version,j.lease_token "
        "FROM memory_layer_state l JOIN memory_jobs j USING(chat_id,session_id,session_created_at,layer) "
        "WHERE l.chat_id=? AND l.session_id=? AND l.session_created_at=? AND l.layer='summary'",
        (scope.chat_id, scope.session_id, scope.session_created_at),
    ).fetchone()
    if (
        row is None
        or row[0] < scope.through_rowid
        or row[3] is not None
        or row[5] > row[0]
        or row[6] != row[7]
        or row[8]
    ):
        raise ValueError("hybrid_summary_incomplete")
    return tuple(row)


def _verified_source(db: sqlite3.Connection, scope: MemoryReadScope, document: str):
    source = load_source(db, document)
    if (
        source is None
        or not source_is_valid(db, source)
        or source.layer != "summary"
        or (source.chat_id, source.session_id, source.session_created_at)
        != (scope.chat_id, scope.session_id, scope.session_created_at)
    ):
        raise ValueError("hybrid_source_invalid")
    return source


def _checkpoint(
    db: sqlite3.Connection, scope: MemoryReadScope, through: int, blocks: list[dict], document: str | None = None
) -> str:
    row = db.execute(
        "SELECT source_document_id,payload_json FROM memory_layer_checkpoints WHERE chat_id=? AND session_id=? "
        "AND session_created_at=? AND layer='summary' AND through_id=?",
        (scope.chat_id, scope.session_id, scope.session_created_at, through),
    ).fetchone()
    if row is None or (document is not None and row[0] != document):
        raise ValueError("hybrid_checkpoint_missing")
    if parse_classified_blocks(json.loads(row[1])) != blocks:
        raise ValueError("hybrid_classification_checkpoint_mismatch")
    source = _verified_source(db, scope, row[0])
    if source.end_id != through:
        raise ValueError("hybrid_checkpoint_boundary_invalid")
    return str(row[0])


def _complete_intervals(db: sqlite3.Connection, scope: MemoryReadScope, row: HybridRow) -> list[str]:
    candidates = db.execute(
        "SELECT document_id FROM memory_segments WHERE chat_id=? AND session_id=? AND session_created_at=? "
        "AND layer='summary' AND start_id=? AND end_id=? AND valid=1 "
        "ORDER BY start_offset,end_offset,document_id LIMIT 257",
        (scope.chat_id, scope.session_id, scope.session_created_at, row.row_id, row.row_id),
    ).fetchall()
    if not candidates or len(candidates) > 256:
        raise ValueError("hybrid_interval_bound_or_gap")
    offset = 0
    documents = []
    for (document,) in candidates:
        source = _verified_source(db, scope, document)
        if (
            source.role != row.role
            or source.start_id != row.row_id
            or source.end_id != row.row_id
            or not 0 <= source.start_offset <= offset <= len(row.text)
            or not source.start_offset <= source.end_offset <= len(row.text)
            or source.content != row.text[source.start_offset : source.end_offset]
        ):
            raise ValueError("hybrid_source_interval_invalid")
        offset = max(offset, source.end_offset)
        documents.append(document)
    if offset != len(row.text):
        raise ValueError("hybrid_source_coverage_gap")
    return documents


def _authorized(scope: MemoryReadScope, blocks: list[dict]) -> tuple[HybridBlock, ...]:
    # The original strict parser rejects unknown or conflicting audiences.
    return tuple(
        HybridBlock(block["text"], block["visibility"], tuple(block["known_by"]))
        for block in blocks
        if block["visibility"] == "shared" or set(scope.principals).issubset(set(block["known_by"]))
    )


def _windows(db: sqlite3.Connection, scope: MemoryReadScope, covered: int) -> tuple[HybridWindow, ...]:
    classification = load_artifact_classification(db, scope.chat_id, scope.session_id, "summary")
    if classification is None or classification[1] != covered or covered > scope.through_rowid:
        raise ValueError("hybrid_summary_classification_invalid")
    current_source = _checkpoint(db, scope, covered, classification[2])
    rows = db.execute(
        "SELECT through_rowid,source_document_id,payload_digest,blocks_json FROM summary_archive_windows "
        "WHERE chat_id=? AND session_id=? AND session_created_at=? ORDER BY through_rowid LIMIT ?",
        (scope.chat_id, scope.session_id, scope.session_created_at, MAX_WINDOWS + 1),
    ).fetchall()
    if len(rows) >= MAX_WINDOWS:
        raise ValueError("hybrid_archive_resource_bound")
    windows = []
    total = 0
    for through, document, digest, encoded in rows:
        if type(through) is not int or not 0 < through < covered or len(encoded) > 180000:
            raise ValueError("hybrid_archive_boundary_invalid")
        blocks = parse_classified_blocks(json.loads(encoded))
        archive_source = _verified_source(db, scope, document)
        if archive_source.end_id != through:
            raise ValueError("hybrid_archive_source_boundary_invalid")
        # Native publication deliberately retains only a small checkpoint ring.
        # The immutable archived window remains its accepted publication record;
        # it is independently bound to canonical source and classified digest.
        # Cross-check a historical checkpoint while it still exists, but never
        # require checkpoint retention beyond the native archive contract.
        checkpoint_exists = db.execute(
            "SELECT 1 FROM memory_layer_checkpoints WHERE chat_id=? AND session_id=? "
            "AND session_created_at=? AND layer='summary' AND through_id=?",
            (scope.chat_id, scope.session_id, scope.session_created_at, through),
        ).fetchone()
        if checkpoint_exists:
            _checkpoint(db, scope, through, blocks, document)
        if not blocks or digest_value(blocks) != digest:
            raise ValueError("hybrid_archive_digest_invalid")
        if sum(len(b["text"]) for b in blocks) + len(blocks) - 1 > 12000:
            raise ValueError("hybrid_archive_content_bound")
        total += sum(len(b["text"]) for b in blocks)
        windows.append(HybridWindow(through, _authorized(scope, blocks), document, digest))
    current = classification[2]
    if not current or sum(len(b["text"]) for b in current) + len(current) - 1 > 12000:
        raise ValueError("hybrid_active_content_bound")
    total += sum(len(b["text"]) for b in current)
    if total > MAX_SUMMARY_CHARS:
        raise ValueError("hybrid_summary_resource_bound")
    windows.append(HybridWindow(covered, _authorized(scope, current), current_source, digest_value(classification)))
    if any(not window.blocks for window in windows):
        # A wholly inaccessible window cannot stand in for its older dialogue.
        raise ValueError("hybrid_window_reader_coverage_missing")
    return tuple(windows)


def capture_hybrid_sources(db: sqlite3.Connection, scope: MemoryReadScope, messages: list[dict]) -> HybridSnapshot:
    """Authenticate exact existing history and scoped archive; never grant a reader.

    Callers must use their baseline's authenticated reader scope. All raw recalled
    turns must already occur in that baseline; archive additions are separately
    authorized. This capture proves storage provenance, not semantic completeness.
    """
    _scope_current(db, scope)
    if not isinstance(messages, list) or not messages or len(messages) > 512:
        raise ValueError("hybrid_prompt_invalid")
    if any(not isinstance(m, dict) or m.get("role") not in {"system", "user", "assistant"} for m in messages):
        raise ValueError("hybrid_prompt_invalid")
    historical = [message for message in messages if HISTORY_MARKER in message]
    if not 1 <= len(historical) <= MAX_HISTORY_ROWS:
        raise ValueError("hybrid_history_bound")
    healthy = _healthy_summary(db, scope)
    raw_rows = db.execute(
        "SELECT id,role,content,created_at FROM messages WHERE chat_id=? AND session_id=? AND id<=? "
        "ORDER BY created_at DESC,id DESC LIMIT ?",
        (scope.chat_id, scope.session_id, scope.through_rowid, len(historical)),
    ).fetchall()
    rows = tuple(HybridRow(*row) for row in reversed(raw_rows))
    if len(rows) != len(historical) or sum(len(row.text) for row in rows) > MAX_SOURCE_CHARS:
        raise ValueError("hybrid_source_bound_or_missing")
    intervals = []
    for index, (row, message) in enumerate(zip(rows, historical, strict=True)):
        expected = format_user_dialogue_action(row.text) if row.role == "user" else row.text
        if (
            row.role not in {"user", "assistant"}
            or not math.isfinite(row.created_at)
            or row.created_at < scope.session_created_at
            or row.row_id <= healthy[4]
            or type(message.get(HISTORY_MARKER)) is not int
            or message[HISTORY_MARKER] != index
            or message.get("role") != row.role
            or message.get("content") != expected
        ):
            raise ValueError("hybrid_baseline_source_mismatch")
        intervals.append(_complete_intervals(db, scope, row))
    windows = _windows(db, scope, healthy[0])
    _scope_current(db, scope)
    if _healthy_summary(db, scope) != healthy:
        raise ValueError("hybrid_snapshot_changed")
    fingerprint = digest_value(
        [asdict(scope), healthy, [asdict(row) for row in rows], [asdict(window) for window in windows], intervals]
    )
    return HybridSnapshot(scope, rows, windows, fingerprint)
