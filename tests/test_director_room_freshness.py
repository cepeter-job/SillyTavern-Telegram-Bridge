"""Room status matches canonical planning eligibility, without publishing story facts."""

import pytest
from test_director_service import directed as directed
from test_director_service import reassess
from test_director_service import session_db as session_db
from test_narrative_reconciliation import add_story

from bridge.director_guidance import active_director_plan, director_guidance_for_session
from bridge.director_room import director_room
from bridge.sqlite_store import write_transaction


def test_room_marks_unreconciled_direction_inactive(directed):
    _, db, _ = directed
    assert reassess(directed).result == "accepted"
    saved_direction = director_room(db, "chat", "s1")["direction"]
    add_story(db, "A new synthetic event awaits reconciliation.")
    before = db.execute("SELECT * FROM messages").fetchall()

    room = director_room(db, "chat", "s1")

    assert room["direction"] == saved_direction
    assert room["direction_status"] == "inactive"
    assert room["narrative_current"] is False
    assert active_director_plan(db, "chat", "s1") is None
    assert director_guidance_for_session(db, "chat", "s1") == ""
    assert db.execute("SELECT * FROM messages").fetchall() == before


def test_telegram_labels_stale_direction_as_last_accepted(directed):
    from bridge.director_panels import director_panel

    _, db, session = directed
    assert reassess(directed).result == "accepted"
    add_story(db, "Another synthetic event awaits reconciliation.")

    text, _ = director_panel(db, "chat", session, "alice")

    assert "Last accepted direction (inactive)" in text
    assert "<b>Current direction</b>" not in text
    assert "not currently used" in text
    assert "Narrative continuity is not current" in text


def test_current_and_empty_direction_status(directed):
    _, db, _ = directed
    empty = director_room(db, "chat", "s1")
    assert empty["direction_status"] == "none"
    assert empty["narrative_current"] is True
    assert reassess(directed).result == "accepted"
    assert director_room(db, "chat", "s1")["direction_status"] == "active"


@pytest.mark.parametrize(
    "statement",
    [
        "UPDATE director_state SET accepted_rewrite_revision=999",
        "UPDATE director_state SET accepted_settings_revision=999",
        "UPDATE director_state SET accepted_scene_id='other-scene'",
        "UPDATE director_state SET direction_until_turn=0",
    ],
    ids=["rewrite", "settings", "scene", "ttl"],
)
def test_room_does_not_bypass_canonical_direction_invalidation(directed, statement):
    _, db, _ = directed
    assert reassess(directed).result == "accepted"
    with write_transaction(db):
        db.execute(statement)
    room = director_room(db, "chat", "s1")
    assert room["narrative_current"] is True
    assert room["direction_status"] == "inactive"
    assert active_director_plan(db, "chat", "s1") is None
