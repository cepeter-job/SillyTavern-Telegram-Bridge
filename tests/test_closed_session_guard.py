"""Completed stories reserve all creative work while preserving read-only delivery."""

import sqlite3
from dataclasses import replace
from types import SimpleNamespace

import pytest
from test_epilogue_service import complete, producer_for, resolved
from test_memory_completion_safety import session_db as session_db
from test_narrative_generation import invoke_story_flow

from bridge import message_commands
from bridge.delivery_progress import checkpoint, prepare_progress
from bridge.ending_service import load_ending_state
from bridge.narrative_settings import load_session_narrative_settings, save_session_narrative_settings
from bridge.sqlite_store import write_transaction


@pytest.fixture
def closed_case(session_db):
    _, db, _ = session_db
    resolved(session_db)
    result = complete(session_db, producer_for(db, []))
    assert result.lifecycle == "closed"
    return session_db


def test_closed_guard_returns_the_fixed_bridge_message_without_writes(closed_case):
    from bridge.closed_session_guard import CLOSED_STORY_MESSAGE, guard_story_mutation, story_mutation_message

    _, db, session = closed_case
    before = db.total_changes
    assert story_mutation_message(db, "chat", "s1") == "This story has ended. Please start new story."
    with pytest.raises(ValueError, match="This story has ended"):
        guard_story_mutation(db, "chat", session["session_id"])
    assert db.total_changes == before
    assert CLOSED_STORY_MESSAGE == "This story has ended. Please start new story."


@pytest.mark.parametrize("flow", ["text", "regen", "continue", "edit", "image"])
def test_actual_story_entry_points_reject_closed_session_before_generation(closed_case, monkeypatch, flow):
    config, db, session = closed_case
    before = db.execute("SELECT * FROM messages").fetchall()
    with pytest.raises(ValueError, match="This story has ended"):
        invoke_story_flow(flow, (db, session, config, 1), monkeypatch)
    assert db.execute("SELECT * FROM messages").fetchall() == before


def test_reset_cannot_purge_memory_or_reopen_a_completed_story(closed_case, monkeypatch):
    _, db, session = closed_case
    with pytest.raises(ValueError, match="This story has ended"):
        message_commands.reset_session(
            db,
            "token",
            "chat",
            session,
            memory_service=SimpleNamespace(queue_cleanup=lambda *a: pytest.fail("Purged closed memory")),
            npc_service=SimpleNamespace(purge_session=lambda *a: pytest.fail("Purged closed NPC state")),
        )
    assert load_ending_state(db, "chat", "s1").lifecycle == "closed"


@pytest.mark.parametrize(
    "sql",
    [
        "INSERT INTO messages(chat_id,session_id,role,content,created_at) VALUES('chat','s1','user','new decision',9)",
        "UPDATE messages SET content='Different ending' WHERE chat_id='chat' AND session_id='s1'",
        "DELETE FROM messages WHERE chat_id='chat' AND session_id='s1'",
        "UPDATE ending_state SET lifecycle='open' WHERE chat_id='chat' AND session_id='s1'",
        "DELETE FROM ending_state WHERE chat_id='chat' AND session_id='s1'",
        "UPDATE narrative_state SET story_phase='development' WHERE chat_id='chat' AND session_id='s1'",
    ],
)
def test_database_is_final_defense_against_queued_or_unchecked_story_mutation(closed_case, sql):
    _, db, _ = closed_case
    with pytest.raises(sqlite3.IntegrityError, match=r"immutable|ended|closed|closing"):
        with write_transaction(db):
            db.execute(sql)
    assert load_ending_state(db, "chat", "s1").lifecycle == "closed"


def test_closure_still_allows_delivery_acknowledgement_and_explicit_parent_deletion(closed_case):
    _, db, _ = closed_case
    rowid = load_ending_state(db, "chat", "s1").epilogue_committed_rowid
    payload, ids, _, source = prepare_progress(db, rowid, "ignored because the committed payload exists")
    checkpoint(db, rowid, ids, complete=True, expected_source=source, expected_payload=payload)
    with write_transaction(db):
        db.execute("DELETE FROM sessions WHERE chat_id='chat' AND session_id='s1'")
        db.execute("DELETE FROM messages WHERE chat_id='chat' AND session_id='s1'")
    assert db.execute("SELECT COUNT(*) FROM ending_state").fetchone()[0] == 0


def test_narrative_configuration_cannot_reopen_a_closed_original(closed_case):
    _, db, _ = closed_case
    settings = load_session_narrative_settings(db, "chat", "s1")
    with pytest.raises(ValueError, match=r"ended|finale|locked"):
        save_session_narrative_settings(db, "chat", "s1", replace(settings, ending_mode="open_ended"))


