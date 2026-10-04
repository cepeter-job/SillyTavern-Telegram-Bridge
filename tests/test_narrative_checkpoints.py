"""Finale checkpoint, source prefix and complete local state share one atomic boundary."""

import json
import sqlite3

import pytest
from test_ending_state import goal, ready, ready_case
from test_memory_completion_safety import session_db as session_db
from test_narrative_reconciliation import add_story

from bridge import narrative_checkpoints as checkpoints
from bridge.ending_service import load_ending_state
from bridge.model_selection import set_director_reasoning, set_task_model
from bridge.narrative_repository import load_narrative_clock
from bridge.narrative_settings import load_session_narrative_settings
from bridge.sqlite_store import write_transaction


def prepared(case):
    db = ready_case(case)
    goal(db, "Mara resolves the rebellion without taking the throne.")
    ready(db)
    return db


def enter(db, operation="finale-op-1", **versions):
    ending = load_ending_state(db, "chat", "s1")
    params = {
        "expected_story_revision": load_narrative_clock(db, "chat", "s1")["state_revision"],
        "expected_lifecycle_revision": ending.lifecycle_revision,
        "operation_id": operation,
    }
    params.update(versions)
    return checkpoints.create_pre_finale_checkpoint_and_enter(db, "chat", "s1", **params)


def test_finale_entry_saves_immutable_versioned_snapshot_and_explicit_identity(session_db):
    db = prepared(session_db)
    before = db.execute("SELECT * FROM messages").fetchall()
    clock = load_narrative_clock(db, "chat", "s1")
    cp = enter(db)
    ending = load_ending_state(db, "chat", "s1")
    assert ending.lifecycle == "finale" and ending.checkpoint_id == cp.checkpoint_id
    assert ending.finale_operation_id == "finale-op-1"
    assert cp.source_revision == clock["state_revision"] and cp.through_rowid == clock["latest_rowid"]
    assert cp.payload["format_version"] == 1
    assert cp.payload["origin"] == {"chat_id": "chat", "session_id": "s1"}
    assert cp.payload["ending"]["lifecycle"] == "finale_ready"
    assert cp.payload["ending"]["required_arcs"] == ["rebellion_arc"]
    assert cp.payload["settings"] == load_session_narrative_settings(db, "chat", "s1").to_dict()
    assert cp.payload["arcs"][0]["arc_id"] == "rebellion_arc"
    assert cp.payload["transcript"]["count"] == len(before)
    assert len(cp.payload["transcript"]["sha256"]) == 64
    assert checkpoints.validate_pre_finale_checkpoint(db, "chat", "s1", cp.checkpoint_id) == cp
    assert db.execute("SELECT * FROM messages").fetchall() == before


def test_same_finale_operation_reuses_one_checkpoint_but_other_operation_cannot_enter_twice(session_db):
    db = prepared(session_db)
    cp = enter(db)
    changes = db.total_changes
    assert enter(db) == cp and db.total_changes == changes
    with pytest.raises(ValueError, match=r"already|operation"):
        enter(db, "different-op")
    assert db.execute("SELECT COUNT(*) FROM narrative_checkpoints WHERE kind='pre_finale'").fetchone()[0] == 1


def test_snapshot_and_finale_transition_roll_back_as_one_operation(session_db, monkeypatch):
    db = prepared(session_db)
    before = load_ending_state(db, "chat", "s1")

    def fail(*args, **kwargs):
        raise RuntimeError("Injected final transition failure")

    monkeypatch.setattr(checkpoints, "publish_ending_state", fail)
    with pytest.raises(RuntimeError, match="Injected"):
        enter(db)
    assert load_ending_state(db, "chat", "s1") == before
    assert db.execute("SELECT COUNT(*) FROM narrative_checkpoints WHERE kind='pre_finale'").fetchone()[0] == 0


@pytest.mark.parametrize("field", ["expected_story_revision", "expected_lifecycle_revision"])
def test_stale_entry_cannot_create_an_orphan_checkpoint(session_db, field):
    db = prepared(session_db)
    with pytest.raises(ValueError, match=r"changed|revision"):
        enter(db, **{field: 0})
    assert load_ending_state(db, "chat", "s1").lifecycle == "finale_ready"
    assert db.execute("SELECT COUNT(*) FROM narrative_checkpoints WHERE kind='pre_finale'").fetchone()[0] == 0


