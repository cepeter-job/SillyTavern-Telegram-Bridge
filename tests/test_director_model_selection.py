"""Independent Director routing and reasoning through real provider controls."""

import json

import pytest
from application_test_setup import make_test_request_context
from settings_test_support import make_test_settings

from bridge import cards, model_selection, provider_callbacks, provider_panels, settings_input
from bridge.callback_tokens import dynamic_callback_token
from bridge.generation_settings import get_generation_settings, update_generation_settings
from bridge.metadata import get_meta, set_meta
from bridge.session_core import create_session
from bridge.sqlite_store import db_connect


@pytest.fixture
def setup(tmp_path):
    config = make_test_settings(home=tmp_path)
    db = db_connect(tmp_path / "director.sqlite3", app_settings=config)
    session = create_session(db, "chat", "story::main", session_id="story", app_settings=config)
    context = make_test_request_context(db, "story", "owner", app_settings=config)
    try:
        yield config, db, session, context
    finally:
        db.close()


def reasoning_api():
    setter = getattr(model_selection, "set_director_reasoning", None)
    getter = getattr(model_selection, "director_reasoning_for_session", None)
    assert callable(setter) and callable(getter), "Director reasoning API is missing"
    return setter, getter


def test_director_target_and_model_inheritance_are_independent(setup):
    config, db, session, _context = setup
    model_selection.set_model_target_selection(db, "chat", "story", "director")
    assert model_selection.get_model_target_selection(db, "chat", "story") == "director"
    assert model_selection.task_model_for_session(db, "chat", session, "director", app_settings=config) == "story::main"
    model_selection.set_task_model(db, "chat", "story", "utility::worker")
    assert (
        model_selection.task_model_for_session(db, "chat", session, "director", app_settings=config)
        == "utility::worker"
    )
    model_selection.set_task_model(db, "chat", "story", "director::planner", "director")
    assert (
        model_selection.task_model_for_session(db, "chat", session, "director", app_settings=config)
        == "director::planner"
    )
    assert (
        model_selection.task_model_for_session(db, "chat", session, "utility", app_settings=config) == "utility::worker"
    )
    assert session["model_id"] == "story::main"
    model_selection.set_task_model(db, "chat", "story", "inherit", "director")
    assert (
        model_selection.task_model_for_session(db, "chat", session, "director", app_settings=config)
        == "utility::worker"
    )


@pytest.mark.parametrize("budget", [0, 1024, 32000])
def test_director_reasoning_does_not_change_other_models_or_sessions(setup, budget):
    _config, db, _session, _context = setup
    setter, getter = reasoning_api()
    model_selection.set_utility_reasoning(db, "chat", "story", 8192)
    update_generation_settings(db, "chat", "story", reasoning_budget=4096)
    assert setter(db, "chat", "story", budget) == budget
    assert getter(db, "chat", "story") == budget
    assert getter(db, "other", "story") == 0
    assert getter(db, "chat", "other") == 0
    assert model_selection.utility_reasoning_for_session(db, "chat", "story") == 8192
    assert get_generation_settings(db, "chat", "story")["reasoning_budget"] == 4096


@pytest.mark.parametrize("budget", [-1, 32001, True, 1.5])
def test_invalid_director_reasoning_never_writes(setup, budget):
    _config, db, _session, _context = setup
    setter, _getter = reasoning_api()
    before = db.total_changes
    with pytest.raises(ValueError):
        setter(db, "chat", "story", budget)
    assert db.total_changes == before


def test_target_panel_shows_three_routes_and_reasoning(setup, monkeypatch):
    _config, db, _session, context = setup
    setter, _getter = reasoning_api()
    setter(db, "chat", "story", 8192)
    model_selection.set_task_model(db, "chat", "story", "director::planner", "director")
    captured = []
    monkeypatch.setattr(cards, "send_panel_request", lambda _token, _method, payload, **kw: captured.append(payload))
    provider_panels.send_model_target_menu("token", "chat", "story::main", "utility::worker", request_context=context)
    payload = captured[0]
    assert "Story: story::main" in payload["text"]
    assert "Utility: utility::worker" in payload["text"]
    assert "Director: director::planner" in payload["text"]
    assert "Director reasoning: High (8192)" in payload["text"]
    rows = payload["reply_markup"]["inline_keyboard"]
    assert [[button["callback_data"] for button in row] for row in rows[:3]] == [
        ["modeltarget:story", "models:story-reasoning"],
        ["modeltarget:utility", "models:utility-reasoning"],
        ["modeltarget:director", "models:director-reasoning"],
    ]
    actions = {b["callback_data"] for row in rows for b in row}
    assert {"modeltarget:director", "models:director-reasoning"} <= actions
    quote = payload["text"].split("\n\nConfigure models:")[0]
    assert payload["entities"][0]["length"] == len(quote.encode("utf-16-le")) // 2


