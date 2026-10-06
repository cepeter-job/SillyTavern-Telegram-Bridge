"""Checkpoint branching preserves completed canon and survives interrupted optional memory work."""

from unittest.mock import patch

import pytest
from test_epilogue_service import complete, producer_for
from test_finale_resolution import resolved_packet
from test_memory_completion_safety import session_db as session_db
from test_narrative_checkpoints import enter, prepared
from test_narrative_reconciliation import add_story, run_reconciliation

from bridge.ending_service import load_ending_state
from bridge.meta_repository import load_meta_value, store_meta_value
from bridge.narrative_checkpoints import load_pre_finale_checkpoint
from bridge.narrative_context import narrative_clock_is_current
from bridge.narrative_repository import load_narrative_clock
from bridge.session_repository import load_session_row
from bridge.sqlite_store import write_transaction


def close_story(case, seed=None):
    _, db, _ = case
    prepared(case)
    if seed:
        seed(db, load_narrative_clock(db, "chat", "s1")["latest_rowid"])
    cp = enter(db)
    quote = "The governor surrendered. The rebellion ended peacefully."
    rowid = add_story(db, quote)
    run_reconciliation(case, lambda *a, **k: resolved_packet(rowid, quote))
    assert complete(case, producer_for(db, [])).completed
    with write_transaction(db):
        store_meta_value(db, "active_session:chat", "s1")
    return cp


def branch(case, cp, op="request-one", seed=None):
    from bridge.alternate_ending import create_alternate_ending

    _, db, _ = case
    return create_alternate_ending(db, "chat", "s1", cp.checkpoint_id, op, seed_memory=seed)


def test_new_session_copies_only_checkpoint_prefix_and_keeps_original_closed(session_db):
    cp = close_story(session_db)
    _, db, _ = session_db
    original = db.execute("SELECT * FROM messages WHERE session_id='s1'").fetchall()
    ending = load_ending_state(db, "chat", "s1")
    result = branch(session_db, cp)
    target = result.session["session_id"]
    assert target != "s1" and result.applied and result.memory_status == "disabled"
    assert result.session["title"].endswith(" — Alternate Ending")
    rows = db.execute(
        "SELECT id,role,content,telegram_message_id,telegram_message_ids FROM messages WHERE session_id=? ORDER BY id",
        (target,),
    ).fetchall()
    prefix = db.execute(
        "SELECT role,content FROM messages WHERE session_id='s1' AND id<=? ORDER BY id", (cp.through_rowid,)
    ).fetchall()
    assert [(r[1], r[2]) for r in rows] == prefix
    assert all(r[3] is None and r[4] == "[]" for r in rows)
    assert all(r[0] > cp.through_rowid for r in rows)
    assert load_ending_state(db, "chat", target).lifecycle == "open"
    assert load_ending_state(db, "chat", target).checkpoint_id is None
    assert narrative_clock_is_current(load_narrative_clock(db, "chat", target))
    assert db.execute("SELECT * FROM messages WHERE session_id='s1'").fetchall() == original
    assert load_ending_state(db, "chat", "s1") == ending
    assert load_pre_finale_checkpoint(db, "chat", "s1") == cp


def test_duplicate_operation_is_read_only_and_checkpoint_is_reusable(session_db):
    cp = close_story(session_db)
    _, db, _ = session_db
    first = branch(session_db, cp)
    before = db.total_changes
    repeated = branch(session_db, cp)
    assert repeated.session == first.session and db.total_changes == before
    second = branch(session_db, cp, "request-two")
    assert second.session["session_id"] != first.session["session_id"]
    assert load_pre_finale_checkpoint(db, "chat", "s1") == cp
    assert db.execute("SELECT COUNT(*) FROM narrative_branches").fetchone()[0] == 2


def test_operation_identity_cannot_be_rebound_to_another_checkpoint(session_db):
    from bridge.alternate_ending import create_alternate_ending

    cp = close_story(session_db)
    _, db, _ = session_db
    branch(session_db, cp)
    with pytest.raises(ValueError, match=r"operation|another|match"):
        create_alternate_ending(db, "chat", "s1", "another-checkpoint", "request-one")
    with pytest.raises(ValueError):
        create_alternate_ending(db, "other-chat", "s1", cp.checkpoint_id, "request-one")
    assert db.execute("SELECT COUNT(*) FROM narrative_branches").fetchone()[0] == 1


