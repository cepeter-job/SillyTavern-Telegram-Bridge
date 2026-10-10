"""Expired setup clicks retire the dead panel without applying any settings."""

import json

import pytest
from test_conversation_setup import setup as setup
from test_light_novel_storage import novel_db as novel_db

from bridge import callbacks as panel_callbacks
from bridge import conversation_setup_callbacks as callbacks
from bridge.callback_tokens import dynamic_callback_token
from bridge.metadata import get_meta, set_meta
from bridge.panel_bindings import bind_panel_session, panel_session_for_message
from bridge.request_types import RequestContext


def _click(setup, monkeypatch, *, expiry="draft", action="cancel", stage=None, reject_delete=False):
    db, session, service, state = setup
    command = {"nonce": state["nonce"], "stage": stage or state["stage"], "action": action, "value": "player_centric"}
    token = dynamic_callback_token("conversation_setup", json.dumps(command), "chat", db=db)
    if expiry == "draft":
        state["expires_at"] = 0
        set_meta(db, "conversation_setup:chat:owner", json.dumps(state))
    elif expiry == "token":
        db.execute("UPDATE callback_tokens SET expires_at=0 WHERE token=?", (token,))
        db.commit()
    elif expiry == "missing":
        set_meta(db, "conversation_setup:chat:owner", "")
    bind_panel_session(db, "chat", 55, "story", "owner")
    requests, feedback, panels = [], [], []

    def request(_token, method, payload):
        requests.append((method, payload))
        if reject_delete and method == "deleteMessage":
            raise RuntimeError("Message cannot be deleted")
        return {}

    monkeypatch.setattr(panel_callbacks, "telegram_request", request)
    monkeypatch.setattr(callbacks, "send_text", lambda _t, _c, text: feedback.append(text))
    monkeypatch.setattr(callbacks, "send_setup_panel", lambda *a, **k: panels.append(a[2]))
    original = db.execute("SELECT * FROM sessions").fetchall()
    draft = get_meta(db, "conversation_setup:chat:owner")
    assert callbacks.handle_setup_callback(
        db,
        "token",
        {"id": "callback"},
        lambda *a: None,
        f"setup:{token}",
        "chat",
        {"message_id": 55},
        session,
        persona_service=service.persona_service,
        request_context=RequestContext(db, "story", "owner", app_settings=service.app_settings),
    )
    assert db.execute("SELECT * FROM sessions").fetchall() == original
    assert get_meta(db, "conversation_setup:chat:owner") == draft
    return requests, feedback, panels


@pytest.mark.parametrize("expiry", ["draft", "token", "missing"])
def test_cancel_closes_expired_panel(setup, monkeypatch, expiry):
    requests, feedback, panels = _click(setup, monkeypatch, expiry=expiry)
    assert requests == [("deleteMessage", {"chat_id": "chat", "message_id": 55})]
    assert panel_session_for_message(setup[0], "chat", 55) is None
    assert feedback == ["Setup expired; run /character again."] and not panels


@pytest.mark.parametrize("action", ["back", "pick", "apply"])
def test_other_expired_setup_buttons_also_close_panel(setup, monkeypatch, action):
    requests, _, panels = _click(setup, monkeypatch, action=action)
    assert requests[0][0] == "deleteMessage" and not panels


def test_expired_panel_delete_failure_removes_buttons_with_closed_marker(setup, monkeypatch):
    requests, _, _ = _click(setup, monkeypatch, reject_delete=True)
    assert requests[-1] == (
        "editMessageText",
        {
            "chat_id": "chat",
            "message_id": 55,
            "text": "Panel closed.",
            "reply_markup": {"inline_keyboard": []},
        },
    )
    assert panel_session_for_message(setup[0], "chat", 55) is None


def test_stale_stage_does_not_close_a_valid_setup_panel(setup, monkeypatch):
    requests, feedback, panels = _click(setup, monkeypatch, expiry="none", stage="mode")
    assert not requests and not panels and feedback == ["This setup step is no longer active"]
    assert panel_session_for_message(setup[0], "chat", 55) == "story"


def test_invalid_choice_does_not_close_a_valid_setup_panel(setup, monkeypatch):
    requests, feedback, _ = _click(setup, monkeypatch, expiry="none", action="invalid")
    assert not requests and feedback == ["Choose a Narrative Style or open Advanced"]
    assert panel_session_for_message(setup[0], "chat", 55) == "story"
