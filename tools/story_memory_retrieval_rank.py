"""Candidate components, canonical final checks and production selected-set fusion."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import asdict, dataclass, field, replace
from time import perf_counter_ns

from story_memory_retrieval_fixture import sha256

from bridge.memory_contracts import MemoryBlock, MemoryEvidence, MemoryReadScope
from bridge.memory_fact_store import StoredMemoryFact
from bridge.memory_scope_store import eligible_fact, ranked_fact_block, read_episodic_block, validate_memory_blocks
from bridge.memory_search_store import search_fact_ids


def fact_fingerprint(stored: StoredMemoryFact) -> str:
    return sha256(json.dumps(asdict(stored), sort_keys=True, separators=(",", ":")))


@dataclass(frozen=True)
class CandidateRanking:
    backend: str
    ordered_memory_ids: tuple[int, ...]
    evidence_kind: str
    status: str = "available"
    timing_ns: dict[str, int] = field(default_factory=dict)
    identities: tuple[str, ...] = ()
    scores: tuple[float, ...] = ()
    raw_candidate_ids: tuple[str, ...] = ()
    details: dict = field(default_factory=dict)
    reason: str = ""


@dataclass(frozen=True)
class FinalizedRanking:
    ordered_memory_ids: tuple[int, ...]
    summaries: tuple[str, ...]
    evidence: tuple[MemoryEvidence, ...]
    timing_ns: int


def eligible_facts(db: sqlite3.Connection, scope: MemoryReadScope) -> tuple[StoredMemoryFact, ...]:
    """Inventory the complete authorized corpus without lexical or recent-N pruning."""
    rows = db.execute(
        "SELECT memory_id FROM episodic_memories WHERE chat_id=? AND session_id=? ORDER BY memory_id",
        (scope.chat_id, scope.session_id),
    )
    return tuple(stored for (mid,) in rows if (stored := eligible_fact(db, scope, mid)) is not None)


def candidate_ranking(backend, facts, evidence_kind, **kwargs):
    facts = tuple(facts)
    return CandidateRanking(
        backend,
        tuple(stored.memory_id for stored in facts),
        evidence_kind,
        identities=tuple(fact_fingerprint(stored) for stored in facts),
        **kwargs,
    )


def fts_rank(db: sqlite3.Connection, scope: MemoryReadScope, query: str) -> CandidateRanking:
    start = perf_counter_ns()
    ids = search_fact_ids(db, scope, query)
    searched = perf_counter_ns()
    facts = tuple(stored for mid in ids if (stored := eligible_fact(db, scope, mid)) is not None)
    return candidate_ranking(
        "fts",
        facts,
        "curated_synthetic_retrieval",
        timing_ns={"fts_search": searched - start, "candidate_eligibility": perf_counter_ns() - searched},
        details={
            "candidate_count": len(ids),
            "explicit_assertion_ids": [stored.memory_id for stored in facts if stored.provenance_kind == "explicit"],
        },
    )


def finalize_ranking(db: sqlite3.Connection, scope: MemoryReadScope, ranking: CandidateRanking) -> FinalizedRanking:
    start = perf_counter_ns()
    facts = []
    seen = set()
    for mid, identity in zip(ranking.ordered_memory_ids, ranking.identities, strict=True):
        stored = eligible_fact(db, scope, mid)
        if stored is not None and mid not in seen and fact_fingerprint(stored) == identity:
            facts.append(stored)
            seen.add(mid)
    return FinalizedRanking(
        tuple(stored.memory_id for stored in facts),
        tuple(stored.fact.summary for stored in facts),
        tuple(stored.evidence for stored in facts),
        perf_counter_ns() - start,
    )


def fused_blocks(
    db, scope, query, supplemental: FinalizedRanking | None = None, *, documents=(), channel="local_embedding"
):
    """Return real channel blocks, not a purported globally ranked list."""
    fts = read_episodic_block(db, scope, query)
    if channel == "recall":
        supplement = ranked_fact_block(db, scope, list(documents))
    else:
        evidence = (
            () if supplemental is None else tuple(replace(item, document_id="") for item in supplemental.evidence[:6])
        )
        supplement = MemoryBlock(evidence=evidence, channel=channel)
    # Current MemoryService order; empty summary/scene are part of this controlled corpus.
    inputs = (supplement, fts, MemoryBlock(channel="summary"), MemoryBlock(channel="scene"))
    output = validate_memory_blocks(db, scope, inputs)
    return output, {block.channel: len(block.evidence) for block in inputs}
