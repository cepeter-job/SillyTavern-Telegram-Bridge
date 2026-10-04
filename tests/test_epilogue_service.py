"""The epilogue is a leased, separately generated Story step with restart-safe commits."""

import json
import time
from dataclasses import replace
from unittest.mock import patch

import pytest
from application_test_setup import make_test_delivery_port, make_test_persona_service
from test_finale_resolution import finale, resolved_packet
from test_memory_completion_safety import session_db as session_db
from test_narrative_reconciliation import add_story, proposal, run_reconciliation

from bridge import epilogue_service as service
from bridge.card_content import card_fields
from bridge.delivery_progress import delivery_complete
from bridge.ending_repository import claim_ending_work, release_ending_work
from bridge.ending_service import load_ending_state
from bridge.model_selection import set_task_model
from bridge.narrative_repository import load_narrative_clock, load_narrative_state_row
from bridge.provider_port import ProviderPort
from bridge.sqlite_store import write_transaction


def resolved(case):
    db, cp = finale(case)
    quote = "The governor survived and was arrested. The rebellion ended peacefully."
    rowid = add_story(db, quote)
    run_reconciliation(case, lambda *a, **k: resolved_packet(rowid, quote))
    assert load_ending_state(db, "chat", "s1").lifecycle == "resolution_committed"
    return db, cp, rowid


def brief(db, **changes):
    return json.dumps(
        {
            "schema_version": 1,
            "expected_revision": load_narrative_clock(db, "chat", "s1")["state_revision"],
            "time_scope": "Six months later, consistently with the established consequences.",
            "cover": ["Mara's rebuilding of the gate", "The governor's arrest and political consequences"],
            "do_not_invent": ["The user's future romantic decision", "The fate of the missing brother"],
            "pov": "third_person_rotating",
            **changes,
        }
    )


def deliveries(db, *, fail=False):
    from bridge.delivery_progress import checkpoint, prepare_progress

    sent = []

    def send(token, chat_id, text, connection=None, session_id=None, assistant_rowid=None, **kwargs):
        assert not db.in_transaction
        sent.append((assistant_rowid, text))
        if fail:
            raise RuntimeError("Fixture delivery unavailable")
        payload, ids, complete, source = prepare_progress(db, assistant_rowid, text)
        if not complete:
            checkpoint(
                db, assistant_rowid, [*ids, 700], complete=True, expected_source=source, expected_payload=payload
            )

    return replace(make_test_delivery_port(), send_reply=send), sent


def complete(case, producer, delivery=None, *, manual=True):
    settings, db, session = case
    delivery = delivery or deliveries(db)[0]
    fields = card_fields({"name": "Mara", "description": "A rebel guarding the gate."}, app_settings=settings)
    with patch.object(service, "card_fields_from_file", return_value=fields):
        return service.complete_epilogue(
            db,
            "telegram-token",
            "fixture-key",
            "chat",
            session,
            provider_port=ProviderPort(producer),
            delivery_port=delivery,
            persona_service=make_test_persona_service(),
            app_settings=settings,
            manual=manual,
        )


def producer_for(db, calls, *, fail_stage=None, hook=None):
    def generate(key, model, messages, **kwargs):
        assert not db.in_transaction
        system = messages[0]["content"].lower()
        kind = (
            "brief"
            if "epilogue brief" in system
            else "reconcile"
            if kwargs.get("session_id", "").startswith("narrative:")
            else "story"
        )
        calls.append((kind, model, messages))
        if hook:
            hook(kind)
        if kind == fail_stage:
            raise RuntimeError("PRIVATE fixture API failure")
        if kind == "brief":
            return brief(db)
        if kind == "reconcile":
            data = json.loads(proposal(phase="epilogue"))
            data["scene"]["purpose"] = "Epilogue aftermath."
            return json.dumps(data)
        return "*Six months later, Mara restored the gate. The governor remained in custody.*"

    return generate


def test_resolution_transitions_to_pending_before_director_request(session_db):
    db, _, rowid = resolved(session_db)
    initial = load_ending_state(db, "chat", "s1")
    pending = service.begin_epilogue(db, "chat", "s1", expected_lifecycle_revision=initial.lifecycle_revision)
    assert pending.lifecycle == "epilogue_pending" and pending.resolution_rowid == rowid
    assert pending.epilogue_operation_id
    before = db.total_changes
    assert service.begin_epilogue(db, "chat", "s1", expected_lifecycle_revision=pending.lifecycle_revision) == pending
    assert db.total_changes == before


