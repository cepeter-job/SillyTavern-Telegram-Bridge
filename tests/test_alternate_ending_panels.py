"""Alternate-ending controls use one actor-bound request ID per admitted action."""

import json

import pytest
from application_test_setup import make_test_application_services
from miniapp_test_support import identity
from test_alternate_ending import close_story
from test_director_panels import dispatch
from test_memory_completion_safety import session_db as session_db

from bridge import director_callbacks, miniapp_director, miniapp_jobs
from bridge.callback_tokens import resolve_dynamic_callback_token
from bridge.conversation_lifecycle import conversation_state
from bridge.director_panels import director_panel
from bridge.director_room import director_room
from bridge.ending_service import load_ending_state
from bridge.miniapp_errors import MiniAppError
from bridge.miniapp_http import api_routes
from bridge.sqlite_store import db_connect


def alternative_button(case):
    _, db, session = case
    _, markup = director_panel(db, "chat", session, "alice", page="ending")
    return next(b for row in markup["inline_keyboard"] for b in row if b["text"] == "Alternate Ending")


def test_button_only_appears_for_completed_story_with_linked_checkpoint(session_db):
    _, db, _session = session_db
    assert not director_room(db, "chat", "s1")["ending"]["alternate_available"]
    close_story(session_db)
    assert director_room(db, "chat", "s1")["ending"]["alternate_available"]
    button = alternative_button(session_db)
    values = json.loads(
        resolve_dynamic_callback_token(button["callback_data"].split(":", 1)[1], "director", "chat", db=db)
    )
    assert (
        values["actor_id"] == "alice" and values["checkpoint_id"] == load_ending_state(db, "chat", "s1").checkpoint_id
    )
    assert values["operation_id"]
    again = alternative_button(session_db)
    values2 = json.loads(
        resolve_dynamic_callback_token(again["callback_data"].split(":", 1)[1], "director", "chat", db=db)
    )
    assert values2["operation_id"] != values["operation_id"]


def test_telegram_action_is_actor_bound_and_double_delivery_creates_one_branch(session_db, monkeypatch):
    _, db, _ = session_db
    close_story(session_db)
    button = alternative_button(session_db)
    notices = []
    monkeypatch.setattr(director_callbacks, "send_text", lambda *args: notices.append(args[-1]))
    monkeypatch.setattr(director_callbacks, "seed_alternate_ending_memory", lambda *a, **k: "disabled")
    dispatch(session_db, button["callback_data"], actor="bob")
    assert db.execute("SELECT COUNT(*) FROM narrative_branches").fetchone()[0] == 0
    dispatch(session_db, button["callback_data"])
    dispatch(session_db, button["callback_data"])
    assert db.execute("SELECT COUNT(*) FROM narrative_branches").fetchone()[0] == 1
    assert load_ending_state(db, "chat", "s1").lifecycle == "closed"
    assert any("Alternate" in message for message in notices)


def test_miniapp_job_retries_reuse_same_request_after_target_activation(session_db, monkeypatch):
    config, db, _ = session_db
    close_story(session_db)
    services = make_test_application_services(app_settings=config)
    services.db_factory = lambda: db_connect(app_settings=config)
    who = identity("chat")
    data = director_room(db, "chat", "s1")
    body = {
        "session_id": "s1",
        "epoch": conversation_state(db, "chat", "s1").epoch,
        "revision": data["revision"],
        "checkpoint_id": data["ending"]["checkpoint_id"],
        "operation_id": "miniapp-alternate-once",
        "confirm": True,
    }
    monkeypatch.setattr(miniapp_director, "seed_alternate_ending_memory", lambda *a, **k: "disabled")
    monkeypatch.setattr(miniapp_jobs, "submit_background", lambda _name, func: (func(), True)[1])
    with pytest.raises(MiniAppError):
        miniapp_director.alternate_ending(services, who, body | {"confirm": False})
    with pytest.raises(MiniAppError):
        miniapp_director.alternate_ending(services, identity("67890"), body)
    first = miniapp_jobs.submit_job(services, who, "alternate_ending", body, miniapp_director.alternate_ending)
    assert first["state"] == "succeeded" and first["result"]["applied"]
    repeated = miniapp_jobs.submit_job(services, who, "alternate_ending", body, miniapp_director.alternate_ending)
    assert repeated["id"] == first["id"]
    assert db.execute("SELECT COUNT(*) FROM narrative_branches").fetchone()[0] == 1
    assert load_ending_state(db, "chat", first["result"]["session"]["session_id"]).lifecycle == "open"
    match = [r for r in api_routes() if r.path == "/director/alternate-ending"]
    assert len(match) == 1 and match[0].background_kind == "alternate_ending"
