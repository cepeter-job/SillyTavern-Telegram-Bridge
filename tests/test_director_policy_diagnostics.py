"""Policy diagnostics preserve rejection, privacy, transactions and the call budget."""

import json
import logging
from dataclasses import replace

import pytest
from test_director_service import directed as directed
from test_director_service import reassess, response
from test_memory_completion_safety import session_db as session_db
from test_narrative_acceptance_recovery import snapshot
from test_narrative_arcs import arc, packet
from test_narrative_reconciliation import add_story, run_reconciliation

from bridge import director_service
from bridge.diagnostic_logging import DiagnosticFormatter
from bridge.director_contracts import DirectorProposalError, parse_director_proposal
from bridge.director_repository import load_director_state
from bridge.director_validation import validate_director_proposal
from bridge.ending_service import load_ending_state
from bridge.narrative_context import load_narrative_state
from bridge.narrative_policy import narrative_policy
from bridge.narrative_settings import preset_narrative_settings, save_session_narrative_settings

CASES = [
    *[
        ("user_authorship", {field: "PRIVATE_USER_CONTENT"}, {})
        for field in ("user_dialogue", "user_thoughts", "user_decision", "user_action")
    ],
    (
        "arc_outcome_change",
        {"arc_updates": [{"arc_id": "rebellion_arc", "direction": "PRIVATE_ARC_CONTENT", "status": "resolved"}]},
        {},
    ),
    (
        "ending_mode_required",
        {"ending_goal_update": "PRIVATE_GOAL", "ending_goal_reason": "PRIVATE_REASON"},
        {"ending_mode": "open_ended"},
    ),
    ("ending_mode_required", {"finale_ready": False, "finale_reason": "PRIVATE_REASON"}, {"ending_mode": "open_ended"}),
    ("finale_phase_undeveloped", {"finale_ready": True, "finale_reason": "PRIVATE_REASON"}, {}),
    ("user_speaker", {"speaker": "aLeX"}, {}),
    ("pov_policy_conflict", {"pov": "cinematic"}, {}),
    ("user_first_person", {"viewpoint": "Alex"}, {"pov_mode": "first_person"}),
    ("absent_user_viewpoint", {"viewpoint": "{{user}}", "user_present": False}, {}),
    ("continue_transition", {"transition_type": "cut"}, {}),
    ("continue_transition", {"new_thread": {"thread_id": "PRIVATE_THREAD_ID", "title": "PRIVATE_TITLE"}}, {}),
    ("continue_viewpoint_change", {"viewpoint": "Governor"}, {}),
    ("continue_pov_change", {"pov": "cinematic"}, {"pov_mode": "cinematic"}),
    ("continue_presence_change", {"user_present": True}, {}),
]


@pytest.fixture
def policy_case(session_db, request):
    code, changes, style_changes = request.param
    _, db, _ = session_db
    settings = replace(
        preset_narrative_settings("world_driven"),
        preset="custom",
        ending_mode="closed_story",
        **{key: value for key, value in style_changes.items() if key != "ending_mode"},
    )
    settings = replace(settings, ending_mode=style_changes.get("ending_mode", "closed_story"))
    save_session_narrative_settings(db, "chat", "s1", settings)
    add_story(db, "PRIVATE_STORY_CONTENT")
    run_reconciliation(session_db, lambda *a, **k: packet(arc()))
    return session_db, settings, code, changes


def formatted_rejection(caplog, code):
    rendered = [json.loads(DiagnosticFormatter().format(record)) for record in caplog.records]
    rejected = [record for record in rendered if record["event"] == "director.rejected"]
    assert len(rejected) == 1
    assert rejected[0]["reason"] == "policy"
    assert rejected[0]["rejection_code"] == code
    assert rejected[0]["level"] == "WARNING"
    assert "PRIVATE_" not in json.dumps(rendered)
    assert "PRIVATE_" not in caplog.text
    assert "chat_id" not in rejected[0] and "session_id" not in rejected[0]
    assert rejected[0]["chat_ref"] != "chat" and rejected[0]["session_ref"] != "s1"


@pytest.mark.parametrize("policy_case", CASES, indirect=True)
def test_each_policy_guard_has_precise_repairability_and_is_content_free(policy_case, caplog):
    case, settings, code, changes = policy_case
    _, db, _ = case
    raw = response(db, direction="PRIVATE_PROPOSAL_CONTENT", **changes)
    before = snapshot(db)
    with pytest.raises(DirectorProposalError) as raised:
        proposal = parse_director_proposal(raw)
        validate_director_proposal(
            proposal,
            policy=narrative_policy(settings),
            state=load_narrative_state(db, "chat", "s1"),
            valid_characters={"Mara", "Governor"},
            user_characters={"Alex"},
            valid_threads={"rebellion", "palace"},
            arc_states={"rebellion_arc": "active"},
        )
    assert raised.value.category == "policy"
    assert raised.value.rejection_code == code
    assert raised.value.repairable is (code == "pov_policy_conflict")
    assert snapshot(db) == before

    assert reassess(case).result == "accepted"
    accepted = load_director_state(db, "chat", "s1")
    ending = load_ending_state(db, "chat", "s1")
    calls = []

    def generate(*args, **kwargs):
        assert not db.in_transaction
        calls.append(True)
        return raw

    caplog.clear()
    with caplog.at_level(logging.WARNING):
        result = reassess(case, generate)
    assert result.result == "rejected" and result.proposal is None
    assert calls == [True] * (2 if code == "pov_policy_conflict" else 1)
    assert snapshot(db) == before
    assert load_ending_state(db, "chat", "s1") == ending
    current = load_director_state(db, "chat", "s1")
    for key in ("active_direction", "active_proposal_json", "state_revision"):
        assert current[key] == accepted[key]
    assert current["degraded_state"] and current["inflight_token"] == ""
    assert not db.in_transaction
    saved = db.execute(
        "SELECT proposal_json,accepted_direction,reason FROM director_decisions WHERE decision_id=?",
        (result.decision_id,),
    ).fetchone()
    assert saved == ("{}", "", result.reason)
    assert "(policy)" in result.reason and "PRIVATE_" not in result.reason
    formatted_rejection(caplog, code)


