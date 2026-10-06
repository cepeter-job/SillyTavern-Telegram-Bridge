"""SQL-only durable job state transitions and update deduplication."""

from __future__ import annotations

import sqlite3

from bridge.repository_contracts import require_active_transaction


def insert_job(
    db: sqlite3.Connection,
    update_id: int,
    chat_id: str,
    session_id: str,
    message_id: str,
    kind: str,
    payload_json: str,
    now: float,
) -> int:
    require_active_transaction(db)
    db.execute(
        "INSERT OR IGNORE INTO jobs(update_id,chat_id,session_id,telegram_message_id,kind,payload_j"
        "son,state,attempts,last_error,created_at,updated_at) "
        "VALUES(?,?,?,?,?,?,'queued',0,'',?,?)",
        (update_id, chat_id, session_id, message_id, kind, payload_json, now, now),
    )
    row = db.execute("SELECT job_id FROM jobs WHERE update_id=?", (update_id,)).fetchone()
    if row is None:
        raise RuntimeError("job handoff failed")
    store_processed_update(db, update_id, now)
    return int(row[0])


def store_processed_update(db: sqlite3.Connection, update_id: int, now: float) -> None:
    require_active_transaction(db)
    db.execute("INSERT OR IGNORE INTO processed_updates(update_id,processed_at) VALUES(?,?)", (update_id, now))


def insert_panel_callback_job(
    db: sqlite3.Connection,
    update_id: int,
    chat_id: str,
    session_id: str,
    message_id: str,
    payload_json: str,
    now: float,
) -> int | None:
    """Atomically admit at most one active callback job for a Telegram panel."""
    require_active_transaction(db)
    if message_id:
        row = db.execute(
            "SELECT job_id FROM jobs WHERE chat_id=? AND telegram_message_id=? AND kind='callback' "
            "AND state IN ('queued','scheduled','running') ORDER BY job_id LIMIT 1",
            (chat_id, message_id),
        ).fetchone()
        if row is not None:
            return None
    return insert_job(db, update_id, chat_id, session_id, message_id, "callback", payload_json, now)


def load_job_payload(db: sqlite3.Connection, job_id: int) -> str:
    row = db.execute("SELECT payload_json FROM jobs WHERE job_id=?", (job_id,)).fetchone()
    return str(row[0] or "{}") if row else "{}"


def replace_job_payload(db: sqlite3.Connection, job_id: int, payload_json: str, now: float) -> bool:
    require_active_transaction(db)
    return (
        db.execute(
            "UPDATE jobs SET payload_json=?, updated_at=? WHERE job_id=? AND state='queued'",
            (payload_json, now, job_id),
        ).rowcount
        == 1
    )


def schedule_job(db: sqlite3.Connection, job_id: int, now: float) -> bool:
    require_active_transaction(db)
    return (
        db.execute(
            "UPDATE jobs SET state='scheduled', updated_at=? WHERE job_id=? AND state='queued'",
            (now, job_id),
        ).rowcount
        == 1
    )


def start_job(db: sqlite3.Connection, job_id: int, now: float) -> bool:
    require_active_transaction(db)
    exhaust_committed_deliveries(db, now, job_id)
    return (
        db.execute(
            "UPDATE jobs SET state='running', attempts=attempts+1, updated_at=? WHERE job_id=? AND stat"
            "e IN ('queued','scheduled')",
            (now, job_id),
        ).rowcount
        == 1
    )


def finish_job_row(db: sqlite3.Connection, job_id: int, state: str, error: str, now: float) -> None:
    require_active_transaction(db)
    db.execute("UPDATE jobs SET state=?, last_error=?, updated_at=? WHERE job_id=?", (state, error, now, job_id))


def reset_running_jobs(db: sqlite3.Connection, now: float) -> None:
    require_active_transaction(db)
    exhaust_committed_deliveries(db, now)
    db.execute("UPDATE jobs SET state='queued', updated_at=? WHERE state IN ('running','scheduled')", (now,))


def queued_job_rows(db: sqlite3.Connection) -> list[tuple]:
    return db.execute(
        "SELECT job_id,chat_id,session_id,telegram_message_id,kind,payload_json "
        "FROM jobs WHERE state='queued' ORDER BY created_at LIMIT 128"
    ).fetchall()


def retry_delivery_row(db: sqlite3.Connection, job_id: int, error: str, now: float, limit: int) -> bool:
    require_active_transaction(db)
    return (
        db.execute(
            "UPDATE jobs SET state='queued',last_error=?,updated_at=? "
            "WHERE job_id=? AND state='running' AND attempts<?",
            (error, now, job_id, limit),
        ).rowcount
        == 1
    )


def exhaust_committed_deliveries(db: sqlite3.Connection, now: float, job_id: int | None = None) -> None:
    require_active_transaction(db)
    db.execute(
        """UPDATE jobs SET state='failed',last_error='delivery incomplete: automatic attempt limit reached; use /retry',
        updated_at=? WHERE state IN ('queued','scheduled','running') AND attempts>=3
          AND (? IS NULL OR job_id=?) AND (EXISTS (SELECT 1 FROM job_delivery_intents d WHERE d.job_id=jobs.job_id)
            OR EXISTS (SELECT 1 FROM operations o WHERE o.operation_id=CAST(jobs.job_id AS TEXT)
              AND o.state='local_committed'
              AND (o.kind IN ('greeting','start_greeting','edit','regen','continue','simulation_check')
                OR EXISTS (SELECT 1 FROM meta m WHERE m.key='operation_payload:' || o.operation_id
                  AND json_type(CASE WHEN json_valid(m.value) THEN m.value ELSE '{}' END,'$.assistant_rowid')='integer'
                  AND json_type(CASE WHEN json_valid(m.value) THEN m.value ELSE '{}' END,'$.source_content')='text'
                  AND json_type(CASE WHEN json_valid(m.value) THEN m.value ELSE '{}' END,'$.delivery_payload')='text'
                ))))""",
        (now, job_id, job_id),
    )
