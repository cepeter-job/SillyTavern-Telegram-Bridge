"""One locally verified story-knowledge boundary for every derived prompt channel."""

from __future__ import annotations

import sqlite3
from dataclasses import replace
from typing import Literal

from bridge.limits import EPISODIC_CONTEXT_MAX_CHARS, HINDSIGHT_CONTEXT_MAX_CHARS
from bridge.memory_artifact_store import read_artifact_block
from bridge.memory_contracts import MemoryBlock, MemoryEvidence, MemoryReadScope
from bridge.memory_fact_store import StoredMemoryFact, index_fact_is_current, load_current_fact, normalize_principals
from bridge.memory_relevance import relevance_terms
from bridge.memory_store import external_memory_boundary, request_source_cutoff


def resolve_memory_scope(
    db: sqlite3.Connection,
    chat_id: str,
    session: dict[str, str],
    fields: dict[str, str],
    *,
    through_rowid: int | None = None,
    principals: tuple[str, ...] | None = None,
    consumer: Literal["character", "narrator"] = "character",
    historical: bool | None = None,
) -> MemoryReadScope | None:
    if consumer not in {"character", "narrator"}:
        raise ValueError("Memory consumer must be explicitly character or narrator")
    session_id = session["session_id"]
    # One statement gives every request boundary the same SQLite read snapshot.
    row = db.execute(
        "SELECT s.created_at,COALESCE(n.rewrite_revision,0),"
        "(SELECT COALESCE(MAX(id),0) FROM messages m WHERE m.chat_id=s.chat_id AND m.session_id=s.session_id),"
        "(SELECT COALESCE(MAX(event_id),0) FROM memory_explicit_events e WHERE e.chat_id=s.chat_id "
        "AND e.session_id=s.session_id AND e.session_created_at=s.created_at),"
        "COALESCE((SELECT l.purge_epoch FROM memory_layer_state l WHERE l.chat_id=s.chat_id "
        "AND l.session_id=s.session_id AND l.session_created_at=s.created_at AND l.layer='hindsight'),0),"
        "(SELECT COALESCE(MAX(change_id),0) FROM memory_source_rewrites r WHERE r.chat_id=s.chat_id "
        "AND r.session_id=s.session_id AND r.session_created_at=s.created_at) "
        "FROM sessions s LEFT JOIN narrative_state n ON n.chat_id=s.chat_id AND n.session_id=s.session_id "
        "WHERE s.chat_id=? AND s.session_id=?",
        (chat_id, session_id),
    ).fetchone()
    if row is None:
        return None
    latest = int(row[2])
    return MemoryReadScope(
        chat_id,
        session_id,
        float(row[0]),
        latest if through_rowid is None else max(0, min(int(through_rowid), latest)),
        int(row[1]),
        normalize_principals(principals if principals is not None else (fields.get("name", ""),)),
        consumer,
        through_rowid is not None if historical is None else historical,
        int(row[3]),
        int(row[4]),
        int(row[5]),
    )


def scope_is_current(db: sqlite3.Connection, scope: MemoryReadScope, *, external: bool = False) -> bool:
    row = db.execute(
        "SELECT created_at FROM sessions WHERE chat_id=? AND session_id=?",
        (scope.chat_id, scope.session_id),
    ).fetchone()
    if row is None or row[0] != scope.session_created_at:
        return False
    if external:
        boundary = external_memory_boundary(db, scope.chat_id, scope.session_id)
        return bool(boundary and boundary["purge_epoch"] == scope.external_epoch)
    return True


def audience_allows(scope: MemoryReadScope, visibility: str, known_by: tuple[str, ...]) -> bool:
    if scope.consumer == "narrator":
        return True
    return bool(scope.principals) and (visibility == "shared" or set(scope.principals).issubset(set(known_by)))


def eligible_fact(db: sqlite3.Connection, scope: MemoryReadScope, memory_id: int) -> StoredMemoryFact | None:
    if not scope_is_current(db, scope):
        return None
    stored = load_current_fact(db, memory_id)
    if stored is None or (stored.chat_id, stored.session_id, stored.session_created_at) != (
        scope.chat_id,
        scope.session_id,
        scope.session_created_at,
    ):
        return None
    if not audience_allows(scope, stored.fact.visibility, stored.fact.known_by):
        return None
    if stored.provenance_kind == "explicit":
        if stored.evidence.explicit_event_id > scope.explicit_event_cutoff or (
            scope.historical and scope.through_rowid <= stored.accepted_after_rowid
        ):
            return None
    elif stored.evidence.source_end_rowid > request_source_cutoff(db, scope):
        return None
    return stored


