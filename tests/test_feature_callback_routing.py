"""Bounded feature routing preserves common authorization and route ordering."""

import ast
from pathlib import Path
from types import SimpleNamespace

import pytest

from bridge import callback_dispatch, feature_callbacks

ROOT = Path(__file__).parents[1]


@pytest.mark.parametrize(
    "filename,name",
    (("feature_callbacks.py", "handle_feature_panel_callback"), ("callback_dispatch.py", "process_callback")),
)
def test_feature_and_common_dispatchers_are_bounded(filename, name):
    tree = ast.parse((ROOT / "bridge" / filename).read_text())
    function = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == name)
    assert function.end_lineno - function.lineno + 1 <= 95


@pytest.mark.parametrize("family", ("summary", "imagine", "scene", "goal", "curated"))
def test_feature_prefix_dispatches_exactly_one_owner(monkeypatch, family):
    handler_name = "_handle_" + family
    assert hasattr(feature_callbacks, handler_name)
    calls = []
    for name in ("summary", "imagine", "scene", "goal", "curated"):
        monkeypatch.setattr(
            feature_callbacks, "_handle_" + name, lambda *a, _name=name, **kw: calls.append(_name) or True
        )
    assert feature_callbacks.handle_feature_panel_callback(
        object(),
        "token",
        {},
        lambda *a: None,
        family + ":action",
        "chat",
        {},
        {},
        "session",
        1,
        group_service=object(),
        provider_port=object(),
        delivery_port=object(),
        request_context=object(),
    )
    assert calls == [family]


def test_unknown_feature_has_no_side_effects():
    assert not feature_callbacks.handle_feature_panel_callback(
        object(),
        "token",
        {},
        lambda *a: None,
        "unrelated:action",
        "chat",
        {},
        {},
        "session",
        1,
        group_service=object(),
        provider_port=object(),
        delivery_port=object(),
        request_context=object(),
    )


@pytest.mark.parametrize("queued", (False, True))
def test_foreign_panel_owner_never_reaches_routing(monkeypatch, queued):
    assert hasattr(callback_dispatch, "_route_callback")
    calls = []
    monkeypatch.setattr(
        callback_dispatch, "_route_callback", lambda *a, **kw: pytest.fail("foreign owner reached routing")
    )
    monkeypatch.setattr(callback_dispatch, "panel_owner_for_message", lambda *a: "owner")
    monkeypatch.setattr(callback_dispatch, "panel_session_for_message", lambda *a: "session")
    monkeypatch.setattr(callback_dispatch, "send_text", lambda *a: calls.append(a[-1]))
    monkeypatch.setattr(callback_dispatch, "answer_callback", lambda *a: calls.append(a[-1]))
    callback_dispatch.process_callback(
        object(),
        "token",
        {
            "id": "cb",
            "from": {"id": "other"},
            "data": "scene:refresh",
            "_queued": queued,
            "message": {"message_id": 91, "chat": {"id": "chat"}},
        },
        services=object(),
    )
    assert calls == ["This panel belongs to another user"]


def test_common_route_order_is_unchanged(monkeypatch):
    assert hasattr(callback_dispatch, "_prepare_callback_scope")
    session = {"session_id": "session"}
    context = SimpleNamespace(session_id="session", actor_id="owner", app_settings=object())
    monkeypatch.setattr(
        callback_dispatch,
        "_prepare_callback_scope",
        lambda *a, **kw: (lambda *a: None, "unknown:action", "chat", {}, session, context),
    )
    calls = []
    for name in (
        "handle_setup_callback",
        "handle_light_novel_mode_callback",
        "handle_primary_panel_callback",
        "handle_entity_panel_callback",
        "handle_provider_model_callback",
    ):
        monkeypatch.setattr(callback_dispatch, name, lambda *a, _name=name, **kw: calls.append(_name) or False)
    services = SimpleNamespace(
        group=object(),
        provider=SimpleNamespace(policy=object()),
        delivery=object(),
        memory=object(),
        npc=object(),
        persona=object(),
        sync=object(),
        provider_probes=None,
    )
    callback_dispatch.process_callback(object(), "token", {}, services=services)
    assert calls == [
        "handle_setup_callback",
        "handle_light_novel_mode_callback",
        "handle_primary_panel_callback",
        "handle_entity_panel_callback",
        "handle_provider_model_callback",
    ]