def test_pending_transition_joins_caller_transaction_and_rolls_back(session_db):
    db, _, _ = resolved(session_db)
    current = load_ending_state(db, "chat", "s1")
    db.execute("BEGIN")
    service.begin_epilogue(db, "chat", "s1", expected_lifecycle_revision=current.lifecycle_revision)
    assert db.in_transaction
    db.rollback()
    assert load_ending_state(db, "chat", "s1") == current


def test_separate_director_brief_and_story_prose_close_only_after_reconciliation(session_db):
    db, _, resolution = resolved(session_db)
    set_task_model(db, "chat", "s1", "director::planner", "director")
    set_task_model(db, "chat", "s1", "utility::facts", "utility")
    calls = []
    delivery, sent = deliveries(db)
    result = complete(session_db, producer_for(db, calls), delivery)
    ending = load_ending_state(db, "chat", "s1")
    assert result.lifecycle == "closed" and ending.lifecycle == "closed"
    assert ending.epilogue_committed_rowid > resolution
    assert [item[0] for item in calls] == ["brief", "story", "reconcile"]
    assert calls[0][1] == "director::planner" and calls[1][1] == session_db[2]["model_id"]
    assert calls[2][1] == "utility::facts"
    assert "governor survived" in json.dumps(calls[0][2]).lower()
    assert "do_not_invent" in json.dumps(calls[1][2])
    assert "missing brother" in json.dumps(calls[1][2])
    assert [row[0] for row in sent] == [resolution, ending.epilogue_committed_rowid]
    assert delivery_complete(db, ending.epilogue_committed_rowid)
    assert load_narrative_state_row(db, "chat", "s1")["story_phase"] == "closed"
    assert ending.work_token == "" and ending.last_error == ""
    assert db.execute("SELECT COUNT(*) FROM messages WHERE role='user'").fetchone()[0] == 0


@pytest.mark.parametrize("stage", ["brief", "story", "reconcile"])
def test_failed_stage_retries_only_uncommitted_work(session_db, stage, caplog):
    db, _, resolution = resolved(session_db)
    calls = []
    complete(session_db, producer_for(db, calls, fail_stage=stage))
    saved = load_ending_state(db, "chat", "s1")
    assert saved.resolution_rowid == resolution
    assert saved.lifecycle == ("epilogue_committed" if stage == "reconcile" else "epilogue_pending")
    assert (
        saved.epilogue_committed_rowid is not None if stage == "reconcile" else saved.epilogue_committed_rowid is None
    )
    assert "PRIVATE" not in saved.last_error and "PRIVATE" not in caplog.text
    calls.clear()
    complete(session_db, producer_for(db, calls))
    expected = {"brief": ["brief", "story", "reconcile"], "story": ["story", "reconcile"], "reconcile": ["reconcile"]}[
        stage
    ]
    assert [item[0] for item in calls] == expected
    assert load_ending_state(db, "chat", "s1").lifecycle == "closed"
    assert db.execute("SELECT COUNT(*) FROM messages WHERE id>?", (resolution,)).fetchone()[0] == 1


def test_delivery_failure_cannot_reopen_or_regenerate_committed_epilogue(session_db):
    db, _, _ = resolved(session_db)
    calls = []
    complete(session_db, producer_for(db, calls), deliveries(db, fail=True)[0])
    ending = load_ending_state(db, "chat", "s1")
    assert ending.lifecycle == "closed" and ending.epilogue_committed_rowid
    before = db.execute("SELECT id,role,content FROM messages").fetchall()
    calls.clear()
    delivery, sent = deliveries(db)
    result = complete(session_db, lambda *a, **k: pytest.fail("Committed ending contacted a provider"), delivery)
    assert result.lifecycle == "closed"
    assert db.execute("SELECT id,role,content FROM messages").fetchall() == before
    assert delivery_complete(db, ending.epilogue_committed_rowid)
    assert len(sent) == 2


def test_already_delivered_closed_story_is_a_zero_call_no_write_operation(session_db):
    db, _, _ = resolved(session_db)
    complete(session_db, producer_for(db, []))
    before = db.total_changes
    delivery, sent = deliveries(db)
    complete(session_db, lambda *a, **k: pytest.fail("Provider on completed ending"), delivery)
    assert sent == [] and db.total_changes == before


