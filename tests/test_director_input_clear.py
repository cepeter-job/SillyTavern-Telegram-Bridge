"""Clear commands remove scoped plans without creating story events."""

import pytest
from test_director_panels import context
from test_director_panels import directed as directed
from test_director_panels import session_db as session_db

from bridge import director_input
from bridge.director_repository import load_director_state
from bridge.director_room import apply_direction, director_room


@pytest.mark.parametrize("scope", ["persistent", "next_scene"])
def test_clear_removes_direction_in_the_selected_scope(directed, monkeypatch, scope):
    _, db, session = directed
    revision = director_room(db, "chat", "s1")["revision"]
    apply_direction(db, "chat", "s1", revision, "Keep the synthetic scene quiet.", scope)
    director_input.begin_director_input(db, "chat", "s1", "alice", director_room(db, "chat", "s1")["revision"], scope)
    before = db.execute("SELECT * FROM messages").fetchall()
    monkeypatch.setattr(director_input, "send_text", lambda *a, **k: None)

    assert director_input.handle_director_input(db, "", "chat", session, "/clear", context(directed))

    state = load_director_state(db, "chat", "s1")
    assert state["goal" if scope == "persistent" else "active_direction"] == ""
    assert db.execute("SELECT * FROM messages").fetchall() == before


@pytest.mark.parametrize("scope", ["persistent", "next_scene"])
def test_clear_rejects_an_editor_from_an_old_room_revision(directed, monkeypatch, scope):
    _, db, session = directed
    director_input.begin_director_input(db, "chat", "s1", "alice", director_room(db, "chat", "s1")["revision"], scope)
    apply_direction(db, "chat", "s1", director_room(db, "chat", "s1")["revision"], "Newer objective.", "persistent")
    before = load_director_state(db, "chat", "s1")
    monkeypatch.setattr(director_input, "send_text", lambda *a, **k: None)
    assert director_input.handle_director_input(db, "", "chat", session, "/clear", context(directed))
    assert load_director_state(db, "chat", "s1") == before


def test_unrelated_command_does_not_cancel_the_editor(directed, monkeypatch):
    from bridge.metadata import get_meta

    _, db, session = directed
    director_input.begin_director_input(
        db, "chat", "s1", "alice", director_room(db, "chat", "s1")["revision"], "persistent"
    )
    monkeypatch.setattr(director_input, "send_text", lambda *a, **k: None)
    assert not director_input.handle_director_input(db, "", "chat", session, "/help", context(directed))
    assert get_meta(db, "director_input:chat:alice", "")
    assert director_input.handle_director_input(db, "", "chat", session, "/cancel", context(directed))
    assert get_meta(db, "director_input:chat:alice", "") == ""