@pytest.mark.parametrize("repair_first", [False, True])
def test_policy_after_optional_format_repair_never_gets_another_call(directed, caplog, repair_first):
    case = directed
    _, db, _ = case
    calls = []

    def generate(*args, **kwargs):
        calls.append(True)
        return "not JSON" if repair_first and len(calls) == 1 else response(db, speaker="Alex")

    with caplog.at_level(logging.WARNING):
        assert reassess(case, generate).result == "rejected"
    assert len(calls) == 1 + repair_first
    formatted_rejection(caplog, "user_speaker")


@pytest.mark.parametrize("code", ["", "PRIVATE_IDENTIFIER", "user_speaker PRIVATE_CONTENT", ["user_speaker"], None])
def test_unknown_policy_code_and_exception_text_stay_generic(directed, caplog, monkeypatch, code):
    case = directed
    error = DirectorProposalError("policy", "PRIVATE_EXCEPTION_CONTENT", rejection_code=code)
    assert error.rejection_code == "policy"

    def fail(*args, **kwargs):
        raise error

    monkeypatch.setattr(director_service, "validate_director_proposal", fail)
    caplog.clear()
    with caplog.at_level(logging.WARNING):
        assert reassess(case).result == "rejected"
    formatted_rejection(caplog, "policy")


def test_nonpolicy_error_cannot_claim_a_policy_code():
    error = DirectorProposalError("reference", "PRIVATE_ENTITY_ID", rejection_code="user_speaker")
    assert error.rejection_code == ""


@pytest.mark.parametrize("category", ["PRIVATE_CATEGORY", "", None, ["policy"]])
def test_unknown_director_category_is_generic(category):
    error = DirectorProposalError(category, "PRIVATE_EXCEPTION", rejection_code="user_speaker")
    assert error.category == "unavailable"
    assert error.rejection_code == ""


@pytest.mark.parametrize("error_type", [ValueError, RuntimeError])
def test_unknown_late_error_rolls_back_ending_changes_without_disclosing_content(
    session_db, caplog, monkeypatch, error_type
):
    from test_director_ending import decision, direct
    from test_ending_state import goal, ready_case

    from bridge.ending_service import ending_goal_history

    db = ready_case(session_db)
    goal(db, "PRIVATE_ORIGINAL_GOAL")
    before = snapshot(db)
    history = ending_goal_history(db, "chat", "s1")
    calls = []

    def fail_after_ending(*args, **kwargs):
        assert db.in_transaction
        assert load_ending_state(db, "chat", "s1").current_goal == "PRIVATE_NEW_GOAL"
        assert load_ending_state(db, "chat", "s1").lifecycle == "finale_ready"
        raise error_type("PRIVATE_EXCEPTION_CONTENT user_speaker")

    def generate(*args, **kwargs):
        assert not db.in_transaction
        calls.append(True)
        return decision(
            db,
            ending_goal_update="PRIVATE_NEW_GOAL",
            ending_goal_reason="PRIVATE_REASON",
            finale_ready=True,
            finale_reason="PRIVATE_REASON",
        )

    monkeypatch.setattr(director_service, "publish_director_plan", fail_after_ending)
    caplog.clear()
    with caplog.at_level(logging.WARNING):
        result = direct(session_db, generate)
    assert result.result == "rejected" and result.proposal is None
    assert calls == [True] and not db.in_transaction
    assert snapshot(db) == before
    assert ending_goal_history(db, "chat", "s1") == history
    assert load_director_state(db, "chat", "s1")["inflight_token"] == ""
    logs = [json.loads(DiagnosticFormatter().format(record)) for record in caplog.records]
    rejected = [record for record in logs if record["event"] == "director.rejected"]
    assert len(rejected) == 1 and rejected[0]["reason"] == "unavailable"
    assert "rejection_code" not in rejected[0]
    assert "PRIVATE_" not in json.dumps(logs) and "PRIVATE_" not in caplog.text
    assert "user_speaker" not in json.dumps(logs)


def test_broken_diagnostic_handler_does_not_change_policy_failure(directed, monkeypatch):
    _, db, _ = directed
    before = snapshot(db)

    def broken(*args, **kwargs):
        raise RuntimeError("PRIVATE_LOG_FAILURE")

    monkeypatch.setattr(logging.getLogger("bridge.events"), "log", broken)
    result = reassess(directed, lambda *a, **k: response(db, speaker="Alex"))
    assert result.result == "rejected"
    assert snapshot(db) == before
    assert not db.in_transaction
    assert load_director_state(db, "chat", "s1")["inflight_token"] == ""
