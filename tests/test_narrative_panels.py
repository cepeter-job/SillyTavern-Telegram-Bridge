"""Narrative setup and actor/session-bound preference controls."""

import json

import pytest
from test_conversation_setup import choose, ready
from test_conversation_setup import setup as setup
from test_light_novel_storage import novel_db as novel_db

from bridge.callback_tokens import resolve_dynamic_callback_token
from bridge.conversation_lifecycle import conversation_state
from bridge.metadata import get_meta
from bridge.narrative_settings import (
    load_session_narrative_settings,
    load_user_narrative_default,
    preset_narrative_settings,
    save_user_narrative_default,
)
from bridge.request_types import RequestContext


def test_character_setup_starts_with_personal_narrative_prefill(setup):
    db, session, service, _ = setup
    save_user_narrative_default(db, "owner", preset_narrative_settings("observer"))
    before = db.execute("SELECT * FROM narrative_settings").fetchall()
    state = service.begin(db, "chat", session, "owner", "Alice.png")
    assert state["stage"] == "narrative"
    assert state["narrative_settings"] == preset_narrative_settings("observer").to_dict()
    assert db.execute("SELECT * FROM narrative_settings").fetchall() == before


def test_setup_preset_advances_to_mode_without_mutating_preferences(setup):
    db, _, _, _ = setup
    before = db.execute("SELECT * FROM narrative_settings").fetchall()
    state = choose(setup, "narrative", "world_driven")
    assert state["stage"] == "mode"
    assert state["narrative_settings"]["offscreen_policy"] == "free"
    assert db.execute("SELECT * FROM narrative_settings").fetchall() == before
    assert load_user_narrative_default(db, "owner").preset == "player_centric"
    assert get_meta(db, "grounded_user:chat:story", "") == ""


def test_advanced_draft_changes_are_custom_and_actor_scoped(setup):
    db, _, service, _ = setup
    assert choose(setup, "narrative", action="advanced")["stage"] == "narrative_advanced"
    assert choose(setup, "narrative_advanced", "offscreen_policy")["stage"] == "narrative_field"
    state = choose(setup, "narrative_field", "free")
    assert state["stage"] == "narrative_advanced"
    assert state["narrative_settings"]["preset"] == "custom"
    assert state["narrative_settings"]["offscreen_policy"] == "free"
    assert load_session_narrative_settings(db, "chat", "story").offscreen_policy == "rare"
    with pytest.raises(ValueError, match="expired"):
        service.load(db, "chat", "story", "someone-else")


def test_invalid_first_person_does_not_change_setup_draft(setup):
    db, _, service, _ = setup
    choose(setup, "narrative", action="advanced")
    choose(setup, "narrative_advanced", "pov_mode")
    before = service.load(db, "chat", "story", "owner")
    with pytest.raises(ValueError, match="AI-controlled"):
        choose(setup, "narrative_field", "first_person")
    assert service.load(db, "chat", "story", "owner") == before


def test_setup_back_returns_to_narrative_and_retains_choice(setup):
    db, session, service, state = setup
    choose(setup, "narrative", "observer")
    returned = service.back(db, "chat", session, "owner", state["nonce"])
    assert returned["stage"] == "narrative"
    assert returned["narrative_settings"]["preset"] == "observer"
    assert choose(setup, "narrative", action="next")["stage"] == "mode"


def test_cancelled_narrative_setup_persists_no_session_or_personal_style(setup):
    db, session, service, state = setup
    before = db.execute("SELECT * FROM narrative_settings").fetchall()
    choose(setup, "narrative", "observer")
    service.cancel(db, "chat", session, "owner", state["nonce"])
    assert db.execute("SELECT * FROM narrative_settings").fetchall() == before
    assert db.execute("SELECT COUNT(*) FROM narrative_defaults").fetchone()[0] == 0


def test_apply_writes_narrative_style_with_session_and_mode(setup):
    db, session, service, state = setup
    ready(setup, "lightnovel", preset="world_driven")
    result = service.apply(db, "chat", session, "owner", state["nonce"])
    assert load_session_narrative_settings(db, "chat", result["session_id"]).preset == "world_driven"
    assert conversation_state(db, "chat", result["session_id"]).strategy == "b"
    assert load_user_narrative_default(db, "owner").preset == "player_centric"


def test_failed_narrative_apply_rolls_back_entire_new_session(setup, monkeypatch):
    from bridge import conversation_setup

    db, session, service, state = setup
    ready(setup, preset="observer")
    service.back(db, "chat", session, "owner", state["nonce"])
    choose(setup, "session", "$new")
    service.set_title(db, "chat", session, "owner", "New story")
    before = db.execute("SELECT * FROM sessions").fetchall()

    def fail(*args, **kwargs):
        assert db.in_transaction
        raise RuntimeError("injected narrative write failure")

    monkeypatch.setattr(conversation_setup, "save_session_narrative_settings", fail)
    with pytest.raises(RuntimeError, match="injected"):
        service.apply(db, "chat", session, "owner", state["nonce"])
    assert db.execute("SELECT * FROM sessions").fetchall() == before
    assert get_meta(db, "active_session:chat") == "story"
    assert service.load(db, "chat", "story", "owner")["stage"] == "review"


