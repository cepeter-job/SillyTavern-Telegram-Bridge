"""SQL-only cleanup obligations; no provider/client or executor ownership."""

import sqlite3
import time
import uuid
from typing import Any

from bridge.memory_identity import hindsight_generation_tags, hindsight_session_prefix
from bridge.sqlite_store import write_transaction

# A deferred transport/provenance failure permits known exact cleanup. Its cursor
# was reset before deferral, so offset pagination cannot skip the deleted page.
RETIREMENT_SCOPE_UNPAUSED = (
    "NOT EXISTS(SELECT 1 FROM memory_cleanup_discovery d WHERE d.chat_id=r.chat_id "
    "AND d.session_id=r.session_id AND d.phase='enumerate' AND (d.lease_token<>'' OR d.next_attempt_at<=?))"
)


def reopen_retirement(db: sqlite3.Connection, chat_id: str, session_id: str, document_id: str) -> None:
    db.execute(
        "INSERT INTO memory_retired_documents(chat_id,session_id,document_id) VALUES(?,?,?) "
        "ON CONFLICT(chat_id,session_id,document_id) DO UPDATE SET deleted=0,next_attempt_at=0,"
        "retirement_revision=memory_retired_documents.retirement_revision+1",
        (chat_id, session_id, document_id),
    )


def queue_session_memory_cleanup(db: sqlite3.Connection, chat_id: str, session_id: str) -> None:
    """Atomically fence the old generation before the caller removes canonical rows."""
    from bridge.memory_store import purge_external_memory

    if not db.in_transaction:
        raise RuntimeError("Memory cleanup queue requires an active transaction")
    owner = (str(chat_id), str(session_id))
    session = db.execute("SELECT created_at FROM sessions WHERE chat_id=? AND session_id=?", owner).fetchone()
    if session is None:
        raise ValueError("Session no longer exists")
    key = f"hindsight_epoch:{chat_id}:{session_id}"
    meta = db.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
    try:
        epoch = max(0, int(meta[0])) if meta else 0
    except (ValueError, TypeError):
        epoch = 0
    state = db.execute(
        "SELECT purge_epoch FROM memory_layer_state WHERE chat_id=? AND session_id=? "
        "AND session_created_at=? AND layer='hindsight'",
        (*owner, session[0]),
    ).fetchone()
    epoch = max(epoch, int(state[0]) if state else 0)
    targets = db.execute(
        "SELECT document_id FROM hindsight_documents WHERE chat_id=? AND session_id=? UNION "
        "SELECT document_id FROM memory_segments WHERE chat_id=? AND session_id=? AND layer='hindsight' UNION "
        "SELECT document_id FROM memory_fact_index WHERE chat_id=? AND session_id=? UNION "
        "SELECT document_id FROM memory_archival_attempts WHERE chat_id=? AND session_id=?",
        owner * 4,
    ).fetchall()
    prefix = hindsight_session_prefix(session_id)
    for document_id in {row[0] for row in targets} | {
        prefix + "-conversation",
        prefix + "-curated",
        f"st-session-{session_id}",
    }:
        reopen_retirement(db, *owner, document_id)
    purge_external_memory(db, *owner, purge_epoch=epoch + 1)
    db.execute(
        "INSERT INTO meta(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (key, str(epoch + 1)),
    )
    generation = hindsight_generation_tags(*owner, session[0], epoch)[1]
    db.execute(
        "INSERT OR IGNORE INTO memory_cleanup_discovery"
        "(request_id,chat_id,session_id,session_created_at,external_epoch,generation_tag) VALUES(?,?,?,?,?,?)",
        ("cleanup:" + generation.split(":", 1)[1], *owner, session[0], epoch, generation),
    )
    db.execute("DELETE FROM hindsight_documents WHERE chat_id=? AND session_id=?", owner)


def load_discovery(db: sqlite3.Connection, request_id: str, lease_token: str) -> dict[str, Any] | None:
    cursor = db.execute(
        "SELECT chat_id,session_id,generation_tag,phase,query_kind,offset,scan_found FROM memory_cleanup_discovery "
        "WHERE request_id=? AND lease_token=? AND phase<>'complete'",
        (request_id, lease_token),
    )
    row = cursor.fetchone()
    return dict(zip((field[0] for field in cursor.description), row, strict=True)) if row else None


