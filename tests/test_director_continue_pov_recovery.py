"""A rendering policy may differ from the observed scene without rewriting canon."""

import json
from contextlib import closing
from dataclasses import replace

import pytest
from application_test_setup import make_native_test_persona_service
from npc_test_support import fields
from test_director_service import reassess, response
from test_memory_completion_safety import session_db as session_db
from test_narrative_acceptance_recovery import snapshot
from test_narrative_reconciliation import add_story, run_reconciliation

from bridge.director_contracts import DirectorProposalError, parse_director_proposal
from bridge.director_goals import set_director_goal
from bridge.director_guidance import active_director_plan
from bridge.director_repository import load_director_state
from bridge.director_validation import validate_director_proposal
from bridge.generation import build_chat_messages
from bridge.narrative_context import load_narrative_state, narrative_clock_is_current, narrative_context_for_session
from bridge.narrative_policy import narrative_policy
from bridge.narrative_repository import load_narrative_clock
from bridge.narrative_settings import (
    load_session_narrative_settings,
    preset_narrative_settings,
    save_session_narrative_settings,
)
from bridge.sqlite_store import db_connect


@pytest.fixture
def cinematic_scene(session_db):
    """Select cinematic before any prose, then reconcile a rotating observation."""
    _, db, _ = session_db
    assert load_narrative_clock(db, "chat", "s1")["latest_rowid"] == 0
    save_session_narrative_settings(
        db,
        "chat",
        "s1",
        replace(
            preset_narrative_settings("world_driven"),
            preset="custom",
            pov_mode="cinematic",
            ending_mode="closed_story",
        ),
    )
    add_story(db, "Mara watches the synthetic gate. The user is elsewhere.")
    state = run_reconciliation(session_db)
    assert state.pov_mode == "third_person_rotating"
    assert state.active_scene_id == "gate" and state.viewpoint_character == "Mara"
    assert load_session_narrative_settings(db, "chat", "s1").pov_mode == "cinematic"
    assert narrative_clock_is_current(load_narrative_clock(db, "chat", "s1"))
    return session_db


def canonical_snapshot(db):
    """Include preferences, physical scene state, ending history and every clock."""
    return (
        snapshot(db)
        | {
            table: db.execute("SELECT * FROM " + table).fetchall()  # noqa: S608 -- fixed synthetic table allowlist
            for table in ("narrative_settings", "narrative_defaults", "scene_states", "ending_goal_history")
        }
        | {"clock": load_narrative_clock(db, "chat", "s1")}
    )


@pytest.mark.parametrize(
    "pov,code",
    [("cinematic", "continue_pov_change"), ("third_person_rotating", "pov_policy_conflict")],
)
def test_both_nonempty_continue_modes_remain_rejected_but_repairable(cinematic_scene, pov, code):
    _, db, _ = cinematic_scene
    before = canonical_snapshot(db)
    with pytest.raises(DirectorProposalError) as raised:
        validate_director_proposal(
            parse_director_proposal(response(db, pov=pov)),
            policy=narrative_policy(load_session_narrative_settings(db, "chat", "s1")),
            state=load_narrative_state(db, "chat", "s1"),
            valid_characters={"Mara", "Governor"},
            user_characters={"Alex"},
            valid_threads={"rebellion"},
        )
    assert raised.value.category == "policy"
    assert raised.value.rejection_code == code
    assert canonical_snapshot(db) == before
    assert raised.value.repairable, f"{code} must permit correction within the shared retry"


