"""Commands and callbacks share canonical domain senders and explicit delivery."""

import importlib
import inspect
from types import SimpleNamespace

import pytest
from application_test_setup import make_test_delivery_port
from source_test_support import top_level_functions

from bridge import feature_callbacks, panel_callback_routes

CASES = (
    ("scene_state", "send_scene_menu", "scene:clear_confirm"),
    ("director_goals", "send_director_goal_menu", "goal:clear"),
    ("memory_curator", "send_curated_memory_menu", "curated:refresh"),
)


@pytest.mark.parametrize("module_name,sender,_action", CASES)
def test_panel_has_one_domain_owner(module_name, sender, _action):
    owner = importlib.import_module("bridge." + module_name)
    assert sender not in top_level_functions("feature_panels.py")
    assert getattr(feature_callbacks, sender) is getattr(owner, sender)


@pytest.mark.parametrize("name", ("handle_prompt_and_feature_callback", "handle_feature_panel_callback"))
def test_feature_callbacks_require_delivery_port(name):
    parameter = inspect.signature(getattr(feature_callbacks, name)).parameters.get("delivery_port")
    assert parameter is not None and parameter.default is inspect.Parameter.empty


@pytest.mark.parametrize("module_name,sender,action", CASES)
def test_command_and_callback_deliver_same_domain_panel(monkeypatch, module_name, sender, action):
    owner = importlib.import_module("bridge." + module_name)
    db = object()
    session = {"session_id": "session", "character_file": "Alice.png"}
    context = SimpleNamespace(app_settings=SimpleNamespace())
    calls = []
    delivery = make_test_delivery_port(
        send_panel_request=lambda token, method, payload, **kw: calls.append((token, method, payload, kw)) or {},
    )
    monkeypatch.setattr(feature_callbacks, "card_fields_from_file", lambda *a, **kw: {"name": "Alice"})
    monkeypatch.setattr(feature_callbacks, "send_typing", lambda *a: None)
    monkeypatch.setattr(feature_callbacks, "clear_scene_state", lambda *a: None)
    monkeypatch.setattr(feature_callbacks, "set_director_goal", lambda *a: None)
    monkeypatch.setattr(feature_callbacks, "curate_memory_now", lambda *a, **kw: [])
    monkeypatch.setattr(feature_callbacks, "memory_mode", lambda *a: "on")
    if module_name == "scene_state":
        monkeypatch.setattr(owner, "get_scene_state", lambda *a: ({}, 17))
        owner.handle_scene_command(
            db,
            "token",
            "",
            "chat",
            session,
            {},
            "/scene",
            provider_port=object(),
            delivery_port=delivery,
            request_context=context,
        )
    elif module_name == "director_goals":
        monkeypatch.setattr(owner, "get_director_goal", lambda *a: "Protect the witness")
        monkeypatch.setattr(owner, "parse_topic_scope", lambda *a: ("chat", 1))
        owner.handle_director_goal_command(
            db,
            "token",
            "chat",
            session,
            "/group goal",
            delivery_port=delivery,
            request_context=context,
        )
    else:
        monkeypatch.setattr(owner, "curated_memory_text", lambda *a: "- [fact] The door is locked.")
        owner.handle_curated_memory_command(
            db,
            "token",
            "",
            "chat",
            session,
            {},
            "/memory curated",
            provider_port=object(),
            delivery_port=delivery,
            request_context=context,
        )
    command_call = calls.pop()
    message = {"message_id": 91}
    assert panel_callback_routes.handle_primary_panel_callback(
        db,
        "token",
        {"id": "callback", "message": message},
        lambda *a: None,
        action,
        "chat",
        message,
        session,
        "session",
        52,
        group_service=object(),
        provider_port=object(),
        delivery_port=delivery,
        memory_service=object(),
        npc_service=object(),
        persona_service=object(),
        sync_service=object(),
        request_context=context,
    )
    assert len(calls) == 1
    callback_call = calls[0]
    assert command_call[1] == "sendMessage"
    assert callback_call[1] == "editMessageText"
    assert callback_call[2] == {**command_call[2], "message_id": 91}
    assert callback_call[3]["request_context"] is context
    assert command_call[3]["request_context"] is context
