"""Arc facts and user-authored ending plans remain separate across both control surfaces."""

from contextlib import closing

import pytest
from miniapp_test_support import identity, make_services
from test_ending_state import goal, ready_case
from test_memory_completion_safety import session_db as session_db

from bridge import director_input, director_panels
from bridge import director_room as room
from bridge import miniapp_director as api
from bridge.director_guidance import director_guidance_for_session
from bridge.director_repository import load_director_state
from bridge.ending_service import load_ending_state
from bridge.miniapp_errors import MiniAppError
from bridge.narrative_arc_repository import load_arc_row
from bridge.request_types import RequestContext
from bridge.sqlite_store import write_transaction


def test_arc_guidance_is_a_plan_not_an_edit_to_committed_arc_facts(session_db):
    db = ready_case(session_db)
    before = load_arc_row(db, "chat", "s1", "rebellion_arc")
    view = room.director_room(db, "chat", "s1")
    result = room.apply_arc_guidance(
        db, "chat", "s1", view["revision"], "rebellion_arc", "Keep the rebellion unresolved until Mara returns."
    )
    assert result.result == "accepted"
    saved = room.director_room(db, "chat", "s1")
    assert saved["revision"] != view["revision"]
    assert saved["arcs"][0]["guidance"] == "Keep the rebellion unresolved until Mara returns."
    assert saved["arcs"][0]["status"] == "active"
    assert load_arc_row(db, "chat", "s1", "rebellion_arc") == before
    assert "Keep the rebellion unresolved" in director_guidance_for_session(db, "chat", "s1")
    assert saved["history"][0]["source"] == "user"


@pytest.mark.parametrize(
    "arc_id,direction", [("unknown", "Plan"), ("rebellion_arc", "x" * 1001), ("rebellion_arc", [])]
)
def test_invalid_arc_notes_do_not_mutate_any_state(session_db, arc_id, direction):
    db = ready_case(session_db)
    view = room.director_room(db, "chat", "s1")
    with pytest.raises(ValueError):
        room.apply_arc_guidance(db, "chat", "s1", view["revision"], arc_id, direction)
    assert room.director_room(db, "chat", "s1") == view


def test_arc_note_changes_are_revision_bound_and_can_be_cleared(session_db):
    db = ready_case(session_db)
    first = room.director_room(db, "chat", "s1")
    room.apply_arc_guidance(db, "chat", "s1", first["revision"], "rebellion_arc", "Keep tension")
    with pytest.raises(ValueError, match="changed"):
        room.apply_arc_guidance(db, "chat", "s1", first["revision"], "rebellion_arc", "Stale")
    current = room.director_room(db, "chat", "s1")
    room.apply_arc_guidance(db, "chat", "s1", current["revision"], "rebellion_arc", "")
    assert room.director_room(db, "chat", "s1")["arcs"][0]["guidance"] == ""


def test_ending_goal_change_invalidates_room_revision_and_old_ai_direction(session_db):
    db = ready_case(session_db)
    view = room.director_room(db, "chat", "s1")
    with write_transaction(db):
        db.execute(
            "INSERT INTO director_state(chat_id,session_id,active_direction,direction_source) "
            "VALUES('chat','s1','Obsolete AI plan','ai')"
        )
    view = room.director_room(db, "chat", "s1")
    room.apply_ending_goal(db, "chat", "s1", view["revision"], "Mara rejects the throne.")
    current = room.director_room(db, "chat", "s1")
    assert current["revision"] != view["revision"]
    assert current["ending"]["goal"] == "Mara rejects the throne."
    assert current["ending"]["history"][0]["source"] == "user"
    assert load_director_state(db, "chat", "s1")["active_direction"] == ""
    with pytest.raises(ValueError, match="changed"):
        room.apply_ending_goal(db, "chat", "s1", view["revision"], "Stale goal")


