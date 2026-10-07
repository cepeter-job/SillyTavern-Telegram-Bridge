"""Automatic mechanics must propose stakes, never manufacture dice or facts."""

import json

import pytest


def proposal(**changes):
    value = {
        "schema_version": 1,
        "decision": "check",
        "domain": "stealth",
        "focus": "quietly open the door",
        "dc": 13,
        "reason": "A nearby guard could hear the latch.",
        "success": "The attempted opening is quiet.",
        "failure": "The latch makes an audible noise.",
        "evidence": [{"source": "17", "quote": "A guard waits beside the noisy door."}],
    }
    return json.dumps(value | changes)


def parse(raw):
    from bridge.action_contracts import parse_action_proposal

    return parse_action_proposal(
        raw, {"17": "A guard waits beside the noisy door.", "action": "I quietly open the door."}
    )


def test_meaningful_action_proposal_keeps_dice_bridge_owned():
    value = parse(proposal())
    assert value["decision"] == "check"
    assert value["dc"] == 13
    assert "roll" not in value and "modifier" not in value


@pytest.mark.parametrize("field", ["roll", "modifier", "outcome", "hidden_adjustment"])
def test_model_cannot_supply_mechanical_result(field):
    with pytest.raises(ValueError):
        parse(proposal(**{field: 20}))


@pytest.mark.parametrize("dc", [True, 0, 21, "13", 13.5, None])
def test_difficulty_requires_bounded_integer(dc):
    with pytest.raises(ValueError):
        parse(proposal(dc=dc))


@pytest.mark.parametrize("decision", ["no_check", "auto_success"])
def test_routine_decision_does_not_require_a_roll(decision):
    value = parse(json.dumps({"schema_version": 1, "decision": decision}))
    assert value["decision"] == decision
    assert "dc" not in value


def test_unproven_hidden_opposition_cannot_authorize_a_check():
    with pytest.raises(ValueError):
        parse(proposal(evidence=[{"source": "director-plan", "quote": "A secret guard will appear."}]))
    with pytest.raises(ValueError):
        parse(proposal(evidence=[{"source": "17", "quote": "A trap is armed."}]))
