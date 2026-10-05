"""SQLite durable memory leases and archival source provenance.

Raw source validity does not authorize any character to know a fact.
"""

from __future__ import annotations

import hashlib
import json
import time
import uuid
from dataclasses import dataclass

from bridge.sqlite_store import write_transaction

MAX_CLAIMS = 1
LEASE_SECONDS = 900
SEGMENT_CHARS = 12000


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


def enqueue_memory(db, chat_id, session_id, layer) -> bool:
    with write_transaction(db):
        cursor = db.execute(
            "INSERT INTO memory_jobs(chat_id,session_id,session_created_at,layer,target_id) "
            "SELECT chat_id,session_id,created_at,?,"
            "(SELECT COALESCE(MAX(id),0) FROM messages WHERE chat_id=? AND session_id=?) "
            "FROM sessions WHERE chat_id=? AND session_id=? "
            "ON CONFLICT(chat_id,session_id,session_created_at,layer) DO UPDATE SET "
            "dirty_version=memory_jobs.dirty_version+1,target_id=excluded.target_id,next_attempt_at=0",
            (layer, chat_id, session_id, chat_id, session_id),
        )
    return bool(cursor.rowcount)


def recover_expired_jobs(db, *, now=None) -> int:
    now = time.time() if now is None else now
    with write_transaction(db):
        return db.execute(
            "UPDATE memory_jobs SET lease_token='',lease_deadline=0 WHERE lease_token<>'' AND lease_deadline<=?",
            (now,),
        ).rowcount


def claim_jobs(db, *, now=None, layers=None, limit=MAX_CLAIMS, lease_seconds=LEASE_SECONDS):
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
            "AND EXISTS(SELECT 1 FROM sessions s WHERE s.chat_id=memory_jobs.chat_id "
            "AND s.session_id=memory_jobs.session_id AND s.created_at=memory_jobs.session_created_at) "
            "ORDER BY next_attempt_at,attempts,chat_id,session_id,layer",
            (now,),
        )
        try:
            for row in rows:
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
                claims.append(MemoryClaim(*row, token, *identity))
                if len(claims) >= min(MAX_CLAIMS, max(1, limit)):
                    break
        finally:
            rows.close()
    return claims


def _scope(claim):
    return (claim.chat_id, claim.session_id, claim.session_created_at, claim.layer, claim.token, claim.version)


def acknowledge_job(db, claim) -> bool:
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


def fail_job(db, claim, error="work_failed", *, now=None, deferred=False) -> bool:
    # Only internal safe codes are persisted, never provider text or transcript.
    safe_error = (
        error
        if error in {"work_failed", "retain_failed", "stale_source", "executor_rejected", "disabled", "deferred"}
        else "work_failed"
    )
    now = time.time() if now is None else now
    with write_transaction(db):
        return bool(
            db.execute(
                "UPDATE memory_jobs SET lease_token='',lease_deadline=0,last_error=?,"
                "next_attempt_at=? + MIN(300,5 * (1 << MIN(attempts,6))) "
                "WHERE chat_id=? AND session_id=? AND session_created_at=? AND layer=? "
                "AND lease_token=? AND claimed_version=?",
                (safe_error, now if not deferred else now - 9, *_scope(claim)),
            ).rowcount
        )


def _digest(role, content):
    return hashlib.sha256(json.dumps([role, content], ensure_ascii=False).encode()).hexdigest()


def next_source_segment(db, chat_id, session_id, layer, *, max_chars=SEGMENT_CHARS, through_id=None):
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


def source_is_valid(db, source) -> bool:
    state = db.execute(
        "SELECT s.created_at,l.rewrite_identity,l.purge_epoch FROM sessions s JOIN memory_layer_state l "
        "ON l.chat_id=s.chat_id AND l.session_id=s.session_id AND l.session_created_at=s.created_at "
        "WHERE s.chat_id=? AND s.session_id=? AND l.layer=?",
        (source.chat_id, source.session_id, source.layer),
    ).fetchone()
    if state is None or state[0] != source.session_created_at or state[2] != source.purge_epoch:
        return False
    stored = db.execute("SELECT valid FROM memory_segments WHERE document_id=?", (source.document_id,)).fetchone()
    if (stored is not None and not stored[0]) or (stored is None and state[1] != source.rewrite_identity):
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


def store_segment(db, source) -> bool:
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
        if row and row[0] == source.end_offset:
            db.execute(
                "UPDATE memory_layer_state SET covered_id=MAX(covered_id,?) WHERE chat_id=? AND session_id=? "
                "AND session_created_at=? AND layer=?",
                (source.end_id, source.chat_id, source.session_id, source.session_created_at, source.layer),
            )
    return True


def invalidate_memory(db, chat_id, session_id, *, purge_epoch):
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


def external_memory_boundary(db, chat_id, session_id, layer="hindsight"):
    """Expose purge floor/epoch for future semantic indexing without deleting native facts."""
    row = db.execute(
        "SELECT l.session_created_at,l.purge_epoch,l.source_floor_id FROM memory_layer_state l JOIN sessions s "
        "ON s.chat_id=l.chat_id AND s.session_id=l.session_id AND s.created_at=l.session_created_at "
        "WHERE l.chat_id=? AND l.session_id=? AND l.layer=?",
        (chat_id, session_id, layer),
    ).fetchone()
    return dict(zip(("session_created_at", "purge_epoch", "source_floor_id"), row, strict=True)) if row else None


def purge_external_memory(db, chat_id, session_id, *, purge_epoch):
    """Fence external work and retain only future accepted source after explicit purge."""
    with write_transaction(db):
        for layer in ("hindsight", "curator"):
            db.execute(
                "INSERT OR IGNORE INTO memory_layer_state(chat_id,session_id,session_created_at,layer) "
                "SELECT chat_id,session_id,created_at,? FROM sessions WHERE chat_id=? AND session_id=?",
                (layer, chat_id, session_id),
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
            "source_floor_id=?,covered_id=? WHERE chat_id=? AND session_id=? AND layer IN ('hindsight','curator')",
            (purge_epoch, floor, floor, chat_id, session_id),
        )
        db.execute(
            "UPDATE memory_jobs SET completed_version=dirty_version,lease_token='',lease_deadline=0,"
            "next_attempt_at=0,last_error='' WHERE chat_id=? AND session_id=? AND layer IN ('hindsight','curator')",
            (chat_id, session_id),
        )


def pending_memory_invalidation(db, chat_id, session_id, layer):
    """Earliest unreconciled rewrite, available to retrieval before worker recovery."""
    row = db.execute(
        "SELECT l.invalidated_from_id FROM memory_layer_state l JOIN sessions s "
        "ON s.chat_id=l.chat_id AND s.session_id=l.session_id AND s.created_at=l.session_created_at "
        "WHERE l.chat_id=? AND l.session_id=? AND l.layer=?",
        (chat_id, session_id, layer),
    ).fetchone()
    return row[0] if row else None
