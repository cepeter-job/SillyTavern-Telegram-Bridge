"""Transaction-pure SQL for explicit manual summary updates."""

from __future__ import annotations

import sqlite3

from bridge.repository_contracts import require_active_transaction


def latest_message(db: sqlite3.Connection, chat_id: str, session_id: str) -> int:
    row = db.execute(
        "SELECT COALESCE(MAX(rowid),0) FROM messages WHERE chat_id=? AND session_id=?", (chat_id, session_id)
    ).fetchone()
    return int(row[0])


def store_summary(
    db: sqlite3.Connection, chat_id: str, session_id: str, summary: str, covered: int, now: float
) -> None:
    require_active_transaction(db)
    # A manual text replacement has no classified-source proof for archived
    # windows. Never let older private facts survive as silent prompt authority.
    db.execute(
        "DELETE FROM summary_archive_windows WHERE chat_id=? AND session_id=?",
        (chat_id, session_id),
    )
    db.execute(
        "INSERT OR REPLACE INTO session_summaries(chat_id,session_id,summary,covered_until_rowid,updated_at) "
        "VALUES(?,?,?,?,?)",
        (chat_id, session_id, summary, covered, now),
    )