def test_review_shows_effective_narrative_values_and_recommendation(setup, monkeypatch):
    from bridge import conversation_setup_panels as panels

    db, _, service, _ = setup
    state = ready(setup, preset="world_driven")
    sent = []
    monkeypatch.setattr(panels, "send_panel_request", lambda *a, **k: sent.append(a[2]))
    panels.send_setup_panel(
        "token",
        "chat",
        state,
        request_context=RequestContext(db, "story", "owner", app_settings=service.app_settings),
        persona_service=service.persona_service,
    )
    text = sent[-1]["text"]
    for expected in ("World-driven", "Third-person limited, rotating", "Free", "Physical continuity", "I am not MC"):
        assert expected in text
    assert "not enabled automatically" in text


def panel_buttons(setup, monkeypatch, *, view="presets", field=""):
    from bridge import narrative_panels as panels

    db, session, service, _ = setup
    sent = []
    monkeypatch.setattr(panels, "send_panel_request", lambda *a, **k: sent.append(a[2]))
    context = RequestContext(db, "story", "owner", app_settings=service.app_settings)
    panels.send_narrative_menu("token", "chat", session, request_context=context, view=view, field=field)
    return sent[-1], context


def dispatch(setup, context, button, monkeypatch):
    from bridge import narrative_callbacks as callbacks

    db, session, _, _ = setup
    notices = []
    monkeypatch.setattr(callbacks, "send_narrative_menu", lambda *a, **k: None)
    monkeypatch.setattr(callbacks, "send_text", lambda *a: notices.append(a[-1]))
    answers = []
    result = callbacks.handle_narrative_callback(
        db,
        "token",
        {"id": "callback"},
        lambda *a: answers.append(a[-1]),
        button["callback_data"],
        "chat",
        {"message_id": 55},
        session,
        "story",
        None,
        request_context=context,
    )
    assert result is True
    return notices, answers


def test_narrative_panel_exposes_presets_and_opaque_scoped_tokens(setup, monkeypatch):
    payload, _ = panel_buttons(setup, monkeypatch)
    buttons = [b for row in payload["reply_markup"]["inline_keyboard"] for b in row]
    assert {"Player-centric", "Ensemble", "World-driven", "Observer", "Advanced", "Save as my default"} <= {
        b["text"] for b in buttons
    }
    db, _, _, _ = setup
    for b in buttons:
        assert len(b["callback_data"].encode()) <= 64
        decoded = json.loads(
            resolve_dynamic_callback_token(b["callback_data"].split(":")[1], "narrative", "chat", db=db)
        )
        assert decoded["session_id"] == "story"
        assert decoded["actor_id"] == "owner"
        assert type(decoded["settings_revision"]) is int


def test_narrative_callback_rejects_wrong_actor_without_writes(setup, monkeypatch):
    payload, context = panel_buttons(setup, monkeypatch)
    button = next(b for row in payload["reply_markup"]["inline_keyboard"] for b in row if b["text"] == "Observer")
    context = RequestContext(context.db, "story", "thief", app_settings=context.app_settings)
    before = context.db.total_changes
    notices, _ = dispatch(setup, context, button, monkeypatch)
    assert notices
    assert context.db.total_changes == before
    assert load_session_narrative_settings(context.db, "chat", "story").preset == "player_centric"


def test_narrative_callback_stale_revision_cannot_overwrite_new_choice(setup, monkeypatch):
    payload, context = panel_buttons(setup, monkeypatch)
    buttons = {b["text"]: b for row in payload["reply_markup"]["inline_keyboard"] for b in row}
    dispatch(setup, context, buttons["Observer"], monkeypatch)
    assert load_session_narrative_settings(context.db, "chat", "story").preset == "observer"
    notices, _ = dispatch(setup, context, buttons["Ensemble"], monkeypatch)
    assert notices and "changed" in notices[-1].lower()
    assert load_session_narrative_settings(context.db, "chat", "story").preset == "observer"


def test_save_as_default_is_explicit_and_does_not_change_other_sessions(setup, monkeypatch):
    from bridge.narrative_settings import save_session_narrative_settings

    db, _, _, _ = setup
    save_session_narrative_settings(db, "chat", "story", preset_narrative_settings("observer"))
    payload, context = panel_buttons(setup, monkeypatch)
    button = next(
        b for row in payload["reply_markup"]["inline_keyboard"] for b in row if b["text"] == "Save as my default"
    )
    before = db.execute("SELECT * FROM narrative_settings").fetchall()
    dispatch(setup, context, button, monkeypatch)
    assert load_user_narrative_default(db, "owner").preset == "observer"
    assert load_user_narrative_default(db, "another-user").preset == "player_centric"
    assert db.execute("SELECT * FROM narrative_settings").fetchall() == before


def test_narrative_callback_requires_common_session_binding():
    from bridge.callbacks import is_session_scoped_panel_callback

    assert is_session_scoped_panel_callback("narrative:opaque")


def test_help_catalog_lists_narrative_style():
    from bridge.help_details import HELP_CATEGORIES

    commands = {command for entries in HELP_CATEGORIES.values() for command, _ in entries}
    assert "/narrative" in commands
