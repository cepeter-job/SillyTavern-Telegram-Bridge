"""Forward-only raw archival dispatch obligations, independent of source validity."""

import sqlite3


def migrate_archival_attempts(db: sqlite3.Connection) -> None:
    db.execute("""CREATE TABLE memory_archival_attempts(
        attempt_token TEXT PRIMARY KEY,document_id TEXT NOT NULL,
        chat_id TEXT NOT NULL,session_id TEXT NOT NULL,
        session_created_at REAL,started_at REAL,
        finished INTEGER NOT NULL DEFAULT 0 CHECK(finished IN (0,1)),
        next_check_at REAL NOT NULL DEFAULT 0)""")
    db.execute("CREATE INDEX memory_archival_document_idx ON memory_archival_attempts(document_id)")
    db.execute("CREATE INDEX memory_archival_due_idx ON memory_archival_attempts(next_check_at,attempt_token)")
    db.execute(
        "CREATE INDEX memory_archival_scope_due_idx "
        "ON memory_archival_attempts(chat_id,session_id,next_check_at,attempt_token)"
    )
    # Recorded IDs prove neither an old dispatch nor its completion. Preserve
    # that uncertainty, including an older request hidden by a successful retry.
    db.execute(
        "INSERT INTO memory_archival_attempts(attempt_token,document_id,chat_id,session_id,session_created_at) "
        "SELECT 'legacy:'||document_id,document_id,chat_id,session_id,session_created_at "
        "FROM memory_segments WHERE layer='hindsight'"
    )
    db.execute(
        "INSERT OR IGNORE INTO memory_archival_attempts(attempt_token,document_id,chat_id,session_id) "
        "SELECT 'legacy:'||document_id,document_id,chat_id,session_id "
        "FROM hindsight_documents WHERE kind='source_segment'"
    )
