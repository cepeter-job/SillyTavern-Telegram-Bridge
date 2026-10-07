"""Inline /check mode panel behavior."""

from application_test_setup import (
    make_test_application_services,
    make_test_delivery_port,
    make_test_provider_port,
)
from test_memory_completion_safety import session_db as session_db

from bridge.action_adjudication import action_mode
from bridge.callback_dispatch import process_callback
from bridge.callbacks import is_session_scoped_panel_callback
from bridge.request_types import RequestContext
from bridge.simulation_checks import perform_check
from bridge.simulation_commands import _simulation_command_route
from bridge.sqlite_store import write_transaction


def _open_panel(db, settings, session, monkeypatch):
    import bridge.telegram as telegram

    calls = []
    monkeypatch.setattr(
        telegram,
        "telegram_request",
        lambda _token, method, payload=None: calls.append((method, payload or {})) or {"message_id": 77},
    )
    sent = []
    handled = _simulation_command_route(
        db,
        "token",
        "",
        "",
        {},
        "chat",
        "/check",
        "/check",
        session,
        "s1",
        "",
        "",
        "",
        operation_id=None,
        request_context=RequestContext(db, "s1", "actor", app_settings=settings),
        delivery_port=make_test_delivery_port(send_text=lambda *a, **k: sent.append(a[-1])),
        provider_port=make_test_provider_port(),
    )
    assert handled is True
    assert sent == []
    return calls


def _button_map(payload):
    buttons = {}
    for row in payload["reply_markup"]["inline_keyboard"]:
        for button in row:
            buttons[button["callback_data"]] = button["text"]
    return buttons


def test_check_without_arguments_opens_inline_mode_panel(session_db, monkeypatch):
    settings, db, session = session_db
    calls = _open_panel(db, settings, session, monkeypatch)
    method, payload = calls[0]
    assert method == "sendMessage"
    assert payload["chat_id"] == "chat"
    assert "Current mode: Auto" in payload["text"]
    buttons = _button_map(payload)
    assert buttons["checkmode:auto"].startswith("✅")
    expected = {
        "checkmode:director",
        "checkmode:manual",
        "checkmode:manual_help",
        "checkmode:recent",
        "checkmode:close",
    }
    assert expected <= set(buttons)


def test_check_mode_callback_updates_session_and_rerenders_same_panel(session_db, monkeypatch):
    settings, db, session = session_db
    calls = _open_panel(db, settings, session, monkeypatch)
    answers = []
    import bridge.callback_dispatch as callbacks

    monkeypatch.setattr(callbacks, "answer_callback", lambda _token, _id, text="": answers.append(text))
    process_callback(
        db,
        "token",
        {
            "id": "cb",
            "from": {"id": "actor"},
            "data": "checkmode:director",
            "message": {
                "message_id": 77,
                "chat": {"id": "chat"},
                "reply_markup": calls[0][1]["reply_markup"],
            },
        },
        actor_id="actor",
        services=make_test_application_services(app_settings=settings),
    )
    assert action_mode(db, "chat", "s1") == "director"
    assert answers[-1] == "Director mode"
    method, payload = calls[-1]
    assert method == "editMessageText"
    assert payload["message_id"] == 77
    assert "Current mode: Director" in payload["text"]
    assert _button_map(payload)["checkmode:director"].startswith("✅")


def test_check_panel_recent_view_uses_saved_checks_without_model_call(session_db, monkeypatch):
    settings, db, session = session_db
    calls = _open_panel(db, settings, session, monkeypatch)
    with write_transaction(db):
        sql = "INSERT INTO messages(chat_id,session_id,role,content,created_at) VALUES(?,?,?,?,?)"
        rowid = db.execute(sql, ("chat", "s1", "user", "Open door", 1)).lastrowid
    perform_check(
        db,
        "chat",
        "s1",
        request_key="check:panel-test",
        domain="stealth",
        actor="user",
        action="Open door",
        dc=13,
        roll=9,
        modifier=2,
        source_rowid=rowid,
    )
    import bridge.callback_dispatch as callbacks

    monkeypatch.setattr(callbacks, "answer_callback", lambda *_a, **_k: None)
    process_callback(
        db,
        "token",
        {
            "id": "cb-recent",
            "from": {"id": "actor"},
            "data": "checkmode:recent",
            "message": {
                "message_id": 77,
                "chat": {"id": "chat"},
                "reply_markup": calls[0][1]["reply_markup"],
            },
        },
        actor_id="actor",
        services=make_test_application_services(app_settings=settings),
    )
    method, payload = calls[-1]
    assert method == "editMessageText"
    assert "Recent Checks" in payload["text"]
    assert "stealth" in payload["text"]
    assert "d20 9 +2 = 11 vs DC 13" in payload["text"]
    assert _button_map(payload)["checkmode:back"] == "⬅️ Back"


def test_check_panel_callbacks_are_session_scoped():
    assert is_session_scoped_panel_callback("checkmode:auto")
    assert is_session_scoped_panel_callback("checkmode:recent")
