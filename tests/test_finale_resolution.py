"""Finale resolution follows committed prose and arc evidence, not Director intention."""

import json

import pytest
from test_memory_completion_safety import session_db as session_db
from test_narrative_arcs import arc, packet
from test_narrative_checkpoints import enter, prepared
from test_narrative_reconciliation import add_story, run_reconciliation

from bridge.ending_service import load_ending_state
from bridge.narrative_arc_repository import load_arc_row
from bridge.narrative_repository import load_narrative_clock
from bridge.sqlite_store import write_transaction


def finale(case):
    db = prepared(case)
    cp = enter(db)
    return db, cp


def resolved_packet(rowid, quote, **overrides):
    data = json.loads(packet(arc(status="resolved", quote=quote, rowid=rowid), phase="resolution"))
    data["resolution"] = {"resolved": True, "evidence": [{"rowid": rowid, "quote": quote}]}
    data.update(overrides)
    return json.dumps(data)


def test_finale_commit_records_assistant_identity_without_changing_story_state(session_db):
    db, cp = finale(session_db)
    before = load_ending_state(db, "chat", "s1")
    db.execute("BEGIN")
    rowid = add_story(db, "Mara reaches the governor's chamber.")
    pending = load_ending_state(db, "chat", "s1")
    assert pending.lifecycle == "finale" and pending.finale_committed_rowid == rowid
    assert pending.lifecycle_revision == before.lifecycle_revision + 1
    assert pending.resolution_rowid is None and pending.checkpoint_id == cp.checkpoint_id
    assert db.in_transaction
    db.rollback()
    assert load_ending_state(db, "chat", "s1") == before


def test_finale_can_take_multiple_story_turns_without_premature_resolution(session_db):
    db, _ = finale(session_db)
    first = add_story(db, "The governor refuses to yield.")
    run_reconciliation(session_db, lambda *a, **k: packet(arc(), phase="climax"))
    assert load_ending_state(db, "chat", "s1").lifecycle == "finale"
    assert load_ending_state(db, "chat", "s1").finale_committed_rowid == first
    quote = "The governor surrendered. Mara secured the gate and the rebellion ended."
    last = add_story(db, quote)
    run_reconciliation(session_db, lambda *a, **k: resolved_packet(last, quote))
    ending = load_ending_state(db, "chat", "s1")
    assert ending.lifecycle == "resolution_committed" and ending.resolution_rowid == last
    assert ending.finale_committed_rowid == last
    assert json.loads(ending.resolution_evidence_json) == [{"rowid": last, "quote": quote}]
    assert load_arc_row(db, "chat", "s1", "rebellion_arc")["status"] == "resolved"
    assert load_narrative_clock(db, "chat", "s1")["updated_through_rowid"] == last


@pytest.mark.parametrize(
    "bad", ["no_quote", "invented", "future", "foreign", "user_row", "false", "unfinished_arc", "new_major_arc"]
)
def test_unproven_or_incomplete_resolution_cannot_advance_ending(session_db, bad):
    db, _ = finale(session_db)
    quote = "Mara reaches the gate."
    rowid = add_story(db, quote)
    data = json.loads(resolved_packet(rowid, quote))
    if bad == "no_quote":
        data["resolution"]["evidence"] = []
    elif bad == "invented":
        data["resolution"]["evidence"][0]["quote"] = "The governor died as the Director planned."
    elif bad == "future":
        data["resolution"]["evidence"][0]["rowid"] = rowid + 100
    elif bad == "foreign":
        with write_transaction(db):
            foreign = db.execute(
                "INSERT INTO messages(chat_id,session_id,role,content,created_at) "
                "VALUES('other','other','assistant',?,1)",
                (quote,),
            ).lastrowid
        data["resolution"]["evidence"][0]["rowid"] = foreign
    elif bad == "user_row":
        with write_transaction(db):
            user_row = db.execute(
                "INSERT INTO messages(chat_id,session_id,role,content,created_at) VALUES('chat','s1','user',?,1)",
                (quote,),
            ).lastrowid
        data["resolution"]["evidence"][0]["rowid"] = user_row
    elif bad == "false":
        data["resolution"]["resolved"] = False
    elif bad == "unfinished_arc":
        data["arcs"] = [arc()]
    else:
        data["arcs"].append(arc() | {"arc_id": "new_unresolved_major"})
    before = db.execute("SELECT * FROM messages").fetchall()
    run_reconciliation(session_db, lambda *a, **k: json.dumps(data))
    assert load_ending_state(db, "chat", "s1").lifecycle == "finale"
    assert load_ending_state(db, "chat", "s1").resolution_rowid is None
    assert db.execute("SELECT * FROM messages").fetchall() == before


def test_finale_committed_identity_updates_on_edit_and_restores_previous_after_delete(session_db):
    db, _ = finale(session_db)
    first = add_story(db, "The first confrontation.")
    last = add_story(db, "The second confrontation.")
    before = load_ending_state(db, "chat", "s1")
    with write_transaction(db):
        db.execute("UPDATE messages SET content='A revised second confrontation.' WHERE id=?", (last,))
    changed = load_ending_state(db, "chat", "s1")
    assert changed.finale_committed_rowid == last and changed.lifecycle_revision > before.lifecycle_revision
    with write_transaction(db):
        db.execute("DELETE FROM messages WHERE id=?", (last,))
    assert load_ending_state(db, "chat", "s1").finale_committed_rowid == first


def test_open_story_cannot_be_closed_by_a_resolution_packet(session_db):
    _, db, _ = session_db
    quote = "The rebellion ended."
    rowid = add_story(db, quote)
    run_reconciliation(session_db, lambda *a, **k: resolved_packet(rowid, quote))
    assert load_ending_state(db, "chat", "s1").lifecycle == "open"
    assert load_ending_state(db, "chat", "s1").resolution_rowid is None


def test_reconciliation_provider_failure_leaves_finale_reply_and_checkpoint_intact(session_db):
    db, cp = finale(session_db)
    rowid = add_story(db, "The committed finale turn survives.")

    def fail(*args, **kwargs):
        raise RuntimeError("Fixture provider error")

    run_reconciliation(session_db, fail)
    saved = load_ending_state(db, "chat", "s1")
    assert (
        saved.lifecycle == "finale"
        and saved.finale_committed_rowid == rowid
        and saved.checkpoint_id == cp.checkpoint_id
    )


def test_resolution_uses_actual_story_outcome_not_old_director_expectation(session_db):
    db, _ = finale(session_db)
    with write_transaction(db):
        db.execute(
            "INSERT INTO director_state(chat_id,session_id,active_direction) "
            "VALUES('chat','s1','PLAN: the governor dies') "
            "ON CONFLICT(chat_id,session_id) DO UPDATE SET active_direction=excluded.active_direction"
        )
    quote = "The governor survived and was arrested. The rebellion ended peacefully."
    rowid = add_story(db, quote)
    observed = []
    run_reconciliation(session_db, lambda *a, **k: observed.append(a[2]) or resolved_packet(rowid, quote))
    assert load_ending_state(db, "chat", "s1").lifecycle == "resolution_committed"
    assert "survived" in load_ending_state(db, "chat", "s1").resolution_evidence_json
    assert "PLAN: the governor dies" not in json.dumps(observed)
