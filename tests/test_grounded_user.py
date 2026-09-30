from __future__ import annotations

import pytest
from application_test_setup import make_test_input_flow_service, make_test_rag_service

from bridge.callbacks import is_session_scoped_panel_callback
from bridge.generation import build_chat_messages
from bridge.request_types import RequestContext
from bridge.session_core import ensure_session, load_session, update_session
from bridge.settings import load_app_settings
from bridge.sqlite_store import db_connect


class _PersonaService:
    def name(self, persona_id: str) -> str:
        return "Arin" if persona_id else "User"

    def get(self, persona_id: str):
        if not persona_id:
            return None
        return {
            "name": "Arin",
            "description": "Crown prince of Lysa and an explicitly gifted fire mage.",
        }


def _fields() -> dict[str, str]:
    return {
        "name": "Mira",
        "description": "A skeptical palace guard.",
        "personality": "Observant and independent.",
        "scenario": "A tense royal court.",
        "first_mes": "",
        "mes_example": "",
        "system_prompt": "",
        "post_history_instructions": "",
    }


def _session(*, grounded: str) -> dict[str, str]:
    return {
        "session_id": "s",
        "model_id": "fixture::model",
        "persona_id": "arin.png",
        "system_prompt": "",
        "author_note": "",
        "world_file": "",
        "response_language": "auto",
        "grounded_user": grounded,
    }


def test_grounded_user_setting_normalizes_aliases_and_rejects_unknown_values():
    from bridge import grounded_user_settings as grounded

    assert grounded.normalize_grounded_user("ON") == "on"
    assert grounded.normalize_grounded_user(" true ") == "on"
    assert grounded.normalize_grounded_user("disabled") == "off"
    assert grounded.grounded_user_enabled("on")
    assert not grounded.grounded_user_enabled(None)
    assert grounded.grounded_user_label("on") == "On"
    assert grounded.grounded_user_label(None) == "Off"
    with pytest.raises(ValueError):
        grounded.normalize_grounded_user("sometimes")


def test_grounded_user_policy_is_injected_without_erasing_explicit_persona_advantages(tmp_path):
    settings = load_app_settings({}, home=tmp_path)

    messages = build_chat_messages(
        _session(grounded="on"),
        _fields(),
        "I address the council.",
        [],
        persona_service=_PersonaService(),
        app_settings=settings,
    )

    system = str(messages[0]["content"])
    assert "## Grounded User Policy" in system
    assert "Do not grant the user unearned" in system
    assert "NPCs retain independent goals, opinions, loyalties, and preferences." in system
    assert "Respect explicit established advantages, status, abilities, and relationships." in system
    assert "Do not force failure, humiliation, weakness, or punishment merely to oppose the user." in system
    assert system.index("## User Persona") < system.index("## Grounded User Policy")
    assert "Crown prince of Lysa and an explicitly gifted fire mage." in system


def test_grounded_user_policy_is_absent_when_disabled(tmp_path):
    settings = load_app_settings({}, home=tmp_path)

    messages = build_chat_messages(
        _session(grounded="off"),
        _fields(),
        "I address the council.",
        [],
        persona_service=_PersonaService(),
        app_settings=settings,
    )

    assert "## Grounded User Policy" not in str(messages[0]["content"])


@pytest.fixture
def context(tmp_path):
    settings = load_app_settings({}, home=tmp_path)
    db = db_connect(app_settings=settings)
    session = ensure_session(db, "chat", "fixture::model", app_settings=settings)
    try:
        yield db, session, RequestContext(db, session["session_id"], "actor", app_settings=settings)
    finally:
        db.close()


def test_grounded_user_uses_session_metadata_without_schema_change(context):
    db, session, ctx = context
    assert "grounded_user" not in {row[1] for row in db.execute("PRAGMA table_info(sessions)")}
    assert session["grounded_user"] == "off"

    update_session(db, "chat", session["session_id"], grounded_user="on")
    reloaded = load_session(db, "chat", session["session_id"], "fixture::model", app_settings=ctx.app_settings)

    assert reloaded["grounded_user"] == "on"


def test_grounded_user_callback_is_session_scoped():
    assert is_session_scoped_panel_callback("enum:grounded:toggle")


def test_settings_panel_exposes_single_grounded_user_toggle(context, monkeypatch):
    from bridge import cards, settings_panels

    db, session, ctx = context
    calls = []
    monkeypatch.setattr(
        cards,
        "send_panel_request",
        lambda _token, _method, payload, **_kwargs: calls.append(payload) or {},
    )

    settings_panels.send_settings_menu("token", "chat", db, session["session_id"], request_context=ctx)
    buttons = [
        button
        for row in calls[-1]["reply_markup"]["inline_keyboard"]
        for button in row
        if str(button.get("callback_data", "")).startswith("enum:grounded")
    ]
    assert buttons == [{"text": "Grounded User: OFF", "callback_data": "enum:grounded:toggle"}]

    update_session(db, "chat", session["session_id"], grounded_user="on")
    calls.clear()
    settings_panels.send_settings_menu("token", "chat", db, session["session_id"], request_context=ctx)
    buttons = [
        button
        for row in calls[-1]["reply_markup"]["inline_keyboard"]
        for button in row
        if str(button.get("callback_data", "")).startswith("enum:grounded")
    ]
    assert buttons == [{"text": "Grounded User: ON", "callback_data": "enum:grounded:toggle"}]


def test_grounded_user_toggle_and_settings_reset(context, monkeypatch):
    from bridge import enum_callbacks

    db, session, ctx = context
    monkeypatch.setattr(enum_callbacks, "send_settings_menu", lambda *a, **k: None)
    input_flow = make_test_input_flow_service(app_settings=ctx.app_settings)
    rag = make_test_rag_service()

    enum_callbacks.handle_enum_callback(
        db,
        "token",
        "chat",
        session,
        "enum:grounded:toggle",
        {},
        input_flow_service=input_flow,
        request_context=ctx,
        rag_service=rag,
    )
    assert (
        load_session(db, "chat", session["session_id"], "fixture::model", app_settings=ctx.app_settings)[
            "grounded_user"
        ]
        == "on"
    )

    enum_callbacks.handle_enum_callback(
        db,
        "token",
        "chat",
        session,
        "enum:settings:reset",
        {},
        input_flow_service=input_flow,
        request_context=ctx,
        rag_service=rag,
    )
    assert (
        load_session(db, "chat", session["session_id"], "fixture::model", app_settings=ctx.app_settings)[
            "grounded_user"
        ]
        == "off"
    )



def test_session_deletion_removes_grounded_user_metadata(context):
    from bridge.grounded_user_settings import grounded_user_key
    from bridge.metadata import get_meta
    from bridge.session_core import create_session, delete_session_data

    db, active, ctx = context
    target = create_session(
        db,
        "chat",
        "fixture::model",
        session_id="grounded-delete",
        app_settings=ctx.app_settings,
    )
    update_session(db, "chat", target["session_id"], grounded_user="on")
    assert get_meta(db, grounded_user_key("chat", target["session_id"]), "") == "on"

    class _Memory:
        def purge_session(self, *_args, **_kwargs):
            return 0

    deleted, reason = delete_session_data(
        db,
        "chat",
        target["session_id"],
        active["session_id"],
        memory_service=_Memory(),
    )

    assert deleted, reason
    assert get_meta(db, grounded_user_key("chat", target["session_id"]), "") == ""
