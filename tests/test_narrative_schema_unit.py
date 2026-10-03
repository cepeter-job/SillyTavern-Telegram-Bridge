"""Exercise the migration transaction against a minimal real SQLite baseline."""

import json
import sqlite3
from contextlib import closing

import pytest


def database():
    db = sqlite3.connect(":memory:")
    db.execute("PRAGMA foreign_keys=ON")
    db.executescript(
        "CREATE TABLE sessions(chat_id TEXT,session_id TEXT,PRIMARY KEY(chat_id,session_id));"
        "INSERT INTO sessions VALUES('chat','story');"
        "CREATE TABLE director_goals(chat_id TEXT,session_id TEXT,goal TEXT,updated_at REAL,"
        "PRIMARY KEY(chat_id,session_id));"
        "INSERT INTO director_goals VALUES('chat','story','Keep the gate closed.',1);"
        "CREATE TRIGGER director_goals_session_delete AFTER DELETE ON sessions BEGIN "
        "DELETE FROM director_goals WHERE chat_id=OLD.chat_id AND session_id=OLD.session_id; END;"
    )
    return db


def test_migration_copies_goal_and_session_settings():
    from bridge.narrative_schema import migrate_narrative_engine_foundation

    with closing(database()) as db:
        db.execute("BEGIN IMMEDIATE")
        migrate_narrative_engine_foundation(db)
        assert db.in_transaction
        db.commit()
        assert db.execute("SELECT goal FROM director_state").fetchone() == ("Keep the gate closed.",)
        settings = json.loads(db.execute("SELECT settings_json FROM narrative_settings").fetchone()[0])
        assert settings["preset"] == "player_centric"
        assert settings["ending_mode"] == "open_ended"
        db.execute("DELETE FROM sessions")
        assert db.execute("SELECT COUNT(*) FROM director_state").fetchone()[0] == 0
        assert db.execute("SELECT COUNT(*) FROM narrative_settings").fetchone()[0] == 0


def test_failed_migration_rolls_back_legacy_goal(monkeypatch):
    from bridge import narrative_schema

    with closing(database()) as db:

        def reject(_db):
            raise ValueError("Director goal copy verification failed")

        monkeypatch.setattr(narrative_schema, "verify_director_goal_copy", reject)
        db.execute("BEGIN IMMEDIATE")
        with pytest.raises(ValueError, match="goal copy"):
            narrative_schema.migrate_narrative_engine_foundation(db)
        db.rollback()
        assert db.execute("SELECT goal FROM director_goals").fetchone() == ("Keep the gate closed.",)
        assert db.execute("SELECT name FROM sqlite_master WHERE name='narrative_settings'").fetchone() is None


def test_migration_requires_callers_transaction():
    from bridge.narrative_schema import migrate_narrative_engine_foundation

    with closing(database()) as db:
        with pytest.raises(RuntimeError, match="transaction"):
            migrate_narrative_engine_foundation(db)


def test_history_retention_is_per_session_and_transactional():
    from bridge.narrative_schema import migrate_narrative_engine_foundation

    with closing(database()) as db:
        db.execute("BEGIN IMMEDIATE")
        migrate_narrative_engine_foundation(db)
        db.commit()
        db.execute("BEGIN IMMEDIATE")
        db.executemany(
            "INSERT INTO director_decisions(chat_id,session_id,source,result,expected_revision,created_at) "
            "VALUES('chat','story','ai','accepted',?,?)",
            [(n, float(n)) for n in range(201)],
        )
        assert db.execute("SELECT COUNT(*) FROM director_decisions").fetchone()[0] == 200
        assert db.execute("SELECT MIN(expected_revision) FROM director_decisions").fetchone()[0] == 1
        db.executemany(
            "INSERT INTO ending_goal_history(chat_id,session_id,goal_revision,source,story_revision,"
            "previous_goal,new_goal,reason,created_at) VALUES('chat','story',?,'director',0,'old','new','why',?)",
            [(n, float(n)) for n in range(101)],
        )
        assert db.execute("SELECT COUNT(*) FROM ending_goal_history").fetchone()[0] == 100
        assert db.execute("SELECT MIN(goal_revision) FROM ending_goal_history").fetchone()[0] == 1
        db.rollback()
        assert db.execute("SELECT COUNT(*) FROM director_decisions").fetchone()[0] == 0
        assert db.execute("SELECT COUNT(*) FROM ending_goal_history").fetchone()[0] == 0


def test_cascades_and_constraints_reject_cross_session_or_invalid_lifecycle():
    from bridge.narrative_schema import migrate_narrative_engine_foundation

    with closing(database()) as db:
        db.execute("BEGIN IMMEDIATE")
        migrate_narrative_engine_foundation(db)
        db.commit()
        with pytest.raises(sqlite3.IntegrityError):
            db.execute("INSERT INTO ending_state(chat_id,session_id,lifecycle) VALUES('chat','missing','open')")
        db.rollback()
        with pytest.raises(sqlite3.IntegrityError):
            db.execute("INSERT INTO ending_state(chat_id,session_id,lifecycle) VALUES('chat','story','invalid')")
        db.rollback()