def test_nonclosed_or_invalid_checkpoint_cannot_create_any_session(session_db):
    from bridge.alternate_ending import create_alternate_ending

    _, db, _ = session_db
    prepared(session_db)
    before = db.execute("SELECT COUNT(*) FROM sessions").fetchone()[0]
    with pytest.raises(ValueError):
        create_alternate_ending(db, "chat", "s1", "missing", "request-one")
    assert db.execute("SELECT COUNT(*) FROM sessions").fetchone()[0] == before


def test_failure_mid_copy_rolls_back_the_entire_new_session_then_retry_converges(session_db, monkeypatch):
    from bridge import alternate_ending

    cp = close_story(session_db)
    _, db, _ = session_db
    original = alternate_ending.restore_checkpoint_state

    def fail(*args, **kwargs):
        original(*args, **kwargs)
        raise RuntimeError("Injected local transaction failure")

    monkeypatch.setattr(alternate_ending, "restore_checkpoint_state", fail)
    with pytest.raises(RuntimeError):
        branch(session_db, cp)
    assert db.execute("SELECT COUNT(*) FROM sessions").fetchone()[0] == 1
    assert db.execute("SELECT COUNT(*) FROM narrative_branches").fetchone()[0] == 0
    monkeypatch.setattr(alternate_ending, "restore_checkpoint_state", original)
    assert branch(session_db, cp).applied
    assert db.execute("SELECT COUNT(*) FROM sessions").fetchone()[0] == 2


def test_optional_memory_failure_does_not_destroy_the_branch_or_retry_forever(session_db):
    cp = close_story(session_db)
    _, db, _ = session_db
    calls = []

    def fail(db, chat, session):
        assert not db.in_transaction
        calls.append(session["session_id"])
        raise RuntimeError("private backend failure")

    result = branch(session_db, cp, seed=fail)
    assert result.applied and result.memory_status == "degraded"
    assert result.session["session_id"] == calls[0]
    assert branch(session_db, cp, seed=fail).applied and len(calls) == 1
    assert load_ending_state(db, "chat", "s1").lifecycle == "closed"


def test_crash_after_local_commit_resumes_one_target_even_after_source_deleted(session_db):
    from bridge import alternate_ending

    cp = close_story(session_db)
    _, db, _ = session_db
    with patch.object(alternate_ending, "finish_alternate_ending", side_effect=KeyboardInterrupt):
        with pytest.raises(KeyboardInterrupt):
            branch(session_db, cp)
    assert db.execute("SELECT COUNT(*) FROM narrative_branches").fetchone()[0] == 1
    target = db.execute("SELECT target_session_id FROM narrative_branches").fetchone()[0]
    with write_transaction(db):
        db.execute("DELETE FROM sessions WHERE chat_id='chat' AND session_id='s1'")
        db.execute("DELETE FROM messages WHERE chat_id='chat' AND session_id='s1'")
    result = branch(session_db, cp)
    assert result.applied and result.session["session_id"] == target
    assert load_session_row(db, "chat", target)


def test_expired_memory_lease_reuses_target_and_same_seed_identity(session_db):
    cp = close_story(session_db)
    _, db, _ = session_db
    calls = []

    def crash(db, chat, session):
        calls.append((chat, session["session_id"]))
        raise KeyboardInterrupt()

    with pytest.raises(KeyboardInterrupt):
        branch(session_db, cp, seed=crash)
    duplicate = branch(session_db, cp, seed=lambda *a: pytest.fail("Duplicate live seed"))
    assert not duplicate.applied
    with write_transaction(db):
        db.execute("UPDATE operations SET updated_at=0 WHERE kind='alternate_ending'")
    resumed = branch(
        session_db, cp, seed=lambda db, chat, session: calls.append((chat, session["session_id"])) or "ready"
    )
    assert resumed.applied and resumed.memory_status == "ready" and calls[0] == calls[1]
    assert db.execute("SELECT COUNT(*) FROM narrative_branches").fetchone()[0] == 1


