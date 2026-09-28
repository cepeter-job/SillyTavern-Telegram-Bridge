"""Forward-only usage-ledger schema; no content or credentials are retained."""

from __future__ import annotations

import sqlite3


def migrate_token_usage(db: sqlite3.Connection) -> None:
    db.execute("""CREATE TABLE token_usage_events (
        id INTEGER PRIMARY KEY,
        chat_id TEXT NOT NULL, session_id TEXT NOT NULL,
        model TEXT NOT NULL, purpose TEXT NOT NULL, created_at REAL NOT NULL,
        status TEXT NOT NULL, elapsed_ms INTEGER NOT NULL CHECK(elapsed_ms >= 0),
        input_tokens INTEGER, output_tokens INTEGER, total_tokens INTEGER,
        cached_tokens INTEGER, reasoning_tokens INTEGER,
        reported INTEGER NOT NULL CHECK(reported IN (0,1)),
        complete INTEGER NOT NULL CHECK(complete IN (0,1))
    )""")
    db.execute("CREATE INDEX token_usage_chat_time ON token_usage_events(chat_id,created_at)")
    db.execute("CREATE INDEX token_usage_session_time ON token_usage_events(chat_id,session_id,created_at)")
    db.execute("CREATE INDEX token_usage_retention ON token_usage_events(created_at)")
    db.execute("""CREATE TRIGGER token_usage_session_delete AFTER DELETE ON sessions
        BEGIN DELETE FROM token_usage_events WHERE chat_id=OLD.chat_id AND session_id=OLD.session_id; END""")
