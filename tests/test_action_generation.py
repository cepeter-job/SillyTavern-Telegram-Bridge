"""Real preflight, receipt and Story ordering, with only external calls replaced."""

import json
import time

import pytest
from application_test_setup import make_test_application_services, make_test_provider_port
from test_memory_completion_safety import session_db as session_db
from test_npc_generation_wiring import _fields
from test_simulation_trackers import _assistant_row

from bridge.metadata import set_meta


def prepare(case, generate, *, action="I try to open the door quietly.", key="message:42", **kwargs):
    from bridge.action_adjudication import prepare_action_context

    settings, db, session = case
    messages = [{"role": "system", "content": "Write the story."}, {"role": "user", "content": action}]
    ticket = prepare_action_context(
        db,
        "chat",
        session,
        _fields(),
        action,
        messages,
        request_key=key,
        actor_id="player",
        provider_port=make_test_provider_port(generate_backend=generate),
        app_settings=settings,
        **kwargs,
    )
    return ticket, messages


def check_response(messages):
    snapshot = json.loads(messages[-1]["content"])
    source, content = next(iter(snapshot["evidence"].items()))
    return json.dumps(
        {
            "schema_version": 1,
            "decision": "check",
            "domain": "stealth",
            "dc": 13,
            "reason": "An established guard may hear the attempt.",
            "success": "The attempted opening is quiet.",
            "failure": "The attempt is audible.",
            "evidence": [{"source": source, "quote": content}],
        }
    )


def test_preflight_saves_one_result_and_retry_has_no_model_call(session_db, monkeypatch):
    from bridge import action_repository

    _, db, _ = session_db
    _assistant_row(db, "A guard waits beside the noisy door.")
    calls = []
    monkeypatch.setattr(action_repository.secrets, "randbelow", lambda n: 4)

    def generate(_key, model, messages, **kwargs):
        assert not db.in_transaction
        calls.append((model, kwargs["session_id"]))
        return check_response(messages)

    ticket, messages = prepare(session_db, generate)
    assert ticket["result"]["roll"] == 5
    assert db.execute("SELECT status FROM action_attempts").fetchone() == ("ready",)
    assert "Locked action result" in messages[0]["content"]
    assert "An established guard" not in messages[0]["content"]
    again, _ = prepare(session_db, lambda *a, **k: pytest.fail("Retry called adjudicator"))
    assert again["result"] == ticket["result"]
    assert len(calls) == 1 and calls[0][1].startswith("action:")


@pytest.mark.parametrize("action", ["[Narrative steering] Follow Maya.", "/status", "[Next Scene]"])
def test_steering_does_not_request_adjudication(session_db, action):
    _assistant_row(session_db[1], "A guard waits beside the noisy door.")
    ticket, _ = prepare(session_db, lambda *a, **k: pytest.fail("Steering adjudicated"), action=action)
    assert ticket is None


@pytest.mark.parametrize("decision", ["no_check", "auto_success"])
def test_routine_action_never_rolls(session_db, monkeypatch, decision):
    from bridge import action_repository

    _assistant_row(session_db[1], "A cup rests on the table.")
    monkeypatch.setattr(action_repository.secrets, "randbelow", lambda *a: pytest.fail("Routine rolled"))
    ticket, messages = prepare(
        session_db, lambda *a, **k: json.dumps({"schema_version": 1, "decision": decision}), action="I take the cup."
    )
    assert ticket["result"] == {"decision": decision}
    assert "Locked action result" not in messages[0]["content"]


@pytest.mark.parametrize("mode,expected", [("auto", "dummy::utility"), ("director", "dummy::director")])
def test_selected_model_route_is_used_once(session_db, mode, expected):
    from bridge.action_settings import set_action_mode
    from bridge.model_selection import set_task_model

    _, db, session = session_db
    _assistant_row(db, "A guard waits beside the noisy door.")
    set_action_mode(db, "chat", "s1", mode)
    set_task_model(db, "chat", "s1", "dummy::utility", "utility")
    set_task_model(db, "chat", "s1", "dummy::director", "director")
    observed = []

    def generate(_key, model, messages, **kwargs):
        observed.append(model)
        return check_response(messages)

    prepare(session_db, generate)
    assert observed == [expected]
    assert session["session_id"] == "s1"