def claim_discovery(db: sqlite3.Connection) -> tuple[str, str] | None:
    now, token = time.time(), uuid.uuid4().hex
    with write_transaction(db):
        row = db.execute(
            "SELECT request_id FROM memory_cleanup_discovery d WHERE phase<>'complete' AND lease_token='' "
            "AND next_attempt_at<=? AND (phase='enumerate' OR NOT EXISTS(SELECT 1 FROM memory_retired_documents r "
            "WHERE r.chat_id=d.chat_id AND r.session_id=d.session_id AND r.deleted=0 AND r.next_attempt_at<=?)) "
            "ORDER BY next_attempt_at,attempts,request_id LIMIT 1",
            (now, now),
        ).fetchone()
        if row is None:
            return None
        db.execute(
            "UPDATE memory_cleanup_discovery SET lease_token=?,lease_deadline=?,attempts=attempts+1 WHERE request_id=?",
            (token, now + 900, row[0]),
        )
    return row[0], token


def prepare_discovery_call(db: sqlite3.Connection, request_id: str, token: str) -> dict[str, Any] | None:
    with write_transaction(db):
        db.execute(
            "UPDATE memory_cleanup_discovery SET phase='enumerate',query_kind='tag',offset=0,scan_found=0 "
            "WHERE request_id=? AND lease_token=? AND phase='verify'",
            (request_id, token),
        )
        db.execute(
            "UPDATE memory_cleanup_discovery SET lease_deadline=? WHERE request_id=? AND lease_token=?",
            (time.time() + 900, request_id, token),
        )
        return load_discovery(db, request_id, token)


def defer_discovery(db: sqlite3.Connection, request_id: str, token: str, *, delay: int = 300) -> None:
    db.execute(
        "UPDATE memory_cleanup_discovery SET phase='verify',query_kind='tag',offset=0,scan_found=0,next_attempt_at=? "
        "WHERE request_id=? AND lease_token=?",
        (time.time() + delay, request_id, token),
    )


def accept_discovery_page(
    db: sqlite3.Connection,
    request_id: str,
    token: str,
    captured: dict[str, Any],
    targets: list[str],
    *,
    unknown: bool,
    count: int,
    total: int,
) -> bool:
    """An owned page records only proven IDs; verify starts again after exact deletes."""
    with write_transaction(db):
        if load_discovery(db, request_id, token) != captured:
            return False
        for document_id in targets:
            reopen_retirement(db, captured["chat_id"], captured["session_id"], document_id)
        if unknown:
            defer_discovery(db, request_id, token)
            return False
        found = int(bool(captured["scan_found"] or targets))
        offset = captured["offset"] + count
        if count and offset < total:
            db.execute(
                "UPDATE memory_cleanup_discovery SET offset=?,scan_found=? WHERE request_id=? AND lease_token=?",
                (offset, found, request_id, token),
            )
            return True
        if captured["query_kind"] == "tag":
            db.execute(
                "UPDATE memory_cleanup_discovery SET query_kind='prefix',offset=0,scan_found=? WHERE "
                "request_id=? AND lease_token=?",
                (found, request_id, token),
            )
            return True
        owner = (captured["chat_id"], captured["session_id"])
        pending = db.execute(
            "SELECT 1 FROM memory_retired_documents WHERE chat_id=? AND session_id=? AND deleted=0 UNION ALL "
            "SELECT 1 FROM memory_archival_attempts WHERE chat_id=? AND session_id=? LIMIT 1",
            owner * 2,
        ).fetchone()
        phase = "verify" if found or pending else "complete"
        db.execute(
            "UPDATE memory_cleanup_discovery SET phase=?,query_kind='tag',offset=0,scan_found=0,next_attempt_at=? "
            "WHERE request_id=? AND lease_token=?",
            (phase, time.time() + 300 if pending and not found else 0, request_id, token),
        )
        return False


def release_discovery(db: sqlite3.Connection, request_id: str, token: str) -> None:
    db.execute(
        "UPDATE memory_cleanup_discovery SET lease_token='',lease_deadline=0 WHERE request_id=? AND lease_token=?",
        (request_id, token),
    )
