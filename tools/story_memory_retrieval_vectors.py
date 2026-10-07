"""Evaluation-only exact cosine over little-endian float32 SQLite BLOBs."""

from __future__ import annotations

import math
import re
import sqlite3
import struct
from dataclasses import dataclass
from time import perf_counter_ns

from story_memory_retrieval_fixture import require
from story_memory_retrieval_rank import CandidateRanking, candidate_ranking, eligible_facts, fact_fingerprint

from bridge.memory_contracts import MemoryReadScope
from bridge.memory_fact_store import StoredMemoryFact


@dataclass(frozen=True)
class EmbeddingProfile:
    profile_id: str
    model: str
    dimensions: int
    revision: str
    evidence_kind: str
    provider_kind: str

    def __post_init__(self):
        require(bool(re.fullmatch(r"[0-9a-f]{24}", self.profile_id)), "Invalid embedding namespace")
        require(type(self.dimensions) is int and 1 <= self.dimensions <= 8192, "Invalid embedding dimensions")
        require(isinstance(self.model, str) and 0 < len(self.model) <= 200, "Missing embedding model")
        require(isinstance(self.revision, str) and 0 < len(self.revision) <= 100, "Missing embedding revision")
        require(self.evidence_kind in {"contract_only", "genuine_model_retrieval"}, "Invalid vector evidence kind")
        require(self.provider_kind in {"synthetic", "external_provider"}, "Unverified inference boundary")
        require(
            (self.evidence_kind == "contract_only") == (self.provider_kind == "synthetic"), "Conflicting provenance"
        )


def decode_vector(blob: bytes, dimensions: int) -> tuple[tuple[float, ...], float]:
    require(isinstance(blob, bytes) and len(blob) == 4 * dimensions, "Invalid float32 BLOB length")
    values = struct.unpack(f"<{dimensions}f", blob)
    require(all(math.isfinite(value) for value in values), "Nonfinite float32 vector")
    norm = math.hypot(*values)
    require(math.isfinite(norm) and norm > 0, "Zero or nonfinite vector norm")
    return values, norm


def pack_vector(vector, dimensions: int) -> tuple[bytes, float]:
    require(isinstance(vector, (tuple, list)) and len(vector) == dimensions, "Wrong embedding dimensions")
    require(
        all(type(value) in {float, int} and math.isfinite(value) for value in vector), "Nonfinite/nonnumeric vector"
    )
    values = tuple(float(value) for value in vector)
    norm = math.hypot(*values)
    require(math.isfinite(norm) and norm > 0, "Zero or nonfinite vector norm")
    blob = struct.pack(f"<{dimensions}f", *(value / norm for value in values))
    _, stored_norm = decode_vector(blob, dimensions)
    return blob, stored_norm


class BlobIndex:
    """No application schema migration; the table belongs to this temporary connection."""

    def __init__(self, db: sqlite3.Connection, profile: EmbeddingProfile):
        self.db, self.profile = db, profile
        db.execute(
            "CREATE TEMP TABLE IF NOT EXISTS evaluation_vectors ("
            "memory_id INTEGER,profile_id TEXT,content_hash TEXT,authority_hash TEXT,dimensions INTEGER,"
            "vector BLOB,norm REAL,PRIMARY KEY(memory_id,profile_id))"
        )

    def put(self, stored: StoredMemoryFact, vector) -> None:
        blob, norm = pack_vector(vector, self.profile.dimensions)
        self.db.execute(
            "INSERT OR REPLACE INTO evaluation_vectors VALUES(?,?,?,?,?,?,?)",
            (
                stored.memory_id,
                self.profile.profile_id,
                stored.payload_digest,
                fact_fingerprint(stored),
                self.profile.dimensions,
                blob,
                norm,
            ),
        )
        self.db.commit()


def blob_rank(index: BlobIndex, scope: MemoryReadScope, query_vector) -> CandidateRanking:
    start = perf_counter_ns()
    facts = eligible_facts(index.db, scope)
    eligible_end = perf_counter_ns()
    query_blob, query_norm = pack_vector(query_vector, index.profile.dimensions)
    query, _ = decode_vector(query_blob, index.profile.dimensions)
    ranked = []
    decode_ns = perf_counter_ns() - eligible_end
    score_ns = 0
    for stored in facts:
        row = index.db.execute(
            "SELECT content_hash,authority_hash,dimensions,vector,norm FROM evaluation_vectors "
            "WHERE memory_id=? AND profile_id=?",
            (stored.memory_id, index.profile.profile_id),
        ).fetchone()
        if row is None or row[:3] != (stored.payload_digest, fact_fingerprint(stored), index.profile.dimensions):
            continue
        decoded_start = perf_counter_ns()
        vector, norm = decode_vector(row[3], index.profile.dimensions)
        require(
            math.isfinite(row[4]) and row[4] > 0 and math.isclose(norm, row[4], rel_tol=1e-12), "Stored norm changed"
        )
        score_start = perf_counter_ns()
        decode_ns += score_start - decoded_start
        cosine = math.fsum(a * b for a, b in zip(query, vector, strict=True)) / (norm * query_norm)
        ranked.append((cosine, stored))
        score_ns += perf_counter_ns() - score_start
    ranked.sort(key=lambda pair: (-pair[0], pair[1].memory_id))
    return candidate_ranking(
        "blob",
        [pair[1] for pair in ranked],
        index.profile.evidence_kind,
        scores=tuple(pair[0] for pair in ranked),
        timing_ns={
            "eligibility": eligible_end - start,
            "decode": decode_ns,
            "score": score_ns,
            "rank_total": perf_counter_ns() - start,
        },
        details={
            "eligible_count": len(facts),
            "scored_count": len(ranked),
            "missing_or_stale_vectors": len(facts) - len(ranked),
        },
    )
