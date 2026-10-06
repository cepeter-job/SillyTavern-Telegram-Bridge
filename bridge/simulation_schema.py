"""Canonical session-scoped simulation tracker storage."""

from __future__ import annotations

import sqlite3


def migrate_simulation_trackers(db: sqlite3.Connection) -> None:
    if not db.in_transaction:
        raise RuntimeError("Simulation tracker migration requires an active transaction")
    db.execute("""CREATE TABLE simulation_revisions(
        chat_id TEXT NOT NULL,session_id TEXT NOT NULL,revision INTEGER NOT NULL DEFAULT 0,
        PRIMARY KEY(chat_id,session_id),
        FOREIGN KEY(chat_id,session_id) REFERENCES sessions(chat_id,session_id) ON DELETE CASCADE)""")
    db.execute("""CREATE TABLE simulation_sources(
        chat_id TEXT NOT NULL,session_id TEXT NOT NULL,source_rowid INTEGER NOT NULL,
        source_digest TEXT NOT NULL,PRIMARY KEY(chat_id,session_id,source_rowid),
        FOREIGN KEY(chat_id,session_id) REFERENCES sessions(chat_id,session_id) ON DELETE CASCADE)""")
    db.execute(
        """
        CREATE TABLE simulation_state(
            chat_id TEXT NOT NULL,
            session_id TEXT NOT NULL,
            kind TEXT NOT NULL,
            entity_key TEXT NOT NULL,
            value_json TEXT NOT NULL CHECK(json_valid(value_json) AND length(value_json)<=16384),
            updated_rowid INTEGER NOT NULL,
            updated_at REAL NOT NULL,
            PRIMARY KEY(chat_id,session_id,kind,entity_key),
            FOREIGN KEY(chat_id,session_id) REFERENCES sessions(chat_id,session_id) ON DELETE CASCADE
        )
        """
    )
    db.execute(
        "CREATE INDEX simulation_state_session_kind_idx ON simulation_state(chat_id,session_id,kind,updated_rowid)"
    )
    db.execute(
        """
        CREATE TABLE simulation_state_history(
            change_id INTEGER PRIMARY KEY AUTOINCREMENT,
            chat_id TEXT NOT NULL,
            session_id TEXT NOT NULL,
            kind TEXT NOT NULL,
            entity_key TEXT NOT NULL,
            before_json TEXT,
            after_json TEXT,
            before_rowid INTEGER NOT NULL DEFAULT 0,
            after_rowid INTEGER NOT NULL DEFAULT 0,
            source_rowid INTEGER NOT NULL,
            created_at REAL NOT NULL,
            FOREIGN KEY(chat_id,session_id) REFERENCES sessions(chat_id,session_id) ON DELETE CASCADE
        )
        """
    )
    db.execute(
        "CREATE INDEX simulation_history_session_row_idx "
        "ON simulation_state_history(chat_id,session_id,source_rowid,change_id)"
    )
    db.execute(
        """
        CREATE TABLE simulation_checks(
            check_id INTEGER PRIMARY KEY AUTOINCREMENT,
            chat_id TEXT NOT NULL,
            session_id TEXT NOT NULL,
            request_key TEXT NOT NULL,
            source_rowid INTEGER NOT NULL,
            domain TEXT NOT NULL,
            actor TEXT NOT NULL,
            action TEXT NOT NULL,
            dc INTEGER NOT NULL,
            roll INTEGER NOT NULL,
            modifier INTEGER NOT NULL,
            delta INTEGER NOT NULL,
            outcome TEXT NOT NULL,
            created_at REAL NOT NULL,
            source_digest TEXT NOT NULL,
            UNIQUE(chat_id,session_id,request_key),
            FOREIGN KEY(chat_id,session_id) REFERENCES sessions(chat_id,session_id) ON DELETE CASCADE
        )
        """
    )
    db.execute(
        "CREATE INDEX simulation_checks_session_row_idx ON simulation_checks(chat_id,session_id,source_rowid,check_id)"
    )
