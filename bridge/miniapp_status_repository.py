"""Private, read-only operational counts for the Mini App dashboard."""

from __future__ import annotations

import sqlite3


def counts(db: sqlite3.Connection, chat_id: str) -> dict[str, int]:
    sessions = int(db.execute("SELECT COUNT(*) FROM sessions WHERE chat_id=?", (chat_id,)).fetchone()[0])
    messages = int(db.execute("SELECT COUNT(*) FROM messages WHERE chat_id=?", (chat_id,)).fetchone()[0])
    return {"sessions": sessions, "messages": messages}