@pytest.mark.parametrize("first_pov", ["cinematic", "third_person_rotating"])
def test_continue_recovers_by_provider_omission_without_mutating_canon(cinematic_scene, first_pov, caplog):
    _, db, _ = cinematic_scene
    before = canonical_snapshot(db)
    rejected_raw = response(db, pov=first_pov, direction="PRIVATE_DISCARDED_DIRECTION")
    accepted_raw = response(db, transition_type="continue", direction="Hold the existing gate scene.")
    assert "pov" not in json.loads(accepted_raw)
    calls = []

    def generate(_key, _model, messages, **kwargs):
        assert not db.in_transaction
        calls.append([dict(message) for message in messages])
        assert len(calls) <= 2, "Director exhausted its single shared retry"
        return rejected_raw if len(calls) == 1 else accepted_raw

    result = reassess(cinematic_scene, generate)
    assert canonical_snapshot(db) == before
    assert result.result == "accepted", result.reason
    assert len(calls) == 2
    assert calls[1][:2] == calls[0]
    assert result.proposal == parse_director_proposal(accepted_raw)
    assert result.proposal.action == "continue" and result.proposal.pov == ""
    assert result.proposal.transition_type == "continue" and result.proposal.new_thread is None
    current = active_director_plan(db, "chat", "s1")
    assert current["active_direction"] == "Hold the existing gate scene."
    assert parse_director_proposal(current["active_proposal_json"]) == result.proposal
    saved = db.execute("SELECT result,proposal_json FROM director_decisions WHERE source='ai'").fetchall()
    assert saved == [("accepted", current["active_proposal_json"])]
    assert "PRIVATE_" not in json.dumps(saved) and "PRIVATE_" not in caplog.text
    assert not db.in_transaction


def test_omitted_continue_pov_is_accepted_in_one_call(cinematic_scene):
    _, db, _ = cinematic_scene
    before = canonical_snapshot(db)
    calls = []

    def generate(*args, **kwargs):
        calls.append(True)
        assert len(calls) == 1, "Omitted POV needs no recovery"
        return response(db)

    result = reassess(cinematic_scene, generate)
    assert result.result == "accepted"
    assert result.proposal.pov == "" and result.proposal.action == "continue"
    assert calls == [True]
    assert canonical_snapshot(db) == before


def test_director_request_distinguishes_rendering_policy_from_observed_scene(cinematic_scene):
    _, db, _ = cinematic_scene
    requests = []

    def generate(_key, _model, messages, **kwargs):
        requests.append(messages)
        return response(db)

    assert reassess(cinematic_scene, generate).result == "accepted"
    # Check the actual provider boundary, including the separately stored modes.
    request = json.JSONDecoder().raw_decode(requests[0][1]["content"])[0]
    assert request["narrative_policy"]["pov_mode"] == "cinematic"
    assert request["committed_state"]["pov_mode"] == "third_person_rotating"
    instruction = requests[0][0]["content"]
    assert "narrative_policy.pov_mode" in instruction
    assert "future prose rendering" in instruction
    assert "committed_state.pov_mode" in instruction, "Director needs the observed scene POV distinguished explicitly"
    assert "omit pov" in instruction.lower(), "Continue must omit a POV that would change the established scene"


@pytest.mark.parametrize(
    "pov,code",
    [("cinematic", "continue_pov_change"), ("third_person_rotating", "pov_policy_conflict")],
)
def test_continue_feedback_recommends_omission_when_policy_and_scene_differ(cinematic_scene, pov, code):
    _, db, _ = cinematic_scene
    calls = []

    def generate(_key, _model, messages, **kwargs):
        calls.append([dict(message) for message in messages])
        assert len(calls) <= 2
        return response(db, pov=pov, direction="PRIVATE_REJECTED_PLAN") if len(calls) == 1 else response(db)

    reassess(cinematic_scene, generate)
    assert len(calls) == 2, f"{code} must reach the existing correction attempt"
    feedback = calls[1][-1]["content"]
    assert code in feedback
    assert "omit pov" in feedback.lower()
    assert "keep action continue" in feedback.lower()
    assert "transition_type continue" in feedback.lower()
    assert "do not invent a scene transition" in feedback.lower()
    scene_anchors = ("committed_state.pov_mode", "established scene", "current scene")
    assert any(anchor in feedback.lower() for anchor in scene_anchors), (
        "Omission must account for scene continuity, even when modes differ"
    )
    assert "when unchanged" not in feedback.lower(), "Omission cannot be limited to already matching modes"
    assert "PRIVATE_" not in feedback
    assert db.execute("SELECT content FROM messages").fetchone()[0] not in feedback
    assert calls[1][:2] == calls[0]


