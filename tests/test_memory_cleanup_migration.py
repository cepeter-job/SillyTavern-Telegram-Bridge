"""Upgrade preserves native canonical state and dispatch uncertainty."""

import sqlite3

from bridge.schema import SCHEMA_MIGRATIONS, _run_migrations, initialize_database_schema


def test_forward_cleanup_migration_preserves_v26_state_and_uncertainty():
    db = sqlite3.connect(":memory:")
    try:
        _run_migrations(db, tuple(m for m in SCHEMA_MIGRATIONS if m.version <= 26))
        db.execute(
            "INSERT INTO sessions(chat_id,session_id,title,character_file,model_id,persona_id,world_file,created_at,"
            "updated_at) VALUES('c','s','Story','','m','','',1,1)"
        )
        db.execute(
            "INSERT INTO messages(chat_id,session_id,role,content,created_at) VALUES('c','s','user','canonical',2)"
        )
        db.execute("INSERT INTO memory_archival_attempts VALUES('raw-token','raw','c','s',1,3,0,40)")
        db.execute(
            "INSERT INTO memory_fact_index(document_id,memory_id,chat_id,session_id,session_created_at,external_epoch,"
            "payload_digest,state,created_at) VALUES('native',123,'c','s',1,0,'digest','pending',4)"
        )
        db.commit()
        applied = db.execute("SELECT * FROM schema_migrations").fetchall()
        initialize_database_schema(db)
        assert db.execute("SELECT * FROM schema_migrations WHERE version<=26").fetchall() == applied
        assert db.execute("SELECT content FROM messages").fetchone() == ("canonical",)
        assert db.execute("SELECT * FROM memory_archival_attempts WHERE attempt_token='raw-token'").fetchone() == (
            "raw-token",
            "raw",
            "c",
            "s",
            1,
            3,
            0,
            40,
            "raw",
        )
        assert db.execute(
            "SELECT kind,finished,started_at FROM memory_archival_attempts WHERE attempt_token='legacy-native:native'"
        ).fetchone() == ("native_fact", 0, None)
        db.execute("UPDATE memory_fact_index SET state='retired' WHERE document_id='native'")
        db.execute("UPDATE memory_retired_documents SET deleted=1,lease_token='owner',next_attempt_at=500")
        revision = db.execute(
            "SELECT retirement_revision FROM memory_retired_documents WHERE document_id='native'"
        ).fetchone()[0]
        db.execute("UPDATE memory_fact_index SET state='pending' WHERE document_id='native'")
        db.execute("UPDATE memory_fact_index SET state='retired' WHERE document_id='native'")
        assert db.execute(
            "SELECT deleted,lease_token,next_attempt_at,retirement_revision FROM "
            "memory_retired_documents WHERE document_id='native'"
        ).fetchone() == (0, "owner", 0, revision + 1)
        db.commit()
        initialize_database_schema(db)
        assert db.execute("SELECT count(*) FROM memory_archival_attempts").fetchone() == (2,)
    finally:
        db.close()
