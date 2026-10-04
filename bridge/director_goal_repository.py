"""Canonical director goal repository owner."""

from __future__ import annotations

import sqlite3

from bridge.repository_contracts import require_active_transaction


def load_director_goal(
    db: sqlite3.Connection,
    chat_id: str,
    session_id: str,
) -> str:
    row = db.execute(
        "SELECT goal FROM director_state WHERE chat_id=? AND session_id=?",
        (str(chat_id), str(session_id)),
    ).fetchone()
    return str(row[0]) if row else ""


def store_director_goal(
    db: sqlite3.Connection,
    chat_id: str,
    session_id: str,
    goal: str,
    updated_at: float,
    expected_revision: int,
) -> bool:
    """Publish a manual objective and invalidate an older in-flight AI lease."""
    require_active_transaction(db)
    if expected_revision == 0:
        db.execute(
            "INSERT INTO director_state(chat_id,session_id,goal,updated_at) VALUES(?,?,'',?) "
            "ON CONFLICT(chat_id,session_id) DO NOTHING",
            (chat_id, session_id, updated_at),
        )
    cursor = db.execute(
        "UPDATE director_state SET goal=?,state_revision=state_revision+1,updated_at=?,inflight_token='',"
        "inflight_started_at=0 WHERE chat_id=? AND session_id=? AND state_revision=?",
        (goal, updated_at, chat_id, session_id, expected_revision),
    )
    return cursor.rowcount == 1
