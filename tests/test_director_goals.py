"""Group goal is an authenticated surface of the canonical persistent Director objective."""

from pathlib import Path

import pytest
from test_memory_completion_safety import session_db as session_db

from bridge.director_goals import director_goal_policy, get_director_goal, set_director_goal
from bridge.director_repository import load_director_state
from bridge.director_room import apply_direction, director_room
from bridge.group_director_service import GroupDirectorService
from bridge.model_selection import set_task_model, task_model_for_session
from bridge.sqlite_store import write_transaction


def test_goal_is_session_local_and_whitespace_normalized(session_db):
    _, db, _ = session_db
    assert set_director_goal(db, "chat", "s1", "  Reveal   the door slowly. ") == "Reveal the door slowly."
    assert get_director_goal(db, "chat", "s1") == "Reveal the door slowly."
    assert get_director_goal(db, "other", "s1") == ""
    assert get_director_goal(db, "chat", "missing") == ""


@pytest.mark.parametrize("invalid", [None, {}, "x" * 4001])
def test_goal_limit_does_not_silently_truncate_or_mutate(session_db, invalid):
    _, db, _ = session_db
    set_director_goal(db, "chat", "s1", "Keep this goal.")
    before = load_director_state(db, "chat", "s1")["state_revision"]
    with pytest.raises(ValueError):
        set_director_goal(db, "chat", "s1", invalid)
    assert get_director_goal(db, "chat", "s1") == "Keep this goal."
    assert load_director_state(db, "chat", "s1")["state_revision"] == before


def test_goal_and_director_room_share_revision_history(session_db):
    _, db, _ = session_db
    set_director_goal(db, "chat", "s1", "Keep tension unresolved.")
    view = director_room(db, "chat", "s1")
    assert view["history"][0]["direction"] == "Keep tension unresolved."
    assert view["history"][0]["source"] == "user"
    apply_direction(db, "chat", "s1", view["revision"], "Protect the witness.", "persistent")
    assert get_director_goal(db, "chat", "s1") == "Protect the witness."
    assert len(director_room(db, "chat", "s1")["history"]) == 2


def test_group_goal_setter_invalidates_inflight_ai_proposal_lease(session_db):
    _, db, _ = session_db
    set_director_goal(db, "chat", "s1", "Before")
    with write_transaction(db):
        db.execute(
            "UPDATE director_state SET inflight_token='old-worker',inflight_started_at=1 "
            "WHERE chat_id='chat' AND session_id='s1'"
        )
    before = load_director_state(db, "chat", "s1")["state_revision"]
    set_director_goal(db, "chat", "s1", "User overrides the previous plan.")
    saved = load_director_state(db, "chat", "s1")
    assert saved["state_revision"] > before and saved["inflight_token"] == ""


def test_goal_changes_join_caller_transaction_and_rollback_together(session_db):
    _, db, _ = session_db
    db.execute("BEGIN")
    set_director_goal(db, "chat", "s1", "Pending goal")
    assert db.in_transaction
    assert director_room(db, "chat", "s1")["history"]
    db.rollback()
    assert get_director_goal(db, "chat", "s1") == ""
    assert director_room(db, "chat", "s1")["history"] == []


@pytest.mark.parametrize("lifecycle", ["resolution_committed", "epilogue_pending", "epilogue_committed", "closed"])
def test_group_goal_cannot_mutate_closing_or_closed_story(session_db, lifecycle):
    _, db, _ = session_db
    set_director_goal(db, "chat", "s1", "Saved objective")
    with write_transaction(db):
        db.execute("INSERT INTO ending_state(chat_id,session_id,lifecycle) VALUES('chat','s1',?)", (lifecycle,))
    with pytest.raises(ValueError, match=r"closing|ended"):
        set_director_goal(db, "chat", "s1", "")
    assert get_director_goal(db, "chat", "s1") == "Saved objective"


def test_clearing_goal_is_an_audited_revision_not_state_deletion(session_db):
    _, db, _ = session_db
    set_director_goal(db, "chat", "s1", "Resolve the argument.")
    set_director_goal(db, "chat", "s1", "")
    view = director_room(db, "chat", "s1")
    assert view["objective"] == ""
    assert len(view["history"]) == 2


def test_model_routing_is_owned_by_canonical_director_not_group_adapter(session_db):
    settings, db, session = session_db
    set_task_model(db, "chat", "s1", "utility::fallback", "utility")
    assert task_model_for_session(db, "chat", session, "director", app_settings=settings) == "utility::fallback"
    set_task_model(db, "chat", "s1", "director::special", "director")
    set_director_goal(db, "chat", "s1", "Protect the archive.")
    policy = director_goal_policy(db, "chat", session)
    assert not hasattr(policy, "model")
    assert "Protect the archive." in policy.speaker_context
    assert task_model_for_session(db, "chat", session, "director", app_settings=settings) == "director::special"
    set_task_model(db, "chat", "s1", "", "director")
    assert task_model_for_session(db, "chat", session, "director", app_settings=settings) == "utility::fallback"
    set_task_model(db, "chat", "s1", "", "utility")
    assert task_model_for_session(db, "chat", session, "director", app_settings=settings) == session["model_id"]


def test_generation_context_keeps_shared_goal_hidden_but_actionable(session_db):
    _, db, session = session_db
    set_director_goal(db, "chat", "s1", "Keep the letter unopened.")
    service = GroupDirectorService(
        load_group_state=lambda *_: {
            "enabled": True,
            "mode": "director",
            "turn_index": 0,
            "members": ["Alice.png", "Bob.png"],
        },
        safe_character=Path,
        member_labels=lambda members: members,
        card_fields=lambda filename: {"name": Path(filename).stem},
        director_policy=director_goal_policy,
    )
    before = db.execute("SELECT * FROM messages").fetchall()
    context = service.prompt_context(db, "chat", session, "Alice.png", "Keep the pace measured.")
    assert "Keep the letter unopened" in context and "Never mention" in context
    assert "not a fact" in context
    assert db.execute("SELECT * FROM messages").fetchall() == before


def test_group_goal_panel_is_bounded_in_telegram_utf16_units():
    from bridge.director_goal_panel import director_goal_panel

    text, _ = director_goal_panel("🌍" * 4000)
    assert len(text.encode("utf-16-le")) // 2 < 4096
    assert "/director" in text


@pytest.mark.parametrize("source", ["ai", "user"])
def test_persistent_objective_supersedes_ai_plan_but_preserves_explicit_user_scene_plan(session_db, source):
    _, db, _ = session_db
    set_director_goal(db, "chat", "s1", "Old objective")
    with write_transaction(db):
        db.execute(
            "UPDATE director_state SET active_direction='Old scene',active_proposal_json='{}',direction_source=?",
            (source,),
        )
    set_director_goal(db, "chat", "s1", "A new objective that may contradict an old AI direction")
    current = load_director_state(db, "chat", "s1")
    assert current["active_direction"] == ("Old scene" if source == "user" else "")
