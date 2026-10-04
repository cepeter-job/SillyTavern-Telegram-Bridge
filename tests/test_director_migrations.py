"""Director leases and edit epochs are forward schema changes, never provider work."""

import sqlite3
from contextlib import closing

from bridge.migrations import run_migrations
from bridge.schema import SCHEMA_MIGRATIONS


def old_database():
    db = sqlite3.connect(":memory:")
    db.execute("PRAGMA foreign_keys=ON")
    run_migrations(db, tuple(m for m in SCHEMA_MIGRATIONS if m.version <= 11))
    db.execute(
        "INSERT INTO sessions(chat_id,session_id,title,character_file,model_id,persona_id,"
        "world_file,created_at,updated_at) "
        "VALUES('chat','story','Story','','model','','',1,1)"
    )
    db.execute(
        "INSERT INTO messages(chat_id,session_id,role,content,created_at) "
        "VALUES('chat','story','assistant','Old scene',1)"
    )
    db.execute("INSERT INTO director_state(chat_id,session_id,goal) VALUES('chat','story','Keep this objective')")
    db.commit()
    return db


def test_director_runtime_is_forward_migration_twelve():
    assert any(m.version == 12 and m.name == "director_runtime" for m in SCHEMA_MIGRATIONS)


def test_upgrade_preserves_manual_goal_and_does_not_backfill_model_plans():
    with closing(old_database()) as db:
        before = db.execute("SELECT content FROM messages").fetchall()
        run_migrations(db, SCHEMA_MIGRATIONS)
        assert db.execute("SELECT goal,inflight_token,active_proposal_json FROM director_state").fetchone() == (
            "Keep this objective",
            "",
            "{}",
        )
        assert db.execute("SELECT content FROM messages").fetchall() == before
        changes = db.total_changes
        run_migrations(db, SCHEMA_MIGRATIONS)
        assert db.total_changes == changes


def test_rewrite_epoch_distinguishes_append_delivery_and_in_place_history_edits():
    with closing(old_database()) as db:
        run_migrations(db, SCHEMA_MIGRATIONS)

        def epoch():
            return db.execute("SELECT rewrite_revision FROM narrative_state").fetchone()[0]

        initial = epoch()
        db.execute(
            "INSERT INTO messages(chat_id,session_id,role,content,created_at) "
            "VALUES('chat','story','assistant','New scene',2)"
        )
        assert epoch() == initial
        db.execute("UPDATE messages SET telegram_message_ids='[1]' WHERE id=1")
        assert epoch() == initial
        db.execute("UPDATE messages SET content='Changed old scene' WHERE id=1")
        assert epoch() == initial + 1
        db.execute("DELETE FROM messages WHERE id=2")
        assert epoch() == initial + 2