def test_selecting_director_model_updates_only_director(setup, monkeypatch):
    config, db, session, context = setup
    model_selection.set_model_target_selection(db, "chat", "story", "director")
    handle = dynamic_callback_token("model", "director::planner", "chat", db=db)
    monkeypatch.setattr(provider_callbacks, "send_text", lambda *a, **k: None)
    monkeypatch.setattr(provider_callbacks, "send_model_target_menu", lambda *a, **k: None)
    assert provider_callbacks.handle_provider_model_callback(
        db,
        "token",
        {"id": "cb"},
        lambda *a: None,
        "model:" + handle,
        "chat",
        {"message_id": 1},
        session,
        "story",
        None,
        request_context=context,
    )
    assert (
        model_selection.task_model_for_session(db, "chat", session, "director", app_settings=config)
        == "director::planner"
    )
    assert model_selection.task_model_for_session(db, "chat", session, "utility", app_settings=config) == "story::main"
    assert session["model_id"] == "story::main"


@pytest.mark.parametrize("target", ["utility", "director"])
def test_back_to_targets_does_not_label_task_model_as_story(setup, monkeypatch, target):
    _config, db, session, context = setup
    model_selection.set_model_target_selection(db, "chat", "story", target)
    model_selection.set_task_model(db, "chat", "story", "task::selected", target)
    shown = []
    monkeypatch.setattr(provider_callbacks, "send_model_target_menu", lambda *a, **k: shown.append(a))
    assert provider_callbacks.handle_provider_model_callback(
        db,
        "token",
        {"id": "cb"},
        lambda *a: None,
        "models:target",
        "chat",
        {"message_id": 1},
        session,
        "story",
        None,
        request_context=context,
    )
    assert shown[0][2] == "story::main"


def test_director_reasoning_callback_and_custom_input_share_route(setup, monkeypatch):
    _config, db, session, context = setup
    _setter, getter = reasoning_api()
    monkeypatch.setattr(cards, "send_panel_request", lambda *a, **k: None)
    monkeypatch.setattr(provider_callbacks, "remove_inline_keyboard", lambda *a, **k: None)
    monkeypatch.setattr(provider_callbacks, "send_text", lambda *a, **k: [7])

    def choose(action):
        return provider_callbacks.handle_provider_model_callback(
            db,
            "token",
            {"id": "cb"},
            lambda *a: None,
            action,
            "chat",
            {"message_id": 1},
            session,
            "story",
            None,
            request_context=context,
        )

    assert choose("directorreasoning:high")
    assert getter(db, "chat", "story") == 8192
    assert choose("directorreasoning:custom")
    state = json.loads(get_meta(db, "settings_input:chat", "{}"))
    assert state["scope"] == "director_reasoning"
    state["prompt_message_ids"] = []
    monkeypatch.setattr(settings_input, "send_model_target_menu", lambda *a, **k: None)
    assert settings_input._handle_settings_input(db, "token", "chat", "story", "5000", state, request_context=context)
    assert getter(db, "chat", "story") == 5000
    assert model_selection.utility_reasoning_for_session(db, "chat", "story") == 0
    assert get_generation_settings(db, "chat", "story")["reasoning_budget"] == 0


def test_corrupt_director_reasoning_uses_safe_zero(setup):
    _config, db, _session, _context = setup
    _setter, getter = reasoning_api()
    for raw in ("NaN", "-1", "32001", "1.5"):
        set_meta(db, "director_reasoning:chat:story", raw)
        assert getter(db, "chat", "story") == 0


def test_director_reasoning_buttons_are_session_scoped():
    from bridge.callbacks import is_session_scoped_panel_callback

    assert is_session_scoped_panel_callback("directorreasoning:high")
