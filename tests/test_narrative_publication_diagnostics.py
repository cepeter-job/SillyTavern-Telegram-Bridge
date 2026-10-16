"""Publication rejection codes identify guards without changing acceptance or logging story data."""

import json

import pytest
from session_test_support import session_db as session_db
from test_narrative_acceptance_recovery import diagnostics, snapshot
from test_narrative_arcs import arc
from test_narrative_reconciliation import add_story, proposal, run_reconciliation

from bridge import narrative_reconciliation
from bridge.narrative_repository import load_narrative_clock
from bridge.narrative_response import narrative_failure_reason
from bridge.sqlite_store import write_transaction

PUBLICATION_ERRORS = (
    ("A completed scene identifier cannot be reused", "scene_id_reused"),
    ("A viewpoint change requires a new scene identifier", "scene_viewpoint_changed"),
    ("A new scene requires an explicit transition", "scene_transition_missing"),
    ("Active scene must reference an established thread", "scene_thread_missing"),
    ("An arc must reference established narrative threads", "arc_thread_missing"),
    ("Epilogue facts require the committed epilogue identity", "epilogue_identity_mismatch"),
    ("Narrative state changed before publication", "state_revision_conflict"),
)


def private_proposal(**kwargs):
    return proposal("PRIVATE_SCENE_CANARY", "PRIVATE_THREAD_CANARY", summary="PRIVATE_PAYLOAD_CANARY", **kwargs)


def assert_rejection(caplog, db, reason):
    records = diagnostics(caplog)
    assert len(records) == 1
    record = records[0]
    assert record["event"] == "narrative.reconciliation_rejected"
    assert record["phase"] == "publish"
    assert record["reason"] == reason
    assert record["accepted"] is False
    clock = load_narrative_clock(db, "chat", "s1")
    assert record["revision"] == clock["state_revision"]
    assert record["source_message_id"] == clock["latest_rowid"]
    for rendered in (caplog.text, json.dumps(records)):
        assert "PRIVATE_" not in rendered
        assert all(message not in rendered for message, _ in PUBLICATION_ERRORS)


@pytest.mark.parametrize("reason", [reason for _, reason in PUBLICATION_ERRORS[:-1]])
def test_real_publication_guards_are_distinct_private_atomic_and_not_retried(session_db, caplog, reason):
    _, db, _ = session_db
    first = add_story(db, "PRIVATE_SOURCE_CANARY: Mara waits at the gate.")
    run_reconciliation(session_db, lambda *a, **k: private_proposal())
    candidate = json.loads(private_proposal())
    if reason == "scene_id_reused":
        add_story(db, "Mara leaves the gate.")
        run_reconciliation(
            session_db, lambda *a, **k: proposal("PRIVATE_NEXT_SCENE_CANARY", "PRIVATE_THREAD_CANARY", transition="cut")
        )
        candidate["scene"]["transition_type"] = "cut"
    elif reason == "scene_viewpoint_changed":
        candidate["scene"]["viewpoint_character"] = "PRIVATE_VIEWPOINT_CANARY"
    elif reason == "scene_transition_missing":
        candidate["scene"]["scene_id"] = "PRIVATE_NEXT_SCENE_CANARY"
    elif reason == "scene_thread_missing":
        candidate["scene"].update(
            scene_id="PRIVATE_NEXT_SCENE_CANARY", thread_id="PRIVATE_MISSING_THREAD_CANARY", transition_type="cut"
        )
        candidate["threads"] = []
    elif reason == "arc_thread_missing":
        candidate["arcs"] = [arc(related=("PRIVATE_MISSING_THREAD_CANARY",))]
        candidate["arcs"][0]["arc_id"] = "PRIVATE_ARC_CANARY"
    else:
        candidate["story_phase"] = "epilogue"
    add_story(db, "PRIVATE_SOURCE_CANARY: Mara considers what comes next.")
    if reason == "epilogue_identity_mismatch":
        with write_transaction(db):
            db.execute(
                "INSERT INTO ending_state(chat_id,session_id,lifecycle,epilogue_committed_rowid) "
                "VALUES('chat','s1','epilogue_committed',?)",
                (first,),
            )
    before = snapshot(db)
    calls = []
    caplog.clear()

    def generate(*args, **kwargs):
        assert not db.in_transaction
        calls.append(True)
        return json.dumps(candidate)

    run_reconciliation(session_db, generate)
    assert len(calls) == 1
    assert snapshot(db) == before
    assert not db.in_transaction
    assert_rejection(caplog, db, reason)


