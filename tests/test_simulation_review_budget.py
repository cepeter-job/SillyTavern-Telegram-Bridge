"""Actual planning and separate-choice routes compact only optional tracker text."""

import json
from dataclasses import replace

import pytest
from test_director_service import directed as directed
from test_director_service import reassess, response
from test_light_novel_generation import story_row
from test_light_novel_storage import novel_db as novel_db
from test_memory_completion_safety import session_db as session_db

from bridge.context_compaction import context_profile, estimate_message_tokens
from bridge.director_prompt import DIRECTOR_INSTRUCTION, build_director_input
from bridge.director_repository import load_director_state
from bridge.narrative_context import load_narrative_state
from bridge.narrative_repository import load_narrative_clock
from bridge.narrative_settings import load_session_narrative_settings
from bridge.provider_port import ProviderPort

TRACKERS = "TRACKER numerical state. " * 250


@pytest.mark.parametrize("fixed_overflow", [False, True])
def test_director_compacts_trackers_and_preserves_required_revision_context(directed, monkeypatch, fixed_overflow):
    settings, db, session = directed
    monkeypatch.setattr("bridge.director_prompt.simulation_context_for_prompt", lambda *a, **k: TRACKERS)
    data, *_ = build_director_input(
        db,
        "chat",
        session,
        load_narrative_state(db, "chat", "s1"),
        load_session_narrative_settings(db, "chat", "s1"),
        load_director_state(db, "chat", "s1"),
        app_settings=settings,
        valid_characters={"Mara", "Governor"},
        user_characters={"Alex"},
    )
    data.pop("simulation_state")
    data["reassessment_reason"] = "manual"
    core = [
        {"role": "system", "content": DIRECTOR_INSTRUCTION},
        {"role": "user", "content": json.dumps(data, ensure_ascii=False)},
    ]
    settings = replace(settings, context_window_tokens=estimate_message_tokens(core) + 256 + 2000 + 512)
    if fixed_overflow:
        session = session | {"system_prompt": "Required system instruction. " * 150}
    before = load_narrative_clock(db, "chat", "s1")
    calls = []

    def generate(_key, model, messages, **kwargs):
        calls.append(1)
        assert not fixed_overflow, "Fixed context overflow must stop before dispatch"
        profile = context_profile(model, app_settings=settings, requested_output_tokens=2000)
        assert estimate_message_tokens(messages) <= profile.input_budget_tokens
        assert messages[0]["content"] == DIRECTOR_INSTRUCTION
        restored, end = json.JSONDecoder().raw_decode(messages[-1]["content"])
        assert restored == data
        assert len(messages[-1]["content"][end:]) < len(TRACKERS)
        assert all("_context_optional" not in message for message in messages)
        return response(db)

    result = reassess((settings, db, session), generate)
    assert result.result == ("rejected" if fixed_overflow else "accepted")
    assert len(calls) == (0 if fixed_overflow else 1)
    assert load_narrative_clock(db, "chat", "s1") == before


@pytest.mark.parametrize("strategy,expected", [("a", "story::test"), ("b", "utility::test"), ("c", "story::test")])
def test_separate_choices_compact_trackers_with_the_selected_model_budget(novel_db, monkeypatch, strategy, expected):
    from bridge.conversation_lifecycle import configure_conversation, conversation_state, mark_started
    from bridge.light_novel_service import attach_turn, ensure_choices, prepare_turn
    from bridge.model_selection import set_task_model

    db, session, settings = novel_db
    settings = replace(settings, context_window_tokens=3600)
    session = session | {"system_prompt": "FIXED_SENTINEL " * 90}
    configure_conversation(db, "chat", "story", "lightnovel", strategy)
    mark_started(db, "chat", "story", conversation_state(db, "chat", "story").epoch)
    set_task_model(db, "chat", "story", "utility::test")
    record = prepare_turn(db, "chat", session, "opening:1", "owner", rng=lambda _: 3)
    attach_turn(db, record, story_row(db), "The door opens.")
    monkeypatch.setattr("bridge.light_novel_service.story_simulation_context", lambda *a, **k: TRACKERS)
    calls = []

    def generate(_key, model, messages, **kwargs):
        calls.append(1)
        assert not db.in_transaction and model == expected
        profile = context_profile(model, app_settings=settings, requested_output_tokens=1200)
        assert estimate_message_tokens(messages) <= profile.input_budget_tokens
        assert "Generate exactly 3 distinct next choices" in messages[0]["content"]
        assert "Return only JSON" in messages[0]["content"]
        fixed, end = json.JSONDecoder().raw_decode(messages[-1]["content"])
        assert fixed["current_story"] == "The door opens."
        assert "FIXED_SENTINEL" in fixed["system_prompt"]
        assert "narrative_policy" in fixed and "user_persona" in fixed
        assert "simulation_state" not in fixed
        assert len(messages[-1]["content"][end:]) < len(TRACKERS)
        assert all("_context_optional" not in message for message in messages)
        return json.dumps({"choices": ["Go inside", "Wait outside", "Look around"]})

    result = ensure_choices(
        db, record.nonce, session, {"name": "Alice"}, provider_port=ProviderPort(generate), app_settings=settings
    )
    assert calls == [1] and result.generation_status == "ready"
