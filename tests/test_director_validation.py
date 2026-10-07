"""Untrusted Director proposals must be bounded, versioned and revision-safe."""

import json
from dataclasses import replace

import pytest

from bridge.director_contracts import DirectorProposalError, parse_director_proposal
from bridge.director_validation import validate_director_proposal
from bridge.narrative_policy import narrative_policy
from bridge.narrative_settings import preset_narrative_settings
from bridge.narrative_values import NarrativeState


def payload(**changes):
    return json.dumps(
        {
            "schema_version": 1,
            "action": "continue",
            "expected_revision": 4,
            "scene_id": "gate",
            "thread_id": "rebellion",
            "direction": "Keep the gate tense.",
        }
        | changes
    )


def state(**changes):
    return NarrativeState(
        active_scene_id="gate",
        active_thread_id="rebellion",
        state_revision=4,
        viewpoint_character="Mara",
        pov_mode="third_person_rotating",
        user_present=False,
        **changes,
    )


def validate(proposal, *, preset="world_driven", current=None, ending="open"):
    validate_director_proposal(
        proposal,
        policy=narrative_policy(preset_narrative_settings(preset)),
        state=current or state(),
        valid_characters={"Mara", "Governor"},
        valid_threads={"rebellion", "palace"},
        user_characters={"Alex"},
        ending_state=ending,
    )


def test_version_one_continue_is_valid_and_unknown_optional_data_is_not_persisted():
    proposal = parse_director_proposal(payload(ignored={"future": "metadata"}))
    validate(proposal)
    assert proposal.direction == "Keep the gate tense."
    assert "ignored" not in proposal.to_dict()


@pytest.mark.parametrize("version", [None, 0, 2, "1", True, 1.0])
def test_unsupported_or_missing_version_is_not_repairable(version):
    values = json.loads(payload())
    if version is None:
        values.pop("schema_version")
    else:
        values["schema_version"] = version
    with pytest.raises(DirectorProposalError) as exc:
        parse_director_proposal(json.dumps(values))
    assert exc.value.category == "version"
    assert not exc.value.repairable


@pytest.mark.parametrize("bad", ['{"schema_version":1,', "not json", "[]", '{"schema_version":1,"schema_version":2}'])
def test_malformed_output_is_never_an_accepted_proposal(bad):
    with pytest.raises(DirectorProposalError):
        parse_director_proposal(bad)


@pytest.mark.parametrize("field", ["action", "expected_revision"])
def test_required_fields_are_not_inferred(field):
    values = json.loads(payload())
    values.pop(field)
    with pytest.raises(DirectorProposalError):
        parse_director_proposal(json.dumps(values))


@pytest.mark.parametrize("revision", [-1, True, "4", 4.0])
def test_revision_is_a_nonnegative_integer(revision):
    with pytest.raises(DirectorProposalError):
        parse_director_proposal(payload(expected_revision=revision))


@pytest.mark.parametrize(
    "changes",
    [
        {"direction": "x" * 4001},
        {"direction_ttl": 0},
        {"direction_ttl": True},
        {"direction_ttl": 1001},
        {"user_present": "false"},
        {"thread_id": "bad/id"},
        {"scene_id": ["gate"]},
        {"direction": {"text": "plan"}},
    ],
)
def test_known_fields_are_strictly_typed_and_bounded(changes):
    with pytest.raises(DirectorProposalError):
        parse_director_proposal(payload(**changes))



def test_provider_json_fence_is_accepted_when_it_contains_only_one_object():
    proposal = parse_director_proposal("'''json".replace("'", String.fromCharCode(96)) + "\n" + payload() + "\n" + String.fromCharCode(96).repeat(3))
    validate(proposal)
    assert proposal.direction == "Keep the gate tense."


def test_null_optional_text_fields_are_treated_as_omitted():
    values = json.loads(payload())
    values.update(
        viewpoint=None,
        pov=None,
        purpose=None,
        transition_type=None,
        location=None,
        time_scope=None,
        speaker=None,
        story_phase=None,
        ending_goal_reason=None,
        finale_reason=None,
    )
    proposal = parse_director_proposal(json.dumps(values))
    validate(proposal)
    assert proposal.viewpoint == ""
    assert proposal.pov == ""
    assert proposal.transition_type == "continue"


def test_text_outside_a_json_fence_remains_rejected():
    fenced = "prefix\n" + String.fromCharCode(96).repeat(3) + "json\n" + payload() + "\n" + String.fromCharCode(96).repeat(3)
    with pytest.raises(DirectorProposalError):
        parse_director_proposal(fenced)



