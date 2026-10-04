"""Director Room actions must be actor-owned, revision-bound plans, not story facts."""

import json

import pytest
from application_test_setup import make_test_persona_service, make_test_provider_port
from test_memory_completion_safety import session_db as session_db
from test_narrative_reconciliation import add_story, run_reconciliation

from bridge import director_callbacks as callbacks
from bridge import director_input
from bridge import director_panels as panels
from bridge import director_room as room
from bridge.callback_tokens import dynamic_callback_token, resolve_dynamic_callback_token
from bridge.callbacks import is_session_scoped_panel_callback
from bridge.conversation_lifecycle import has_pending_management_input
from bridge.director_repository import load_director_state
from bridge.director_service import DirectorService
from bridge.narrative_repository import load_narrative_state_row, upsert_narrative_scene, upsert_narrative_thread
from bridge.narrative_settings import preset_narrative_settings, save_session_narrative_settings
from bridge.request_types import RequestContext
from bridge.sqlite_store import write_transaction


@pytest.fixture
def directed(session_db):
    _settings, db, _session = session_db
    save_session_narrative_settings(db, "chat", "s1", preset_narrative_settings("world_driven"))
    add_story(db)
    run_reconciliation(session_db)
    return session_db


def context(directed, actor="alice"):
    settings, db, session = directed
    return RequestContext(db, session["session_id"], actor, app_settings=settings)


def payload(db, action, revision, *, actor="alice", **extra):
    value = {"actor_id": actor, "session_id": "s1", "revision": revision, "action": action, **extra}
    return "director:" + dynamic_callback_token("director", json.dumps(value), "chat", db=db)


def dispatch(directed, data, actor="alice"):
    _settings, db, session = directed
    answers = []
    handled = callbacks.handle_director_callback(
        db,
        "token",
        {"id": "cb"},
        lambda _t, _i, text: answers.append(text),
        data,
        "chat",
        {},
        session,
        "s1",
        None,
        provider_port=make_test_provider_port(),
        persona_service=make_test_persona_service(),
        request_context=context(directed, actor),
    )
    return handled, answers


def test_room_is_read_only_bounded_and_does_not_publish_internal_tokens(directed):
    _, db, _ = directed
    traced = []
    db.set_trace_callback(traced.append)
    view = room.director_room(db, "chat", "s1")
    db.set_trace_callback(None)
    assert view["session_id"] == "s1"
    assert view["scene"]["viewpoint"] == "Mara"
    assert view["threads"][0]["thread_id"] == "rebellion"
    assert view["mutable"] is True
    assert len(view["revision"]) == 64
    assert not any(sql.lstrip().upper().startswith(("INSERT", "UPDATE", "DELETE", "REPLACE")) for sql in traced)
    assert not {"inflight_token", "active_proposal_json", "prompt", "api_key"} & view.keys()


@pytest.mark.parametrize("scope", ["next_scene", "persistent"])
def test_manual_edit_has_explicit_scope_and_never_writes_transcript(directed, scope):
    _, db, _ = directed
    old = db.execute("SELECT * FROM messages").fetchall()
    view = room.director_room(db, "chat", "s1")
    result = room.apply_direction(db, "chat", "s1", view["revision"], "Keep Mara at the gate.", scope)
    assert result.result == "accepted"
    updated = room.director_room(db, "chat", "s1")
    assert updated["revision"] != view["revision"]
    assert "Keep Mara" in updated["objective" if scope == "persistent" else "direction"]
    assert db.execute("SELECT * FROM messages").fetchall() == old
    assert updated["history"][0]["source"] == "user"