def test_conditional_state_update_conflict_rolls_back_real_entity_writes(session_db, caplog):
    _, db, _ = session_db
    add_story(db, "PRIVATE_SOURCE_CANARY")
    before = snapshot(db)
    # Force the real conditional UPDATE to affect no rows after scenes/threads were written.
    with write_transaction(db):
        db.execute(
            "CREATE TEMP TRIGGER reject_publication_update BEFORE UPDATE ON narrative_state "
            "WHEN EXISTS(SELECT 1 FROM narrative_scenes) AND EXISTS(SELECT 1 FROM narrative_threads) "
            "BEGIN SELECT RAISE(IGNORE); END"
        )
    calls = []
    run_reconciliation(session_db, lambda *a, **k: calls.append(True) or private_proposal())
    assert len(calls) == 1
    assert snapshot(db) == before
    assert not db.in_transaction
    assert_rejection(caplog, db, "state_revision_conflict")


@pytest.mark.parametrize("error_type", [ValueError, RuntimeError])
def test_unknown_late_publication_fault_is_generic_private_and_rolls_back(session_db, caplog, monkeypatch, error_type):
    _, db, _ = session_db
    rowid = add_story(db, "PRIVATE_SOURCE_CANARY")
    before = snapshot(db)

    def fail_after_checkpoint(*args, **kwargs):
        assert db.in_transaction
        assert db.execute("SELECT COUNT(*) FROM narrative_checkpoints").fetchone()[0] == 1
        assert load_narrative_clock(db, "chat", "s1")["updated_through_rowid"] == rowid
        assert db.execute("SELECT COUNT(*) FROM narrative_scenes").fetchone()[0] == 1
        assert db.execute("SELECT COUNT(*) FROM narrative_arcs").fetchone()[0] == 1
        raise error_type("PRIVATE_EXCEPTION_CANARY: PRIVATE_SCENE_CANARY PRIVATE_PAYLOAD_CANARY")

    monkeypatch.setattr(narrative_reconciliation, "reconcile_finale_outcome", fail_after_checkpoint)
    candidate = json.loads(private_proposal())
    candidate["arcs"] = [arc(related=("PRIVATE_THREAD_CANARY",))]
    calls = []
    run_reconciliation(session_db, lambda *a, **k: calls.append(True) or json.dumps(candidate))
    assert len(calls) == 1
    assert snapshot(db) == before
    assert not db.in_transaction
    assert_rejection(caplog, db, "state_rejected")


@pytest.mark.parametrize(("message", "reason"), PUBLICATION_ERRORS)
@pytest.mark.parametrize("phase", ["publish", "provider", "parse"])
def test_fixed_publication_codes_are_only_used_in_the_publication_phase(message, reason, phase):
    error_type = (
        narrative_reconciliation.NarrativeStateUnavailable if reason == "state_revision_conflict" else ValueError
    )
    expected = {"publish": reason, "provider": "provider_failed", "parse": "invalid_shape"}[phase]
    assert narrative_failure_reason(error_type(message), phase) == expected


@pytest.mark.parametrize(("message", "_reason"), PUBLICATION_ERRORS)
@pytest.mark.parametrize("error_type", [ValueError, RuntimeError])
def test_publication_message_matching_is_exact_and_never_echoes_exception_text(message, _reason, error_type):
    for altered in ("PRIVATE_EXCEPTION_CANARY: " + message, message + " PRIVATE_EXCEPTION_CANARY"):
        assert narrative_failure_reason(error_type(altered), "publish") == "state_rejected"


@pytest.mark.parametrize(("message", "_reason"), PUBLICATION_ERRORS)
def test_provider_exception_that_matches_a_guard_is_not_semantically_retried(session_db, caplog, message, _reason):
    _, db, _ = session_db
    add_story(db, "PRIVATE_SOURCE_CANARY")
    before = snapshot(db)
    calls = []

    def generate(*args, **kwargs):
        calls.append(True)
        raise ValueError(message)

    run_reconciliation(session_db, generate)
    assert len(calls) == 1
    assert snapshot(db) == before
    records = diagnostics(caplog)
    rejected = [record for record in records if record["event"] == "narrative.reconciliation_rejected"]
    assert len(rejected) == 1
    assert rejected[0]["phase"] == "provider"
    assert rejected[0]["reason"] == "provider_failed"
    assert all(record["event"] != "narrative.extraction_retry" for record in records)
    assert message not in json.dumps(records)
    assert "PRIVATE_" not in json.dumps(records)
