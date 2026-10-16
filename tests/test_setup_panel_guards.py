"""Delayed or unreadable callbacks must not retire a newer setup render."""

import json
import sqlite3

import pytest
from test_conversation_setup import setup as setup
from test_light_novel_storage import novel_db as novel_db

from bridge import callbacks as panel_callbacks
from bridge import conversation_setup_callbacks as callbacks
from bridge import conversation_setup_panels as panels
from bridge import telegram
from bridge.metadata import get_meta, set_meta
from bridge.panel_bindings import panel_session_for_message
from bridge.panel_singleflight import mark_panel_busy, remember_panel_busy_state, restore_busy_panel_if_unchanged
from bridge.request_types import RequestContext


@pytest.fixture
def ui(setup, monkeypatch):
    db, session, service, _ = setup
    messages, feedback = {}, []
    context = RequestContext(db, "story", "owner", app_settings=service.app_settings)

    def request(_token, method, payload):
        message_id = payload.get("message_id", 55)
        if method == "deleteMessage":
            messages.pop(message_id, None)
        elif method == "editMessageReplyMarkup":
            messages[message_id]["reply_markup"] = payload["reply_markup"]
        else:
            messages[message_id] = payload
        return {"message_id": message_id}

    monkeypatch.setattr(telegram, "telegram_request", request)
    monkeypatch.setattr(panel_callbacks, "telegram_request", request)
    monkeypatch.setattr(callbacks, "send_text", lambda _t, _c, text: feedback.append(text))

    def render(state, message_id):
        panels.send_setup_panel(
            "token", "chat", state, message_id, request_context=context, persona_service=service.persona_service
        )
        return next(
            button["callback_data"]
            for row in messages[message_id or 55]["reply_markup"]["inline_keyboard"]
            for button in row
            if button["text"] == "Cancel"
        )

    def click(data, message_id, callback=None):
        assert callbacks.handle_setup_callback(
            db,
            "token",
            callback or {"id": "callback"},
            lambda *a: None,
            data,
            "chat",
            {"message_id": message_id},
            session,
            persona_service=service.persona_service,
            request_context=context,
        )

    return render, click, messages, feedback


@pytest.mark.parametrize("replacement_message_id", [55, 56])
@pytest.mark.parametrize("expired_token", [False, True])
def test_old_draft_click_preserves_new_render(setup, ui, replacement_message_id, expired_token):
    db, session, service, state = setup
    render, click, messages, feedback = ui
    old_data = render(state, 55)
    replacement = service.begin(db, "chat", session, "owner", "Alice.png")
    render(replacement, replacement_message_id)
    if expired_token:
        db.execute("UPDATE callback_tokens SET expires_at=0 WHERE token=?", (old_data.split(":", 1)[1],))
        db.commit()
    draft = get_meta(db, "conversation_setup:chat:owner")
    current_panel = messages[replacement_message_id].copy()
    original_session = db.execute("SELECT * FROM sessions").fetchall()

    click(old_data, 55)

    assert messages.get(replacement_message_id) == current_panel
    assert panel_session_for_message(db, "chat", replacement_message_id) == "story"
    assert get_meta(db, "conversation_setup:chat:owner") == draft
    assert db.execute("SELECT * FROM sessions").fetchall() == original_session
    assert feedback == ["Setup expired; run /character again."]
    if replacement_message_id != 55:
        assert 55 not in messages
        assert panel_session_for_message(db, "chat", 55) is None


@pytest.mark.parametrize("expiry", ["token", "draft"])
def test_current_expired_render_still_closes(setup, ui, expiry):
    db, _, _, state = setup
    render, click, messages, _ = ui
    data = render(state, None)
    if expiry == "token":
        db.execute("UPDATE callback_tokens SET expires_at=0 WHERE token=?", (data.split(":", 1)[1],))
        db.commit()
    else:
        draft = json.loads(get_meta(db, "conversation_setup:chat:owner"))
        draft["expires_at"] = 0
        set_meta(db, "conversation_setup:chat:owner", json.dumps(draft))

    click(data, 55)

    assert 55 not in messages
    assert panel_session_for_message(db, "chat", 55) is None


