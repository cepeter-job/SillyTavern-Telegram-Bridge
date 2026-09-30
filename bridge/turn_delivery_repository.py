"""SQL-only immutable job delivery bindings, retained after transcript deletion."""

import sqlite3

from bridge.repository_contracts import require_active_transaction


def bind_turn_delivery(
    db: sqlite3.Connection, job_id: int, user_rowid: int, assistant_rowid: int, payload: str
) -> bool:
    require_active_transaction(db)
    if db.execute("SELECT 1 FROM jobs WHERE job_id=?", (job_id,)).fetchone() is None:
        return False
    db.execute(
        """INSERT OR IGNORE INTO job_delivery_intents(
            job_id,user_rowid,user_content,assistant_rowid,source_content,payload,user_message_id)
        SELECT j.job_id,u.rowid,u.content,a.rowid,a.content,?,u.telegram_message_id
        FROM jobs j JOIN messages a ON a.rowid=? AND a.chat_id=j.chat_id AND a.session_id=j.session_id
        JOIN messages u ON u.chat_id=j.chat_id AND u.session_id=j.session_id AND u.role='user'
          AND u.rowid=? AND u.rowid<a.rowid
        WHERE j.job_id=? AND a.role='assistant'
          AND NOT EXISTS (SELECT 1 FROM messages b WHERE b.chat_id=j.chat_id AND b.session_id=j.session_id
            AND b.rowid>u.rowid AND b.rowid<a.rowid)""",
        (payload, assistant_rowid, user_rowid, job_id),
    )
    row = db.execute(
        "SELECT user_rowid,assistant_rowid,payload FROM job_delivery_intents WHERE job_id=?", (job_id,)
    ).fetchone()
    if row != (user_rowid, assistant_rowid, payload):
        raise ValueError("Cannot bind an ambiguous or changed committed delivery")
    return True


def turn_delivery_target(db: sqlite3.Connection, job_id: int | str | None) -> tuple | None:
    if job_id is None:
        return None
    return db.execute(
        """SELECT j.chat_id,j.session_id,d.assistant_rowid,d.payload,
        CASE WHEN u.role='user' AND u.chat_id=j.chat_id AND u.session_id=j.session_id
          AND u.telegram_message_id IS d.user_message_id AND u.content=d.user_content
          AND a.role='assistant' AND a.chat_id=j.chat_id AND a.session_id=j.session_id
          AND a.content=d.source_content AND u.rowid<a.rowid
          AND NOT EXISTS (SELECT 1 FROM messages b WHERE b.chat_id=j.chat_id AND b.session_id=j.session_id
            AND b.rowid>u.rowid AND b.rowid<a.rowid)
          AND (p.assistant_rowid IS NULL OR (p.source_content=d.source_content AND p.payload=d.payload))
        THEN 1 ELSE 0 END,d.ambiguity_reason
        FROM job_delivery_intents d JOIN jobs j ON j.job_id=d.job_id
        LEFT JOIN messages u ON u.rowid=d.user_rowid LEFT JOIN messages a ON a.rowid=d.assistant_rowid
        LEFT JOIN assistant_delivery_progress p ON p.assistant_rowid=d.assistant_rowid WHERE d.job_id=?""",
        (job_id,),
    ).fetchone()