def test_branch_completion_does_not_steal_a_different_active_story(session_db):
    cp = close_story(session_db)
    _, db, _ = session_db

    def seed(db, chat, session):
        with write_transaction(db):
            store_meta_value(db, "active_session:chat", "user-switched-away")
        return "ready"

    result = branch(session_db, cp, seed=seed)
    assert result.applied
    assert load_meta_value(db, "active_session:chat") == "user-switched-away"


def test_checkpoint_configuration_and_memories_do_not_leak_finale_future(session_db):
    from bridge.conversation_lifecycle import conversation_state
    from bridge.memory_artifact_store import store_artifact_visibility
    from bridge.memory_contracts import MemoryFact
    from bridge.memory_fact_store import accept_source_facts
    from bridge.memory_store import next_source_segment
    from bridge.model_selection import task_model_for_session
    from bridge.narrative_settings import load_session_narrative_settings

    config, db, _ = session_db

    def seed(db, through):
        with write_transaction(db):
            db.execute("INSERT INTO session_summaries VALUES('chat','s1','Before finale summary',?,1)", (through,))
            db.execute("INSERT INTO scene_states VALUES('chat','s1','{\"location\":\"Gate\"}',?,1)", (through,))
            source = next_source_segment(db, "chat", "s1", "episodes", through_id=through)
            assert source is not None and source.end_id <= through
            assert accept_source_facts(db, source, [MemoryFact("event", 1, "Before the coup", "shared", ())])
            store_artifact_visibility(
                db,
                "chat",
                "s1",
                "summary",
                [{"text": "Before finale summary", "visibility": "shared", "known_by": []}],
            )
            store_artifact_visibility(
                db,
                "chat",
                "s1",
                "scene",
                [{"text": "Location: Gate", "visibility": "shared", "known_by": []}],
            )
            npc = db.execute(
                "INSERT INTO npc_entities(chat_id,session_id,canonical_name,display_name,aliases_json,first_seen_rowid,"
                "last_seen_rowid,created_at,updated_at) VALUES('chat','s1','mara','Mara','[]',?,?,1,1)",
                (through, through),
            ).lastrowid
            db.execute(
                "INSERT INTO npc_fields VALUES(?,'role','\"rebel\"','mutable','shared','[]',?,1)", (npc, through)
            )
            store_meta_value(db, "task_model:director:chat:s1", "fixture::planner")
            store_meta_value(db, "conversation_mode:chat:s1", "lightnovel")
            store_meta_value(db, "lightnovel_strategy:chat:s1", "b")
            store_meta_value(db, "conversation_started:chat:s1", "1")

    cp = close_story(session_db, seed)
    with write_transaction(db):
        db.execute("UPDATE session_summaries SET summary='Future ending' WHERE session_id='s1'")
        db.execute("UPDATE npc_fields SET value_json='\"queen after ending\"'")
        store_meta_value(db, "task_model:director:chat:s1", "fixture::future")
        store_meta_value(db, "operation_payload:unrelated", "PRIVATE")
    result = branch(session_db, cp)
    sid = result.session["session_id"]
    summary = db.execute(
        "SELECT summary,covered_until_rowid FROM session_summaries WHERE session_id=?", (sid,)
    ).fetchone()
    assert summary[0] == "Before finale summary" and summary[1] > cp.through_rowid
    assert task_model_for_session(db, "chat", result.session, "director", app_settings=config) == "fixture::planner"
    assert conversation_state(db, "chat", sid).started and conversation_state(db, "chat", sid).strategy == "b"
    assert load_session_narrative_settings(db, "chat", sid).ending_mode == "closed_story"
    entity = db.execute("SELECT npc_id,first_seen_rowid FROM npc_entities WHERE session_id=?", (sid,)).fetchone()
    assert entity[1] > cp.through_rowid
    assert db.execute("SELECT value_json FROM npc_fields WHERE npc_id=?", (entity[0],)).fetchone()[0] == '"rebel"'
    assert (
        db.execute("SELECT source_rowid FROM npc_field_history WHERE npc_id=?", (entity[0],)).fetchone()[0]
        > cp.through_rowid
    )
    for query in (
        "SELECT COUNT(*) FROM jobs WHERE session_id=?",
        "SELECT COUNT(*) FROM response_variants WHERE session_id=?",
        "SELECT COUNT(*) FROM failed_turns WHERE session_id=?",
        "SELECT COUNT(*) FROM panel_sessions WHERE session_id=?",
        "SELECT COUNT(*) FROM hindsight_documents WHERE session_id=?",
    ):
        assert db.execute(query, (sid,)).fetchone()[0] == 0
    assert not db.execute("SELECT 1 FROM meta WHERE key LIKE ? AND value='PRIVATE'", ("%" + sid,)).fetchone()


