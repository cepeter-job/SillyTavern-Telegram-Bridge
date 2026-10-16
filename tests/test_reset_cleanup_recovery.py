"""Reset recovery must finish its frozen Telegram cleanup, not reset the story twice."""

import json
import sqlite3
from unittest.mock import patch

import pytest
import test_reset_behavior as reset_tests
from application_test_setup import make_test_application_services, make_test_memory_service, make_test_npc_service

from bridge import callback_dispatch, conversation_callbacks
from bridge import message_commands as commands
from bridge.job_store import enqueue_job
from bridge.operations import operation_phase


class ProcessStopped(BaseException):
    pass


@pytest.fixture
def reset_case():
    case = reset_tests.ResetBehaviorTests(methodName="test_reset_clears_episodic_memories")
    case.setUp()
    try:
        case.db.execute(
            "INSERT INTO messages(chat_id,session_id,role,content,telegram_message_ids,created_at) "
            "VALUES(?,?,'assistant','synthetic old reply','[41,42]',1)",
            ("chat", case.session["session_id"]),
        )
        case.db.commit()
        yield case
    finally:
        case.tearDown()


def reset(case, operation_id="reset-recovery"):
    return commands.reset_session(
        case.db,
        "token",
        "chat",
        case.session,
        operation_id=operation_id,
        memory_service=make_test_memory_service(),
        npc_service=make_test_npc_service(),
    )


def test_resume_after_local_commit_deletes_frozen_messages_only(reset_case):
    case = reset_case
    with patch.object(commands, "telegram_request", side_effect=ProcessStopped), pytest.raises(ProcessStopped):
        reset(case)
    assert operation_phase(case.db, "reset-recovery") == "local_committed"
    assert case.db.execute("SELECT count(*) FROM messages").fetchone()[0] == 0
    case.db.execute(
        "INSERT INTO messages(chat_id,session_id,role,content,telegram_message_ids,created_at) "
        "VALUES(?,?,'assistant','synthetic new reply','[99]',2)",
        ("chat", case.session["session_id"]),
    )
    case.db.commit()
    deleted = []
    with patch.object(commands, "telegram_request", side_effect=lambda _t, _m, p: deleted.append(p["message_id"])):
        report = reset(case)
    assert deleted == [41, 42]
    assert report == (2, 2, 0)
    assert case.db.execute("SELECT count(*) FROM messages").fetchone()[0] == 1
    assert operation_phase(case.db, "reset-recovery") == "applied"


def test_resume_after_new_story_enters_finale_only_deletes_old_messages(reset_case):
    case = reset_case
    with patch.object(commands, "telegram_request", side_effect=ProcessStopped), pytest.raises(ProcessStopped):
        reset(case)
    case.db.execute(
        "INSERT INTO messages(chat_id,session_id,role,content,telegram_message_ids,created_at) "
        "VALUES(?,?,'assistant','new story reply','[99]',2)",
        ("chat", case.session["session_id"]),
    )
    case.db.execute(
        "INSERT INTO ending_state(chat_id,session_id,lifecycle) VALUES(?,?,'finale')",
        ("chat", case.session["session_id"]),
    )
    case.db.commit()

    deleted = []
    with patch.object(commands, "telegram_request", side_effect=lambda _t, _m, p: deleted.append(p["message_id"])):
        report = reset(case)

    assert deleted == [41, 42]
    assert report == (2, 2, 0)
    assert case.db.execute("SELECT telegram_message_ids FROM messages").fetchone()[0] == "[99]"
    assert case.db.execute("SELECT lifecycle FROM ending_state").fetchone()[0] == "finale"



def test_resume_after_panel_binding_expires_uses_durable_job_scope(reset_case):
    case = reset_case
    callback = {
        "id": "reset-callback",
        "from": {"id": "owner"},
        "data": "reset:confirm",
        "_queued": True,
        "message": {"message_id": 77, "chat": {"id": "chat"}},
    }
    job_id = enqueue_job(
        case.db, 880, "chat", case.session["session_id"], 77, "callback", {"actor_id": "owner"}
    )
    with patch.object(commands, "telegram_request", side_effect=ProcessStopped), pytest.raises(ProcessStopped):
        reset(case, operation_id=job_id)
    assert operation_phase(case.db, job_id) == "local_committed"

    deleted = []
    with (
        patch.object(commands, "telegram_request", side_effect=lambda _t, _m, p: deleted.append(p["message_id"])),
        patch.object(callback_dispatch, "send_text"),
        patch.object(callback_dispatch, "close_panel_message"),
        patch.object(conversation_callbacks, "send_text"),
        patch.object(conversation_callbacks, "remove_inline_keyboard"),
    ):
        callback_dispatch.process_callback(
            case.db,
            "token",
            callback,
            operation_id=job_id,
            actor_id="owner",
            services=make_test_application_services(app_settings=case.app_settings_builder.build()),
        )

    assert deleted == [41, 42]
    assert operation_phase(case.db, job_id) == "applied"

