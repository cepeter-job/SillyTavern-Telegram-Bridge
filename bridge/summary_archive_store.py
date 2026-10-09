"""Immutable, source-proven classified Summary windows and scoped recall.

Only *already accepted* classified Summary windows are archived. No model output
or transcript is copied speculatively. Current windows remain capped independently.
"""

from __future__ import annotations

import json
import sqlite3
from typing import Any

from bridge.memory_artifact_store import load_artifact_classification, parse_classified_blocks
from bridge.memory_contracts import (
    MemoryBlock,
    MemoryBlockLeaf,
    MemoryEvidence,
    MemoryReadScope,
    ordered_relevance_terms,
    relevance_terms,
)
from bridge.memory_fact_store import digest_value, load_source
from bridge.memory_store import request_source_cutoff, source_is_valid

MAX_ARCHIVED_PROMPT_CHARS = 6000
MAX_SEARCH_WINDOWS = 256


def archive_accepted_summary(
    db: sqlite3.Connection,
    chat_id: str,
    session_id: str,
    payload: dict[str, Any],
) -> None:
    """Save the full accepted previous window before replacing its live sidecar."""
    if not db.in_transaction:
        raise RuntimeError("Archiving requires the accepted publication transaction")
    through = payload.get("_summary_rollover_from")
    digest = payload.get("_summary_rollover_digest")
    if type(through) is not int or through <= 0 or not isinstance(digest, str) or len(digest) != 64:
        raise ValueError("Summary rollover requires an authenticated previous window")
    row = db.execute(
        "SELECT s.created_at,ss.covered_until_rowid FROM sessions s "
        "JOIN session_summaries ss ON ss.chat_id=s.chat_id AND ss.session_id=s.session_id "
        "WHERE s.chat_id=? AND s.session_id=?",
        (chat_id, session_id),
    ).fetchone()
    classification = load_artifact_classification(db, chat_id, session_id, "summary")
    if row is None or row[1] != through or classification is None or classification[1] != through:
        raise ValueError("Summary rollover cannot archive unaccepted publication")
    blocks = classification[2]
    if digest_value(blocks) != digest:
        raise ValueError("Summary rollover previous payload changed")
    checkpoint = db.execute(
        "SELECT source_document_id FROM memory_layer_checkpoints "
        "WHERE chat_id=? AND session_id=? AND session_created_at=? AND layer='summary' AND through_id=?",
        (chat_id, session_id, row[0], through),
    ).fetchone()
    source = load_source(db, str(checkpoint[0])) if checkpoint else None
    if (
        source is None
        or not source_is_valid(db, source)
        or source.end_id != through
        or source.session_created_at != row[0]
        or source.layer != "summary"
    ):
        raise ValueError("Summary rollover has no authenticated source checkpoint")
    encoded = json.dumps(blocks, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    record = (chat_id, session_id, row[0], through, source.document_id, digest, encoded)
    existing = db.execute(
        "SELECT chat_id,session_id,session_created_at,through_rowid,"
        "source_document_id,payload_digest,blocks_json FROM summary_archive_windows "
        "WHERE chat_id=? AND session_id=? AND session_created_at=? AND through_rowid=?",
        (chat_id, session_id, row[0], through),
    ).fetchone()
    if existing is not None:
        if existing != record:
            raise ValueError("Conflicting historical Summary window")
        return
    db.execute("INSERT INTO summary_archive_windows VALUES(?,?,?,?,?,?,?)", record)


def read_summary_archive(
    db: sqlite3.Connection,
    scope: MemoryReadScope,
    query: str,
    *,
    required_evidence: tuple[MemoryEvidence, ...] | None = None,
    max_chars: int = MAX_ARCHIVED_PROMPT_CHARS,
) -> MemoryBlock:
    """Reauthorize every archival block from current canonical source and audience.

    Query-based recalls are bounded and lexical; validator rehydration instead uses
    exact previously selected evidence. No query means no speculative archive dump.
    """
    cutoff = request_source_cutoff(db, scope)
    if max_chars <= 0 or (required_evidence is None and not query.strip()):
        return MemoryBlock(channel="summary")
    terms = relevance_terms(query) if required_evidence is None else set()
    if required_evidence is None and not terms:
        return MemoryBlock(channel="summary")
    # Candidate windows are constrained by story incarnation and source boundary;
    # a source pointer must still validate against the current canonical bytes.
    base = (
        "SELECT a.through_rowid,a.source_document_id,a.payload_digest,a.blocks_json "
        "FROM summary_archive_windows a JOIN sessions s ON s.chat_id=a.chat_id "
        "AND s.session_id=a.session_id AND s.created_at=a.session_created_at "
        "JOIN memory_segments g ON g.document_id=a.source_document_id AND g.valid=1 "
        "AND g.layer='summary' AND g.session_created_at=a.session_created_at "
        "AND g.end_id=a.through_rowid "
        "WHERE a.chat_id=? AND a.session_id=? AND a.session_created_at=? AND a.through_rowid<=?"
    )
    args: list[object] = [scope.chat_id, scope.session_id, scope.session_created_at, cutoff]
    if required_evidence is not None:
        allowed = sorted(
            {
                ev.source_end_rowid
                for ev in required_evidence
                if ev.artifact_kind == "summary_archive" and ev.source_end_rowid > 0
            }
        )
        if not allowed:
            return MemoryBlock(channel="summary")
        base += " AND a.through_rowid IN (" + ",".join("?" for _ in allowed) + ")"
        args.extend(allowed)
    else:
        # Bound the amount parsed on a long-lived story without excluding old
        # matching windows merely because 256 newer windows also exist.
        sought = ordered_relevance_terms(query, limit=12)
        if not sought:
            return MemoryBlock(channel="summary")
        base += " AND (" + " OR ".join("instr(lower(a.blocks_json),?)>0" for _ in sought) + ")"
        args.extend(sought)
    rows = db.execute(base + " ORDER BY a.through_rowid DESC LIMIT ?", (*args, MAX_SEARCH_WINDOWS)).fetchall()
    candidates: list[tuple[int, int, str, MemoryEvidence, str, tuple[str, ...]]] = []
    required = set(required_evidence) if required_evidence is not None else None
    for through, document_id, digest, encoded in rows:
        source = load_source(db, document_id)
        if source is None or not source_is_valid(db, source):
            continue
        try:
            blocks = parse_classified_blocks(json.loads(encoded))
        except (ValueError, TypeError):
            continue
        if digest_value(blocks) != digest:
            continue
        for index, block in enumerate(blocks):
            evidence = MemoryEvidence(
                source_end_rowid=through,
                artifact_kind="summary_archive",
                artifact_digest=digest,
                block_index=index,
            )
            if required is not None and evidence not in required:
                continue
            if scope.consumer != "narrator" and (
                not scope.principals
                or (block["visibility"] != "shared" and not set(scope.principals).issubset(set(block["known_by"])))
            ):
                continue
            text = block["text"]
            relevance = len(terms & relevance_terms(text)) if required is None else 1
            if not relevance:
                continue
            candidates.append((relevance, through, text, evidence, block["visibility"], tuple(block["known_by"])))
    if required is None:
        candidates.sort(key=lambda item: (-item[0], -item[1], item[2]))
    else:
        positions = {ev: i for i, ev in enumerate(required_evidence or ())}
        candidates.sort(key=lambda item: positions[item[3]])
    used = 0
    lines: list[str] = []
    pointers: list[MemoryEvidence] = []
    leaves: list[MemoryBlockLeaf] = []
    for _score, _through, text, evidence, visibility, known_by in candidates:
        if used + len(text) + bool(lines) > max_chars:
            continue
        lines.append(text)
        pointers.append(evidence)
        leaves.append(MemoryBlockLeaf(text, evidence, scope, visibility, known_by, scope.rewrite_revision))
        used += len(text) + (len(lines) > 1)
    return MemoryBlock("\n".join(lines), tuple(pointers), "summary", tuple(leaves))