@pytest.mark.parametrize("first_pov", ["cinematic", "third_person_rotating"])
@pytest.mark.parametrize("valid_transition", [True, False], ids=["valid-transition", "invalid-reference"])
def test_replacement_transition_uses_ordinary_validation_without_an_action_lock(
    cinematic_scene, first_pov, valid_transition
):
    _, db, _ = cinematic_scene
    before = canonical_snapshot(db)
    outputs = [
        response(db, pov=first_pov),
        response(
            db,
            action="transition_scene",
            transition_type="pov_switch",
            thread_id="rebellion" if valid_transition else "unknown",
            viewpoint="Governor",
            pov="cinematic",
        ),
    ]
    calls = []

    def generate(*args, **kwargs):
        assert not db.in_transaction
        calls.append(True)
        assert len(calls) <= 2
        return outputs[len(calls) - 1]

    result = reassess(cinematic_scene, generate)
    assert len(calls) == 2
    # Keeping continue is prompt guidance; a valid replacement transition remains valid.
    if valid_transition:
        assert result.result == "accepted", result.reason
        assert result.proposal == parse_director_proposal(outputs[1])
        assert result.proposal.action == "transition_scene" and result.proposal.pov == "cinematic"
        current = active_director_plan(db, "chat", "s1")
        assert parse_director_proposal(current["active_proposal_json"]) == result.proposal
    else:
        assert result.result == "rejected" and result.proposal is None
        assert active_director_plan(db, "chat", "s1") is None
        assert "(reference)" in result.reason
    assert canonical_snapshot(db) == before


@pytest.mark.parametrize(
    "first,second",
    [
        ("scene", "scene"),
        ("policy", "policy"),
        ("scene", "policy"),
        ("policy", "scene"),
        ("parse", "scene"),
        ("scene", "parse"),
        ("parse", "policy"),
        ("policy", "parse"),
        ("scene", "agency"),
        ("scene", "finale"),
        ("scene", "reference"),
        ("scene", "stale"),
        ("scene", "version"),
    ],
)
def test_scene_policy_and_parse_failures_share_one_retry_and_never_get_a_third_call(cinematic_scene, first, second):
    _, db, _ = cinematic_scene
    assert reassess(cinematic_scene).result == "accepted"
    accepted = load_director_state(db, "chat", "s1")
    before = canonical_snapshot(db)
    outputs = {
        "scene": response(db, pov="cinematic"),
        "policy": response(db, pov="third_person_rotating"),
        "parse": "not JSON",
        "agency": response(db, speaker="Alex"),
        "finale": response(db, finale_ready=True, finale_reason="Synthetic undeveloped finale"),
        "reference": response(db, thread_id="unknown"),
        "stale": response(db, expected_revision=0),
        "version": response(db, schema_version=2),
    }
    calls = []

    def generate(*args, **kwargs):
        assert not db.in_transaction
        calls.append(True)
        assert len(calls) <= 2, "No third attempt is permitted, even if it could succeed"
        return outputs[first if len(calls) == 1 else second]

    result = reassess(cinematic_scene, generate)
    assert result.result == "rejected" and result.proposal is None
    assert canonical_snapshot(db) == before
    current = load_director_state(db, "chat", "s1")
    for key in ("active_direction", "active_proposal_json", "state_revision"):
        assert current[key] == accepted[key]
    assert current["inflight_token"] == ""
    assert len(calls) == 2, "Both conflicts are repairable, but only on the first attempt"
    assert db.execute("SELECT proposal_json FROM director_decisions WHERE result='rejected'").fetchall() == [("{}",)]