def test_ending_goal_panel_and_actor_input_preserve_exact_goal_without_story_mutation(session_db, monkeypatch):
    settings, db, session = session_db
    ready_case(session_db)
    revision = room.director_room(db, "chat", "s1")["revision"]
    director_input.begin_director_input(db, "chat", "s1", "alice", revision, "ending_goal")
    before = db.execute("SELECT * FROM messages").fetchall()
    monkeypatch.setattr(director_input, "send_text", lambda *a, **k: None)
    text = "Let Mara leave the throne behind."
    ctx = RequestContext(db, "s1", "alice", app_settings=settings)
    assert director_input.handle_director_input(db, "token", "chat", session, text, ctx)
    assert load_ending_state(db, "chat", "s1").current_goal == text
    output, markup = director_panels.director_panel(db, "chat", session, "alice", page="ending")
    assert text in output and "Ending" in output
    assert any("Edit ending goal" == b["text"] for row in markup["inline_keyboard"] for b in row)
    assert db.execute("SELECT * FROM messages").fetchall() == before


def test_telegram_arc_note_input_is_bound_to_the_rendered_arc(session_db, monkeypatch):
    settings, db, session = session_db
    ready_case(session_db)
    view = room.director_room(db, "chat", "s1")
    director_input.begin_director_input(db, "chat", "s1", "alice", view["revision"], "arc_note", arc_id="rebellion_arc")
    monkeypatch.setattr(director_input, "send_text", lambda *a, **k: None)
    assert director_input.handle_director_input(
        db,
        "token",
        "chat",
        session,
        "Focus on civilian consequences.",
        RequestContext(db, "s1", "alice", app_settings=settings),
    )
    assert room.director_room(db, "chat", "s1")["arcs"][0]["guidance"] == "Focus on civilian consequences."


@pytest.mark.parametrize(
    "lifecycle", ["finale", "resolution_committed", "epilogue_pending", "epilogue_committed", "closed"]
)
def test_ending_goal_controls_lock_at_finale_but_history_remains_readable(session_db, lifecycle):
    _, db, session = session_db
    ready_case(session_db)
    goal(db, "Original goal")
    with write_transaction(db):
        db.execute("UPDATE ending_state SET lifecycle=?", (lifecycle,))
    view = room.director_room(db, "chat", "s1")
    assert not view["ending"]["editable"]
    assert view["ending"]["history"]
    with pytest.raises(ValueError):
        room.apply_ending_goal(db, "chat", "s1", view["revision"], "Change the ending")
    _, markup = director_panels.director_panel(db, "chat", session, "alice", page="ending")
    assert not any("Edit ending goal" == b["text"] for row in markup["inline_keyboard"] for b in row)


def test_miniapp_ending_goal_and_arc_notes_use_shared_owners_and_enforce_session(tmp_path):
    services = make_services(tmp_path)
    who = identity()
    data = api.get_director(services, who, {})
    body = {
        "session_id": data["session_id"],
        "revision": data["revision"],
        "goal": "A peaceful ending if the story permits it.",
    }
    assert api.edit_ending_goal(services, who, body)["saved"]
    with closing(services.db_factory()) as db:
        assert load_ending_state(db, who.chat_id, data["session_id"]).current_goal == body["goal"]
    with pytest.raises(MiniAppError):
        api.edit_ending_goal(services, who, body | {"goal": "Stale"})
    with pytest.raises(MiniAppError):
        api.edit_ending_goal(services, identity("67890"), body)
    updated = api.get_director(services, who, {})
    with pytest.raises(MiniAppError):
        api.edit_arc_guidance(
            services,
            who,
            {
                "session_id": updated["session_id"],
                "revision": updated["revision"],
                "arc_id": "unknown",
                "direction": "fake",
            },
        )


def test_clear_ending_goal_is_consumed_by_pending_input_and_reports_correct_scope(session_db, monkeypatch):
    settings, db, session = session_db
    ready_case(session_db)
    goal(db, "A fixed destination")
    revision = room.director_room(db, "chat", "s1")["revision"]
    director_input.begin_director_input(db, "chat", "s1", "alice", revision, "ending_goal")
    notices = []
    monkeypatch.setattr(director_input, "send_text", lambda *a, **k: notices.append(a[2]))
    assert director_input.handle_director_input(
        db, "token", "chat", session, "/clear", RequestContext(db, "s1", "alice", app_settings=settings)
    )
    assert load_ending_state(db, "chat", "s1").current_goal == ""
    assert notices == ["Ending goal saved."]