def test_manual_mode_skips_preflight_but_reuses_manual_check(session_db):
    from bridge.action_settings import set_action_mode
    from bridge.simulation_checks import perform_check

    _, db, _ = session_db
    set_action_mode(db, "chat", "s1", "manual")
    source = db.execute(
        "INSERT INTO messages(chat_id,session_id,role,content,created_at) VALUES('chat','s1','user',?,?)",
        ("[Check action: stealth, DC 13] open the door quietly", time.time()),
    ).lastrowid
    db.commit()
    perform_check(
        db,
        "chat",
        "s1",
        request_key="manual:1",
        domain="stealth",
        actor="user",
        action="open the door quietly",
        dc=13,
        source_rowid=source,
        roll=7,
    )
    ticket, messages = prepare(session_db, lambda *a, **k: pytest.fail("Manual check was rolled again"))
    assert ticket is None
    assert '"roll": 7' in messages[0]["content"]
    _assistant_row(db, "The latch clicks.")
    _, messages = prepare(session_db, lambda *a, **k: pytest.fail("Manual mode called a model"), key="message:43")
    assert "Locked action result" not in messages[0]["content"]


@pytest.mark.parametrize(
    "raw", ["not json", '{"decision":"check"}', '{"schema_version":1,"decision":"check","roll":20}']
)
def test_invalid_preflight_does_not_roll_or_commit(session_db, monkeypatch, raw):
    from bridge import action_repository

    _, db, _ = session_db
    _assistant_row(db, "A guard waits beside the noisy door.")
    monkeypatch.setattr(action_repository.secrets, "randbelow", lambda *a: pytest.fail("Invalid response rolled"))
    with pytest.raises(ValueError):
        prepare(session_db, lambda *a, **k: raw)
    assert db.execute("SELECT COUNT(*) FROM messages").fetchone() == (1,)
    assert db.execute("SELECT status,lease_until FROM action_attempts").fetchone() == ("pending", 0)
    assert db.execute("SELECT COUNT(*) FROM simulation_checks").fetchone() == (0,)


def test_new_action_pipeline_orders_adjudication_before_story_and_binds_result(session_db, monkeypatch):
    from bridge import message_commands
    from bridge.conversation_lifecycle import conversation_state, mark_started

    settings, db, session = session_db
    session.update(response_language="auto", humanizer="off", _actor_id="player")
    mark_started(db, "chat", "s1", conversation_state(db, "chat", "s1").epoch)
    _assistant_row(db, "A guard waits beside the noisy door.")
    set_meta(db, "stream_mode:chat", "off")
    order = []

    def generate(_key, _model, messages, **kwargs):
        assert not db.in_transaction
        if kwargs["session_id"].startswith("action:"):
            order.append("adjudicate")
            return check_response(messages)
        order.append("story")
        assert db.execute("SELECT status FROM action_attempts").fetchone() == ("ready",)
        assert "Locked action result" in messages[0]["content"]
        return "The attempt follows the saved outcome."

    services = make_test_application_services(
        app_settings=settings, provider=make_test_provider_port(generate_backend=generate)
    )
    for name in ("send_typing", "send_reply", "queue_user_quote_tts"):
        monkeypatch.setattr(message_commands, name, lambda *a, **k: None)
    message_commands.generate_and_store_reply(
        db,
        "token",
        "key",
        _fields(),
        "chat",
        "I try to open the door quietly.",
        session,
        "s1",
        session["model_id"],
        None,
        "",
        42,
        None,
        group_service=services.group,
        provider_port=services.provider,
        memory_service=services.memory,
        npc_service=services.npc,
        persona_service=services.persona,
        app_settings=settings,
        rag_service=services.rag,
    )
    assert order == ["adjudicate", "story"]
    assert db.execute("SELECT status FROM action_attempts").fetchone() == ("bound",)
    assert db.execute("SELECT m.role FROM simulation_checks c JOIN messages m ON m.id=c.source_rowid").fetchone() == (
        "user",
    )
