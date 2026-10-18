"""Director prerequisite failures retain a safe cause without publishing stale facts."""

import json

import pytest
from application_test_setup import make_test_provider_port
from session_test_support import session_db as session_db
from test_director_service import directed as directed
from test_director_service import reassess
from test_ending_state import goal
from test_narrative_acceptance_recovery import diagnostics, snapshot
from test_narrative_arcs import arc, packet
from test_narrative_reconciliation import add_story, proposal

from bridge import director_service
from bridge.director_repository import load_director_state
from bridge.ending_service import load_ending_state
from bridge.model_selection import set_task_model
from bridge.narrative_reconciliation import NarrativeStateUnavailable, ensure_narrative_state_current
from bridge.narrative_repository import load_narrative_clock


@pytest.mark.parametrize(
    ("failure", "code", "expected_calls", "explanation"),
    [
        ("evidence_quote", "invalid_evidence", 1, "story evidence"),
        ("evidence_shape", "invalid_evidence", 1, "story evidence"),
        ("malformed", "malformed_json", 2, "valid JSON"),
        ("shape", "invalid_shape", 2, "valid JSON"),
        ("provider", "provider_failed", 1, "provider"),
        ("publication", "scene_viewpoint_changed", 1, "accepted story state"),
    ],
)
def test_stale_prerequisite_reports_fixed_cause_without_planning_or_mutating_accepted_state(
    directed, caplog, failure, code, expected_calls, explanation
):
    _, db, _ = directed
    goal(db, "PRIVATE_ENDING_GOAL_CANARY")
    assert reassess(directed).result == "accepted"
    accepted = load_director_state(db, "chat", "s1")
    ending = load_ending_state(db, "chat", "s1")
    rowid = add_story(db, "PRIVATE_SOURCE_CANARY: Mara waits at the gate.")
    before = snapshot(db)
    set_task_model(db, "chat", "s1", "fixture::utility")
    set_task_model(db, "chat", "s1", "fixture::director", "director")
    calls = []

    def generate(_key, model, messages, **kwargs):
        assert not db.in_transaction
        assert model == "fixture::utility", "A rejected prerequisite must never call the Director planner"
        assert kwargs["settings"]["max_tokens"] == 2400
        calls.append(messages)
        if failure == "provider":
            raise RuntimeError("PRIVATE_EXCEPTION_CANARY")
        if failure == "malformed":
            return '{"PRIVATE_RESPONSE_CANARY":'
        if failure == "shape":
            return '{"PRIVATE_RESPONSE_CANARY":true}'
        if failure == "publication":
            candidate = json.loads(proposal())
            candidate["scene"]["viewpoint_character"] = "PRIVATE_VIEWPOINT_CANARY"
            return json.dumps(candidate)
        candidate = arc(status="resolved", quote="PRIVATE_EVIDENCE_CANARY", rowid=rowid)
        if failure == "evidence_shape":
            candidate["evidence"][0]["rowid"] = True
        return packet(candidate, phase="resolution")

    caplog.clear()
    result = reassess(directed, generate)
    assert result.result == "rejected" and result.proposal is None
    assert len(calls) == expected_calls
    assert snapshot(db) == before
    assert load_ending_state(db, "chat", "s1") == ending
    current = load_director_state(db, "chat", "s1")
    for key in ("active_direction", "active_proposal_json", "state_revision"):
        assert current[key] == accepted[key]
    assert current["inflight_token"] == "" and current["inflight_started_at"] == 0
    assert not db.in_transaction
    assert explanation in result.reason
    assert "reassess" in result.reason.lower()
    assert "saved story and accepted direction are unchanged" in result.reason
    saved = db.execute(
        "SELECT proposal_json,accepted_direction,reason FROM director_decisions WHERE decision_id=?",
        (result.decision_id,),
    ).fetchone()
    assert saved == ("{}", "", result.reason)
    rejected = [record for record in diagnostics(caplog) if record["event"] == "director.rejected"]
    assert len(rejected) == 1
    assert rejected[0]["reason"] == "reconciliation"
    assert rejected[0]["rejection_code"] == code
    assert current["degraded_state"] == "reconciliation"
    for rendered in (caplog.text, json.dumps(diagnostics(caplog)), result.reason, json.dumps(saved)):
        assert "PRIVATE_" not in rendered


def test_source_changes_do_not_attach_an_obsolete_rejection_to_current_state(directed, caplog):
    settings, db, session = directed
    target = add_story(db, "PRIVATE_SOURCE_CANARY")
    accepted_through = load_narrative_clock(db, "chat", "s1")["updated_through_rowid"]
    calls = []

    def generate(*args, **kwargs):
        calls.append(True)
        add_story(db, "PRIVATE_NEW_SOURCE_CANARY")
        candidate = arc(status="resolved", quote="PRIVATE_EVIDENCE_CANARY", rowid=target)
        candidate["evidence"][0]["rowid"] = True
        return packet(candidate, phase="resolution")

    with pytest.raises(NarrativeStateUnavailable) as raised:
        ensure_narrative_state_current(
            db,
            "",
            "chat",
            session,
            target,
            provider_port=make_test_provider_port(generate_backend=generate),
            app_settings=settings,
        )
    assert len(calls) == 4
    assert load_narrative_clock(db, "chat", "s1")["updated_through_rowid"] == accepted_through
    assert raised.value.rejection_code == ""
    assert "PRIVATE_" not in str(raised.value)
    assert "PRIVATE_" not in caplog.text


def test_unavailable_exception_keeps_legacy_arguments_and_unclassified_errors_private(directed, monkeypatch, caplog):
    _, db, _ = directed
    legacy = NarrativeStateUnavailable("Legacy unavailable message")
    assert legacy.args == ("Legacy unavailable message",)
    assert legacy.rejection_code == ""
    assert NarrativeStateUnavailable().args == ()
    before = snapshot(db)

    def unavailable(*args, **kwargs):
        raise NarrativeStateUnavailable("PRIVATE_EXCEPTION_CANARY", rejection_code="PRIVATE_CODE_CANARY")

    monkeypatch.setattr(director_service, "ensure_narrative_state_current", unavailable)
    result = reassess(directed, lambda *a, **k: pytest.fail("Unavailable facts must not call the planner"))
    assert result.result == "rejected"
    assert snapshot(db) == before
    rejected = [record for record in diagnostics(caplog) if record["event"] == "director.rejected"]
    assert len(rejected) == 1
    assert rejected[0]["reason"] == "unavailable"
    assert rejected[0].get("rejection_code", "") == ""
    assert "PRIVATE_" not in result.reason
    assert "PRIVATE_" not in caplog.text
