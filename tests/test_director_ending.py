"""Director ending proposals are atomic plans, not a shortcut around committed facts."""

import json

import pytest
from test_ending_state import goal, ready_case
from test_memory_completion_safety import session_db as session_db

from bridge.director_contracts import DirectorProposalError, parse_director_proposal
from bridge.director_guidance import director_guidance_for_session
from bridge.director_repository import load_director_state
from bridge.director_service import DirectorService
from bridge.ending_service import ending_goal_history, load_ending_state
from bridge.narrative_arc_repository import load_arc_row
from bridge.narrative_repository import load_narrative_clock, load_narrative_state_row
from bridge.narrative_settings import preset_narrative_settings, save_session_narrative_settings
from bridge.provider_port import ProviderPort
from bridge.sqlite_store import write_transaction


def decision(db, **values):
    return json.dumps(
        {
            "schema_version": 1,
            "action": "continue",
            "expected_revision": load_narrative_clock(db, "chat", "s1")["state_revision"],
            "direction": "Keep pressure on the gate.",
            **values,
        }
    )


def direct(case, produce, *, usage=None):
    settings, db, session = case
    return DirectorService().reassess(
        db,
        "test-key",
        "chat",
        session,
        provider_port=ProviderPort(produce, usage_recorder=usage.append if usage is not None else None),
        app_settings=settings,
        reason="ending",
        valid_characters={"Mara", "Boris"},
    )


def test_director_reads_arcs_and_ending_goal_and_records_distinct_usage(session_db):
    db = ready_case(session_db)
    goal(db, "Let Mara determine the monarchy's future.")
    seen, usage = [], []

    def produce(*args, **kwargs):
        assert not db.in_transaction
        seen.append(json.loads(args[2][1]["content"]))
        return decision(
            db,
            arc_updates=[{"arc_id": "rebellion_arc", "status": "active", "direction": "Reveal the army's loyalties."}],
            story_phase="escalation",
        )

    before_arc = load_arc_row(db, "chat", "s1", "rebellion_arc")
    before_state = load_narrative_state_row(db, "chat", "s1")
    result = direct(session_db, produce, usage=usage)
    assert result.result == "accepted"
    assert seen[0]["ending"]["goal"] == "Let Mara determine the monarchy's future."
    assert seen[0]["arcs"][0]["arc_id"] == "rebellion_arc"
    assert usage[0].scope.purpose == "director_ending"
    assert load_arc_row(db, "chat", "s1", "rebellion_arc") == before_arc
    assert load_narrative_state_row(db, "chat", "s1") == before_state
    assert "Reveal the army's loyalties" in director_guidance_for_session(db, "chat", "s1")


def test_automatic_goal_adaptation_and_readiness_commit_together(session_db):
    db = ready_case(session_db)
    goal(db, "Mara becomes queen.")
    result = direct(
        session_db,
        lambda *a, **k: decision(
            db,
            ending_goal_update="Mara determines the monarchy's future.",
            ending_goal_reason="Her rejection of the throne makes the old goal implausible.",
            finale_ready=True,
            finale_reason="The central conflict is developed and approaching its climax.",
        ),
    )
    assert result.result == "accepted"
    ending = load_ending_state(db, "chat", "s1")
    assert ending.lifecycle == "finale_ready" and ending.goal_revision == 2
    assert ending_goal_history(db, "chat", "s1")[0].source == "director"
    assert ending_goal_history(db, "chat", "s1")[0].previous_goal == "Mara becomes queen."
    assert ending.required_arcs == ("rebellion_arc",)
    assert ending.finale_direction_revision == load_director_state(db, "chat", "s1")["state_revision"]