def test_oversized_raw_output_is_rejected_before_parsing():
    with pytest.raises(DirectorProposalError) as exc:
        parse_director_proposal(" " * 20000 + payload())
    assert not exc.value.repairable


@pytest.mark.parametrize("field", ["user_dialogue", "user_thoughts", "user_decision", "user_action"])
def test_structural_user_control_mutations_are_rejected(field):
    with pytest.raises(DirectorProposalError) as exc:
        parse_director_proposal(payload(**{field: "Alex joins the army"}))
    assert exc.value.category == "policy"


def test_stale_revision_and_closed_lifecycle_are_categorized():
    with pytest.raises(DirectorProposalError) as stale:
        validate(parse_director_proposal(payload(expected_revision=3)))
    assert stale.value.category == "stale"
    with pytest.raises(DirectorProposalError) as closed:
        validate(parse_director_proposal(payload()), ending="closed")
    assert closed.value.category == "lifecycle"


@pytest.mark.parametrize(
    "changes", [{"scene_id": "other"}, {"thread_id": "palace"}, {"viewpoint": "Governor"}, {"pov": "cinematic"}]
)
def test_continue_cannot_silently_switch_scene_thread_or_viewpoint(changes):
    with pytest.raises(DirectorProposalError):
        validate(parse_director_proposal(payload(**changes)))


def test_transition_can_plan_a_known_offscreen_thread_without_committing_facts():
    proposal = parse_director_proposal(
        payload(
            action="transition_scene",
            thread_id="palace",
            viewpoint="Governor",
            pov="third_person_rotating",
            user_present=False,
            transition_type="thread_switch",
            purpose="Reveal the hidden order.",
        )
    )
    before = state()
    validate(proposal, current=before)
    assert before.active_thread_id == "rebellion"
    assert before.active_scene_id == "gate"


@pytest.mark.parametrize(
    "changes",
    [
        {"thread_id": "unknown"},
        {"viewpoint": "Nobody"},
        {"pov": "cinematic"},
        {"transition_type": "continue"},
        {"speaker": "Alex"},
    ],
)
def test_invalid_transition_references_and_user_speaker_are_rejected(changes):
    data = (
        dict(
            action="transition_scene",
            thread_id="palace",
            viewpoint="Governor",
            pov="third_person_rotating",
            user_present=False,
            transition_type="cut",
        )
        | changes
    )
    with pytest.raises(DirectorProposalError):
        validate(parse_director_proposal(payload(**data)))


def test_new_thread_is_a_validated_proposal_not_an_existing_fact():
    proposal = parse_director_proposal(
        payload(
            action="transition_scene",
            thread_id="border",
            viewpoint="Mara",
            pov="third_person_rotating",
            user_present=False,
            transition_type="cut",
            new_thread={"thread_id": "border", "title": "The border"},
        )
    )
    validate(proposal)
    assert proposal.new_thread.title == "The border"


def test_observer_requires_objective_pov_and_cannot_pick_user_as_speaker():
    objective = parse_director_proposal(
        payload(
            action="transition_scene", thread_id="palace", pov="cinematic", user_present=False, transition_type="cut"
        )
    )
    validate(objective, preset="observer")
    with pytest.raises(DirectorProposalError):
        validate(replace(objective, pov="omniscient"), preset="observer")
    with pytest.raises(DirectorProposalError):
        validate(replace(objective, speaker="Alex"), preset="observer")


def test_first_person_never_uses_user_viewpoint():
    policy = narrative_policy(
        replace(preset_narrative_settings("world_driven"), preset="custom", pov_mode="first_person")
    )
    proposal = parse_director_proposal(
        payload(
            action="transition_scene",
            thread_id="palace",
            viewpoint="Alex",
            pov="first_person",
            user_present=True,
            transition_type="cut",
        )
    )
    with pytest.raises(DirectorProposalError) as exc:
        validate_director_proposal(
            proposal,
            policy=policy,
            state=state(),
            valid_characters={"Mara"},
            valid_threads={"palace"},
            user_characters={"Alex"},
        )
    assert exc.value.category == "policy"


@pytest.mark.parametrize("change", [{"schema_version": True}, {"expected_revision": 4.0}, {"direction": "x" * 4001}])
def test_directly_constructed_proposals_cannot_bypass_schema_validation(change):
    proposal = replace(parse_director_proposal(payload()), **change)
    with pytest.raises(DirectorProposalError):
        validate(proposal)
