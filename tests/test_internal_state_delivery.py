from __future__ import annotations

import sqlite3
import time
from unittest.mock import patch

from application_test_setup import (
    make_test_group_service,
    make_test_memory_service,
    make_test_persona_service,
    make_test_provider_port,
    make_test_rag_service,
)
from settings_test_support import SettingsBuilder

import bridge.edit_messages as edit_messages
import bridge.message_commands as message_commands
import bridge.response_delivery as response_delivery
import bridge.telegram as telegram
from bridge.delivery_progress import bind_committed_turn
from bridge.generation import build_chat_messages
from bridge.job_store import enqueue_job
from bridge.schema import initialize_database_schema
from bridge.sqlite_store import write_transaction


def _session() -> dict[str, str]:
    return {
        "session_id": "s1",
        "model_id": "p::m",
        "persona_id": "",
        "world_file": "",
        "author_note": "",
        "system_prompt": "",
        "response_language": "auto",
    }


def _fields() -> dict[str, str]:
    return {
        "name": "Alice",
        "description": "",
        "personality": "",
        "scenario": "",
        "first_mes": "",
        "mes_example": "",
        "system_prompt": "",
        "post_history_instructions": "",
        "alternate_greetings": "[]",
    }


def _db() -> sqlite3.Connection:
    db = sqlite3.connect(":memory:")
    initialize_database_schema(db)
    now = time.time()
    db.execute(
        """
        INSERT INTO sessions(
            chat_id,session_id,title,character_file,model_id,persona_id,world_file,
            author_note,system_prompt,response_language,created_at,updated_at
        ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        ("chat", "s1", "s1", "char.png", "p::m", "", "", "", "", "auto", now, now),
    )
    db.commit()
    return db


def test_send_reply_hides_wrapped_internal_states_from_telegram():
    requests = []

    def request(_token, method, payload):
        requests.append((method, payload))
        return {"message_id": 91}

    source = (
        "*Visible narration.*\n\n"
        "<!-- GFX_START -->\n"
        "<tg-spoiler>\n"
        "<internal_states>\n"
        "<details><summary>INTERNAL STATES</summary>\n"
        "<b>Secret state value</b>\n"
        "</details>\n"
        "</internal_states>\n"
        "</tg-spoiler>\n"
        "<!-- GFX_END -->"
    )
    with patch.object(telegram, "telegram_request", side_effect=request):
        response_delivery.send_reply(
            "token",
            "chat",
            source,
            app_settings=SettingsBuilder().build(),
        )

    assert requests[0][1]["text"] == "Visible narration."
    assert requests[0][1]["entities"] == [{"type": "italic", "offset": 0, "length": 18}]
    assert "Secret state value" not in requests[0][1]["text"]


def test_committed_hidden_state_uses_visible_delivery_payload():
    db = _db()
    try:
        source = "Visible reply\n<internal_states>Secret state value</internal_states>"
        db.execute(
            "INSERT INTO messages(rowid,chat_id,session_id,role,content,telegram_message_id,created_at) "
            "VALUES(41,'chat','s1','user','prompt','77',0)"
        )
        db.execute(
            "INSERT INTO messages(rowid,chat_id,session_id,role,content,created_at) "
            "VALUES(42,'chat','s1','assistant',?,1)",
            (source,),
        )
        db.commit()
        job_id = enqueue_job(db, 1, "chat", "s1", 77, "generation", {"actor_id": "100"})
        with write_transaction(db):
            bind_committed_turn(db, job_id, 41, 42, source)

        requests = []

        def request(_token, method, payload):
            requests.append((method, payload))
            return {"message_id": 92}

        with patch.object(telegram, "telegram_request", side_effect=request):
            response_delivery.send_reply(
                "token",
                "chat",
                source,
                db,
                "s1",
                42,
                expected_job_id=job_id,
                app_settings=SettingsBuilder().build(),
            )

        assert requests[0][1]["text"] == "Visible reply"
    finally:
        db.close()


def test_ordinary_generation_keeps_internal_states_for_next_turn_but_not_stream_preview():
    db = _db()
    preview_requests = []

    class FakeNpc:
        def context_for_prompt(self, *_args, **_kwargs):
            return ""

    def generate_backend(_api_key, _model, _messages, **kwargs):
        callback = kwargs.get("stream_callback")
        assert callback is not None
        callback("Visible narration.")
        callback("Visible narration.\n<internal_states>Secret state value")
        return "Visible narration.\n<internal_states>Secret state value</internal_states>"

    def telegram_request(_token, method, payload):
        preview_requests.append((method, payload))
        return {"message_id": 501}

    try:
        with (
            patch.object(message_commands, "require_started", return_value=True),
            patch.object(
                message_commands,
                "build_chat_messages",
                return_value=[{"role": "user", "content": "hello"}],
            ),
            patch.object(message_commands, "prepare_turn", return_value=None),
            patch.object(message_commands, "send_typing"),
            patch.object(message_commands, "get_generation_settings", return_value={}),
            patch.object(message_commands, "finalize_generation_messages", side_effect=lambda *_a, **_kw: _a[3]),
            patch.object(message_commands, "save_response_variant", return_value=1),
            patch.object(message_commands, "queue_user_quote_tts"),
            patch.object(message_commands, "send_reply"),
            patch.object(message_commands, "telegram_request", side_effect=telegram_request),
        ):
            message_commands.generate_and_store_reply(
                db,
                "token",
                "key",
                _fields(),
                "chat",
                "hello",
                _session(),
                "s1",
                "p::m",
                None,
                "",
                None,
                None,
                group_service=make_test_group_service(app_settings=SettingsBuilder().build()),
                provider_port=make_test_provider_port(generate_backend=generate_backend),
                memory_service=make_test_memory_service(),
                npc_service=FakeNpc(),
                persona_service=make_test_persona_service(),
                app_settings=SettingsBuilder().build(),
                rag_service=make_test_rag_service(),
            )

        stored = db.execute(
            "SELECT content FROM messages WHERE chat_id='chat' AND session_id='s1' AND role='assistant' "
            "ORDER BY rowid DESC LIMIT 1"
        ).fetchone()[0]
        assert "<internal_states>" in stored
        assert "Secret state value" in stored

        next_messages = build_chat_messages(
            _session(),
            _fields(),
            "next turn",
            [("assistant", stored)],
            persona_service=make_test_persona_service(),
            app_settings=SettingsBuilder().build(),
        )
        assert any(
            message.get("role") == "assistant" and "Secret state value" in str(message.get("content", ""))
            for message in next_messages
        )
        assert preview_requests
        assert all("Secret state value" not in str(payload.get("text", "")) for _, payload in preview_requests)
    finally:
        db.close()


def test_operation_recovery_hides_internal_states_from_delivery_payload():
    db = _db()
    try:
        db.execute(
            "INSERT INTO messages(chat_id,session_id,role,content,telegram_message_id,created_at) "
            "VALUES('chat','s1','user','prompt','77',1)"
        )
        user_rowid = db.execute("SELECT last_insert_rowid()").fetchone()[0]
        source = "Visible reply\n<internal_states>Secret state value</internal_states>"
        db.execute(
            "INSERT INTO messages(chat_id,session_id,role,content,created_at) VALUES('chat','s1','assistant',?,2)",
            (source,),
        )
        assistant_rowid = db.execute("SELECT last_insert_rowid()").fetchone()[0]
        db.commit()

        recovery = edit_messages._COMMAND_OPERATION_RECOVERY
        recovery.set_payload(db, "op-hidden", {"user_rowid": int(user_rowid)})
        recovery.record_delivery_target(db, "op-hidden", int(assistant_rowid), source, source)

        payload = recovery.get_payload(db, "op-hidden")
        progress = db.execute(
            "SELECT payload FROM assistant_delivery_progress WHERE assistant_rowid=?",
            (assistant_rowid,),
        ).fetchone()[0]
        assert payload["source_content"] == source
        assert payload["delivery_payload"] == "Visible reply"
        assert progress == "Visible reply"
    finally:
        db.close()