def test_unconfirmed_story_continuation_requires_fresh_readiness(session_db):
    db = prepared(session_db)
    add_story(db, "Mara postpones the confrontation.")
    with pytest.raises(ValueError):
        enter(db)
    assert checkpoints.load_pre_finale_checkpoint(db, "chat", "s1") is None
    assert load_ending_state(db, "chat", "s1").lifecycle == "open"


def test_checkpoint_freezes_configuration_and_memory_at_its_own_boundary(session_db):
    db = prepared(session_db)
    through = load_narrative_clock(db, "chat", "s1")["latest_rowid"]
    set_task_model(db, "chat", "s1", "utility::small", "utility")
    set_task_model(db, "chat", "s1", "director::planner", "director")
    set_director_reasoning(db, "chat", "s1", 8192)
    with write_transaction(db):
        db.execute("INSERT INTO session_summaries VALUES('chat','s1','Only pre-finale facts',?,1)", (through,))
        memory = db.execute(
            "INSERT INTO episodic_memories(chat_id,session_id,kind,importance,summary,"
            "source_start_rowid,source_end_rowid,created_at) "
            "VALUES('chat','s1','event',1,'Mara chose freedom',?,?,1)",
            (through, through),
        ).lastrowid
        db.execute("INSERT INTO episodic_memory_visibility VALUES(?,'chat','s1','private','[\"Mara\"]')", (memory,))
        npc = db.execute(
            "INSERT INTO npc_entities(chat_id,session_id,canonical_name,display_name,aliases_json,"
            "first_seen_rowid,last_seen_rowid,created_at,updated_at) "
            "VALUES('chat','s1','mara','Mara','[]',?,?,1,1)",
            (through, through),
        ).lastrowid
        db.execute(
            "INSERT INTO npc_fields VALUES(?,'role','\"rebel\"','mutable','private','[\"Mara\"]',?,1)", (npc, through)
        )
        db.execute("INSERT INTO scene_states VALUES('chat','s1','{\"location\":\"Gate\"}',?,1)", (through,))
        db.execute("INSERT INTO meta(key,value) VALUES('api_key:chat:s1','MUST_NOT_COPY_SECRET')")
        db.execute("INSERT INTO meta(key,value) VALUES('pending:chat:s1','MUST_NOT_COPY_PENDING')")
    cp = enter(db)
    body = cp.payload
    assert body["memory"]["summary"]["summary"] == "Only pre-finale facts"
    assert body["memory"]["episodic"][0]["visibility"] == "private"
    assert body["memory"]["npcs"][0]["fields"][0]["value_json"] == '"rebel"'
    assert body["physical_scene"]["state_json"] == '{"location":"Gate"}'
    assert body["config"]["task_models"]["director"] == "director::planner"
    assert body["config"]["director_reasoning"] == 8192
    assert "MUST_NOT_COPY" not in json.dumps(body)
    later = add_story(db, "FUTURE_ENDING: Mara rules the whole country.")
    with write_transaction(db):
        db.execute("UPDATE session_summaries SET summary='FUTURE_ENDING_SUMMARY',covered_until_rowid=?", (later,))
        db.execute("UPDATE npc_fields SET value_json='\"FUTURE_QUEEN\"',updated_rowid=?", (later,))
    loaded = checkpoints.validate_pre_finale_checkpoint(db, "chat", "s1", cp.checkpoint_id)
    assert loaded.payload == body
    assert "FUTURE_" not in json.dumps(loaded.payload)


@pytest.mark.parametrize("operation", ["update", "delete", "insert_past"])
def test_pre_finale_transcript_prefix_is_immutable_during_finale(session_db, operation):
    db = prepared(session_db)
    cp = enter(db)
    with pytest.raises(sqlite3.IntegrityError, match=r"pre-finale|checkpoint"):
        with write_transaction(db):
            if operation == "update":
                db.execute("UPDATE messages SET content='Changed past' WHERE id=?", (cp.through_rowid,))
            elif operation == "delete":
                db.execute("DELETE FROM messages WHERE id=?", (cp.through_rowid,))
            else:
                db.execute(
                    "INSERT OR REPLACE INTO messages(id,chat_id,session_id,role,content,created_at) "
                    "VALUES(?,'chat','s1','assistant','Changed past',1)",
                    (cp.through_rowid,),
                )
    assert checkpoints.validate_pre_finale_checkpoint(db, "chat", "s1", cp.checkpoint_id)
    later = add_story(db, "Finale continues after the safe boundary.")
    with write_transaction(db):
        db.execute("UPDATE messages SET content='A revised later finale turn' WHERE id=?", (later,))
        db.execute("UPDATE messages SET telegram_message_ids='[1,2]' WHERE id=?", (cp.through_rowid,))
    assert checkpoints.validate_pre_finale_checkpoint(db, "chat", "s1", cp.checkpoint_id)


