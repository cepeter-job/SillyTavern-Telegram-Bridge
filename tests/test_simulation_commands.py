"""A check admits one user action and one locked roll through durable command recovery."""

from unittest.mock import Mock

import pytest
from application_test_setup import (
    ensure_application_extensions,
    make_test_conversation_service,
    make_test_delivery_port,
    make_test_provider_port,
)
from test_memory_completion_safety import session_db as session_db
from test_npc_generation_wiring import _fields

from bridge.conversation_lifecycle import conversation_state, mark_started
from bridge.delivery_progress import DeliveryFailure, DeliveryTargetExpired
from bridge.job_store import enqueue_job
from bridge.operations import operation_phase
from bridge.simulation_service import SimulationService
from bridge.sqlite_store import write_transaction


@pytest.fixture
def command_case(session_db, monkeypatch):
    config, db, session = session_db
    mark_started(db, "chat", "s1", conversation_state(db, "chat", "s1").epoch)
    monkeypatch.setattr("bridge.message_commands.card_fields_from_file", lambda *a, **k: _fields())
    ensure_application_extensions()
    sent = Mock(return_value=[123])
    monkeypatch.setattr("bridge.command_routes.send_text", sent)
    monkeypatch.setattr("bridge.telegram.send_text", sent)
    monkeypatch.setattr(
        "bridge.telegram.urllib.request.urlopen", lambda *a, **k: pytest.fail("Unexpected network call")
    )
    service = make_test_conversation_service(
        app_settings=config,
        delivery=make_test_delivery_port(send_text=sent),
        provider=make_test_provider_port(generate_backend=lambda *a, **k: pytest.fail("Check must not call a model")),
    )
    return db, session, service, sent


def invoke(case, command="/check stealth 10 cross the courtyard", operation_id=601, actor="owner"):
    db, session, service, _ = case
    service.process_message(
        db,
        "token",
        "key",
        session["model_id"],
        _fields(),
        "chat",
        command,
        queued_session_id="s1",
        operation_id=operation_id,
        actor_id=actor,
    )


# Bare /check opens the mode panel; this table covers malformed explicit checks only.
@pytest.mark.parametrize(
    "command",
    ["/check stealth x sneak", "/check stealth 0 sneak", "/check stealth 21 sneak", "/check stealth 10"],
)
def test_invalid_check_admits_no_action(command_case, command):
    db, _, _, sent = command_case
    invoke(command_case, command)
    assert db.execute("SELECT COUNT(*) FROM messages").fetchone()[0] == 0
    assert db.execute("SELECT COUNT(*) FROM simulation_checks").fetchone()[0] == 0
    assert "/check <domain> <DC> <action>" in sent.call_args.args[2]


def test_replayed_check_rolls_once_without_an_assistant_timer_turn(command_case, monkeypatch):
    db, _, _, sent = command_case
    rng = Mock(return_value=11)
    monkeypatch.setattr("bridge.simulation_checks.secrets.randbelow", rng)
    invoke(command_case)
    invoke(command_case)
    assert rng.call_count == 1 and sent.call_count == 1
    assert db.execute("SELECT role FROM messages").fetchall() == [("user",)]
    assert db.execute("SELECT roll,outcome FROM simulation_checks").fetchone() == (12, "success")
    assert "roll 12" in SimulationService.context_for_prompt(db, "chat", "s1")


def test_delivery_retry_keeps_original_action_roll_and_result(command_case, monkeypatch):
    db, _, _, sent = command_case
    sent.side_effect = [OSError("Delivery interrupted"), [123]]
    rng = Mock(return_value=11)
    monkeypatch.setattr("bridge.simulation_checks.secrets.randbelow", rng)
    with pytest.raises(DeliveryFailure):
        invoke(command_case)
    assert operation_phase(db, 601) == "local_committed"
    invoke(command_case)
    assert operation_phase(db, 601) == "applied" and rng.call_count == 1
    assert sent.call_args_list[0].args[2] == sent.call_args_list[1].args[2]
    assert db.execute("SELECT COUNT(*) FROM messages").fetchone()[0] == 1


def test_rewritten_source_cancels_delivery_instead_of_rerolling(command_case):
    db, _, _, sent = command_case
    sent.side_effect = OSError("Delivery interrupted")
    with pytest.raises(DeliveryFailure):
        invoke(command_case)
    with write_transaction(db):
        db.execute("UPDATE messages SET content='Replaced action'")
    sent.side_effect = None
    with pytest.raises(DeliveryTargetExpired):
        invoke(command_case)
    assert sent.call_count == 1


