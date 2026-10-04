import sqlite3
import time

import pytest
from persisted_state_test_support import find_test_npc

from bridge.npc_repository import (
    find_npc_by_name_or_alias,
    get_npc_extraction_coverage,
    insert_npc_entity,
    list_npc_entities,
    set_npc_extraction_coverage,
)
from bridge.schema import initialize_database_schema
from bridge.sqlite_store import write_transaction


def _db():
    db = sqlite3.connect(":memory:")
    db.execute("PRAGMA foreign_keys=ON")
    initialize_database_schema(db)
    return db


def _insert_session(db, chat_id, session_id):
    now = time.time()
    db.execute(
        """
        INSERT INTO sessions(
            chat_id,session_id,title,character_file,model_id,persona_id,world_file,
            author_note,system_prompt,response_language,created_at,updated_at
        ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (chat_id, session_id, session_id, "char.png", "p::m", "", "", "", "", "auto", now, now),
    )
    db.commit()


def test_migration_6_creates_npc_tables():
    db = _db()
    try:
        migrations = db.execute("SELECT version,name FROM schema_migrations ORDER BY version").fetchall()
        assert migrations[5] == (6, "npc_bank_core")
        assert migrations[-11:] == [
            (8, "assistant_delivery_progress"),
            (9, "job_delivery_intents"),
            (10, "narrative_engine_foundation"),
            (11, "narrative_history_revisions"),
            (12, "director_runtime"),
            (13, "narrative_arc_evidence"),
            (14, "ending_readiness"),
            (15, "finale_checkpoint_guards"),
            (16, "ending_workflow"),
            (17, "closed_story_guards"),
            (18, "alternate_ending_lineage"),
        ]
        assert len(migrations) == 18
        names = {
            row[0]
            for row in db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'npc_%'").fetchall()
        }
        assert {
            "npc_entities",
            "npc_fields",
            "npc_field_history",
            "npc_extraction_state",
        } <= names
    finally:
        db.close()


def test_insert_and_list_npc_are_session_isolated():
    db = _db()
    try:
        _insert_session(db, "chat", "s1")
        _insert_session(db, "chat", "s2")
        with write_transaction(db):
            first = insert_npc_entity(db, "chat", "s1", "maya torres", "Maya Torres", ("Maya",), 12, 1.0)
            second = insert_npc_entity(db, "chat", "s2", "maya torres", "Maya Torres", (), 14, 2.0)
        assert first != second
        assert [x.npc_id for x in list_npc_entities(db, "chat", "s1")] == [first]
        assert [x.npc_id for x in list_npc_entities(db, "chat", "s2")] == [second]
        assert find_test_npc(db, "chat", "s1", "maya torres").npc_id == first
    finally:
        db.close()


def test_alias_collision_is_ambiguous_instead_of_guessing():
    db = _db()
    try:
        _insert_session(db, "chat", "s1")
        with write_transaction(db):
            insert_npc_entity(db, "chat", "s1", "maya torres", "Maya Torres", ("May",), 1, 1.0)
            insert_npc_entity(db, "chat", "s1", "maya chen", "Maya Chen", ("May",), 2, 2.0)
        assert find_npc_by_name_or_alias(db, "chat", "s1", "may") is None
        assert find_npc_by_name_or_alias(db, "chat", "s1", "maya torres").display_name == "Maya Torres"
    finally:
        db.close()


def test_repository_writes_require_caller_transaction():
    db = _db()
    try:
        _insert_session(db, "chat", "s1")
        with pytest.raises(RuntimeError, match="active caller-owned transaction"):
            insert_npc_entity(db, "chat", "s1", "maya", "Maya", (), 1, 1.0)
    finally:
        db.close()


def test_extraction_coverage_is_session_scoped_and_monotonic_only_by_caller_policy():
    db = _db()
    try:
        _insert_session(db, "chat", "s1")
        _insert_session(db, "chat", "s2")
        assert get_npc_extraction_coverage(db, "chat", "s1") == 0
        with write_transaction(db):
            set_npc_extraction_coverage(db, "chat", "s1", 42, 3.0)
        assert get_npc_extraction_coverage(db, "chat", "s1") == 42
        assert get_npc_extraction_coverage(db, "chat", "s2") == 0
    finally:
        db.close()


def test_deleting_session_cascades_all_npc_storage():
    db = _db()
    try:
        _insert_session(db, "chat", "s1")
        with write_transaction(db):
            npc_id = insert_npc_entity(db, "chat", "s1", "maya", "Maya", (), 1, 1.0)
            db.execute(
                """
                INSERT INTO npc_fields(
                    npc_id,field_key,value_json,field_mode,visibility,known_by_json,updated_rowid,updated_at
                ) VALUES(?,?,?,?,?,?,?,?)
                """,
                (npc_id, "role", '"archivist"', "mutable", "shared", "[]", 1, 1.0),
            )
            db.execute(
                """
                INSERT INTO npc_field_history(
                    npc_id,field_key,operation,before_json,after_json,before_mode,after_mode,
                    before_visibility,after_visibility,before_known_by_json,after_known_by_json,
                    source_rowid,created_at
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    npc_id,
                    "role",
                    "set",
                    None,
                    '"archivist"',
                    None,
                    "mutable",
                    None,
                    "shared",
                    None,
                    "[]",
                    1,
                    1.0,
                ),
            )
            set_npc_extraction_coverage(db, "chat", "s1", 1, 1.0)
        db.execute("DELETE FROM sessions WHERE chat_id=? AND session_id=?", ("chat", "s1"))
        db.commit()
        for query in (
            "SELECT COUNT(*) FROM npc_entities",
            "SELECT COUNT(*) FROM npc_fields",
            "SELECT COUNT(*) FROM npc_field_history",
            "SELECT COUNT(*) FROM npc_extraction_state",
        ):
            assert db.execute(query).fetchone()[0] == 0
    finally:
        db.close()
