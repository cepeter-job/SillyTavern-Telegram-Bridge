"""Canonical group integration preserves source facts and World-driven camera freedom."""

import json
from dataclasses import fields
from pathlib import Path

import pytest
from test_memory_completion_safety import session_db as session_db
from test_narrative_reconciliation import add_story, run_reconciliation

from bridge.director_goals import director_goal_policy, set_director_goal
from bridge.director_room import apply_direction, director_room
from bridge.director_service import DirectorService
from bridge.group_director_service import GroupDirectorService
from bridge.narrative_settings import preset_narrative_settings, save_session_narrative_settings
from bridge.provider_port import ProviderPort


def service(_directed):
    group = {"enabled": True, "mode": "director", "turn_index": 1, "members": ["Mara.png", "Boris.png"]}
    return GroupDirectorService(
        load_group_state=lambda *_: group,
        safe_character=Path,
        member_labels=lambda names: [Path(name).stem for name in names],
        card_fields=lambda name: {"name": Path(name).stem},
        director_policy=director_goal_policy,
    )


@pytest.fixture
def directed(session_db):
    _, db, _ = session_db
    save_session_narrative_settings(db, "chat", "s1", preset_narrative_settings("world_driven"))
    add_story(db)
    run_reconciliation(session_db)
    return session_db


def test_current_canonical_direction_selects_viewpoint_without_a_second_call(directed):
    _settings, db, session = directed
    clock = director_room(db, "chat", "s1")
    result = apply_direction(db, "chat", "s1", clock["revision"], "Stay with Mara at the gate.", "next_scene")
    assert result.result == "accepted"
    before = db.execute("SELECT * FROM messages").fetchall()
    plan = service(directed).plan(db, "chat", session)
    assert plan[0] == "Mara.png"
    assert "Mara" in plan[2]
    assert db.execute("SELECT * FROM messages").fetchall() == before
    assert "generate_text" not in {item.name for item in fields(GroupDirectorService)}


def test_stale_plan_does_not_revive_through_group_adapter(directed):
    _, db, session = directed
    revision = director_room(db, "chat", "s1")["revision"]
    apply_direction(db, "chat", "s1", revision, "Stay with Mara.", "next_scene")
    add_story(db)
    plan = service(directed).plan(db, "chat", session)
    assert plan[0] == "Boris.png" and plan[2] == ""


def test_group_goal_and_room_are_the_same_objective_with_one_history(directed):
    _, db, session = directed
    set_director_goal(db, "chat", "s1", "  Protect   the archive. ")
    view = director_room(db, "chat", "s1")
    assert view["objective"] == "Protect the archive."
    assert view["history"][0]["source"] == "user"
    apply_direction(db, "chat", "s1", view["revision"], "Keep the gate open.", "persistent")
    customization = director_goal_policy(db, "chat", session)
    assert "Keep the gate open." in customization.speaker_context
    assert "no forced return to the user" in customization.narrative_context
    assert "not to recenter the user" in customization.narrative_context


def test_director_ai_plan_is_consumed_by_group_without_extra_model_call(directed):
    settings, db, session = directed
    from bridge.narrative_context import load_narrative_state

    state = load_narrative_state(db, "chat", "s1")
    calls = []
    proposal = {
        "schema_version": 1,
        "action": "continue",
        "expected_revision": state.state_revision,
        "speaker": "Boris",
        "direction": "Let Mara inspect the gate.",
    }
    provider = ProviderPort(lambda *a, **k: calls.append(k) or json.dumps(proposal))
    result = DirectorService().reassess(
        db,
        "",
        "chat",
        session,
        provider_port=provider,
        app_settings=settings,
        reason="manual",
        valid_characters={"Mara", "Boris"},
    )
    assert result.result == "accepted"
    assert service(directed).plan(db, "chat", session)[0] == "Boris.png"
    assert len(calls) == 1


def test_group_adapters_do_not_reintroduce_a_second_director_owner():
    root = Path(__file__).parents[1] / "bridge"
    source = "\n".join(
        (root / filename).read_text()
        for filename in ("group_callbacks.py", "group_commands.py", "group_panels.py", "group_setup.py")
    )
    for obsolete in ("_compat_group_director_service", "group_director_plan", "parse_group_director_decision"):
        assert obsolete not in source
    source = (root / "group_director_service.py").read_text()
    assert "generate_text" not in source
    assert "_parse_decision" not in source
