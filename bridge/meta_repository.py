"""Canonical meta repository owner."""

from __future__ import annotations

import sqlite3

from bridge.repository_contracts import require_active_transaction


def load_meta_value(
    db: sqlite3.Connection,
    key: str,
    default: str = "",
) -> str:
    row = db.execute(
        "SELECT value FROM meta WHERE key=?",
        (str(key),),
    ).fetchone()
    return str(row[0]) if row else str(default)


def store_meta_value(
    db: sqlite3.Connection,
    key: str,
    value: str,
) -> None:
    require_active_transaction(db)
    db.execute(
        "INSERT OR REPLACE INTO meta(key,value) VALUES(?,?)",
        (str(key), str(value)),
    )


def delete_meta_value(
    db: sqlite3.Connection,
    key: str,
) -> None:
    require_active_transaction(db)
    db.execute(
        "DELETE FROM meta WHERE key=?",
        (str(key),),
    )


def session_task_models(db: sqlite3.Connection, chat_id: str, session_id: str) -> dict[str, str]:
    """Read only task-model preferences; never copy arbitrary session-suffixed metadata."""
    suffix = f":{chat_id}:{session_id}"
    rows = db.execute(
        "SELECT key,value FROM meta WHERE substr(key,1,11)='task_model:' AND substr(key,-length(?))=? LIMIT 33",
        (suffix, suffix),
    ).fetchall()
    if len(rows) > 32:
        raise ValueError("Too many task model settings for a bounded narrative snapshot")
    return {str(key)[11 : -len(suffix)]: str(value) for key, value in rows}
