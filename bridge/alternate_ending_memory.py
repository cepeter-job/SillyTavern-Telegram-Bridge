"""Local branch readiness; durable workers process extraction and native indexes later."""

from __future__ import annotations

import sqlite3

from bridge.memory_fact_store import index_fact_is_current
from bridge.settings import AppSettings
from bridge.sqlite_store import write_transaction


def seed_local_session_memory(db: sqlite3.Connection, chat_id: str, session: dict[str, str]) -> str:
    """Validate the target and durably schedule missing work in one short transaction."""
    if db.in_transaction:
        raise ValueError("Local memory initialization must own its transaction")
    session_id = str(session["session_id"])
    with write_transaction(db):
        row = db.execute(
            "SELECT created_at FROM sessions WHERE chat_id=? AND session_id=?", (chat_id, session_id)
        ).fetchone()
        if row is None:
            raise ValueError("The memory target was deleted; it will not be recreated")
        owner = (chat_id, session_id, row[0])
        pending = db.execute(
            "SELECT document_id FROM memory_fact_index WHERE chat_id=? AND session_id=? "
            "AND session_created_at=? AND state='pending'",
            owner,
        ).fetchall()
        has_native_work = any(index_fact_is_current(db, document_id) is not None for (document_id,) in pending)
        layers = ("episodes", "summary", "scene", "npc", "curator") + (("hindsight",) if has_native_work else ())
        for layer in layers:
            db.execute(
                "INSERT OR IGNORE INTO memory_jobs(chat_id,session_id,session_created_at,layer,target_id) "
                "VALUES(?,?,?,?,(SELECT COALESCE(MAX(id),0) FROM messages WHERE chat_id=? AND session_id=?))",
                (*owner, layer, chat_id, session_id),
            )
        if has_native_work:
            db.execute(
                "UPDATE memory_jobs SET dirty_version=dirty_version+1,next_attempt_at=0 WHERE chat_id=? "
                "AND session_id=? AND session_created_at=? AND layer='hindsight' AND dirty_version=completed_version",
                owner,
            )
    return "ready"


def seed_alternate_ending_memory(
    db: sqlite3.Connection,
    chat_id: str,
    target_session: dict[str, str],
    *,
    app_settings: AppSettings,
) -> str:
    return seed_local_session_memory(db, chat_id, target_session)
