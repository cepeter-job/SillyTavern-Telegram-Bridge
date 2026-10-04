"""Canonical operation repository owner."""

from __future__ import annotations

import json
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
    """Greeting delivery scope; other local commits retain their domain recovery."""
    row = db.execute(
        "SELECT j.chat_id,j.session_id,o.kind FROM jobs j JOIN operations o "
        "ON o.operation_id=CAST(j.job_id AS TEXT) "
        "WHERE j.job_id=? AND j.kind='callback' AND o.state='local_committed' "
        "AND o.kind IN ('greeting','start_greeting')",
        (job_id,),
    ).fetchone()
    return (str(row[0]), str(row[1]), str(row[2])) if row else None


def read_scoped_operation(db: sqlite3.Connection, operation_id: str) -> dict | None:
    cursor = db.execute(
        "SELECT o.kind,o.state,o.updated_at,m.value FROM operations o LEFT JOIN meta m "
        "ON m.key='operation_payload:'||o.operation_id WHERE o.operation_id=?",
        (operation_id,),
    )
    row = cursor.fetchone()
    if row is None:
        return None
    raw = row[3] or "{}"
    if len(raw) > 4096:
        raise ValueError("Operation identity payload is oversized")
    payload = json.loads(raw)
    if not isinstance(payload, dict):
        raise ValueError("Operation identity is invalid")
    return {"kind": row[0], "state": row[1], "updated_at": row[2], "payload": payload}


def prepare_scoped_operation(db: sqlite3.Connection, operation_id: str, kind: str, payload: dict, now: float) -> None:
    require_active_transaction(db)
    existing = read_scoped_operation(db, operation_id)
    if existing is not None:
        identity = {k: v for k, v in existing["payload"].items() if k != "lease"}
        if existing["kind"] != kind or identity != payload:
            raise ValueError("This operation belongs to another request")
        return
    encoded = json.dumps(payload, separators=(",", ":"))
    if len(encoded) > 4096:
        raise ValueError("Operation identity payload is oversized")
    db.execute("INSERT INTO operations VALUES(?,?,'prepared',?,?)", (operation_id, kind, now, now))
    db.execute("INSERT INTO meta(key,value) VALUES(?,?)", ("operation_payload:" + operation_id, encoded))


def claim_scoped_operation_stage(
    db: sqlite3.Connection,
    operation_id: str,
    token: str,
    now: float,
    *,
    stage: str,
    prior: str,
) -> bool:
    require_active_transaction(db)
    current = read_scoped_operation(db, operation_id)
    if not current or current["kind"] != "alternate_ending":
        raise ValueError("The alternate-ending operation is missing")
    cursor = db.execute(
        "UPDATE operations SET state=?,updated_at=? WHERE operation_id=? AND kind='alternate_ending' "
        "AND (state=? OR (state=? AND updated_at<?))",
        (stage, now, operation_id, prior, stage, now - 600),
    )
    if cursor.rowcount != 1:
        return False
    payload = current["payload"] | {"lease": token}
    db.execute(
        "UPDATE meta SET value=? WHERE key=?",
        (json.dumps(payload, separators=(",", ":")), "operation_payload:" + operation_id),
    )
    return True


def finish_scoped_operation(db: sqlite3.Connection, operation_id: str, token: str, now: float) -> bool:
    require_active_transaction(db)
    current = read_scoped_operation(db, operation_id)
    if not current or current["state"] != "memory_seeding" or current["payload"].get("lease") != token:
        return False
    db.execute("UPDATE operations SET state='applied',updated_at=? WHERE operation_id=?", (now, operation_id))
    discard_scoped_operation_payload(db, operation_id)
    return True


def recoverable_branch_operations(db: sqlite3.Connection, now: float, limit: int = 32) -> list[str]:
    return [
        row[0]
        for row in db.execute(
            "SELECT o.operation_id FROM operations o LEFT JOIN narrative_branches b ON b.operation_id=o.operation_id "
            "WHERE o.kind='alternate_ending' AND (o.state='prepared' OR (b.memory_status='pending' "
            "AND (o.state='local_committed' OR (o.state='memory_seeding' AND o.updated_at<?)))) "
            "ORDER BY o.updated_at,o.operation_id LIMIT ?",
            (now - 600, max(1, min(32, limit))),
        ).fetchall()
    ]


def discard_scoped_operation_payload(db: sqlite3.Connection, operation_id: str) -> None:
    require_active_transaction(db)
    db.execute("DELETE FROM meta WHERE key=?", ("operation_payload:" + operation_id,))