@pytest.mark.parametrize(
    "text", ["Continue the story", "/regen", "/regen@bridge_bot", "@bridge_bot /continue", "/reset"]
)
def test_normalized_ingress_returns_fixed_notice_before_card_or_provider_work(closed_case, monkeypatch, text):
    from application_test_setup import make_test_application_services, make_test_delivery_port, make_test_provider_port

    config, db, session = closed_case
    notices = []
    services = make_test_application_services(
        app_settings=config,
        delivery=make_test_delivery_port(send_text=lambda _t, _c, message: notices.append(message)),
        provider=make_test_provider_port(generate_backend=lambda *a, **k: pytest.fail("Called a closed provider")),
    )
    monkeypatch.setattr(message_commands, "card_fields_from_file", lambda *a, **k: pytest.fail("Read stale story card"))
    services.conversation.process_message(
        db, "token", "", session["model_id"], {}, "chat", text, queued_session_id="s1", actor_id="alice"
    )
    assert notices == ["This story has ended. Please start new story."]


def test_voice_rejects_closed_story_before_download_or_transcription(closed_case, monkeypatch):
    from application_test_setup import make_test_application_services

    from bridge import voice_jobs

    config, db, session = closed_case
    notices = []
    monkeypatch.setattr(voice_jobs, "send_text", lambda _t, _c, message: notices.append(message))
    monkeypatch.setattr(voice_jobs, "download_telegram_file", lambda *a, **k: pytest.fail("Downloaded closed audio"))
    voice_jobs.process_voice_message(
        db,
        "token",
        "",
        session["model_id"],
        {},
        "chat",
        {"file_id": "fixture"},
        45,
        queued_session_id="s1",
        actor_id="alice",
        services=make_test_application_services(app_settings=config),
    )
    assert notices == ["This story has ended. Please start new story."]


def test_delivery_recovery_never_launches_new_expression_or_speech(closed_case, monkeypatch):
    from bridge import response_delivery

    config, db, _ = closed_case
    current = load_ending_state(db, "chat", "s1")
    rowid = current.epilogue_committed_rowid
    with write_transaction(db):
        db.execute("DELETE FROM assistant_delivery_progress WHERE assistant_rowid=?", (rowid,))
        db.execute("UPDATE messages SET telegram_message_ids='[]' WHERE id=?", (rowid,))
        db.execute("INSERT OR REPLACE INTO meta(key,value) VALUES('voice_mode:chat','tts')")
    monkeypatch.setattr(response_delivery, "deliver_expression", lambda *a, **k: pytest.fail("Generated an expression"))
    monkeypatch.setattr(response_delivery, "submit_background", lambda *a, **k: pytest.fail("Generated speech"))
    monkeypatch.setattr(
        response_delivery, "_send_reply_chunk", lambda _t, _c, _chunk, ack=None: ack(501) if ack else None
    )
    content = db.execute("SELECT content FROM messages WHERE id=?", (rowid,)).fetchone()[0]
    response_delivery.send_reply("token", "chat", content, db, "s1", rowid, app_settings=config)
    assert load_ending_state(db, "chat", "s1").lifecycle == "closed"


def test_only_assistant_epilogue_can_use_the_internal_commit_window(session_db):
    from bridge.ending_repository import claim_ending_work, mark_ending_work_stage
    from bridge.epilogue_service import begin_epilogue

    _, db, _ = session_db
    resolved(session_db)
    before = load_ending_state(db, "chat", "s1")
    begin_epilogue(db, "chat", "s1", expected_lifecycle_revision=before.lifecycle_revision)
    with write_transaction(db):
        assert claim_ending_work(db, "chat", "s1", "epilogue-lease", 1)
        mark_ending_work_stage(db, "chat", "s1", "epilogue-lease", "commit_epilogue")
    with pytest.raises(sqlite3.IntegrityError, match="immutable"):
        with write_transaction(db):
            db.execute(
                "INSERT INTO messages(chat_id,session_id,role,content,created_at) "
                "VALUES('chat','s1','user','extra input',9)"
            )


def test_reset_during_finale_is_rejected_before_deleting_telegram_or_external_memory(session_db, monkeypatch):
    from test_narrative_checkpoints import enter, prepared

    _, db, session = session_db
    prepared(session_db)
    enter(db)
    monkeypatch.setattr(
        message_commands, "delete_tracked_panel_messages", lambda *a, **k: pytest.fail("Deleted user input")
    )
    with pytest.raises(ValueError, match="finale"):
        message_commands.reset_session(
            db,
            "token",
            "chat",
            session,
            memory_service=SimpleNamespace(queue_cleanup=lambda *a: pytest.fail("Purged finale memory")),
            npc_service=SimpleNamespace(purge_session=lambda *a: pytest.fail("Purged finale NPCs")),
        )
    assert load_ending_state(db, "chat", "s1").lifecycle == "finale"