@pytest.mark.parametrize("mutation", ["story", "style", "goal"])
@pytest.mark.parametrize("mutate_on", [1, 2])
@pytest.mark.parametrize("first_pov", ["cinematic", "third_person_rotating"])
def test_concurrent_changes_fence_continue_recovery_and_publication(cinematic_scene, mutation, mutate_on, first_pov):
    settings, db, _ = cinematic_scene
    outputs = [response(db, pov=first_pov), response(db, direction="OBSOLETE_RECOVERED_PLAN")]
    calls = []
    changed = []

    def generate(*args, **kwargs):
        assert not db.in_transaction
        calls.append(True)
        assert len(calls) <= 2
        if len(calls) == mutate_on:
            # A separate real connection commits while the Director request is in flight.
            with closing(db_connect(app_settings=settings)) as other:
                if mutation == "story":
                    add_story(other, "A newer synthetic event is committed.")
                elif mutation == "style":
                    save_session_narrative_settings(other, "chat", "s1", preset_narrative_settings("ensemble"))
                else:
                    set_director_goal(other, "chat", "s1", "New synthetic objective")
                changed.append(canonical_snapshot(other))
        return outputs[len(calls) - 1]

    result = reassess(cinematic_scene, generate)
    assert result.result == "rejected" and result.proposal is None
    assert len(calls) == mutate_on, "State changes suppress retries or reject late publication"
    assert changed and canonical_snapshot(db) == changed[0]
    current = load_director_state(db, "chat", "s1")
    assert current["active_direction"] == "" and current["inflight_token"] == ""
    if mutation == "goal":
        assert current["goal"] == "New synthetic objective"
    accepted = db.execute("SELECT COUNT(*) FROM director_decisions WHERE source='ai' AND result='accepted'").fetchone()
    assert accepted == (0,)


@pytest.mark.parametrize("recover", [False, True])
def test_selected_cinematic_governs_future_prose_after_director_omits_pov(cinematic_scene, recover):
    settings, db, session = cinematic_scene
    before = canonical_snapshot(db)
    calls = []

    def generate(*args, **kwargs):
        calls.append(True)
        return response(db, pov="cinematic") if recover and len(calls) == 1 else response(db)

    result = reassess(cinematic_scene, generate)
    assert result.result == "accepted", result.reason
    assert len(calls) == 1 + recover and result.proposal.pov == ""
    messages = build_chat_messages(
        session,
        fields(),
        "Continue watching the gate.",
        [],
        persona_service=make_native_test_persona_service(app_settings=settings),
        app_settings=settings,
        narrative_context=narrative_context_for_session(db, "chat", "s1", "story"),
    )
    system = messages[0]["content"]
    assert "Use cinematic/objective narration: observable action and dialogue only, without private thoughts." in system
    assert "Use rotating third-person limited narration" not in system
    assert "Hidden Director plan" in system and '"pov":""' in system
    assert load_narrative_state(db, "chat", "s1").pov_mode == "third_person_rotating"
    assert canonical_snapshot(db) == before


@pytest.mark.parametrize(
    "changes",
    [
        {"user_action": "Alex opens the gate"},
        {"speaker": "Alex"},
        {"viewpoint": "Governor"},
        {"user_present": True},
        {"transition_type": "cut"},
        {"thread_id": "unknown"},
        {"finale_ready": True, "finale_reason": "An undeveloped ending"},
        {"schema_version": 2},
    ],
    ids=["agency", "speaker", "viewpoint", "presence", "transition", "reference", "finale", "version"],
)
def test_unrelated_nonrepairable_checks_still_reject_without_retry(cinematic_scene, changes):
    _, db, _ = cinematic_scene
    before = canonical_snapshot(db)
    calls = []

    def generate(*args, **kwargs):
        calls.append(True)
        return response(db, **changes) if len(calls) == 1 else response(db)

    result = reassess(cinematic_scene, generate)
    assert result.result == "rejected" and result.proposal is None
    assert calls == [True]
    assert canonical_snapshot(db) == before
    assert active_director_plan(db, "chat", "s1") is None


def test_noncontinue_policy_repair_still_requires_the_selected_mode(cinematic_scene):
    _, db, _ = cinematic_scene
    before = canonical_snapshot(db)
    calls = []
    target = dict(action="transition_scene", thread_id="rebellion", viewpoint="Governor", transition_type="pov_switch")

    def generate(_key, _model, messages, **kwargs):
        calls.append(messages)
        return response(db, pov="third_person_rotating" if len(calls) == 1 else "cinematic", **target)

    result = reassess(cinematic_scene, generate)
    assert result.result == "accepted" and result.proposal.action == "transition_scene"
    assert result.proposal.pov == "cinematic" and len(calls) == 2
    assert "pov_policy_conflict" in calls[1][-1]["content"]
    assert "cinematic" in calls[1][-1]["content"]
    assert "set a nonempty pov" in calls[1][-1]["content"].lower()
    assert canonical_snapshot(db) == before