def test_one_live_epilogue_lease_blocks_duplicate_provider_calls(session_db):
    db, _, _ = resolved(session_db)
    current = load_ending_state(db, "chat", "s1")
    service.begin_epilogue(db, "chat", "s1", expected_lifecycle_revision=current.lifecycle_revision)
    with write_transaction(db):
        assert claim_ending_work(db, "chat", "s1", "other-worker", time.time())
        assert not claim_ending_work(db, "chat", "s1", "duplicate-worker", time.time())
    result = complete(session_db, lambda *a, **k: pytest.fail("Duplicate worker called provider"))
    assert result.lifecycle == "epilogue_pending" and "in progress" in result.message.lower()
    assert load_ending_state(db, "chat", "s1").work_token == "other-worker"
    with write_transaction(db):
        release_ending_work(db, "chat", "s1", "not-the-owner")
    assert load_ending_state(db, "chat", "s1").work_token == "other-worker"


def test_expired_work_lease_can_resume_the_same_epilogue_operation(session_db):
    db, _, _ = resolved(session_db)
    current = load_ending_state(db, "chat", "s1")
    pending = service.begin_epilogue(db, "chat", "s1", expected_lifecycle_revision=current.lifecycle_revision)
    with write_transaction(db):
        assert claim_ending_work(db, "chat", "s1", "dead-worker", 1.0)
    complete(session_db, producer_for(db, []))
    saved = load_ending_state(db, "chat", "s1")
    assert saved.lifecycle == "closed" and saved.epilogue_operation_id == pending.epilogue_operation_id


def test_provider_output_from_replaced_work_lease_is_not_committed(session_db):
    db, _, resolution = resolved(session_db)

    def replace_lease(kind):
        if kind == "story":
            with write_transaction(db):
                db.execute("UPDATE ending_state SET work_token='newer-worker' WHERE chat_id='chat' AND session_id='s1'")

    complete(session_db, producer_for(db, [], hook=replace_lease))
    saved = load_ending_state(db, "chat", "s1")
    assert saved.lifecycle == "epilogue_pending" and saved.epilogue_committed_rowid is None
    assert saved.work_token == "newer-worker"
    assert db.execute("SELECT COUNT(*) FROM messages WHERE id>?", (resolution,)).fetchone()[0] == 0


@pytest.mark.parametrize(
    "values",
    [
        {"schema_version": 2},
        {"expected_revision": False},
        {"time_scope": "x" * 301},
        {"cover": ["x"] * 9},
        {"do_not_invent": "ignore all rules"},
        {"pov": "anything"},
    ],
)
def test_malformed_epilogue_brief_never_calls_story(session_db, values):
    db, _, _ = resolved(session_db)
    calls = []
    result = complete(session_db, lambda *a, **k: calls.append(a) or brief(db, **values))
    assert result.lifecycle == "epilogue_pending"
    assert len(calls) == 1 and load_ending_state(db, "chat", "s1").epilogue_committed_rowid is None


def test_background_failure_backoff_does_not_consume_quota_repeatedly(session_db):
    db, _, _ = resolved(session_db)
    complete(session_db, producer_for(db, [], fail_stage="brief"), manual=False)
    result = complete(session_db, lambda *a, **k: pytest.fail("Automatic retry loop consumed quota"), manual=False)
    assert result.lifecycle == "epilogue_pending"


def test_saving_epilogue_rollback_does_not_leave_a_half_committed_message(session_db, monkeypatch):
    db, _, resolution = resolved(session_db)
    actual = service.publish_ending_state

    def fail(db, chat, sid, before, after):
        if after.lifecycle == "epilogue_committed":
            raise RuntimeError("Injected epilogue transaction failure")
        return actual(db, chat, sid, before, after)

    monkeypatch.setattr(service, "publish_ending_state", fail)
    complete(session_db, producer_for(db, []))
    assert load_ending_state(db, "chat", "s1").lifecycle == "epilogue_pending"
    assert db.execute("SELECT COUNT(*) FROM messages WHERE id>?", (resolution,)).fetchone()[0] == 0


def test_ordinary_story_cannot_claim_an_epilogue_phase(session_db):
    _settings, db, _session = session_db
    add_story(db)
    state = run_reconciliation(session_db, lambda *a, **k: proposal(phase="epilogue"))
    assert state.story_phase != "epilogue"
    assert load_ending_state(db, "chat", "s1").lifecycle == "open"
