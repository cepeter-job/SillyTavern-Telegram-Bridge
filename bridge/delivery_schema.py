"""Durable assistant delivery checkpoints, independent of operation/job lifecycles."""

import sqlite3


def migrate_delivery_progress(db: sqlite3.Connection) -> None:
    db.execute("""CREATE TABLE assistant_delivery_progress (
        assistant_rowid INTEGER PRIMARY KEY,
        source_content TEXT NOT NULL,
        payload TEXT NOT NULL,
        message_ids TEXT NOT NULL DEFAULT '[]',
        complete INTEGER NOT NULL DEFAULT 0 CHECK(complete IN (0,1)),
        FOREIGN KEY(assistant_rowid) REFERENCES messages(id) ON DELETE CASCADE
    )""")
    db.execute("""CREATE TRIGGER messages_delete_delivery_progress AFTER DELETE ON messages BEGIN
        DELETE FROM assistant_delivery_progress WHERE assistant_rowid=OLD.id;
    END""")


def migrate_job_delivery_intents(db: sqlite3.Connection) -> None:
    db.execute("""CREATE TABLE job_delivery_intents (
        job_id INTEGER PRIMARY KEY,
        user_rowid INTEGER,
        user_content TEXT NOT NULL DEFAULT '',
        assistant_rowid INTEGER,
        source_content TEXT NOT NULL DEFAULT '',
        payload TEXT NOT NULL DEFAULT '',
        ambiguity_reason TEXT NOT NULL DEFAULT '',
        user_message_id TEXT,
        FOREIGN KEY(job_id) REFERENCES jobs(job_id) ON DELETE CASCADE
    )""")
    db.execute("""CREATE TRIGGER jobs_delete_delivery_intent AFTER DELETE ON jobs BEGIN
        DELETE FROM job_delivery_intents WHERE job_id=OLD.job_id;
    END""")
    # Backfill only exact input/session associations. Multiple jobs or sources
    # leave a committed tombstone with no guessed target, so recovery fails closed.
    db.execute("""CREATE TEMP TABLE delivery_backfill AS
        SELECT j.job_id,u.rowid AS user_rowid,u.content AS user_content,
          a.rowid AS assistant_rowid,a.content AS source_content,p.payload,u.telegram_message_id AS user_message_id
        FROM jobs j JOIN messages u ON u.chat_id=j.chat_id AND u.session_id=j.session_id
          AND u.role='user' AND u.telegram_message_id IS j.telegram_message_id
        JOIN messages a ON a.chat_id=j.chat_id AND a.session_id=j.session_id AND a.role='assistant' AND a.rowid>u.rowid
        JOIN assistant_delivery_progress p ON p.assistant_rowid=a.rowid AND p.source_content=a.content
        WHERE j.attempts>0 AND j.kind IN ('generation','image','voice','document')
          AND NOT EXISTS (SELECT 1 FROM operations o WHERE o.operation_id=CAST(j.job_id AS TEXT))
          AND NOT EXISTS (SELECT 1 FROM messages b WHERE b.chat_id=j.chat_id AND b.session_id=j.session_id
            AND b.rowid>u.rowid AND b.rowid<a.rowid)""")
    db.execute("INSERT INTO job_delivery_intents(job_id) SELECT DISTINCT job_id FROM delivery_backfill")
    db.execute("""UPDATE job_delivery_intents SET
        (user_rowid,user_content,assistant_rowid,source_content,payload,user_message_id)=
        (SELECT user_rowid,user_content,assistant_rowid,source_content,payload,user_message_id FROM delivery_backfill b
         WHERE b.job_id=job_delivery_intents.job_id)
        WHERE (SELECT COUNT(*) FROM delivery_backfill b WHERE b.job_id=job_delivery_intents.job_id)=1
          AND (SELECT COUNT(*) FROM jobs j JOIN jobs original ON original.job_id=job_delivery_intents.job_id
            WHERE j.chat_id=original.chat_id AND j.session_id=original.session_id
              AND j.telegram_message_id IS original.telegram_message_id
              AND j.kind IN ('generation','image','voice','document'))=1""")
    db.execute("""INSERT OR IGNORE INTO job_delivery_intents(job_id)
        SELECT j.job_id FROM jobs j WHERE j.attempts>0 AND j.kind IN ('generation','image','voice','document')
          AND NOT EXISTS (SELECT 1 FROM operations o WHERE o.operation_id=CAST(j.job_id AS TEXT))
          AND EXISTS (SELECT 1 FROM assistant_delivery_progress p JOIN messages a ON a.rowid=p.assistant_rowid
            WHERE a.chat_id=j.chat_id AND a.session_id=j.session_id AND p.complete=0
              AND NOT EXISTS (SELECT 1 FROM job_delivery_intents bound WHERE bound.assistant_rowid=p.assistant_rowid))
        """)
    db.execute("""UPDATE job_delivery_intents SET ambiguity_reason='Legacy delivery association is ambiguous'
        WHERE assistant_rowid IS NULL""")
    db.execute("DROP TABLE delivery_backfill")
