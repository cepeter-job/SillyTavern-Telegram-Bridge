"""Exercise the real ordinary-turn commit/retry boundary with synthetic transport."""

import pytest
from application_test_setup import make_test_application_services, make_test_provider_port
from test_action_runtime import output
from test_memory_completion_safety import session_db as session_db
from test_simulation_trackers import _assistant_row

from bridge.conversation_lifecycle import mark_started
from bridge.job_store import enqueue_job
from bridge.metadata import set_meta
from bridge.simulation_repository import list_checks


def generation(case, monkeypatch, generate, *, actor="actor"):
    from bridge import message_commands

    settings, db, session = case
    mark_started(db, "chat", "s1", 0)
    set_meta(db, "stream_mode:chat", "off")
    session["_actor_id"] = actor
    port = make_test_provider_port(generate_backend=generate)
    services = make_test_application_services(app_settings=settings, provider=port)
    for name in ("send_typing", "queue_user_quote_tts", "send_reply"):
        monkeypatch.setattr(message_commands, name, lambda *a, **k: None)
    monkeypatch.setattr(
        message_commands, "build_chat_messages", lambda *a, **k: [{"role": "user", "content": "Action"}]
    )
    return lambda job: message_commands.generate_and_store_reply(
        db,
        "",
        "",
        {"name": "Alice"},
        "chat",
        "I quietly open the door.",
        session,
        "s1",
        session["model_id"],
        None,
        "",
        101,
        job,
        group_service=services.group,
        provider_port=port,
        memory_service=services.memory,
        npc_service=services.npc,
        persona_service=services.persona,
        app_settings=settings,
        rag_service=services.rag,
    )


def job(db, actor="actor"):
    return enqueue_job(db, 100, "chat", "s1", 101, "message", {"actor_id": actor, "text": "I quietly open the door."})


@pytest.mark.parametrize("retry", ["same_job", "new_job", "command"])
def test_failed_story_retry_reuses_preflight_then_binds_one_check(session_db, monkeypatch, retry):
    _, db, _ = session_db
    _assistant_row(db, "A guard waits beside the noisy door.")
    calls, results = [], []

    def generate(_key, _model, messages, **kwargs):
        stage = "preflight" if kwargs["session_id"].startswith("action:") else "story"
        calls.append(stage)
        if stage == "preflight":
            return output(db)
        assert "Locked action result" in str(messages)
        results.append(str(messages[-1]))
        if len(results) == 1:
            raise RuntimeError("Synthetic story outage")
        return "*The latch clicks softly.*"

    run = generation(session_db, monkeypatch, generate)
    action_job = job(db)
    with pytest.raises(RuntimeError, match="Synthetic"):
        run(action_job)
    assert db.execute("SELECT COUNT(*) FROM action_preflights").fetchone()[0] == 1
    assert list_checks(db, "chat", "s1") == []
    retry_job = (
        enqueue_job(db, 200, "chat", "s1", 101, "generation", {"actor_id": "actor"})
        if retry == "new_job"
        else None
        if retry == "command"
        else action_job
    )
    run(retry_job)
    assert calls == ["preflight", "story", "story"]
    assert results[0] == results[1]
    check = list_checks(db, "chat", "s1")[0]
    assert (
        db.execute("SELECT content FROM messages WHERE id=?", (check["source_rowid"],)).fetchone()[0]
        == "I quietly open the door."
    )
    assert db.execute("SELECT COUNT(*) FROM action_preflights").fetchone()[0] == 0


def test_job_actor_mismatch_is_rejected_before_provider(session_db, monkeypatch):
    _, db, _ = session_db
    run = generation(session_db, monkeypatch, lambda *a, **k: pytest.fail("Wrong actor called provider"))
    with pytest.raises(ValueError, match="actor"):
        run(job(db, "other"))


def test_locked_result_survives_ordinary_prompt_budget(session_db):
    from bridge.action_adjudication import locked_action_messages
    from bridge.generation import finalize_generation_messages
    from bridge.simulation_checks import perform_check

    settings, db, session = session_db
    source = _assistant_row(db)
    perform_check(
        db,
        "chat",
        "s1",
        request_key="auto:old",
        domain="stealth",
        actor="user",
        action="open",
        dc=12,
        roll=7,
        source_rowid=source,
    )
    messages = locked_action_messages(db, "chat", "s1", [{"role": "user", "content": "Continue"}], source)
    result = finalize_generation_messages(db, "chat", session, messages, {"max_tokens": 768}, app_settings=settings)
    assert "Locked action result" in str(result) and '"roll": 7' in str(result)


def test_check_mode_control_is_session_scoped_and_non_generating(session_db):
    from application_test_setup import make_test_delivery_port

    from bridge.action_adjudication import action_mode
    from bridge.simulation_commands import handle_check_command

    _, db, _ = session_db
    delivered = []
    delivery = make_test_delivery_port(send_text=lambda *a, **k: delivered.append(a[-1]))
    handle_check_command(db, "", "chat", "s1", "/check mode director", "actor", None, delivery)
    assert action_mode(db, "chat", "s1") == "director"
    assert "Director" in delivered[-1]
    assert db.execute("SELECT COUNT(*) FROM messages").fetchone()[0] == 0


