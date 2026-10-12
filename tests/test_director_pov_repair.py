"""POV corrections share the format-repair budget and retain all acceptance fences."""

import json

import pytest
from test_director_service import directed as directed
from test_director_service import reassess, response
from test_memory_completion_safety import session_db as session_db
from test_narrative_acceptance_recovery import snapshot
from test_narrative_reconciliation import add_story

from bridge.director_repository import load_director_state
from bridge.ending_service import load_ending_state
from bridge.narrative_settings import preset_narrative_settings, save_session_narrative_settings


@pytest.mark.parametrize("transition", [False, True])
def test_policy_conflict_gets_targeted_feedback_and_one_valid_retry(directed, transition, caplog):
    _, db, _ = directed
    before = snapshot(db)
    ending = load_ending_state(db, "chat", "s1")
    calls = []
    changes = dict(action="transition_scene", transition_type="cut", viewpoint="Governor") if transition else {}

    def generate(_key, _model, messages, **kwargs):
        calls.append([dict(message) for message in messages])
        assert not db.in_transaction
        if len(calls) == 1:
            return response(db, pov="cinematic", direction="PRIVATE_DISCARDED_DIRECTION", **changes)
        feedback = messages[-1]["content"]
        assert "pov_policy_conflict" in feedback
        assert "narrative_policy.pov_mode" in feedback
        assert "third_person_rotating" in feedback
        if transition:
            assert "set a nonempty pov" in feedback.lower()
        else:
            assert "omit pov" in feedback.lower()
            assert "keep action continue" in feedback.lower()
            assert "transition_type continue" in feedback.lower()
        assert "PRIVATE_" not in feedback
        assert messages[:2] == calls[0]
        return response(db, pov="third_person_rotating", **changes)

    result = reassess(directed, generate)
    assert result.result == "accepted"
    assert len(calls) == 2
    assert result.proposal.pov == "third_person_rotating"
    assert snapshot(db) == before
    assert load_ending_state(db, "chat", "s1") == ending
    saved = db.execute("SELECT proposal_json FROM director_decisions").fetchall()
    assert len(saved) == 1 and "PRIVATE_" not in json.dumps(saved)
    assert "PRIVATE_" not in caplog.text


@pytest.mark.parametrize("first", ["conflict", "parse"])
@pytest.mark.parametrize("second", ["conflict", "parse", "agency", "finale", "stale"])
def test_repair_budget_is_shared_and_every_replacement_is_validated(directed, first, second):
    _, db, _ = directed
    assert reassess(directed).result == "accepted"
    accepted = load_director_state(db, "chat", "s1")
    before = snapshot(db)
    calls = []
    outputs = {
        "conflict": response(db, pov="cinematic"),
        "parse": "not JSON",
        "agency": response(db, pov="third_person_rotating", speaker="Alex"),
        "finale": response(db, pov="third_person_rotating", finale_ready=True, finale_reason="PRIVATE_REASON"),
        "stale": response(db, pov="third_person_rotating", expected_revision=0),
    }

    def generate(*args, **kwargs):
        calls.append(True)
        return outputs[first if len(calls) == 1 else second]

    result = reassess(directed, generate)
    assert result.result == "rejected" and result.proposal is None
    assert len(calls) == 2
    assert snapshot(db) == before
    current = load_director_state(db, "chat", "s1")
    assert current["active_proposal_json"] == accepted["active_proposal_json"]
    assert current["inflight_token"] == ""


@pytest.mark.parametrize("mutation", ["story", "style", "goal"])
@pytest.mark.parametrize("mutate_on", [1, 2])
def test_pov_retry_cannot_publish_or_call_again_after_state_changes(directed, mutation, mutate_on):
    from bridge.director_goals import set_director_goal

    _, db, _ = directed
    calls = []

    def generate(*args, **kwargs):
        calls.append(True)
        raw = response(db, pov="cinematic" if len(calls) == 1 else "third_person_rotating")
        if len(calls) == mutate_on:
            if mutation == "story":
                add_story(db, "A newer event.")
            elif mutation == "style":
                save_session_narrative_settings(db, "chat", "s1", preset_narrative_settings("observer"))
            else:
                set_director_goal(db, "chat", "s1", "New objective")
        return raw

    assert reassess(directed, generate).result == "rejected"
    assert len(calls) == mutate_on
    assert load_director_state(db, "chat", "s1")["active_direction"] == ""