@pytest.mark.parametrize("change", ["story", "settings", "director", "delete"])
def test_stale_room_cannot_overwrite_newer_work(directed, change):
    _, db, _ = directed
    view = room.director_room(db, "chat", "s1")
    if change == "story":
        add_story(db)
    elif change == "settings":
        save_session_narrative_settings(db, "chat", "s1", preset_narrative_settings("observer"))
    elif change == "director":
        room.apply_direction(db, "chat", "s1", view["revision"], "Protect Mara", "persistent")
    else:
        with write_transaction(db):
            db.execute("DELETE FROM sessions WHERE chat_id='chat' AND session_id='s1'")
    with pytest.raises(ValueError, match=r"changed|exists"):
        room.apply_direction(db, "chat", "s1", view["revision"], "Obsolete edit", "persistent")
    assert load_director_state(db, "chat", "s1")["goal"] != "Obsolete edit"


def test_current_panel_cannot_claim_success_for_unreconciled_manual_scene(directed):
    _, db, _ = directed
    add_story(db)
    view = room.director_room(db, "chat", "s1")
    with pytest.raises(ValueError, match="catching up"):
        room.apply_direction(db, "chat", "s1", view["revision"], "Keep Mara", "next_scene")


@pytest.mark.parametrize("lifecycle", ["resolution_committed", "epilogue_pending", "epilogue_committed", "closed"])
def test_closing_room_is_read_only_and_contains_no_mutation_buttons(directed, lifecycle):
    _, db, session = directed
    with write_transaction(db):
        db.execute("INSERT INTO ending_state(chat_id,session_id,lifecycle) VALUES('chat','s1',?)", (lifecycle,))
    view = room.director_room(db, "chat", "s1")
    assert not view["mutable"]
    with pytest.raises(ValueError, match=r"ended|closing|read-only"):
        room.apply_direction(db, "chat", "s1", view["revision"], "Reopen", "persistent")
    text, markup = panels.director_panel(db, "chat", session, "alice")
    assert "Director" in text
    labels = [b["text"] for row in markup["inline_keyboard"] for b in row]
    assert "Reassess now" not in labels
    assert not any("Edit" in label or "thread" in label for label in labels)


def test_panel_escapes_direction_and_all_callbacks_are_actor_session_bound(directed):
    _, db, session = directed
    revision = room.director_room(db, "chat", "s1")["revision"]
    room.apply_direction(db, "chat", "s1", revision, "<b>Not Telegram markup</b>", "persistent")
    text, markup = panels.director_panel(db, "chat", session, "alice")
    assert "&lt;b&gt;Not Telegram markup&lt;/b&gt;" in text
    assert "<b>Not Telegram markup</b>" not in text
    assert len(text) < 4096
    for row in markup["inline_keyboard"]:
        for b in row:
            assert is_session_scoped_panel_callback(b["callback_data"])
            values = json.loads(
                resolve_dynamic_callback_token(b["callback_data"].split(":")[1], "director", "chat", db=db)
            )
            assert values["actor_id"] == "alice" and values["session_id"] == "s1"
            assert len(b["callback_data"].encode()) <= 64


@pytest.mark.parametrize("actor,change", [("bob", False), ("alice", True)])
def test_callback_rejects_other_actor_or_stale_revision_before_provider(directed, monkeypatch, actor, change):
    _, db, _ = directed
    data = payload(db, "reassess", room.director_room(db, "chat", "s1")["revision"])
    if change:
        add_story(db)
    monkeypatch.setattr(DirectorService, "reassess", lambda *a, **k: pytest.fail("invalid callback called provider"))
    notices = []
    monkeypatch.setattr(callbacks, "send_text", lambda *a, **k: notices.append(a[2]))
    handled, answers = dispatch(directed, data, actor)
    assert handled and answers
    assert "another user" in notices[0] if actor == "bob" else "changed" in notices[0]


