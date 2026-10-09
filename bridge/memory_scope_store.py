"""One locally verified story-knowledge boundary for every derived prompt channel."""

from __future__ import annotations

import sqlite3
from dataclasses import replace
from typing import Literal

from bridge.limits import EPISODIC_CONTEXT_MAX_CHARS, HINDSIGHT_CONTEXT_MAX_CHARS
from bridge.memory_artifact_store import read_artifact_block, read_summary_block
from bridge.memory_contracts import MemoryBlock, MemoryBlockLeaf, MemoryEvidence, MemoryReadScope
from bridge.memory_fact_store import StoredMemoryFact, index_fact_is_current, load_current_fact, normalize_principals
from bridge.memory_search_store import MAX_SEARCH_CANDIDATES, search_fact_ids
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
    facts: list[tuple[StoredMemoryFact, str]], channel: str, *, scope: MemoryReadScope, limit: int = 6, max_chars: int
) -> MemoryBlock:
    lines: list[str] = []
    evidence: list[MemoryEvidence] = []
    leaves: list[MemoryBlockLeaf] = []
    seen: set[str] = set()
    size = 0
    for stored, document_id in facts:
        key = " ".join(stored.fact.summary.split()).casefold()
        if key in seen:
            continue
        pointer = stored.evidence
        source = (
            f"assertion {pointer.explicit_event_id}"
            if pointer.explicit_event_id
            else f"message {pointer.source_start_rowid}, chars {pointer.start_offset}:{pointer.end_offset}"
        )
        line = f"[{stored.fact.kind}] {stored.fact.summary} (source: {source}; fact {stored.memory_id})"
        if size + len(line) + bool(lines) > max_chars:
            continue
        seen.add(key)
        lines.append(line)
        pointer = replace(stored.evidence, document_id=document_id)
        evidence.append(pointer)
        leaves.append(
            MemoryBlockLeaf(line, pointer, scope, stored.fact.visibility, stored.fact.known_by, scope.rewrite_revision)
        )
        size += len(line) + (len(lines) > 1)
        if len(lines) >= limit:
            break
    return MemoryBlock("\n".join(lines), tuple(evidence), channel, tuple(leaves))


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
    if not scope_is_current(db, scope):
        return MemoryBlock(channel="episodic")
    facts = [
        (stored, "")
        for memory_id in search_fact_ids(db, scope, query)
        if (stored := eligible_fact(db, scope, memory_id)) is not None
    ]
    return _fact_block(facts, "episodic", scope=scope, limit=min(6, limit), max_chars=max_chars)


def ranked_fact_block(db: sqlite3.Connection, scope: MemoryReadScope, document_ids: list[str]) -> MemoryBlock:
    facts: list[tuple[StoredMemoryFact, str]] = []
    if scope_is_current(db, scope, external=True):
        for document_id in list(dict.fromkeys(document_ids))[:MAX_SEARCH_CANDIDATES]:
            row = db.execute(
                "SELECT memory_id FROM memory_fact_index WHERE document_id=? AND state='retained'",
                (document_id,),
            ).fetchone()
            if row is None or index_fact_is_current(db, document_id) is None:
                continue
            stored = eligible_fact(db, scope, int(row[0]))
            if stored:
                facts.append((stored, document_id))
    return _fact_block(facts, "recall", scope=scope, max_chars=HINDSIGHT_CONTEXT_MAX_CHARS)


def validate_memory_blocks(
    db: sqlite3.Connection, scope: MemoryReadScope, blocks: tuple[MemoryBlock, ...]
) -> tuple[MemoryBlock, ...]:
    validated: list[MemoryBlock] = []
    ranked: dict[str, list[tuple[StoredMemoryFact, str]]] = {}
    scores: dict[tuple[str, str], float] = {}
    chosen: dict[tuple[str, str], tuple[str, StoredMemoryFact, str]] = {}
    for block in blocks:
        if block.channel in {"summary", "scene"}:
            continue
        facts: list[tuple[StoredMemoryFact, str]] = []
        ranked[block.channel] = facts
        if not scope_is_current(db, scope, external=block.channel == "recall"):
            continue
        seen: set[tuple[str, str]] = set()
        for rank, evidence in enumerate(block.evidence[:MAX_SEARCH_CANDIDATES]):
            stored = eligible_fact(db, scope, evidence.memory_id) if evidence.memory_id else None
            if stored is None or (evidence.document_id and not index_fact_is_current(db, evidence.document_id)):
                continue
            key = (stored.fact.kind, " ".join(stored.fact.summary.split()).casefold())
            if key in seen:
                continue
            seen.add(key)
            # Reciprocal-rank fusion combines lexical and semantic votes; local
            # evidence is rehydrated first, including independent audience grants.
            scores[key] = scores.get(key, 0.0) + 1.0 / (60 + rank + 1)
            if key not in chosen or evidence.document_id:
                chosen[key] = (block.channel, stored, evidence.document_id)
    for key in sorted(scores, key=lambda key: (-scores[key], -chosen[key][1].fact.importance, key))[:6]:
        channel, stored, document_id = chosen[key]
        ranked[channel].append((stored, document_id))
    for block in blocks:
        if block.channel == "summary":
            validated.append(read_summary_block(db, scope, required_evidence=block.evidence))
        elif block.channel == "scene":
            validated.append(read_artifact_block(db, scope, "scene", required_evidence=block.evidence))
        else:
            validated.append(
                _fact_block(
                    ranked.get(block.channel, []),
                    block.channel,
                    scope=scope,
                    max_chars=HINDSIGHT_CONTEXT_MAX_CHARS if block.channel == "recall" else EPISODIC_CONTEXT_MAX_CHARS,
                )
            )
    return tuple(validated)
