from types import SimpleNamespace

from application_test_setup import ensure_application_extensions, make_test_session_service
from settings_test_support import SettingsTestCase, make_test_settings

import bridge.response_delivery as _owner_response_delivery
import bridge.voice_jobs as _owner_voice_jobs

ensure_application_extensions()

import sqlite3
import unittest
from unittest.mock import patch

import bridge.help_details as _m_help_details
import bridge.message_commands as _m_message_commands
from bridge.delivery_repository import clear_progress
from bridge.schema import initialize_database_schema
from bridge.sqlite_store import write_transaction


class QuotedVoiceTests(SettingsTestCase):
    def test_extracts_only_double_quoted_dialogue(self):
        text = '*walks closer* "I am here." *smiles* "Are you ready?"'
        self.assertEqual(_owner_response_delivery.quoted_speech_from_reply(text), "I am here. Are you ready?")

    def test_narration_and_unquoted_text_are_not_spoken(self):
        self.assertEqual(_owner_response_delivery.quoted_speech_from_reply("*walks closer* I am here"), "")

    def test_unclosed_quote_is_not_spoken(self):
        self.assertEqual(_owner_response_delivery.quoted_speech_from_reply('"I am here'), "")

    @patch.object(_owner_response_delivery, "story_mutation_message", new=lambda *a: None)
    def test_user_quote_is_queued_for_tts_when_voice_is_enabled(self):
        calls = []
        original_meta = _owner_response_delivery.get_meta
        original_submit = _owner_response_delivery.submit_background
        _owner_response_delivery.get_meta = lambda *_args: "tts"
        _owner_response_delivery.submit_background = lambda *args: calls.append(args) or True
        try:
            queued = _m_message_commands.queue_user_quote_tts(
                "token",
                "chat",
                '*waves* "Hello there."',
                object(),
                "session",
                44,
                app_settings=self.app_settings_builder.build(),
            )
        finally:
            _owner_response_delivery.get_meta = original_meta
            _owner_response_delivery.submit_background = original_submit
        self.assertTrue(queued)
        self.assertEqual(calls[0][0], "tts")
        self.assertEqual(calls[0][2:5], ("token", "chat", "Hello there."))

    @patch.object(_owner_response_delivery, "story_mutation_message", new=lambda *a: None)
    def test_user_quote_is_not_queued_when_voice_is_disabled(self):
        calls = []
        original_meta = _owner_response_delivery.get_meta
        original_submit = _owner_response_delivery.submit_background
        _owner_response_delivery.get_meta = lambda *_args: "off"
        _owner_response_delivery.submit_background = lambda *args: calls.append(args) or True
        try:
            queued = _m_message_commands.queue_user_quote_tts(
                "token",
                "chat",
                '"Hello there."',
                object(),
                "session",
                44,
                app_settings=self.app_settings_builder.build(),
            )
        finally:
            _owner_response_delivery.get_meta = original_meta
            _owner_response_delivery.submit_background = original_submit
        self.assertFalse(queued)
        self.assertEqual(calls, [])

    def test_tts_command_is_not_in_help(self):
        commands = [command for entries in _m_help_details.HELP_CATEGORIES.values() for command, _summary in entries]
        self.assertNotIn("/tts", commands)

    def test_assistant_tts_uses_content_scoped_idempotency_key(self):
        calls = []
        original_expression = _owner_response_delivery.deliver_expression
        original_send = _owner_response_delivery.send_text
        original_meta = _owner_response_delivery.get_meta
        original_submit = _owner_response_delivery.submit_background
        _owner_response_delivery.deliver_expression = lambda *_args, app_settings=None, **_kwargs: None

        def send_text(*_args, acknowledged_chunk=None):
            if acknowledged_chunk:
                acknowledged_chunk(88)
            return [88]

        _owner_response_delivery.send_text = send_text
        _owner_response_delivery.get_meta = lambda *_args: "tts"
        _owner_response_delivery.submit_background = lambda *args: calls.append(args) or True
        db = sqlite3.connect(":memory:")
        initialize_database_schema(db)
        db.execute(
            "INSERT INTO messages(rowid,chat_id,session_id,role,content,created_at) "
            "VALUES(7,'chat','session','assistant',?,1)",
            ('"Hello there."',),
        )
        db.commit()
        try:
            _m_message_commands.send_reply(
                "token", "chat", '"Hello there."', db, "session", 7, app_settings=self.app_settings_builder.build()
            )
            _m_message_commands.send_reply(
                "token", "chat", '"Hello there."', db, "session", 7, app_settings=self.app_settings_builder.build()
            )
            with write_transaction(db):
                clear_progress(db, 7)
                db.execute("UPDATE messages SET content=? WHERE rowid=7", ('"Changed reply."',))
            _m_message_commands.send_reply(
                "token", "chat", '"Changed reply."', db, "session", 7, app_settings=self.app_settings_builder.build()
            )
        finally:
            _owner_response_delivery.deliver_expression = original_expression
            _owner_response_delivery.send_text = original_send
            _owner_response_delivery.get_meta = original_meta
            _owner_response_delivery.submit_background = original_submit
            db.close()

        first_id = calls[0][-1]
        self.assertEqual(len(calls), 2)
        self.assertNotEqual(first_id, calls[1][-1])
        self.assertTrue(first_id.startswith("assistant-tts:chat:session:7:"))


def test_transcript_uses_injected_service_and_preserves_request_identity(monkeypatch):
    delivered = []

    class ConversationRecorder:
        def process_message(self, *args, **kwargs):
            delivered.append((args, kwargs))

    services = SimpleNamespace(
        config=make_test_settings(),
        group=SimpleNamespace(user_turn_allowed=lambda *a: True),
        conversation=ConversationRecorder(),
        session=make_test_session_service(app_settings=make_test_settings()),
    )
    db = object()
    fields = {"name": "character"}
    monkeypatch.setattr(_owner_voice_jobs, "story_mutation_message", lambda *a: None)
    monkeypatch.setattr(_owner_voice_jobs, "require_started", lambda *a: True)
    monkeypatch.setattr(_owner_voice_jobs, "get_meta", lambda _db, _key, default: default)
    monkeypatch.setattr(_owner_voice_jobs, "download_telegram_file", lambda *_args: b"audio")
    monkeypatch.setattr(_owner_voice_jobs, "transcribe_audio_bytes", lambda *_args, app_settings=None: "spoken message")
    _owner_voice_jobs.process_voice_message(
        db,
        "token",
        "key",
        "model",
        fields,
        "chat",
        {"file_id": "voice-file", "file_size": 5, "file_name": "voice.ogg"},
        42,
        queued_session_id="queued-session",
        actor_id="actor",
        operation_id=99,
        services=services,
    )
    assert delivered == [
        (
            (db, "token", "key", "model", fields, "chat", "spoken message", 42),
            {"queued_session_id": "queued-session", "actor_id": "actor", "operation_id": 99},
        )
    ]


if __name__ == "__main__":
    unittest.main()
