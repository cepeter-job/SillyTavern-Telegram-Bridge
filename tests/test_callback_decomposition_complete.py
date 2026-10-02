"""Final issue-314 guard: scoped routing stays small without skipping workflow checks."""

import ast
from pathlib import Path
from types import SimpleNamespace

import pytest

from bridge import conversation_callbacks, npc_callbacks, world_callbacks

ROOT = Path(__file__).parents[1]
AUDITED = {
    "character_callbacks.py": "handle_character_callback",
    "feature_callbacks.py": "handle_feature_panel_callback",
    "callback_dispatch.py": "process_callback",
    "enum_callbacks.py": "handle_enum_callback",
    "provider_callbacks.py": "handle_provider_model_callback",
    "persona_callbacks.py": "handle_persona_callback",
    "session_callbacks.py": "handle_session_callback",
    "npc_callbacks.py": "handle_npc_callback",
    "world_callbacks.py": "handle_world_callback",
    "conversation_callbacks.py": "handle_greeting_callback",
}


@pytest.mark.parametrize("filename,entry", AUDITED.items())
def test_every_audited_callback_dispatcher_remains_bounded(filename, entry):
    tree = ast.parse((ROOT / "bridge" / filename).read_text())
    fn = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == entry)
    assert fn.end_lineno - fn.lineno + 1 <= 95, entry


@pytest.mark.parametrize(
    "action,arity",
    (
        ("menu", None),
        ("page", 3),
        ("refresh", None),
        ("view", 3),
        ("history", 3),
        ("undo", 5),
        ("undo-confirm", 5),
        ("close", None),
    ),
)
def test_npc_action_routes_once_and_preserves_arity(monkeypatch, action, arity):
    name = "_npc_" + action.replace("-", "_")
    assert hasattr(npc_callbacks, name)
    calls = []
    feedback = []
    monkeypatch.setattr(npc_callbacks, name, lambda *a, **kw: calls.append(action) or True)
    tail = ":1" * ((arity or 2) - 2)
    args = (object(), "token", {}, lambda *a: feedback.append(a[-1]))
    kwargs = {"npc_service": object(), "provider_port": object(), "request_context": object()}
    assert npc_callbacks.handle_npc_callback(*args, "npc:" + action + tail, "chat", {}, {}, {"name": "Alice"}, **kwargs)
    assert calls == [action]
    if arity:
        calls.clear()
        assert npc_callbacks.handle_npc_callback(*args, "npc:" + action, "chat", {}, {}, {"name": "Alice"}, **kwargs)
        assert calls == [] and feedback[-1] == "NPC action invalid"


def test_world_prefix_routing_keeps_delete_confirmation_separate(monkeypatch):
    assert hasattr(world_callbacks, "_bind_routes")
    calls = []
    for name in ("delete_confirm", "delete", "world"):
        monkeypatch.setattr(
            world_callbacks, "_handle_" + name, lambda *a, _name=name, **kw: calls.append(_name) or True
        )
    for prefix, expected in (
        ("worlddeleteconfirm:", "delete_confirm"),
        ("worlddelete:", "delete"),
        ("world:", "world"),
    ):
        calls.clear()
        assert world_callbacks.handle_world_callback(
            object(),
            "token",
            {},
            lambda *a: None,
            prefix + "id",
            "chat",
            {},
            {},
            "session",
            7,
            group_service=object(),
            request_context=object(),
        )
        assert calls == [expected]


def greeting_arguments(monkeypatch):
    monkeypatch.setattr(conversation_callbacks, "card_fields_from_file", lambda *a, **kw: {})
    monkeypatch.setattr(conversation_callbacks, "greeting_options", lambda *a: ["Hello"])
    context = SimpleNamespace(app_settings=SimpleNamespace(default_user_name="User"))
    return (object(), "token", {}, lambda *a: None), {
        "persona_service": object(),
        "request_context": context,
    }


def test_greeting_rejection_never_enters_page_or_choice_action(monkeypatch):
    assert hasattr(conversation_callbacks, "_resolve_greeting_operation")
    monkeypatch.setattr(conversation_callbacks, "_resolve_greeting_operation", lambda *a, **kw: (False, 7))
    monkeypatch.setattr(
        conversation_callbacks, "_greeting_choice", lambda *a, **kw: pytest.fail("Rejected greeting reached action")
    )
    args, kwargs = greeting_arguments(monkeypatch)
    assert conversation_callbacks.handle_greeting_callback(
        *args,
        "greeting:use:0:1",
        "chat",
        {},
        {"character_file": "Alice.png"},
        "session",
        7,
        **kwargs,
    )


def test_greeting_action_receives_recovered_operation_identity(monkeypatch):
    assert hasattr(conversation_callbacks, "_resolve_greeting_operation")
    calls = []
    monkeypatch.setattr(
        conversation_callbacks, "_resolve_greeting_operation", lambda *a, **kw: (True, "recovered-original")
    )
    monkeypatch.setattr(conversation_callbacks, "_greeting_choice", lambda *a, **kw: calls.append((a, kw)) or True)
    args, kwargs = greeting_arguments(monkeypatch)
    assert conversation_callbacks.handle_greeting_callback(
        *args,
        "greeting:use:0:1",
        "chat",
        {},
        {"character_file": "Alice.png"},
        "session",
        7,
        **kwargs,
    )
    assert len(calls) == 1
    assert "recovered-original" in calls[0][0] or "recovered-original" in calls[0][1].values()