def test_checkpoint_payload_cannot_be_changed_by_a_later_writer(session_db):
    db = prepared(session_db)
    cp = enter(db)
    with pytest.raises(sqlite3.IntegrityError, match="immutable"):
        with write_transaction(db):
            db.execute("UPDATE narrative_checkpoints SET payload_json='{}' WHERE checkpoint_id=?", (cp.checkpoint_id,))
    assert checkpoints.load_pre_finale_checkpoint(db, "chat", "s1") == cp


def test_oversized_snapshot_fails_without_entering_finale(session_db):
    db = prepared(session_db)
    through = load_narrative_clock(db, "chat", "s1")["latest_rowid"]
    with write_transaction(db):
        db.execute("INSERT INTO session_summaries VALUES('chat','s1',?,?,1)", ("x" * 1048577, through))
    with pytest.raises(ValueError, match=r"large|bound|snapshot"):
        enter(db)
    assert load_ending_state(db, "chat", "s1").lifecycle == "finale_ready"
    assert checkpoints.load_pre_finale_checkpoint(db, "chat", "s1") is None


def test_missing_or_other_session_checkpoint_is_not_guessed(session_db):
    db = prepared(session_db)
    cp = enter(db)
    assert checkpoints.load_pre_finale_checkpoint(db, "chat", "missing") is None
    with pytest.raises(ValueError):
        checkpoints.validate_pre_finale_checkpoint(db, "other", "s1", cp.checkpoint_id)


def test_valid_checkpoint_survives_rolling_checkpoint_retention(session_db):
    from bridge.narrative_checkpoint_repository import insert_narrative_checkpoint

    db = prepared(session_db)
    cp = enter(db)
    with write_transaction(db):
        for revision in range(100, 140):
            insert_narrative_checkpoint(
                db,
                "chat",
                "s1",
                f"rolling_{revision}",
                kind="reconciliation",
                source_revision=revision,
                through_rowid=cp.through_rowid,
                payload_json="{}",
                created_at=1,
            )
    assert checkpoints.load_pre_finale_checkpoint(db, "chat", "s1") == cp


def test_checkpoint_and_finale_remain_caller_owned(session_db):
    db = prepared(session_db)
    db.execute("BEGIN")
    enter(db)
    assert db.in_transaction
    db.rollback()
    assert load_ending_state(db, "chat", "s1").lifecycle == "finale_ready"
    assert checkpoints.load_pre_finale_checkpoint(db, "chat", "s1") is None


def test_linked_checkpoint_cannot_be_deleted_independently_of_its_story(session_db):
    db = prepared(session_db)
    cp = enter(db)
    with pytest.raises(sqlite3.IntegrityError, match="immutable"):
        with write_transaction(db):
            db.execute("DELETE FROM narrative_checkpoints WHERE checkpoint_id=?", (cp.checkpoint_id,))
    assert checkpoints.load_pre_finale_checkpoint(db, "chat", "s1") == cp


def test_explicit_session_deletion_cascades_immutable_ending_state(session_db):
    from bridge.session_repository import delete_session_rows

    db = prepared(session_db)
    enter(db)
    with write_transaction(db):
        delete_session_rows(db, "chat", "s1", [])
    assert db.execute("SELECT COUNT(*) FROM sessions WHERE chat_id='chat' AND session_id='s1'").fetchone()[0] == 0
    assert db.execute("SELECT COUNT(*) FROM messages WHERE chat_id='chat' AND session_id='s1'").fetchone()[0] == 0
    assert checkpoints.load_pre_finale_checkpoint(db, "chat", "s1") is None
