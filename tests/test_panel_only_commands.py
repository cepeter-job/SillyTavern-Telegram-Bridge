"""Regression coverage for panel-only command surfaces."""

from types import SimpleNamespace

import pytest
from application_test_setup import make_test_delivery_port, make_test_provider_port
from test_memory_completion_safety import session_db as session_db


def _context(settings):
    return SimpleNamespace(app_settings=settings, actor_id="actor", session_id="s1")


def test_language_argument_opens_panel_without_direct_mutation(session_db, monkeypatch):
    import bridge.command_panels as panels

    settings, db, session = session_db
    opened = []
    monkeypatch.setattr(panels, "send_language_menu", lambda *a, **k: opened.append(a))
    assert not hasattr(panels, "handle_language_command")
    assert panels._handle_generation_panels(
        db,
        "token",
        {},
        "chat",
        "/language en",
        "/language",
        session,
        "s1",
        None,
        delivery_port=make_test_delivery_port(),
        provider_port=make_test_provider_port(),
        memory_service=object(),
        npc_service=object(),
        persona_service=object(),
        request_context=_context(settings),
        rag_service=object(),
    )
    assert opened


@pytest.mark.parametrize("command", ["/scene refresh", "/scene clear", "/scene status"])
def test_scene_aliases_only_render_panel(session_db, monkeypatch, command):
    import bridge.scene_state as scene

    settings, db, session = session_db
    opened = []
    monkeypatch.setattr(scene, "send_scene_menu", lambda *a, **k: opened.append(command))
    monkeypatch.setattr(scene, "clear_scene_state", lambda *a, **k: pytest.fail("scene clear must use panel"))
    monkeypatch.setattr(scene, "refresh_scene_state_now", lambda *a, **k: pytest.fail("scene refresh must use panel"))
    scene.handle_scene_command(
        db,
        "token",
        "",
        "chat",
        session,
        {},
        command,
        provider_port=make_test_provider_port(),
        delivery_port=make_test_delivery_port(),
        request_context=_context(settings),
    )
    assert opened == [command]


@pytest.mark.parametrize("command", ["/memory curated refresh", "/memory curated status"])
def test_curated_aliases_only_render_panel(session_db, monkeypatch, command):
    import bridge.memory_curator as curator

    settings, db, session = session_db
    opened = []
    monkeypatch.setattr(curator, "send_curated_memory_menu", lambda *a, **k: opened.append(command))
    monkeypatch.setattr(curator, "curate_memory_now", lambda *a, **k: pytest.fail("curation must use panel"))
    curator.handle_curated_memory_command(
        db,
        "token",
        "",
        "chat",
        session,
        {},
        command,
        provider_port=make_test_provider_port(),
        delivery_port=make_test_delivery_port(),
        request_context=_context(settings),
    )
    assert opened == [command]


@pytest.mark.parametrize("command", ["/group goal clear", "/group goal Protect the witness"])
def test_director_goal_aliases_only_render_panel(session_db, monkeypatch, command):
    import bridge.director_goals as goals

    settings, db, session = session_db
    opened = []
    monkeypatch.setattr(goals, "send_director_goal_menu", lambda *a, **k: opened.append(command))
    monkeypatch.setattr(goals, "set_director_goal", lambda *a, **k: pytest.fail("goal mutation must use panel"))
    goals.handle_director_goal_command(
        db,
        "token",
        "chat|topic:1",
        session,
        command,
        delivery_port=make_test_delivery_port(),
        request_context=_context(settings),
    )
    assert opened == [command]


def test_check_mode_alias_opens_panel_without_direct_mode_mutation(session_db, monkeypatch):
    import bridge.simulation_commands as checks

    settings, db, session = session_db
    opened = []
    monkeypatch.setattr(checks, "send_check_menu", lambda *a, **k: opened.append(True))
    assert not hasattr(checks, "set_action_mode")
    handled = checks._simulation_command_route(
        db,
        "token",
        "",
        "",
        {},
        "chat",
        "/check mode director",
        "/check",
        session,
        "s1",
        "",
        "",
        "",
        request_context=_context(settings),
        delivery_port=make_test_delivery_port(),
        provider_port=make_test_provider_port(),
    )
    assert handled and opened == [True]


def test_group_typed_alias_opens_panel_without_legacy_text_executor(session_db, monkeypatch):
    import bridge.command_panels as panels

    settings, db, session = session_db
    opened = []
    monkeypatch.setattr(panels, "send_group_menu", lambda *a, **k: opened.append(True))
    assert not hasattr(panels, "handle_group_command")
    assert panels._handle_memory_media(
        db,
        "token",
        "",
        "chat|topic:1",
        "/group mode director",
        "/group",
        session,
        {},
        None,
        request_context=_context(settings),
        delivery_port=make_test_delivery_port(),
        group_service=object(),
        memory_service=object(),
        npc_service=object(),
        persona_service=object(),
        provider_port=make_test_provider_port(),
        sync_service=object(),
        rag_service=object(),
    )
    assert opened == [True]


