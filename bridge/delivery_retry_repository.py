"""SQL-only lookup of exact, still-current failed delivery targets."""

import sqlite3


def failed_delivery_target(db: sqlite3.Connection, chat_id: str, session_id: str) -> tuple | None:
    return db.execute(
        """SELECT j.job_id,j.payload_json,o.kind,COALESCE(d.assistant_rowid,m.rowid),COALESCE(d.payload,p.payload)
        FROM jobs j
        LEFT JOIN operations o ON o.operation_id=CAST(j.job_id AS TEXT)
        LEFT JOIN meta payload ON payload.key='operation_payload:' || j.job_id
        LEFT JOIN job_delivery_intents d ON d.job_id=j.job_id
        LEFT JOIN messages m ON m.chat_id=j.chat_id AND m.session_id=j.session_id AND m.role='assistant'
          AND (d.job_id IS NULL OR m.rowid=d.assistant_rowid)
        LEFT JOIN assistant_delivery_progress p ON p.assistant_rowid=m.rowid AND p.source_content=m.content
        WHERE j.chat_id=? AND j.session_id=? AND j.state='failed'
          AND (d.job_id IS NOT NULL OR j.last_error LIKE 'delivery incomplete:%')
          AND (d.job_id IS NOT NULL OR (p.assistant_rowid IS NOT NULL AND (
            (o.state='local_committed'
             AND m.rowid=json_extract(payload.value,'$.assistant_rowid')
             AND m.content=json_extract(payload.value,'$.source_content')
             AND p.payload=json_extract(payload.value,'$.delivery_payload'))
            OR (j.kind IN ('generation','image','voice','document') AND EXISTS (
                SELECT 1 FROM messages u WHERE u.chat_id=j.chat_id AND u.session_id=j.session_id
                AND u.role='user' AND u.telegram_message_id=j.telegram_message_id AND u.rowid<m.rowid
                AND NOT EXISTS (SELECT 1 FROM messages between_turn
                    WHERE between_turn.chat_id=j.chat_id AND between_turn.session_id=j.session_id
                    AND between_turn.rowid>u.rowid AND between_turn.rowid<m.rowid)
            ))
          )))
        ORDER BY j.updated_at DESC,j.job_id DESC LIMIT 1""",
        (chat_id, session_id),
    ).fetchone()
