"""Group speaker selection consumes canonical plans without its own model call."""

from dataclasses import fields, replace
from pathlib import Path

import pytest

from bridge.group_director_service import DirectorCustomization, GroupDirectorService


@pytest.fixture
def group():
    state = {
        "enabled": True,
        "mode": "director",
        "turn_index": 0,
        "forced_speaker": "",
        "members": ["alice.png", "bob.png"],
    }
    service = GroupDirectorService(
        load_group_state=lambda *_: dict(state),
        safe_character=lambda name: name in {"alice.png", "bob.png"},
        member_labels=lambda names: [Path(name).stem.title() for name in names],
        card_fields=lambda name: {"name": Path(name).stem.title()},
        director_policy=lambda *_: DirectorCustomization(speaker="Bob", direction="Watch the gate."),
    )
    return state, service


def test_group_has_no_provider_model_or_token_budget_dependencies():
    names = {item.name for item in fields(GroupDirectorService)}
    assert not {"generate_text", "generation_settings", "default_model"} & names
    assert not {"model", "max_tokens", "hidden_instructions"} & {item.name for item in fields(DirectorCustomization)}


@pytest.mark.parametrize("speaker", ["Bob", "bob", "bob.png", "BOB.PNG"])
def test_canonical_speaker_overrides_round_robin_without_transcript_or_provider(group, speaker):
    state, service = group
    service = replace(
        service, director_policy=lambda *_: DirectorCustomization(speaker=speaker, direction="Watch gate.")
    )
    # No SQLite/transport capability is passed: selecting the accepted plan is pure orchestration.
    assert service.plan(object(), "chat", {"session_id": "s1"}) == ("bob.png", state, "Watch gate.")


@pytest.mark.parametrize(
    "custom",
    [
        None,
        "invalid",
        DirectorCustomization(speaker="unknown"),
        DirectorCustomization(speaker="user"),
        DirectorCustomization(viewpoint="off-screen NPC"),
    ],
)
def test_missing_or_unknown_canonical_choice_falls_back_safely(group, custom):
    state, service = group
    service = replace(service, director_policy=lambda *_: custom)
    assert service.plan(object(), "chat", {"session_id": "s1"}) == ("alice.png", state, "")


def test_canonical_viewpoint_can_select_configured_member(group):
    state, service = group
    service = replace(
        service, director_policy=lambda *_: DirectorCustomization(viewpoint="Bob", direction="Follow Bob.")
    )
    assert service.plan(object(), "chat", {"session_id": "s1"}) == ("bob.png", state, "Follow Bob.")


def test_ambiguous_alias_is_not_guessed(group):
    state, service = group
    service = replace(
        service,
        card_fields=lambda _: {"name": "Same"},
        director_policy=lambda *_: DirectorCustomization(speaker="Same"),
    )
    assert service.plan(object(), "chat", {"session_id": "s1"}) == ("alice.png", state, "")


def test_forced_member_is_explicit_and_never_calls_policy(group):
    state, service = group
    state["forced_speaker"] = "alice.png"
    service = replace(service, director_policy=lambda *_: pytest.fail("forced member consulted Director"))
    assert service.plan(object(), "chat", {"session_id": "s1"}) == ("alice.png", state, "")


@pytest.mark.parametrize(
    "change",
    [{"enabled": False}, {"mode": "round_robin"}, {"members": ["alice.png"]}, {"members": ["missing.png", "bob.png"]}],
)
def test_non_director_or_invalid_group_is_not_planned(group, change):
    state, service = group
    state.update(change)
    assert service.plan(object(), "chat", {"session_id": "s1"}) is None


def test_policy_failure_continues_group_without_leaking_error_details(group, caplog):
    state, service = group

    def fail(*_):
        raise RuntimeError("PRIVATE provider detail")

    service = replace(service, director_policy=fail)
    assert service.plan(object(), "chat", {"session_id": "s1"}) == ("alice.png", state, "")
    assert "PRIVATE" not in caplog.text


def test_plan_direction_and_context_are_bounded(group):
    _, service = group
    service = replace(
        service,
        director_policy=lambda *_: DirectorCustomization(
            speaker="Bob", direction="x" * 10000, speaker_context="y" * 10000, narrative_context="z" * 10000
        ),
    )
    plan = service.plan(object(), "chat", {"session_id": "s1"})
    assert len(plan[2]) <= 500
    context = service.prompt_context(object(), "chat", {"session_id": "s1"}, "bob.png", plan[2])
    assert len(context) < 11000


def test_prompt_does_not_assume_all_configured_characters_are_present(group):
    _, service = group
    context = service.prompt_context(object(), "chat", {"session_id": "s1"}, "alice.png", "Follow distant Bob.")
    assert "Other characters present" not in context
    assert "configured" in context.lower()
    assert "not assume" in context.lower()
    assert "Follow distant Bob" in context
    assert "Do not speak for the user" in context


def test_grounded_user_policy_still_applies(group):
    _, service = group
    context = service.prompt_context(object(), "chat", {"session_id": "s1", "grounded_user": "on"}, "alice.png")
    assert "Do not select a speaker merely to make the user the center of attention" in context


def test_autonomous_mode_remains_bounded(group):
    state, service = group
    state["mode"] = "autonomous"
    context = service.prompt_context(object(), "chat", {"session_id": "s1"}, "alice.png")
    assert "up to 3" in context
    assert "Do not speak for the user" in context
