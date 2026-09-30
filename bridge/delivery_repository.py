"""SQL-only delivery state; write scopes belong to the caller."""

import json
import sqlite3

from bridge.repository_contracts import require_active_transaction


def matching_progress(db: sqlite3.Connection, rowid: int) -> tuple[str, list[int], bool] | None:
    row = db.execute(
        "SELECT p.payload,p.message_ids,p.complete FROM assistant_delivery_progress p "
        "JOIN messages m ON m.rowid=p.assistant_rowid "
        "WHERE p.assistant_rowid=? AND m.content=p.source_content",
        (rowid,),
    ).fetchone()
    return (str(row[0]), json.loads(row[1]), bool(row[2])) if row else None


def is_complete(db: sqlite3.Connection, rowid: int) -> bool:
    row = db.execute(
        "SELECT m.telegram_message_ids,p.assistant_rowid,p.complete,p.source_content=m.content "
        "FROM messages m LEFT JOIN assistant_delivery_progress p ON p.assistant_rowid=m.rowid "
        "WHERE m.rowid=?",
        (rowid,),
    ).fetchone()
    if not row:
        return False
    if row[1] is not None:
        return bool(row[2] and row[3])
    return bool(json.loads(row[0] or "[]"))


def begin_progress(db: sqlite3.Connection, rowid: int, payload: str) -> None:
    require_active_transaction(db)
    cursor = db.execute(
        "INSERT INTO assistant_delivery_progress(assistant_rowid,source_content,payload) "
        "SELECT rowid,content,? FROM messages WHERE rowid=? AND role='assistant'",
        (payload, rowid),
    )
    if cursor.rowcount != 1:
        raise ValueError("assistant delivery source does not exist")
    db.execute("UPDATE messages SET telegram_message_ids='[]' WHERE rowid=?", (rowid,))


def checkpoint_progress(
    db: sqlite3.Connection,
    rowid: int,
    ids: list[int],
    complete: bool,
    expected_source: str | None = None,
    expected_payload: str | None = None,
) -> None:
    require_active_transaction(db)
    cursor = db.execute(
        "UPDATE assistant_delivery_progress SET message_ids=?,complete=? WHERE assistant_rowid=? "
        "AND source_content=(SELECT content FROM messages WHERE rowid=?) "
        "AND (? IS NULL OR source_content=?) AND (? IS NULL OR payload=?)",
        (
            json.dumps(ids),
            int(complete),
            rowid,
            rowid,
            expected_source,
            expected_source,
            expected_payload,
            expected_payload,
        ),
    )
    if cursor.rowcount != 1:
        raise ValueError("assistant delivery source changed")
    db.execute("UPDATE messages SET telegram_message_ids=? WHERE rowid=?", (json.dumps(ids), rowid))


def clear_progress(db: sqlite3.Connection, rowid: int) -> None:
    require_active_transaction(db)
    db.execute("DELETE FROM assistant_delivery_progress WHERE assistant_rowid=?", (rowid,))
    store_delivery_ids(db, rowid, [])


def store_delivery_ids(db: sqlite3.Connection, rowid: int, ids: list[int]) -> None:
    require_active_transaction(db)
    db.execute("UPDATE messages SET telegram_message_ids=? WHERE rowid=?", (json.dumps(ids), rowid))


def assistant_source(db: sqlite3.Connection, rowid: int) -> str | None:
    row = db.execute("SELECT content FROM messages WHERE rowid=? AND role='assistant'", (rowid,)).fetchone()
    return str(row[0]) if row else None


def has_progress(db: sqlite3.Connection, rowid: int) -> bool:
    return (
        db.execute("SELECT 1 FROM assistant_delivery_progress WHERE assistant_rowid=?", (rowid,)).fetchone() is not None
    )


def has_delivery_owner(db: sqlite3.Connection, rowid: int) -> bool:
    """Retain known original ownership even when its target/checkpoint has changed."""
    return (
        db.execute(
            "SELECT 1 FROM job_delivery_intents WHERE assistant_rowid=? "
            "UNION ALL SELECT 1 FROM meta WHERE key LIKE 'operation_payload:%' "
            "AND json_valid(value) AND json_extract(value,'$.assistant_rowid')=? LIMIT 1",
            (rowid, rowid),
        ).fetchone()
        is not None
    )
