"""Forward migration for local-first lifecycle cleanup and native uncertainty."""

import sqlite3


def migrate_local_first_memory_cleanup(db: sqlite3.Connection) -> None:
    db.execute(
        "ALTER TABLE memory_archival_attempts ADD COLUMN kind TEXT NOT NULL DEFAULT 'raw' "
        "CHECK(kind IN ('raw','native_fact'))"
    )
    db.execute("ALTER TABLE memory_retired_documents ADD COLUMN retirement_revision INTEGER NOT NULL DEFAULT 1")
    db.execute("""CREATE TABLE memory_cleanup_discovery(
        request_id TEXT PRIMARY KEY,chat_id TEXT NOT NULL,session_id TEXT NOT NULL,session_created_at REAL,
        external_epoch INTEGER NOT NULL,generation_tag TEXT NOT NULL,next_attempt_at REAL NOT NULL DEFAULT 0,
        attempts INTEGER NOT NULL DEFAULT 0,lease_token TEXT NOT NULL DEFAULT '',lease_deadline REAL NOT NULL DEFAULT 0,
        phase TEXT NOT NULL DEFAULT 'enumerate' CHECK(phase IN ('enumerate','verify','complete')),
        query_kind TEXT NOT NULL DEFAULT 'tag' CHECK(query_kind IN ('tag','prefix')),
        offset INTEGER NOT NULL DEFAULT 0,scan_found INTEGER NOT NULL DEFAULT 0)""")
    db.execute(
        "CREATE INDEX memory_cleanup_discovery_due_idx "
        "ON memory_cleanup_discovery(next_attempt_at,lease_deadline,request_id)"
    )
    # Read only the installed, fixed migration trigger definitions; old migrations stay immutable.
    for name in ("memory_source_retired", "memory_fact_index_retired", "memory_session_delete"):
        sql = db.execute("SELECT sql FROM sqlite_master WHERE type='trigger' AND name=?", (name,)).fetchone()[0]
        sql = sql.replace("DO UPDATE SET deleted=0,next_attempt_at=0", "DO UPDATE SET deleted=0")
        sql = sql.replace(
            "DO UPDATE SET deleted=0",
            "DO UPDATE SET "
            "deleted=0,next_attempt_at=0,retirement_revision=memory_retired_documents.retirement_revision+1",
        )
        if name == "memory_source_retired":
            sql = sql.replace("WHEN NEW.valid=0", "WHEN NEW.valid=0 AND OLD.valid<>0")
        db.execute("DROP TRIGGER " + name)
        db.execute(sql)
    db.execute("DROP TRIGGER memory_job_state")
    db.execute("""CREATE TRIGGER memory_job_state AFTER INSERT ON memory_jobs BEGIN
        INSERT OR IGNORE INTO memory_layer_state(chat_id,session_id,session_created_at,layer,purge_epoch)
        VALUES(NEW.chat_id,NEW.session_id,NEW.session_created_at,NEW.layer,
        CASE WHEN NEW.layer='hindsight' THEN MAX(0,CAST(COALESCE((SELECT value FROM meta
        WHERE key='hindsight_epoch:'||NEW.chat_id||':'||NEW.session_id),'0') AS INTEGER)) ELSE 0 END); END""")
    # Historical pending records cannot distinguish never sent from timeout.
    # A NULL started_at explicitly records inferred uncertainty, not a proven live call.
    db.execute(
        "INSERT OR IGNORE INTO memory_archival_attempts"
        "(attempt_token,document_id,chat_id,session_id,session_created_at,kind) "
        "SELECT 'legacy-native:'||document_id,document_id,chat_id,session_id,session_created_at,'native_fact' "
        "FROM memory_fact_index WHERE state IN ('pending','retained','retired')"
    )
