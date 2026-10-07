"""SQLite durable memory leases; raw source validity never grants fact authority."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import time
import uuid
from collections.abc import Iterable
from dataclasses import dataclass
from typing import TypedDict

from bridge.memory_attempt_store import (
    ARCHIVAL_RECOVERY_BATCH as ARCHIVAL_RECOVERY_BATCH,
)
from bridge.memory_attempt_store import (
    ARCHIVAL_WATCH_SECONDS as ARCHIVAL_WATCH_SECONDS,
)
from bridge.memory_attempt_store import (
    archival_attempts_outstanding as archival_attempts_outstanding,
)
from bridge.memory_attempt_store import (
    begin_external_memory_attempt as begin_external_memory_attempt,
)
from bridge.memory_attempt_store import (
    finish_archival_attempt as finish_archival_attempt,
)
from bridge.memory_attempt_store import (
    reconcile_archival_attempts as reconcile_archival_attempts,
)
from bridge.memory_attempt_store import (
    resolve_archival_attempt as resolve_archival_attempt,
)
from bridge.memory_contracts import MemoryReadScope
from bridge.memory_retry import CONFIGURATION_RETRY_SECONDS, MEMORY_FAILURE_CODES
from bridge.sqlite_store import write_transaction

MAX_CLAIMS, LEASE_SECONDS, SEGMENT_CHARS = 1, 900, 12000
AUTO_IDLE_SECONDS, AUTO_FAILURE_LIMIT = 86400, 8
ARCHIVAL_PENDING = 2  # Accepted readers require 1; 2 reserves retryable raw work without granting coverage.


@dataclass(frozen=True)
class MemoryClaim:
    chat_id: str
    session_id: str
    session_created_at: float
    layer: str
    version: int
    target_id: int
    token: str
    rewrite_identity: int = 0
    purge_epoch: int = 0


@dataclass(frozen=True)
class MemorySource:
    document_id: str
    chat_id: str
    session_id: str
    session_created_at: float
    layer: str
    start_id: int
    end_id: int
    start_offset: int
    end_offset: int
    source_digest: str
    rewrite_identity: int
    purge_epoch: int
    role: str
    content: str


def enqueue_memory(db: sqlite3.Connection, chat_id: str, session_id: str, layer: str) -> bool:
    with write_transaction(db):
        cursor = db.execute(
            "INSERT INTO memory_jobs(chat_id,session_id,session_created_at,layer,target_id) "
            "SELECT chat_id,session_id,created_at,?,"
            "(SELECT COALESCE(MAX(id),0) FROM messages WHERE chat_id=? AND session_id=?) "
            "FROM sessions WHERE chat_id=? AND session_id=? "
            "ON CONFLICT(chat_id,session_id,session_created_at,layer) DO UPDATE SET "
            "dirty_version=memory_jobs.dirty_version+1,target_id=excluded.target_id,"
            "attempts=0,last_error='',next_attempt_at=0",
            (layer, chat_id, session_id, chat_id, session_id),
        )
    return bool(cursor.rowcount)


def recover_expired_jobs(db: sqlite3.Connection, *, now: float | None = None) -> int:
    now = time.time() if now is None else now
    with write_transaction(db):
        return db.execute(
            "UPDATE memory_jobs SET lease_token='',lease_deadline=0 WHERE lease_token<>'' AND lease_deadline<=?",
            (now,),
        ).rowcount


def claim_jobs(
    db: sqlite3.Connection,
    *,
    now: float | None = None,
    layers: Iterable[str] | None = None,
    chat_id: str | None = None,
    session_id: str | None = None,
    limit: int = MAX_CLAIMS,
    lease_seconds: float = LEASE_SECONDS,
    autonomous: bool = False,
) -> list[MemoryClaim]:
    now = time.time() if now is None else now
    recover_expired_jobs(db, now=now)
    claims = []
    with write_transaction(db):
        active = db.execute("SELECT count(*) FROM memory_jobs WHERE lease_token<>''").fetchone()[0]
        limit = min(limit, MAX_CLAIMS - active)
        if limit <= 0:
            return []
        rows = db.execute(
            "SELECT chat_id,session_id,session_created_at,layer,dirty_version,target_id FROM memory_jobs "
            "WHERE dirty_version>completed_version AND lease_token='' AND next_attempt_at<=? "
            "AND (?=0 OR layer='hindsight' OR EXISTS(SELECT 1 FROM messages m WHERE m.chat_id=memory_jobs.chat_id "
            "AND m.session_id=memory_jobs.session_id AND m.created_at>=?)) "
            "AND (?=0 OR layer='hindsight' OR NOT (attempts>=? AND last_error IN ('work_failed','retain_failed'))) "
            "AND EXISTS(SELECT 1 FROM sessions s WHERE s.chat_id=memory_jobs.chat_id "
            "AND s.session_id=memory_jobs.session_id AND s.created_at=memory_jobs.session_created_at) "
            "ORDER BY next_attempt_at,attempts,chat_id,session_id,layer",
            (now, int(autonomous), now - AUTO_IDLE_SECONDS, int(autonomous), AUTO_FAILURE_LIMIT),
        )
        try:
            for row in rows:
                if (chat_id is not None and row[0] != chat_id) or (session_id is not None and row[1] != session_id):
                    continue
                if layers is not None and row[3] not in layers:
                    continue
                token = uuid.uuid4().hex
                db.execute(
                    "UPDATE memory_jobs SET lease_token=?,lease_deadline=?,claimed_version=dirty_version,"
                    "claimed_target_id=target_id,attempts=attempts+1 WHERE chat_id=? AND session_id=? "
                    "AND session_created_at=? AND layer=?",
                    (token, now + lease_seconds, *row[:4]),
                )
                identity = db.execute(
                    "SELECT rewrite_identity,purge_epoch FROM memory_layer_state "
                    "WHERE chat_id=? AND session_id=? AND session_created_at=? AND layer=?",
                    row[:4],
                ).fetchone()
                claims.append(
                    MemoryClaim(
                        str(row[0]),
                        str(row[1]),
                        float(row[2]),
                        str(row[3]),
                        int(row[4]),
                        int(row[5]),
                        token,
                        int(identity[0]),
                        int(identity[1]),
                    )
                )
                if len(claims) >= min(MAX_CLAIMS, max(1, limit)):
                    break
        finally:
            rows.close()
    return claims


def claim_is_current(db: sqlite3.Connection, claim: MemoryClaim) -> bool:
    return bool(
        db.execute(
            "SELECT 1 FROM memory_jobs j JOIN sessions s ON s.chat_id=j.chat_id AND s.session_id=j.session_id "
            "AND s.created_at=j.session_created_at JOIN memory_layer_state l ON l.chat_id=j.chat_id "
            "AND l.session_id=j.session_id AND l.session_created_at=j.session_created_at AND l.layer=j.layer "
            "WHERE j.chat_id=? AND j.session_id=? AND j.layer=? "
            "AND j.session_created_at=? AND j.lease_token=? AND j.claimed_version=? "
            "AND l.rewrite_identity=? AND l.purge_epoch=?",
            (
                claim.chat_id,
                claim.session_id,
                claim.layer,
                claim.session_created_at,
                claim.token,
                claim.version,
                claim.rewrite_identity,
                claim.purge_epoch,
            ),
        ).fetchone()
    )


def _scope(claim: MemoryClaim) -> tuple[str, str, float, str, str, int]:
    return (claim.chat_id, claim.session_id, claim.session_created_at, claim.layer, claim.token, claim.version)


def acknowledge_job(db: sqlite3.Connection, claim: MemoryClaim) -> bool:
    with write_transaction(db):
        accepted = bool(
            db.execute(
                "UPDATE memory_jobs SET completed_version=?,lease_token='',lease_deadline=0,"
                "attempts=0,last_error='',next_attempt_at=0 WHERE chat_id=? AND session_id=? "
                "AND session_created_at=? AND layer=? AND lease_token=? AND claimed_version=? "
                "AND EXISTS(SELECT 1 FROM memory_layer_state l WHERE l.chat_id=memory_jobs.chat_id "
                "AND l.session_id=memory_jobs.session_id AND l.session_created_at=memory_jobs.session_created_at "
                "AND l.layer=memory_jobs.layer AND l.rewrite_identity=? AND l.purge_epoch=?)",
                (claim.version, *_scope(claim), claim.rewrite_identity, claim.purge_epoch),
            ).rowcount
        )
        if accepted:
            db.execute(
                "UPDATE memory_layer_state SET invalidated_from_id=NULL WHERE chat_id=? AND session_id=? "
                "AND session_created_at=? AND layer=? AND rewrite_identity=? AND purge_epoch=?",
                (
                    claim.chat_id,
                    claim.session_id,
                    claim.session_created_at,
                    claim.layer,
                    claim.rewrite_identity,
                    claim.purge_epoch,
                ),
            )
        return accepted


def fail_job(
    db: sqlite3.Connection,
    claim: MemoryClaim,
    error: str = "work_failed",
    *,
    now: float | None = None,
    deferred: bool = False,
) -> bool:
    # Only internal safe codes are persisted, never provider text or transcript.
    safe_error = error if error in MEMORY_FAILURE_CODES else "work_failed"
    now = time.time() if now is None else now
    with write_transaction(db):
        return bool(
            db.execute(
                "UPDATE memory_jobs SET lease_token='',lease_deadline=0,last_error=?,"
                "next_attempt_at=? + CASE WHEN ? THEN 0 WHEN ?='configuration' THEN ? "
                "ELSE MIN(300,5 * (1 << MIN(attempts,6))) END "
                "WHERE chat_id=? AND session_id=? AND session_created_at=? AND layer=? "
                "AND lease_token=? AND claimed_version=?",
                (safe_error, now, deferred, safe_error, CONFIGURATION_RETRY_SECONDS, *_scope(claim)),
            ).rowcount
        )


def retire_derived_layer(db: sqlite3.Connection, chat_id: str, session_id: str, layer: str) -> None:
    """Fence cleared artifacts and schedule complete replay on the existing job."""
    if layer not in {"summary", "scene", "curator"}:
        raise ValueError("Only clearable derived artifacts use this lifecycle")
    with write_transaction(db):
        db.execute(
            "UPDATE memory_layer_state SET covered_id=source_floor_id,rewrite_identity=rewrite_identity+1,"
            "invalidated_from_id=source_floor_id+1 WHERE chat_id=? AND session_id=? AND layer=?",
            (chat_id, session_id, layer),
        )
        db.execute(
            "UPDATE memory_segments SET valid=0 WHERE chat_id=? AND session_id=? AND layer=?",
            (chat_id, session_id, layer),
        )
        db.execute(
            "UPDATE memory_jobs SET dirty_version=dirty_version+1,next_attempt_at=0 "
            "WHERE chat_id=? AND session_id=? AND layer=?",
            (chat_id, session_id, layer),
        )


def _digest(role: str, content: str) -> str:
    return hashlib.sha256(json.dumps([role, content], ensure_ascii=False).encode()).hexdigest()


def next_source_segment(
    db: sqlite3.Connection,
    chat_id: str,
    session_id: str,
    layer: str,
    *,
    max_chars: int = SEGMENT_CHARS,
    through_id: int | None = None,
) -> MemorySource | None:
    state = db.execute(
        "SELECT s.created_at,COALESCE(l.rewrite_identity,0),COALESCE(l.purge_epoch,0),"
        "COALESCE(l.covered_id,0) FROM sessions s "
        "LEFT JOIN memory_layer_state l ON l.chat_id=s.chat_id AND l.session_id=s.session_id "
        "AND l.session_created_at=s.created_at AND l.layer=? WHERE s.chat_id=? AND s.session_id=?",
        (layer, chat_id, session_id),
    ).fetchone()
    if state is None:
        return None
    # Stream oldest canonical rows; complete rows/parts are never silently capped.
    rows = db.execute(
        "SELECT id,role,content FROM messages WHERE chat_id=? AND session_id=? AND id>? AND id<=? ORDER BY id",
        (chat_id, session_id, state[3], through_id if through_id is not None else 9223372036854775807),
    )
    try:
        for row_id, role, text in rows:
            offset = 0
            parts = db.execute(
                "SELECT start_offset,end_offset FROM memory_segments WHERE chat_id=? AND session_id=? "
                "AND session_created_at=? AND layer=? AND start_id=? AND valid=1 ORDER BY start_offset",
                (chat_id, session_id, state[0], layer, row_id),
            )
            try:
                for start, end in parts:
                    if start == offset:
                        offset = end
            finally:
                parts.close()
            if offset >= len(text) and (
                text
                or db.execute(
                    "SELECT 1 FROM memory_segments WHERE chat_id=? AND session_id=? AND layer=? AND start_id=? "
                    "AND session_created_at=? AND valid=1",
                    (chat_id, session_id, layer, row_id, state[0]),
                ).fetchone()
            ):
                continue
            end = min(len(text), offset + min(SEGMENT_CHARS, max(1, max_chars)))
            content = text[offset:end]
            digest = _digest(role, content)
            if layer == "hindsight":
                pending = db.execute(
                    "SELECT document_id,chat_id,session_id,session_created_at,layer,start_id,end_id,"
                    "start_offset,end_offset,source_digest,rewrite_identity,purge_epoch FROM memory_segments "
                    "WHERE chat_id=? AND session_id=? AND session_created_at=? AND layer=? "
                    "AND start_id=? AND start_offset=? AND valid=? ORDER BY created_at,document_id",
                    (chat_id, session_id, state[0], layer, row_id, offset, ARCHIVAL_PENDING),
                ).fetchall()
                for part in pending:
                    captured = MemorySource(
                        document_id=part[0],
                        chat_id=part[1],
                        session_id=part[2],
                        session_created_at=part[3],
                        layer=part[4],
                        start_id=part[5],
                        end_id=part[6],
                        start_offset=part[7],
                        end_offset=part[8],
                        source_digest=part[9],
                        rewrite_identity=part[10],
                        purge_epoch=part[11],
                        role=role,
                        content=text[part[7] : part[8]],
                    )
                    if source_is_valid(db, captured):
                        return captured
            identity = json.dumps(
                [chat_id, session_id, state[0], layer, row_id, offset, end, digest, state[1], state[2]]
            )
            key = hashlib.sha256(identity.encode()).hexdigest()
            return MemorySource(
                "session:" + session_id + ":source:" + key,
                chat_id,
                session_id,
                state[0],
                layer,
                row_id,
                row_id,
                offset,
                end,
                digest,
                state[1],
                state[2],
                role,
                content,
            )
    finally:
        rows.close()
    return None


def source_is_valid(db: sqlite3.Connection, source: MemorySource) -> bool:
    state = db.execute(
        "SELECT s.created_at,l.rewrite_identity,l.purge_epoch FROM sessions s JOIN memory_layer_state l "
        "ON l.chat_id=s.chat_id AND l.session_id=s.session_id AND l.session_created_at=s.created_at "
        "WHERE s.chat_id=? AND s.session_id=? AND l.layer=?",
        (source.chat_id, source.session_id, source.layer),
    ).fetchone()
    if state is None or state[0] != source.session_created_at or state[2] != source.purge_epoch:
        return False
    stored = db.execute("SELECT valid FROM memory_segments WHERE document_id=?", (source.document_id,)).fetchone()
    if (stored is not None and stored[0] not in (1, ARCHIVAL_PENDING)) or (
        stored is None and state[1] != source.rewrite_identity
    ):
        return False
    row = db.execute(
        "SELECT role,content FROM messages WHERE chat_id=? AND session_id=? AND id=?",
        (source.chat_id, source.session_id, source.start_id),
    ).fetchone()
    return bool(
        row is not None
        and len(row[1]) >= source.end_offset
        and _digest(row[0], row[1][source.start_offset : source.end_offset]) == source.source_digest
    )


def reserve_archival_source(db: sqlite3.Connection, source: MemorySource, *, claim: MemoryClaim | None = None) -> bool:
    """Persist raw dispatch identity without accepted offsets, coverage or mapping."""
    if source.layer != "hindsight":
        raise ValueError("Only raw Hindsight sources use archival reservations")
    with write_transaction(db):
        if (claim is not None and not claim_is_current(db, claim)) or not source_is_valid(db, source):
            return False
        db.execute(
            "INSERT OR IGNORE INTO memory_segments VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                source.document_id,
                source.chat_id,
                source.session_id,
                source.session_created_at,
                source.layer,
                source.start_id,
                source.end_id,
                source.start_offset,
                source.end_offset,
                source.source_digest,
                source.rewrite_identity,
                source.purge_epoch,
                ARCHIVAL_PENDING,
                time.time(),
            ),
        )
        return True


def retire_archival_source(db: sqlite3.Connection, source: MemorySource) -> None:
    """A late/uncertain remote write reopens even an already-completed retirement."""
    with write_transaction(db):
        db.execute("UPDATE memory_segments SET valid=0 WHERE document_id=?", (source.document_id,))
        db.execute(
            "INSERT INTO memory_retired_documents(chat_id,session_id,document_id) VALUES(?,?,?) "
            "ON CONFLICT(chat_id,session_id,document_id) DO UPDATE SET deleted=0,next_attempt_at=0,"
            "retirement_revision=memory_retired_documents.retirement_revision+1",
            (source.chat_id, source.session_id, source.document_id),
        )


def store_segment(db: sqlite3.Connection, source: MemorySource, *, advance_coverage: bool = True) -> bool:
    with write_transaction(db):
        if not source_is_valid(db, source):
            return False
        db.execute(
            "INSERT OR REPLACE INTO memory_segments VALUES(?,?,?,?,?,?,?,?,?,?,?,?,1,?)",
            (
                source.document_id,
                source.chat_id,
                source.session_id,
                source.session_created_at,
                source.layer,
                source.start_id,
                source.end_id,
                source.start_offset,
                source.end_offset,
                source.source_digest,
                source.rewrite_identity,
                source.purge_epoch,
                time.time(),
            ),
        )
        row = db.execute("SELECT length(content) FROM messages WHERE id=?", (source.end_id,)).fetchone()
        if advance_coverage and row and row[0] == source.end_offset:
            db.execute(
                "UPDATE memory_layer_state SET covered_id=MAX(covered_id,?) WHERE chat_id=? AND session_id=? "
                "AND session_created_at=? AND layer=?",
                (source.end_id, source.chat_id, source.session_id, source.session_created_at, source.layer),
            )
    return True


def invalidate_memory(db: sqlite3.Connection, chat_id: str, session_id: str, *, purge_epoch: int) -> None:
    with write_transaction(db):
        db.execute("UPDATE memory_segments SET valid=0 WHERE chat_id=? AND session_id=?", (chat_id, session_id))
        db.execute(
            "UPDATE memory_layer_state SET purge_epoch=?,rewrite_identity=rewrite_identity+1,covered_id=0 "
            "WHERE chat_id=? AND session_id=?",
            (purge_epoch, chat_id, session_id),
        )
        db.execute(
            "UPDATE memory_jobs SET dirty_version=dirty_version+1,next_attempt_at=0 WHERE chat_id=? AND session_id=?",
            (chat_id, session_id),
        )


class ExternalMemoryBoundary(TypedDict):
    session_created_at: float
    purge_epoch: int
    source_floor_id: int


def external_memory_boundary(
    db: sqlite3.Connection, chat_id: str, session_id: str, layer: str = "hindsight"
) -> ExternalMemoryBoundary | None:
    """Expose purge floor/epoch for future semantic indexing without deleting native facts."""
    row = db.execute(
        "SELECT l.session_created_at,l.purge_epoch,l.source_floor_id FROM memory_layer_state l JOIN sessions s "
        "ON s.chat_id=l.chat_id AND s.session_id=l.session_id AND s.created_at=l.session_created_at "
        "WHERE l.chat_id=? AND l.session_id=? AND l.layer=?",
        (chat_id, session_id, layer),
    ).fetchone()
    return (
        ExternalMemoryBoundary(session_created_at=float(row[0]), purge_epoch=int(row[1]), source_floor_id=int(row[2]))
        if row
        else None
    )


def purge_external_memory(db: sqlite3.Connection, chat_id: str, session_id: str, *, purge_epoch: int) -> None:
    """Fence external work and retain only future accepted source after explicit purge."""
    with write_transaction(db):
        # Keep cleanup mappings until remote deletion succeeds; retirement blocks recall now.
        db.execute(
            "INSERT INTO memory_retired_documents(chat_id,session_id,document_id) "
            "SELECT chat_id,session_id,document_id FROM hindsight_documents WHERE chat_id=? AND session_id=? "
            "ON CONFLICT(chat_id,session_id,document_id) DO UPDATE SET deleted=0,next_attempt_at=0,"
            "retirement_revision=memory_retired_documents.retirement_revision+1",
            (chat_id, session_id),
        )
        db.execute(
            "INSERT OR IGNORE INTO memory_layer_state(chat_id,session_id,session_created_at,layer) "
            "SELECT chat_id,session_id,created_at,'hindsight' FROM sessions WHERE chat_id=? AND session_id=?",
            (chat_id, session_id),
        )
        floor = db.execute(
            "SELECT COALESCE(MAX(id),0) FROM messages WHERE chat_id=? AND session_id=?", (chat_id, session_id)
        ).fetchone()[0]
        db.execute(
            "UPDATE memory_segments SET valid=0 WHERE chat_id=? AND session_id=? AND layer='hindsight'",
            (chat_id, session_id),
        )
        db.execute(
            "UPDATE memory_layer_state SET purge_epoch=?,rewrite_identity=rewrite_identity+1,"
            "source_floor_id=?,covered_id=? WHERE chat_id=? AND session_id=? AND layer='hindsight'",
            (purge_epoch, floor, floor, chat_id, session_id),
        )
        db.execute(
            "UPDATE memory_jobs SET completed_version=dirty_version,lease_token='',lease_deadline=0,"
            "next_attempt_at=0,last_error='' WHERE chat_id=? AND session_id=? AND layer='hindsight'",
            (chat_id, session_id),
        )
        # Native curator proof survives external purge; only overlapping ownership is revoked.
        db.execute(
            "UPDATE memory_jobs SET lease_token='',lease_deadline=0,next_attempt_at=0 "
            "WHERE chat_id=? AND session_id=? AND layer='curator' AND lease_token<>''",
            (chat_id, session_id),
        )


def pending_memory_invalidation(db: sqlite3.Connection, chat_id: str, session_id: str, layer: str) -> int | None:
    """Earliest unreconciled rewrite, available to retrieval before worker recovery."""
    row = db.execute(
        "SELECT l.invalidated_from_id FROM memory_layer_state l JOIN sessions s "
        "ON s.chat_id=l.chat_id AND s.session_id=l.session_id AND s.created_at=l.session_created_at "
        "WHERE l.chat_id=? AND l.session_id=? AND l.layer=?",
        (chat_id, session_id, layer),
    ).fetchone()
    return int(row[0]) if row and row[0] is not None else None


def request_source_cutoff(db: sqlite3.Connection, scope: MemoryReadScope) -> int:
    """Keep only the canonical prefix unchanged since this request captured its scope."""
    row = db.execute(
        "SELECT MIN(source_rowid) FROM memory_source_rewrites "
        "WHERE chat_id=? AND session_id=? AND session_created_at=? AND change_id>?",
        (scope.chat_id, scope.session_id, scope.session_created_at, scope.rewrite_event_cutoff),
    ).fetchone()
    return min(scope.through_rowid, max(0, int(row[0]) - 1)) if row and row[0] is not None else scope.through_rowid


def begin_archival_attempt(db: sqlite3.Connection, source: MemorySource) -> str:
    """Caller commits this raw token together with the source reservation."""
    return begin_external_memory_attempt(
        db,
        document_id=source.document_id,
        chat_id=source.chat_id,
        session_id=source.session_id,
        session_created_at=source.session_created_at,
        kind="raw",
    )
