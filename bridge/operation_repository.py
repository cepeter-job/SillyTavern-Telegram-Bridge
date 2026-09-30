"""Canonical operation repository owner."""

from __future__ import annotations

import sqlite3

from bridge.repository_contracts import require_active_transaction


def claim_operation(
    db: sqlite3.Connection,
    operation_id: int | str | None,
    kind: str,
    now: float,
) -> bool:
    if operation_id is None:
        return True
    require_active_transaction(db)
    operation_id = str(operation_id)
    cursor = db.execute(
        "INSERT OR IGNORE INTO operations(operation_id,kind,state,created_at,updated_at) VALUES(?,?,'in_progress',?,?)",
        (operation_id, str(kind), float(now), float(now)),
    )
    if cursor.rowcount == 1:
        return True
    row = db.execute(
        "SELECT state FROM operations WHERE operation_id=?",
        (operation_id,),
    ).fetchone()
    return not row or str(row[0]) != "applied"


def mark_operation_applied(
    db: sqlite3.Connection,
    operation_id: int | str | None,
    kind: str,
    now: float,
) -> None:
    if operation_id is None:
        return
    require_active_transaction(db)
    db.execute(
        "UPDATE operations SET state='applied',kind=?,updated_at=? WHERE operation_id=?",
        (str(kind), float(now), str(operation_id)),
    )


def load_operation_phase(db: sqlite3.Connection, operation_id: str) -> str:
    row = db.execute("SELECT state FROM operations WHERE operation_id=?", (operation_id,)).fetchone()
    return str(row[0]) if row else ""


def write_operation_phase(db: sqlite3.Connection, operation_id: str, kind: str, phase: str, now: float) -> None:
    require_active_transaction(db)
    db.execute(
        "UPDATE operations SET state=?, kind=?, updated_at=? WHERE operation_id=?", (phase, kind, now, operation_id)
    )


def delivery_user_source(db: sqlite3.Connection, assistant_rowid: int, user_rowid: int) -> tuple | None:
    return db.execute(
        "SELECT u.content,u.telegram_message_id FROM messages u JOIN messages a "
        "ON a.rowid=? AND a.chat_id=u.chat_id AND a.session_id=u.session_id "
        "WHERE u.rowid=? AND u.role='user' AND a.role='assistant'",
        (assistant_rowid, user_rowid),
    ).fetchone()


def delivery_operation_valid(
    db: sqlite3.Connection, operation_id: int | str, assistant_rowid: int, payload: str | None
) -> bool | None:
    row = db.execute(
        """SELECT CASE WHEN a.role='assistant' AND a.rowid=?
          AND a.content=json_extract(meta.value,'$.source_content')
          AND ?=json_extract(meta.value,'$.delivery_payload')
          AND (j.job_id IS NULL OR (a.chat_id=j.chat_id AND a.session_id=j.session_id))
          AND (p.assistant_rowid IS NULL OR (p.source_content=a.content AND p.payload=?))
          AND (json_extract(meta.value,'$.user_rowid') IS NULL OR
            (u.role='user' AND u.chat_id=a.chat_id AND u.session_id=a.session_id
             AND (json_type(meta.value,'$.user_source_content') IS NULL
               OR u.content=json_extract(meta.value,'$.user_source_content'))
             AND (json_type(meta.value,'$.user_message_id') IS NULL
               OR u.telegram_message_id IS json_extract(meta.value,'$.user_message_id'))))
        THEN 1 ELSE 0 END FROM meta
        LEFT JOIN messages a ON a.rowid=json_extract(meta.value,'$.assistant_rowid')
        LEFT JOIN messages u ON u.rowid=json_extract(meta.value,'$.user_rowid')
        LEFT JOIN assistant_delivery_progress p ON p.assistant_rowid=a.rowid
        LEFT JOIN jobs j ON CAST(j.job_id AS TEXT)=?
        WHERE meta.key='operation_payload:' || ?
          AND json_type(meta.value,'$.source_content') IS NOT NULL
          AND json_type(meta.value,'$.delivery_payload') IS NOT NULL""",
        (assistant_rowid, payload, payload, str(operation_id), str(operation_id)),
    ).fetchone()
    return bool(row[0]) if row else None


def committed_callback_operation(db: sqlite3.Connection, job_id: int | None) -> tuple[str, str, str] | None:
    """The enqueued scope and committed kind, independent of the current panel/view."""
    row = db.execute(
        "SELECT j.chat_id,j.session_id,o.kind FROM jobs j JOIN operations o "
        "ON o.operation_id=CAST(j.job_id AS TEXT) "
        "WHERE j.job_id=? AND j.kind='callback' AND o.state='local_committed'",
        (job_id,),
    ).fetchone()
    return (str(row[0]), str(row[1]), str(row[2])) if row else None