def test_midway_crash_survives_reopening_database(reset_case):
    case = reset_case
    with patch.object(commands, "telegram_request", side_effect=[{}, ProcessStopped()]), pytest.raises(ProcessStopped):
        reset(case)
    case.db.close()
    case.db = sqlite3.connect(case.app_settings_builder.db_file)
    deleted = []
    with patch.object(commands, "telegram_request", side_effect=lambda _t, _m, p: deleted.append(p["message_id"])):
        report = reset(case)
    assert deleted == [42] and report == (2, 2, 0)


def test_delete_ack_crash_treats_already_missing_message_as_success(reset_case):
    from bridge import reset_cleanup

    case = reset_case
    store = reset_cleanup.set_meta

    def stop_before_ack(db, key, value):
        if json.loads(value)["deleted"]:
            raise ProcessStopped
        return store(db, key, value)

    with (
        patch.object(commands, "telegram_request", return_value={}),
        patch.object(reset_cleanup, "set_meta", side_effect=stop_before_ack),
        pytest.raises(ProcessStopped),
    ):
        reset(case)
    with patch.object(
        commands,
        "telegram_request",
        side_effect=[
            RuntimeError("Telegram deleteMessage failed: Bad Request: message to delete not found"),
            {},
        ],
    ):
        report = reset(case)
    assert report == (2, 2, 0)


def test_partial_failure_is_reported_and_retained_without_replaying_reset(reset_case, caplog):
    case = reset_case
    with patch.object(commands, "telegram_request", side_effect=[RuntimeError("private API detail"), {}]):
        report = reset(case)
    saved = case.db.execute("SELECT value FROM meta WHERE key='reset_telegram_cleanup:reset-recovery'").fetchone()[0]
    state = json.loads(saved)
    assert report == (2, 1, 1) and state["failed"] == [41] and state["pending"] == []
    assert "private API detail" not in saved and "private API detail" not in caplog.text
    with patch.object(commands, "telegram_request") as request:
        assert reset(case) is None
    request.assert_not_called()


def test_scope_mismatch_cannot_delete_another_chat(reset_case):
    from bridge.reset_cleanup import finish_reset_cleanup

    case = reset_case
    with patch.object(commands, "telegram_request", side_effect=ProcessStopped), pytest.raises(ProcessStopped):
        reset(case)
    with patch.object(commands, "telegram_request") as request, pytest.raises(ValueError, match="scope"):
        finish_reset_cleanup(
            case.db, "token", "other-chat", case.session["session_id"], "reset-recovery", [], request=request
        )
    request.assert_not_called()


def test_legacy_local_commit_without_snapshot_does_not_sweep_new_messages(reset_case):
    from bridge.operations import begin_operation, set_operation_phase

    case = reset_case
    begin_operation(case.db, "reset-recovery", "reset")
    set_operation_phase(case.db, "reset-recovery", "reset", "local_committed")
    with patch.object(commands, "telegram_request") as request:
        assert reset(case) is None
    request.assert_not_called()
    assert case.db.execute("SELECT count(*) FROM messages").fetchone()[0] == 1


def test_snapshot_and_local_reset_rollback_together(reset_case):
    from bridge import reset_cleanup

    case = reset_case
    store = reset_cleanup.set_meta

    def fail_after_snapshot(db, key, value):
        store(db, key, value)
        raise RuntimeError("injected snapshot failure")

    with patch.object(reset_cleanup, "set_meta", side_effect=fail_after_snapshot), pytest.raises(RuntimeError):
        reset(case)
    assert case.db.execute("SELECT count(*) FROM messages").fetchone()[0] == 1
    assert case.db.execute("SELECT count(*) FROM meta WHERE key LIKE 'reset_telegram_cleanup:%'").fetchone()[0] == 0
    assert operation_phase(case.db, "reset-recovery") == ""


def test_completion_record_failure_resumes_without_redeleting(reset_case):
    case = reset_case
    with (
        patch.object(commands, "telegram_request", return_value={}),
        patch.object(commands, "record_operation", side_effect=RuntimeError("injected completion failure")),
        pytest.raises(RuntimeError),
    ):
        reset(case)
    assert operation_phase(case.db, "reset-recovery") == "local_committed"
    with patch.object(commands, "telegram_request") as request:
        assert reset(case) == (2, 2, 0)
    request.assert_not_called()
    assert operation_phase(case.db, "reset-recovery") == "applied"


@pytest.mark.parametrize("payload", ["null", "{}", '{"pending":"41"}'])
def test_invalid_snapshot_fails_closed_before_telegram(reset_case, payload):
    from bridge.metadata import set_meta
    from bridge.reset_cleanup import finish_reset_cleanup

    case = reset_case
    set_meta(case.db, "reset_telegram_cleanup:reset-recovery", payload)
    case.db.commit()
    with patch.object(commands, "telegram_request") as request, pytest.raises(ValueError, match="snapshot"):
        finish_reset_cleanup(
            case.db, "token", "chat", case.session["session_id"], "reset-recovery", [], request=request
        )
    request.assert_not_called()
