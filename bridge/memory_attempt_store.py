"""Per-dispatch durable evidence; ambiguous upstream calls have no safe expiry."""

import sqlite3
import time
import uuid
from typing import Literal

from bridge.sqlite_store import write_transaction

ARCHIVAL_WATCH_SECONDS = 300
ARCHIVAL_RECOVERY_BATCH = 16


def begin_external_memory_attempt(
    db: sqlite3.Connection,
    *,
    document_id: str,
    chat_id: str,
    session_id: str,
    session_created_at: float,
    kind: Literal["raw", "native_fact"],
) -> str:
    if not db.in_transaction:
        raise RuntimeError("Memory dispatch intent requires an active transaction")
    token = uuid.uuid4().hex
    db.execute(
        "INSERT INTO memory_archival_attempts"
        "(attempt_token,document_id,chat_id,session_id,session_created_at,started_at,kind) VALUES(?,?,?,?,?,?,?)",
        (token, document_id, chat_id, session_id, session_created_at, time.time(), kind),
    )
    return token


def finish_archival_attempt(db: sqlite3.Connection, token: str) -> None:
    """Record known synchronous completion independently from acceptance rollback."""
    with write_transaction(db):
        db.execute("UPDATE memory_archival_attempts SET finished=1,next_check_at=0 WHERE attempt_token=?", (token,))


def resolve_archival_attempt(db: sqlite3.Connection, token: str) -> None:
    """Caller preserves current authority or queues stale debt before resolving its finished token."""
    db.execute("DELETE FROM memory_archival_attempts WHERE attempt_token=? AND finished=1", (token,))


def archival_attempts_outstanding(db: sqlite3.Connection, document_id: str) -> bool:
    return bool(
        db.execute("SELECT 1 FROM memory_archival_attempts WHERE document_id=? LIMIT 1", (document_id,)).fetchone()
    )


def reconcile_archival_attempts(
    db: sqlite3.Connection,
    *,
    chat_id: str | None = None,
    session_id: str | None = None,
    now: float | None = None,
) -> int:
    """Recover at most sixteen raw/native calls, including deleted incarnations."""
    now = time.time() if now is None else now
    with write_transaction(db):
        rows = db.execute(
            "SELECT attempt_token,document_id,chat_id,session_id,finished,kind FROM memory_archival_attempts "
            "WHERE next_check_at<=? AND (? IS NULL OR (chat_id=? AND session_id=?)) "
            "ORDER BY next_check_at,attempt_token LIMIT ?",
            (now, chat_id, chat_id, session_id, ARCHIVAL_RECOVERY_BATCH),
        ).fetchall()
        for token, document_id, owner_chat, owner_session, finished, kind in rows:
            if kind == "raw":
                current = db.execute(
                    "SELECT 1 FROM memory_segments g JOIN sessions s ON s.chat_id=g.chat_id AND "
                    "s.session_id=g.session_id "
                    "AND s.created_at=g.session_created_at JOIN memory_layer_state l ON l.chat_id=g.chat_id "
                    "AND l.session_id=g.session_id AND l.session_created_at=g.session_created_at AND "
                    "l.layer='hindsight' "
                    "AND l.purge_epoch=g.purge_epoch WHERE g.document_id=? AND g.valid IN (1,2)",
                    (document_id,),
                ).fetchone()
            else:
                current = db.execute(
                    "SELECT 1 FROM memory_fact_index f JOIN sessions s ON s.chat_id=f.chat_id AND "
                    "s.session_id=f.session_id "
                    "AND s.created_at=f.session_created_at JOIN memory_layer_state l ON l.chat_id=f.chat_id "
                    "AND l.session_id=f.session_id AND l.session_created_at=f.session_created_at AND "
                    "l.layer='hindsight' "
                    "AND l.purge_epoch=f.external_epoch WHERE f.document_id=? AND f.state IN ('pending','retained')",
                    (document_id,),
                ).fetchone()
            if not current:
                db.execute(
                    "INSERT INTO memory_retired_documents(chat_id,session_id,document_id) VALUES(?,?,?) "
                    "ON CONFLICT(chat_id,session_id,document_id) DO UPDATE SET deleted=0,next_attempt_at=0,"
                    "retirement_revision=memory_retired_documents.retirement_revision+1 "
                    "WHERE memory_retired_documents.deleted=1 OR ?",
                    (owner_chat, owner_session, document_id, bool(finished)),
                )
            if finished:
                resolve_archival_attempt(db, token)
            else:
                db.execute(
                    "UPDATE memory_archival_attempts SET next_check_at=? WHERE attempt_token=?",
                    (now + ARCHIVAL_WATCH_SECONDS, token),
                )
    return len(rows)