def test_only_evidence_json_contains_remappable_row_ids_not_npc_story_values():
    from bridge.checkpoint_remap import checkpoint_row_references, remap_checkpoint

    value = {
        "value_json": '{"source_rowid":404,"source_revision":999}',
        "evidence_json": '[{"source_rowid":7,"quote":"Mara stayed"}]',
        "updated_rowid": 7,
    }
    assert checkpoint_row_references(value) == {7}
    copied = remap_checkpoint(value, {0: 0, 7: 70})
    assert copied["value_json"] == value["value_json"]
    assert "70" in copied["evidence_json"] and copied["updated_rowid"] == 70


def test_streaming_prefix_copy_keeps_only_requested_anchors(session_db):
    from bridge.session_repository import insert_session_row
    from bridge.transcript_repository import clone_transcript_prefix

    _, db, session = session_db
    with write_transaction(db):
        first = add_story(db, "First preserved beat")
        for number in range(1200):
            db.execute(
                "INSERT INTO messages(chat_id,session_id,role,content,created_at) VALUES('chat','s1','assistant',?,1)",
                (str(number),),
            )
        last = db.execute("SELECT MAX(id) FROM messages WHERE session_id='s1'").fetchone()[0]
        insert_session_row(db, session | {"session_id": "copy"}, 2)
        ids = clone_transcript_prefix(db, "chat", "s1", "copy", last, {first, last})
    assert set(ids) == {0, first, last}
    assert db.execute("SELECT COUNT(*) FROM messages WHERE session_id='copy'").fetchone()[0] == 1201


def test_branch_memory_lease_cannot_be_stolen_by_a_late_worker(session_db):
    from bridge.operation_repository import claim_scoped_operation_stage

    cp = close_story(session_db)
    _, db, _ = session_db

    def replaced(db, chat, session):
        op = db.execute(
            "SELECT operation_id FROM narrative_branches WHERE target_session_id=?", (session["session_id"],)
        ).fetchone()[0]
        with write_transaction(db):
            db.execute("UPDATE operations SET updated_at=0 WHERE operation_id=?", (op,))
            assert claim_scoped_operation_stage(
                db, op, "new-worker", 9999, stage="memory_seeding", prior="local_committed"
            )
        return "ready"

    result = branch(session_db, cp, seed=replaced)
    assert not result.applied and result.memory_status == "pending"
    assert load_meta_value(db, "active_session:chat") == "s1"


def test_finished_branch_discards_transient_operation_payload_but_keeps_idempotent_receipt(session_db):
    from bridge.alternate_ending import recover_alternate_ending

    cp = close_story(session_db)
    _, db, _ = session_db
    result = branch(session_db, cp)
    op = db.execute("SELECT operation_id FROM narrative_branches").fetchone()[0]
    assert db.execute("SELECT value FROM meta WHERE key=?", ("operation_payload:" + op,)).fetchone() is None
    assert recover_alternate_ending(db, op).session == result.session


def test_alternate_session_accepts_a_normal_story_turn_without_touching_the_original(session_db, monkeypatch):
    from test_narrative_generation import invoke_story_flow

    cp = close_story(session_db)
    config, db, _ = session_db
    target = branch(session_db, cp).session
    before = db.execute("SELECT id,content FROM messages WHERE session_id='s1' ORDER BY id").fetchall()
    calls = invoke_story_flow("text", (db, target, config, 0), monkeypatch)
    assert len(calls) == 1 and "## Narrative Policy" in calls[0][0]["content"]
    assert (
        db.execute(
            "SELECT content FROM messages WHERE session_id=? ORDER BY id DESC LIMIT 1", (target["session_id"],)
        ).fetchone()[0]
        == "*Mara waits.*"
    )
    assert db.execute("SELECT id,content FROM messages WHERE session_id='s1' ORDER BY id").fetchall() == before
    assert load_ending_state(db, "chat", "s1").lifecycle == "closed"