def test_pending_edit_is_actor_scoped_management_input_not_a_story_turn(directed, monkeypatch):
    _, db, session = directed
    view = room.director_room(db, "chat", "s1")
    director_input.begin_director_input(db, "chat", "s1", "alice", view["revision"], "persistent")
    assert has_pending_management_input(db, "chat", "s1", "alice")
    assert not has_pending_management_input(db, "chat", "s1", "bob")
    sent = []
    monkeypatch.setattr(director_input, "send_text", lambda *a, **k: sent.append(a[2]))
    assert not director_input.handle_director_input(
        db, "token", "chat", session, "Protect Mara", context(directed, "bob")
    )
    assert director_input.handle_director_input(db, "token", "chat", session, "Protect Mara", context(directed))
    assert load_director_state(db, "chat", "s1")["goal"] == "Protect Mara"
    assert not director_input.handle_director_input(db, "token", "chat", session, "Story", context(directed))
    assert "saved" in sent[0].lower()


def test_pending_edit_cannot_cross_story_revisions(directed, monkeypatch):
    _, db, session = directed
    director_input.begin_director_input(
        db, "chat", "s1", "alice", room.director_room(db, "chat", "s1")["revision"], "persistent"
    )
    add_story(db)
    notices = []
    monkeypatch.setattr(director_input, "send_text", lambda *a, **k: notices.append(a[2]))
    assert director_input.handle_director_input(db, "token", "chat", session, "Stale goal", context(directed))
    assert "changed" in notices[0]
    assert load_director_state(db, "chat", "s1")["goal"] == ""


def test_thread_selection_only_changes_next_scene_plan(directed):
    config, db, session = directed
    old = load_narrative_state_row(db, "chat", "s1")
    with write_transaction(db):
        upsert_narrative_thread(
            db,
            "chat",
            "s1",
            {
                "thread_id": "palace",
                "title": "Palace",
                "status": "offscreen",
                "summary": "Mara visited the palace",
                "last_scene_id": "palace_scene",
                "source_revision": 1,
            },
        )
        upsert_narrative_scene(
            db,
            "chat",
            "s1",
            {
                "scene_id": "palace_scene",
                "thread_id": "palace",
                "viewpoint_character": "Mara",
                "pov_mode": "third_person_rotating",
                "user_present": False,
                "purpose": "palace",
                "transition_type": "cut",
                "start_rowid": 1,
                "end_rowid": 1,
                "status": "complete",
                "source_revision": 1,
            },
        )
    result = room.steer_thread(
        db,
        "chat",
        session,
        room.director_room(db, "chat", "s1")["revision"],
        "palace",
        persona_service=make_test_persona_service(),
        app_settings=config,
    )
    assert result.result == "accepted"
    proposed = json.loads(load_director_state(db, "chat", "s1")["active_proposal_json"])
    assert proposed["thread_id"] == "palace" and proposed["transition_type"] == "thread_switch"
    assert load_narrative_state_row(db, "chat", "s1") == old


def test_cadence_presets_and_custom_input_preserve_user_control(directed, monkeypatch):
    from bridge.model_selection import director_reasoning_for_session
    from bridge.narrative_settings import load_session_narrative_settings

    _, db, session = directed
    view = room.director_room(db, "chat", "s1")
    room.apply_controls(db, "chat", "s1", view["revision"], cadence="fixed", interval=4, reasoning=0)
    text, markup = panels.director_panel(db, "chat", session, "alice", page="cadence")
    assert "cadence" in text.lower()
    labels = [button["text"] for row in markup["inline_keyboard"] for button in row]
    assert {"Adaptive", "Every 4 turns", "Every 6 turns", "Every 10 turns", "Custom interval"} <= set(labels)
    revision = room.director_room(db, "chat", "s1")["revision"]
    director_input.begin_director_input(db, "chat", "s1", "alice", revision, "cadence")
    monkeypatch.setattr(director_input, "send_text", lambda *a, **k: None)
    assert director_input.handle_director_input(db, "token", "chat", session, "9", context(directed))
    saved = load_session_narrative_settings(db, "chat", "s1")
    assert saved.director_fixed_interval == 9 and saved.director_cadence_mode == "fixed"
    assert saved.user_control == "physical_continuity"
    assert director_reasoning_for_session(db, "chat", "s1") == 0