@pytest.mark.parametrize("route", ["regen", "continue"])
def test_answer_rewrites_reuse_the_original_check_without_preflight(session_db, monkeypatch, route):
    from bridge import continuation, regeneration
    from bridge.simulation_checks import perform_check
    from bridge.sqlite_store import write_transaction

    settings, db, session = session_db
    mark_started(db, "chat", "s1", 0)
    with write_transaction(db):
        source = db.execute(
            "INSERT INTO messages(chat_id,session_id,role,content,created_at) VALUES('chat','s1','user','Open door',1)"
        ).lastrowid
    _assistant_row(db, "The latch clicked.")
    perform_check(
        db,
        "chat",
        "s1",
        request_key="auto:original",
        domain="stealth",
        actor="user",
        action="Open door",
        dc=13,
        roll=3,
        source_rowid=source,
    )
    observed = []

    def generate(_key, _model, messages, **kwargs):
        observed.append(messages)
        assert not kwargs["session_id"].startswith("action:")
        assert '"roll": 3' in str(messages) and "Locked action result" in str(messages)
        return "*A guard heard the sound.*"

    port = make_test_provider_port(generate_backend=generate)
    services = make_test_application_services(app_settings=settings, provider=port)
    module = regeneration if route == "regen" else continuation
    monkeypatch.setattr(module, "build_chat_messages", lambda *a, **k: [{"role": "user", "content": "Rewrite"}])
    function = regeneration.regenerate_last if route == "regen" else continuation.continue_last
    function(
        db,
        "",
        "",
        session,
        {"name": "Alice"},
        "chat",
        provider_port=port,
        delivery_port=services.delivery,
        memory_service=services.memory,
        npc_service=services.npc,
        persona_service=services.persona,
        app_settings=settings,
        rag_service=services.rag,
    )
    assert len(observed) == 1
    assert list_checks(db, "chat", "s1")[0]["roll"] == 3


def test_edited_user_action_replaces_old_check_only_at_commit(session_db, monkeypatch):
    from test_action_adjudication import proposal

    from bridge import edit_messages
    from bridge.simulation_checks import perform_check
    from bridge.sqlite_store import write_transaction

    settings, db, session = session_db
    mark_started(db, "chat", "s1", 0)
    guard = _assistant_row(db, "A guard waits beside the noisy door.")
    with write_transaction(db):
        user = db.execute(
            "INSERT INTO messages(chat_id,session_id,role,content,created_at) VALUES('chat','s1','user','Old action',2)"
        ).lastrowid
    _assistant_row(db, "The old result.")
    perform_check(
        db,
        "chat",
        "s1",
        request_key="auto:old",
        domain="stealth",
        actor="user",
        action="Old action",
        dc=13,
        roll=3,
        source_rowid=user,
    )
    calls = []

    def generate(_key, _model, messages, **kwargs):
        calls.append(kwargs["session_id"])
        if kwargs["session_id"].startswith("action:"):
            assert "The old result" not in str(messages)
            return proposal(evidence=[{"source": str(guard), "quote": "A guard waits beside the noisy door."}])
        assert "Locked action result" in str(messages)
        assert len(list_checks(db, "chat", "s1")) == 1
        return "*The revised latch attempt makes a sound.*"

    port = make_test_provider_port(generate_backend=generate)
    services = make_test_application_services(app_settings=settings, provider=port)
    for name in ("send_typing", "send_reply"):
        monkeypatch.setattr(edit_messages, name, lambda *a, **k: None)
    monkeypatch.setattr(edit_messages, "build_chat_messages", lambda *a, **k: [{"role": "user", "content": "Edit"}])
    edit_messages.regenerate_edited_turn(
        db,
        "",
        "",
        session,
        {"name": "Alice"},
        "chat",
        user,
        "I quietly open the door.",
        job(db),
        provider_port=port,
        memory_service=services.memory,
        npc_service=services.npc,
        persona_service=services.persona,
        app_settings=settings,
        rag_service=services.rag,
    )
    checks = list_checks(db, "chat", "s1")
    assert len(checks) == 1 and checks[0]["request_key"] != "auto:old"
    assert checks[0]["source_rowid"] == user and len(calls) == 2


def test_image_caption_is_checked_before_story_and_bound_to_image_input(session_db, monkeypatch):
    from bridge import image_messages

    settings, db, session = session_db
    mark_started(db, "chat", "s1", 0)
    _assistant_row(db, "A guard waits beside the noisy door.")
    calls = []

    def generate(_key, _model, messages, **kwargs):
        calls.append(kwargs["session_id"])
        if kwargs["session_id"].startswith("action:"):
            assert "image_url" not in str(messages)
            return output(db)
        assert "Locked action result" in str(messages)
        return "*The latch makes a sound.*"

    port = make_test_provider_port(generate_backend=generate)
    services = make_test_application_services(app_settings=settings, provider=port)
    for name in ("send_typing", "send_reply"):
        monkeypatch.setattr(image_messages, name, lambda *a, **k: None)
    monkeypatch.setattr(image_messages, "build_chat_messages", lambda *a, **k: [{"role": "user", "content": "Image"}])
    image_messages.process_image_message(
        db,
        "",
        "",
        session,
        {"name": "Alice"},
        "chat",
        "I quietly open the door.",
        b"synthetic-image",
        operation_id=job(db),
        group_service=services.group,
        provider_port=port,
        memory_service=services.memory,
        npc_service=services.npc,
        persona_service=services.persona,
        group_director_service=services.group_director,
        app_settings=settings,
        rag_service=services.rag,
    )
    check = list_checks(db, "chat", "s1")[0]
    content = db.execute("SELECT content FROM messages WHERE id=?", (check["source_rowid"],)).fetchone()[0]
    assert content == "[Image input] I quietly open the door." and len(calls) == 2
