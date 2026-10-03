"""Behavioral contracts for narrative persistence, using actual SQLite."""

import json
import sqlite3
from contextlib import closing

import pytest

from bridge.narrative_schema import migrate_narrative_engine_foundation
from bridge.narrative_values import NarrativeScene, NarrativeThread


@pytest.fixture
def db():
    with closing(sqlite3.connect(":memory:")) as connection:
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("CREATE TABLE sessions(chat_id TEXT,session_id TEXT,PRIMARY KEY(chat_id,session_id))")
        connection.execute("INSERT INTO sessions VALUES('chat','story')")
        migrate_narrative_engine_foundation(connection)
        connection.commit()
        yield connection


def test_preference_reads_are_pure_and_writes_join_caller_transaction(db):
    from bridge import narrative_repository as repo

    assert repo.load_narrative_default_row(db, "owner") is None
    assert repo.load_narrative_settings_row(db, "chat", "missing") is None
    assert not db.in_transaction
    with pytest.raises(RuntimeError, match="caller-owned transaction"):
        repo.store_narrative_default_row(db, "owner", "{}", 1.0)
    db.execute("BEGIN")
    repo.store_narrative_default_row(db, "owner", '{"preset":"ensemble"}', 1.0)
    repo.store_narrative_settings_row(db, "chat", "story", '{"preset":"observer"}', 1.0)
    assert db.in_transaction
    assert json.loads(repo.load_narrative_settings_row(db, "chat", "story"))["preset"] == "observer"
    db.rollback()
    assert repo.load_narrative_default_row(db, "owner") is None
    assert json.loads(repo.load_narrative_settings_row(db, "chat", "story"))["preset"] == "player_centric"


def test_state_writes_require_cas_and_reject_older_coverage(db):
    from bridge import narrative_repository as repo

    assert repo.load_narrative_state_row(db, "chat", "story") is None
    with pytest.raises(RuntimeError, match="caller-owned transaction"):
        repo.upsert_narrative_state_if_fresh(db, "chat", "story", "{}", 0, 10, 1.0)
    db.execute("BEGIN")
    assert repo.upsert_narrative_state_if_fresh(db, "chat", "story", "{}", 0, 10, 1.0)
    first = repo.load_narrative_state_row(db, "chat", "story")
    assert first["state_revision"] == 1
    assert first["updated_through_rowid"] == 10
    assert not repo.upsert_narrative_state_if_fresh(db, "chat", "story", "{}", 0, 11, 2.0)
    assert not repo.upsert_narrative_state_if_fresh(db, "chat", "story", "{}", 1, 9, 2.0)
    assert repo.upsert_narrative_state_if_fresh(db, "chat", "story", '{"story_phase":"development"}', 1, 11, 2.0)
    final = repo.load_narrative_state_row(db, "chat", "story")
    assert final["state_revision"] == 2
    assert final["story_phase"] == "development"
    assert not repo.upsert_narrative_state_if_fresh(db, "chat", "absent", "{}", 0, 11, 3.0)
    assert repo.load_narrative_state_row(db, "chat", "absent") is None


def test_scene_thread_identity_is_scoped_and_stale_updates_do_not_win(db):
    from bridge import narrative_repository as repo

    db.execute("BEGIN")
    thread = NarrativeThread("rebellion", "Rebellion", source_revision=10)
    scene = NarrativeScene("gate", "rebellion", viewpoint_character="Mara", start_rowid=10, source_revision=10)
    assert repo.upsert_narrative_thread(db, "chat", "story", thread)
    assert repo.upsert_narrative_scene(db, "chat", "story", scene)
    assert repo.load_narrative_scene(db, "chat", "story", "gate") == scene
    assert repo.load_narrative_scene(db, "other", "story", "gate") is None
    assert not repo.upsert_narrative_thread(db, "chat", "story", NarrativeThread("rebellion", "Old", source_revision=9))
    assert not repo.upsert_narrative_scene(db, "chat", "story", NarrativeScene("gate", "rebellion", source_revision=9))
    assert repo.load_narrative_thread(db, "chat", "story", "rebellion").title == "Rebellion"
    db.commit()
    assert not db.in_transaction
    assert len(repo.list_narrative_threads(db, "chat", "story")) == 1


def test_director_goal_changes_preserve_plans_and_caller_transaction(db):
    from bridge.director_goal_repository import delete_director_goal, load_director_goal, store_director_goal

    db.execute("INSERT INTO director_state(chat_id,session_id,active_direction) VALUES('chat','story','Stay at gate')")
    db.commit()
    with pytest.raises(RuntimeError, match="caller-owned transaction"):
        store_director_goal(db, "chat", "story", "Resolve rebellion", 1.0)
    db.execute("BEGIN")
    store_director_goal(db, "chat", "story", "Resolve rebellion", 1.0)
    assert load_director_goal(db, "chat", "story") == "Resolve rebellion"
    delete_director_goal(db, "chat", "story")
    assert load_director_goal(db, "chat", "story") == ""
    assert db.execute("SELECT active_direction,state_revision FROM director_state").fetchone() == ("Stay at gate", 2)
    db.rollback()
    assert db.execute("SELECT active_direction,state_revision FROM director_state").fetchone() == ("Stay at gate", 0)