def test_databank_typed_alias_opens_panel_without_legacy_text_executor(session_db, monkeypatch):
    import bridge.command_panels as panels

    settings, db, session = session_db
    opened = []
    monkeypatch.setattr(panels, "send_databank_menu", lambda *a, **k: opened.append(True))
    assert not hasattr(panels, "handle_data_bank_command")
    assert panels._handle_memory_media(
        db,
        "token",
        "",
        "chat",
        "/databank reindex",
        "/databank",
        session,
        {},
        None,
        request_context=_context(settings),
        delivery_port=make_test_delivery_port(),
        group_service=object(),
        memory_service=object(),
        npc_service=object(),
        persona_service=object(),
        provider_port=make_test_provider_port(),
        sync_service=object(),
        rag_service=object(),
    )
    assert opened == [True]


def test_prompt_text_alias_opens_prompt_panel_without_plain_text_diagnostic(session_db, monkeypatch):
    import bridge.command_routes as routes

    settings, db, session = session_db
    opened = []
    monkeypatch.setattr(routes, "send_prompt_menu", lambda *a, **k: opened.append(True))
    assert not hasattr(routes, "prompt_diagnostics")
    monkeypatch.setattr(routes, "send_text", lambda *a, **k: pytest.fail("prompt inspector must stay panel-only"))
    assert routes._handle_basic(
        db,
        "token",
        "",
        "",
        {},
        "chat",
        "/prompt text",
        "/prompt",
        session,
        "s1",
        "",
        "",
        "",
        None,
        request_context=_context(settings),
        conversation_service=object(),
        delivery_port=make_test_delivery_port(),
        group_service=object(),
        memory_service=object(),
        provider_port=make_test_provider_port(),
    )
    assert opened == [True]


def test_group_panel_callback_rerenders_without_extra_text(monkeypatch):
    import bridge.group_callbacks as callbacks

    rendered = []
    monkeypatch.setattr(callbacks, "send_text", lambda *a, **k: pytest.fail("group panel must not emit extra text"))
    monkeypatch.setattr(callbacks, "handle_group_command", lambda *a, **k: None)
    monkeypatch.setattr(callbacks, "send_group_menu", lambda *a, **k: rendered.append(True))

    group_service = SimpleNamespace(
        claim_user_turn=lambda *a: True,
        pass_user_turn=lambda *a: True,
    )
    callbacks.handle_group_panel_callback(
        object(),
        "token",
        "chat|topic:1",
        {"session_id": "s1"},
        "group:claim",
        {"message_id": 77},
        sender_id="actor",
        group_service=group_service,
        input_flow_service=object(),
        request_context=SimpleNamespace(db=object()),
    )
    assert rendered == [True]


def test_databank_reindex_callback_rerenders_without_extra_text(monkeypatch):
    import bridge.enum_callbacks as callbacks

    rendered = []
    monkeypatch.setattr(callbacks, "send_text", lambda *a, **k: pytest.fail("Data Bank panel must not emit extra text"))
    monkeypatch.setattr(callbacks, "send_databank_menu", lambda *a, **k: rendered.append(True))
    callbacks._handle_rag_reindex(
        object(),
        "token",
        "chat",
        SimpleNamespace(app_settings=object()),
        SimpleNamespace(reindex=lambda *a: (4, 4)),
        77,
    )
    assert rendered == [True]


def test_language_callback_updates_and_rerenders_same_panel_without_success_text(monkeypatch):
    import bridge.settings_callbacks as callbacks

    rerendered = []
    monkeypatch.setattr(callbacks, "set_response_language", lambda *a, **k: "id")
    monkeypatch.setattr(callbacks, "send_text", lambda *a, **k: pytest.fail("language selection must stay in panel"))
    monkeypatch.setattr(callbacks, "remove_inline_keyboard", lambda *a, **k: pytest.fail("panel should be rerendered"))
    monkeypatch.setattr(callbacks, "send_language_menu", lambda *a, **k: rerendered.append((a, k)))
    answers = []
    handled = callbacks.handle_language_callback(
        object(),
        "token",
        {"id": "cb"},
        lambda _token, _id, text: answers.append(text),
        "language:id",
        "chat",
        {"message_id": 77},
        {"response_language": "auto"},
        "s1",
        9,
        delivery_port=make_test_delivery_port(),
        request_context=SimpleNamespace(app_settings=object()),
    )
    assert handled is True
    assert answers == ["Language selected"]
    assert rerendered and rerendered[-1][0][3] == 77


def test_databank_remove_confirm_mutates_directly_then_rerenders_without_legacy_text(monkeypatch):
    import bridge.enum_callbacks as callbacks

    removed, rendered = [], []
    assert not hasattr(callbacks, "handle_data_bank_command")
    monkeypatch.setattr(callbacks, "resolve_dynamic_callback_token", lambda *a, **k: "notes.pdf")
    monkeypatch.setattr(callbacks, "send_databank_menu", lambda *a, **k: rendered.append(True))
    callbacks._handle_ragremoveconfirm(
        object(),
        "token",
        "chat",
        SimpleNamespace(db=object()),
        SimpleNamespace(remove=lambda _db, _chat, filename: removed.append(filename) or 1),
        77,
        ["enum", "ragremoveconfirm", "token"],
    )
    assert removed == ["notes.pdf"]
    assert rendered == [True]