def _fact_block(
    facts: list[tuple[StoredMemoryFact, str]], channel: str, *, limit: int = 6, max_chars: int
) -> MemoryBlock:
    lines: list[str] = []
    evidence: list[MemoryEvidence] = []
    seen: set[str] = set()
    size = 0
    for stored, document_id in facts:
        key = " ".join(stored.fact.summary.split()).casefold()
        if key in seen:
            continue
        line = f"[{stored.fact.kind}] {stored.fact.summary}"
        if size + len(line) + bool(lines) > max_chars:
            continue
        seen.add(key)
        lines.append(line)
        evidence.append(replace(stored.evidence, document_id=document_id))
        size += len(line) + (len(lines) > 1)
        if len(lines) >= limit:
            break
    return MemoryBlock("\n".join(lines), tuple(evidence), channel)


def read_episodic_block(
    db: sqlite3.Connection,
    scope: MemoryReadScope,
    query: str,
    *,
    limit: int = 6,
    max_chars: int = EPISODIC_CONTEXT_MAX_CHARS,
) -> MemoryBlock:
    if limit <= 0 or max_chars <= 0:
        return MemoryBlock(channel="episodic")
    terms = relevance_terms(query)
    candidates: list[tuple[int, float, int, StoredMemoryFact]] = []
    for row in db.execute(
        "SELECT memory_id FROM memory_fact_provenance WHERE chat_id=? AND session_id=? "
        "AND session_created_at=? AND valid=1 ORDER BY memory_id DESC LIMIT 200",
        (scope.chat_id, scope.session_id, scope.session_created_at),
    ).fetchall():
        stored = eligible_fact(db, scope, int(row[0]))
        if stored is None:
            continue
        overlap = len(terms & relevance_terms(stored.fact.summary))
        if not overlap and stored.provenance_kind != "explicit":
            continue
        candidates.append((overlap, stored.fact.importance, stored.memory_id, stored))
    candidates.sort(key=lambda item: item[:3], reverse=True)
    return _fact_block([(item[3], "") for item in candidates], "episodic", limit=limit, max_chars=max_chars)


def ranked_fact_block(db: sqlite3.Connection, scope: MemoryReadScope, document_ids: list[str]) -> MemoryBlock:
    facts: list[tuple[StoredMemoryFact, str]] = []
    if scope_is_current(db, scope, external=True):
        for document_id in document_ids:
            row = db.execute(
                "SELECT memory_id FROM memory_fact_index WHERE document_id=? AND state='retained'",
                (document_id,),
            ).fetchone()
            if row is None or index_fact_is_current(db, document_id) is None:
                continue
            stored = eligible_fact(db, scope, int(row[0]))
            if stored:
                facts.append((stored, document_id))
    return _fact_block(facts, "recall", max_chars=HINDSIGHT_CONTEXT_MAX_CHARS)


def validate_memory_blocks(
    db: sqlite3.Connection, scope: MemoryReadScope, blocks: tuple[MemoryBlock, ...]
) -> tuple[MemoryBlock, ...]:
    validated: list[MemoryBlock] = []
    for block in blocks:
        if block.channel in {"summary", "scene"}:
            validated.append(read_artifact_block(db, scope, block.channel, required_evidence=block.evidence))
            continue
        facts: list[tuple[StoredMemoryFact, str]] = []
        if not scope_is_current(db, scope, external=block.channel == "recall"):
            validated.append(MemoryBlock(channel=block.channel))
            continue
        for evidence in block.evidence:
            if evidence.memory_id:
                stored = eligible_fact(db, scope, evidence.memory_id)
                if stored and (not evidence.document_id or index_fact_is_current(db, evidence.document_id)):
                    facts.append((stored, evidence.document_id))
        validated.append(
            _fact_block(
                facts,
                block.channel,
                max_chars=HINDSIGHT_CONTEXT_MAX_CHARS if block.channel == "recall" else EPISODIC_CONTEXT_MAX_CHARS,
            )
        )
    return tuple(validated)