@pytest.mark.parametrize(
    "values",
    [
        {"arc_updates": [{"arc_id": "rebellion_arc", "status": "resolved", "direction": "Pretend victory happened."}]},
        {"arc_updates": [{"arc_id": "unknown", "direction": "Advance this."}]},
        {"ending_goal_update": "Invent a new goal without a reason."},
        {"finale_ready": True},
    ],
)
def test_invalid_ending_proposal_never_partially_applies(session_db, values):
    db = ready_case(session_db)
    goal(db, "Original goal")
    ending = load_ending_state(db, "chat", "s1")
    before_arc = load_arc_row(db, "chat", "s1", "rebellion_arc")
    result = direct(session_db, lambda *a, **k: decision(db, **values))
    assert result.result == "rejected"
    assert load_ending_state(db, "chat", "s1") == ending
    assert load_arc_row(db, "chat", "s1", "rebellion_arc") == before_arc
    assert load_director_state(db, "chat", "s1")["active_direction"] == ""


@pytest.mark.parametrize(
    "values",
    [
        {"ending_goal_update": "Revised", "ending_goal_reason": "Changed direction"},
        {"finale_ready": True, "finale_reason": "Ready"},
    ],
)
def test_open_ended_stories_cannot_be_silently_closed_or_retargeted(session_db, values):
    db = ready_case(session_db)
    save_session_narrative_settings(db, "chat", "s1", preset_narrative_settings("world_driven"))
    result = direct(session_db, lambda *a, **k: decision(db, **values))
    assert result.result == "rejected"
    assert load_ending_state(db, "chat", "s1").lifecycle == "open"
    assert load_ending_state(db, "chat", "s1").current_goal == ""


def test_goal_adaptation_is_locked_during_finale(session_db):
    db = ready_case(session_db)
    goal(db, "Frozen goal")
    with write_transaction(db):
        db.execute("UPDATE ending_state SET lifecycle='finale'")
    result = direct(
        session_db,
        lambda *a, **k: decision(db, ending_goal_update="New destination", ending_goal_reason="Change our minds"),
    )
    assert result.result == "rejected"
    assert load_ending_state(db, "chat", "s1").current_goal == "Frozen goal"


def test_manual_goal_change_while_provider_is_running_rejects_stale_result(session_db):
    db = ready_case(session_db)
    goal(db, "Old goal")

    def produce(*args, **kwargs):
        goal(db, "Newer manual goal")
        return decision(db, ending_goal_update="Stale model goal", ending_goal_reason="This used the old plan.")

    result = direct(session_db, produce)
    assert result.result == "rejected"
    assert load_ending_state(db, "chat", "s1").current_goal == "Newer manual goal"
    assert all(row.source == "user" for row in ending_goal_history(db, "chat", "s1"))
    assert load_director_state(db, "chat", "s1")["active_direction"] == ""


def test_new_goal_before_repair_prevents_a_second_paid_call(session_db):
    db = ready_case(session_db)
    calls = []

    def produce(*args, **kwargs):
        calls.append(1)
        goal(db, "New goal")
        return "not JSON"

    assert direct(session_db, produce).result == "rejected"
    assert len(calls) == 1


def test_ending_changes_roll_back_when_director_cas_loses(session_db):
    db = ready_case(session_db)
    before = goal(db, "Original goal")

    def produce(*args, **kwargs):
        with write_transaction(db):
            db.execute(
                "UPDATE director_state SET state_revision=state_revision+1 WHERE chat_id='chat' AND session_id='s1'"
            )
        return decision(
            db,
            ending_goal_update="Should roll back",
            ending_goal_reason="The proposal will lose CAS.",
            finale_ready=True,
            finale_reason="Ready",
        )

    assert direct(session_db, produce).result == "rejected"
    assert load_ending_state(db, "chat", "s1") == before
    assert len(ending_goal_history(db, "chat", "s1")) == 1


@pytest.mark.parametrize(
    "field,value",
    [
        ("arc_updates", "not a list"),
        ("arc_updates", [{"arc_id": "x", "direction": "x"}] * 9),
        ("finale_ready", "yes"),
        ("finale_ready", 1),
        ("story_phase", "closed"),
        ("ending_goal_update", 123),
        ("ending_goal_reason", "x" * 1001),
    ],
)
def test_ending_proposal_types_and_bounds_fail_closed(field, value):
    raw = json.dumps({"schema_version": 1, "action": "continue", "expected_revision": 1, field: value})
    with pytest.raises(DirectorProposalError):
        parse_director_proposal(raw)