@pytest.mark.parametrize("unavailable_table", ["callback_tokens", "meta"])
def test_callback_storage_failure_preserves_panel_for_retry(setup, ui, unavailable_table):
    db, _, _, state = setup
    render, click, messages, feedback = ui
    data = render(state, 55)
    current_panel = messages[55].copy()
    draft = get_meta(db, "conversation_setup:chat:owner")
    failures = []

    def deny_one_token_read(action, table, _column, _database, _trigger):
        if action == sqlite3.SQLITE_READ and table == unavailable_table and not failures:
            failures.append(table)
            return sqlite3.SQLITE_DENY
        return sqlite3.SQLITE_OK

    db.set_authorizer(deny_one_token_read)
    try:
        click(data, 55)
        assert messages.get(55) == current_panel
        assert panel_session_for_message(db, "chat", 55) == "story"
        assert get_meta(db, "conversation_setup:chat:owner") == draft
        assert feedback == ["Setup is temporarily unavailable; try again."]

        click(data, 55)

        assert 55 not in messages
        assert panel_session_for_message(db, "chat", 55) is None
        assert get_meta(db, "conversation_setup:chat:owner") == ""
        assert len(failures) == 1
    finally:
        db.set_authorizer(None)


def test_render_completion_does_not_overwrite_a_newer_draft(setup, monkeypatch):
    db, session, service, state = setup
    newer = []

    def replace_draft_during_send(*_args, **_kwargs):
        newer.append(service.begin(db, "chat", session, "owner", "Alice.png"))
        return {"message_id": 55}

    monkeypatch.setattr(panels, "send_panel_request", replace_draft_during_send)
    panels.send_setup_panel(
        "token",
        "chat",
        state,
        request_context=RequestContext(db, "story", "owner", app_settings=service.app_settings),
    )

    assert json.loads(get_meta(db, "conversation_setup:chat:owner")) == newer[0]


def test_queued_old_click_restores_new_render_after_busy_state(setup, ui):
    db, session, service, state = setup
    render, click, messages, _ = ui
    old_data = render(state, 55)
    callback = {"id": "delayed", "from": {"id": "owner"}, "message": messages[55].copy(), "data": old_data}
    replacement = service.begin(db, "chat", session, "owner", "Alice.png")
    render(replacement, 55)
    current_panel = messages[55].copy()
    assert remember_panel_busy_state(db, "chat", callback)
    assert mark_panel_busy(telegram.telegram_request, "token", "chat", callback)

    click(old_data, 55, callback)
    assert restore_busy_panel_if_unchanged(telegram.telegram_request, "token", db, "chat", callback)

    assert messages[55] == current_panel
    assert panel_session_for_message(db, "chat", 55) == "story"


def test_old_same_stage_cancel_cannot_cancel_newer_render(setup, ui):
    db, session, service, state = setup
    render, click, messages, _ = ui
    old_data = render(state, 55)
    render(service.back(db, "chat", session, "owner", state["nonce"]), 55)
    current_panel = messages[55].copy()
    draft = get_meta(db, "conversation_setup:chat:owner")

    click(old_data, 55)

    assert messages.get(55) == current_panel
    assert get_meta(db, "conversation_setup:chat:owner") == draft


def test_failed_render_preserves_last_successful_render(setup, ui, monkeypatch):
    db, _, _, state = setup
    render, _, _, _ = ui
    render(state, 55)
    draft = get_meta(db, "conversation_setup:chat:owner")

    def fail_send(*_args, **_kwargs):
        raise RuntimeError("Telegram unavailable")

    monkeypatch.setattr(panels, "send_panel_request", fail_send)
    with pytest.raises(RuntimeError, match="Telegram unavailable"):
        render(state, 55)

    assert get_meta(db, "conversation_setup:chat:owner") == draft


@pytest.mark.parametrize("persistent_failure", [False, True])
def test_queued_storage_failure_cannot_restore_stale_keyboard(setup, ui, persistent_failure):
    db, session, service, state = setup
    render, click, messages, _ = ui
    old_data = render(state, 55)
    callback = {"id": "delayed", "from": {"id": "owner"}, "message": messages[55].copy(), "data": old_data}
    replacement = service.begin(db, "chat", session, "owner", "Alice.png")
    current_data = render(replacement, 55)
    current_panel = messages[55].copy()
    failures = []

    def deny_metadata_read(action, table, _column, _database, _trigger):
        if action == sqlite3.SQLITE_READ and table == "meta" and (persistent_failure or not failures):
            failures.append(table)
            return sqlite3.SQLITE_DENY
        return sqlite3.SQLITE_OK

    db.set_authorizer(deny_metadata_read)
    try:
        remember_panel_busy_state(db, "chat", callback)
        mark_panel_busy(telegram.telegram_request, "token", "chat", callback)
        click(old_data, 55, callback)
        restore_busy_panel_if_unchanged(telegram.telegram_request, "token", db, "chat", callback)
        assert messages[55] == current_panel
    finally:
        db.set_authorizer(None)

    click(current_data, 55)
    assert 55 not in messages