def test_manual_retry_uses_original_job_and_actor(command_case):
    db, _, _, sent = command_case
    original = enqueue_job(db, 900, "chat", "s1", 80, "generation", {"actor_id": "owner"})
    sent.side_effect = OSError("Delivery interrupted")
    with pytest.raises(DeliveryFailure):
        invoke(command_case, operation_id=original)
    with write_transaction(db):
        db.execute(
            "UPDATE jobs SET state='failed',last_error='delivery incomplete: network' WHERE job_id=?", (original,)
        )
    sent.side_effect = None
    invoke(command_case, "/retry", operation_id=602, actor="other")
    assert "original actor" in sent.call_args.args[2]
    assert operation_phase(db, original) == "local_committed"
    invoke(command_case, "/retry", operation_id=603)
    assert operation_phase(db, original) == "applied"
    assert db.execute("SELECT COUNT(*) FROM messages").fetchone()[0] == 1
    assert db.execute("SELECT state FROM jobs WHERE job_id=?", (original,)).fetchone()[0] == "done"


def test_unstarted_check_has_no_state_changes(command_case):
    from bridge.conversation_lifecycle import reset_conversation

    db, _, _, sent = command_case
    reset_conversation(db, "chat", "s1")
    invoke(command_case)
    assert "Please use /start" in sent.call_args.args[2]
    assert db.execute("SELECT COUNT(*) FROM simulation_checks").fetchone()[0] == 0


@pytest.mark.parametrize("prefix", ["/CHECK", "/check@BridgeBot", "@BridgeBot /check"])
def test_check_preserves_the_original_action_text(command_case, prefix):
    db, _, _, sent = command_case
    action = "Ask Maya about the Silver Key"
    invoke(command_case, f"{prefix} social 12 {action}")
    assert db.execute("SELECT action FROM simulation_checks").fetchone() == (action,)
    assert action in sent.call_args.args[2]


def test_committed_check_delivery_can_finish_after_story_closure(command_case):
    db, _, _, sent = command_case
    sent.side_effect = OSError("Delivery interrupted")
    with pytest.raises(DeliveryFailure):
        invoke(command_case)
    saved = sent.call_args.args[2]
    with write_transaction(db):
        db.execute("INSERT INTO ending_state(chat_id,session_id,lifecycle) VALUES('chat','s1','closed')")
    sent.side_effect = None
    invoke(command_case)
    assert sent.call_args.args[2] == saved
    assert operation_phase(db, 601) == "applied"
    assert db.execute("SELECT COUNT(*) FROM simulation_checks").fetchone()[0] == 1


def test_manual_group_rejects_a_check_from_another_actor(command_case):
    from bridge.group_core import save_group_state

    db, _, _, sent = command_case
    save_group_state(
        db, "chat", "s1", {"enabled": True, "mode": "manual", "turn_user_id": "owner", "turn_users": ["owner", "other"]}
    )
    invoke(command_case, actor="other")
    assert "Wait for your turn" in sent.call_args.args[2]
    assert db.execute("SELECT COUNT(*) FROM messages").fetchone()[0] == 0
    assert db.execute("SELECT COUNT(*) FROM simulation_checks").fetchone()[0] == 0


def test_job_actor_mismatch_cannot_admit_a_check(command_case):
    db, _, _, _ = command_case
    job = enqueue_job(db, 901, "chat", "s1", 81, "generation", {"actor_id": "owner"})
    with pytest.raises(ValueError, match="another session or actor"):
        invoke(command_case, operation_id=job, actor="other")
    assert db.execute("SELECT COUNT(*) FROM messages").fetchone()[0] == 0


@pytest.mark.parametrize(
    "command,lifecycle",
    [
        ("/trackers", "unstarted"),
        ("/TRACKERS", "started"),
        ("/trackers@BridgeBot", "closed"),
        ("@BridgeBot /trackers", "started"),
    ],
)
def test_trackers_dispatch_inspects_existing_stories_without_a_turn(command_case, command, lifecycle):
    from tracker_view_test_support import seed_trackers

    from bridge.conversation_lifecycle import reset_conversation
    from bridge.meta_repository import store_meta_value

    db, _, _, sent = command_case
    if lifecycle == "unstarted":
        reset_conversation(db, "chat", "s1")
    seed_trackers(db, "chat", "s1")
    with write_transaction(db):
        store_meta_value(db, "active_session:chat", "s1")
        if lifecycle == "closed":
            db.execute("INSERT INTO ending_state(chat_id,session_id,lifecycle) VALUES('chat','s1','closed')")
    before = (
        db.execute("SELECT COUNT(*) FROM messages").fetchone(),
        db.execute("SELECT revision FROM simulation_revisions").fetchall(),
        db.execute("SELECT roll FROM simulation_checks").fetchall(),
    )
    invoke(command_case, command)
    assert "Story trackers" in sent.call_args.args[2]
    assert "Brass key" in sent.call_args.args[2]
    assert "Secret" not in sent.call_args.args[2]
    assert before == (
        db.execute("SELECT COUNT(*) FROM messages").fetchone(),
        db.execute("SELECT revision FROM simulation_revisions").fetchall(),
        db.execute("SELECT roll FROM simulation_checks").fetchall(),
    )
