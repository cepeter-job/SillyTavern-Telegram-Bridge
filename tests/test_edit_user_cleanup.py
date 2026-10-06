"""Command edits remove obsolete Telegram input without affecting native edits."""

import tempfile
import time
from pathlib import Path
from unittest.mock import patch

import pytest
from application_test_setup import (
    ensure_application_extensions,
    make_test_memory_service,
    make_test_npc_service,
    make_test_persona_service,
    make_test_provider_port,
    make_test_rag_service,
)
from npc_test_support import db as _db
from npc_test_support import fields as _fields
from npc_test_support import session as _session
from npc_test_support import turn as _turn
from settings_test_support import SettingsBuilder, SettingsTestCase

import bridge.edit_messages as _owner_edit_messages
import bridge.metadata as _owner_metadata
import bridge.session_core as _owner_session_core
import bridge.sqlite_store as _sqlite_store
from bridge import edit_messages
from bridge.npc_service import NpcService


@pytest.fixture
def edit_turn(monkeypatch):
    db = _db()
    previous = _turn(db, "user", "earlier prompt", 1)
    target = _turn(db, "user", "original prompt", 2)
    assistant = _turn(db, "assistant", "old answer", 3)
    db.execute("UPDATE messages SET telegram_message_id='66' WHERE rowid=?", (previous,))
    db.execute("UPDATE messages SET telegram_message_id='77' WHERE rowid=?", (target,))
    db.execute("UPDATE messages SET telegram_message_ids='[91,92]' WHERE rowid=?", (assistant,))
    db.commit()
    monkeypatch.setattr(edit_messages, "send_typing", lambda *args: None)
    monkeypatch.setattr(edit_messages, "send_reply", lambda *args, **kwargs: None)
    calls = []

    def request(token, method, payload):
        # Cleanup must never race ahead of the durable edit.
        assert db.execute("SELECT content FROM messages WHERE rowid=?", (target,)).fetchone() == ("replacement",)
        calls.append((method, payload))
        return {}

    monkeypatch.setattr(edit_messages, "telegram_request", request)
    kwargs = dict(
        provider_port=make_test_provider_port(generate_backend=lambda *args, **kwargs: "new answer"),
        memory_service=make_test_memory_service(),
        npc_service=NpcService(),
        persona_service=make_test_persona_service(),
        app_settings=SettingsBuilder().build(),
        rag_service=make_test_rag_service(),
    )
    try:
        yield db, target, calls, kwargs
    finally:
        db.close()


def command_edit(db, kwargs, operation_id=None):
    edit_messages.edit_last_user(
        db,
        "token",
        "key",
        _session(),
        _fields(),
        "chat",
        "replacement",
        operation_id=operation_id,
        **kwargs,
    )


def test_command_edit_deletes_original_user_message_after_commit(edit_turn):
    db, target, calls, kwargs = edit_turn
    command_edit(db, kwargs)
    assert {payload["message_id"] for method, payload in calls if method == "deleteMessage"} == {77, 91, 92}
    assert all(payload["chat_id"] == "chat" for _, payload in calls)
    assert db.execute("SELECT content FROM messages WHERE rowid=?", (target,)).fetchone() == ("replacement",)


def test_native_edit_keeps_updated_user_message(edit_turn, monkeypatch):
    db, target, calls, kwargs = edit_turn
    monkeypatch.setattr(edit_messages, "card_fields_from_file", lambda *args, **kwargs: _fields())
    edit_messages.edit_telegram_user_message(
        db,
        "token",
        "key",
        "chat",
        77,
        "replacement",
        "story::main",
        **kwargs,
    )
    assert {payload["message_id"] for _, payload in calls} == {91, 92}
    assert db.execute("SELECT telegram_message_id FROM messages WHERE rowid=?", (target,)).fetchone() == ("77",)


@pytest.mark.parametrize("message_id", [None, "", "invalid", "0", "-1"])
def test_command_edit_handles_missing_or_invalid_user_id(edit_turn, message_id):
    db, target, calls, kwargs = edit_turn
    db.execute("UPDATE messages SET telegram_message_id=? WHERE rowid=?", (message_id, target))
    db.commit()
    command_edit(db, kwargs)
    assert {payload["message_id"] for _, payload in calls} == {91, 92}


def test_failed_generation_preserves_original_telegram_messages(edit_turn):
    db, target, calls, kwargs = edit_turn

    def fail(*args, **kwargs):
        raise RuntimeError("provider failed")

    kwargs["provider_port"] = make_test_provider_port(generate_backend=fail)
    with pytest.raises(RuntimeError, match="provider failed"):
        command_edit(db, kwargs)
    assert calls == []
    assert db.execute("SELECT content FROM messages WHERE rowid=?", (target,)).fetchone() == ("original prompt",)


def test_command_edit_cleanup_failure_does_not_undo_committed_edit(edit_turn, monkeypatch):
    db, target, _, kwargs = edit_turn
    attempted = []

    def fail_delete(token, method, payload):
        attempted.append(payload["message_id"])
        raise RuntimeError("Telegram deletion unavailable")

    monkeypatch.setattr(edit_messages, "telegram_request", fail_delete)
    command_edit(db, kwargs, operation_id=900)
    assert set(attempted) == {77, 91, 92}
    assert db.execute("SELECT content FROM messages WHERE rowid=?", (target,)).fetchone() == ("replacement",)
    assert edit_messages.operation_phase(db, 900) == "applied"


def test_command_edit_recovery_retries_original_user_cleanup_without_generation(edit_turn, monkeypatch):
    db, target, calls, kwargs = edit_turn

    def fail_delivery(*args, **kwargs):
        raise RuntimeError("reply delivery interrupted")

    monkeypatch.setattr(edit_messages, "send_reply", fail_delivery)
    with pytest.raises(RuntimeError, match="reply delivery interrupted"):
        command_edit(db, kwargs, operation_id=901)
    assert edit_messages.operation_phase(db, 901) == "local_committed"
    payload = edit_messages._COMMAND_OPERATION_RECOVERY.get_payload(db, 901)
    assert "77" in payload["old_message_ids"]
    calls.clear()

    def no_generation(*args, **kwargs):
        raise AssertionError("recovery must not generate")

    kwargs["provider_port"] = make_test_provider_port(generate_backend=no_generation)
    monkeypatch.setattr(edit_messages, "send_reply", lambda *args, **kwargs: None)
    command_edit(db, kwargs, operation_id=901)
    assert {payload["message_id"] for _, payload in calls} == {77, 91, 92}
    assert edit_messages.operation_phase(db, 901) == "applied"
    assert db.execute("SELECT content FROM messages WHERE rowid=?", (target,)).fetchone() == ("replacement",)


ensure_application_extensions()


class NativeEditedMessageSessionTests(SettingsTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = _sqlite_store.db_connect(
            Path(self.tmp.name) / "edit.sqlite3", app_settings=self.app_settings_builder.build()
        )
        self.model = "provider::model"

    def tearDown(self):
        self.db.close()
        self.tmp.cleanup()

    def test_edit_uses_message_owning_session_without_switching_active_session(self):
        session_a = _owner_session_core.create_session(
            self.db,
            "chat",
            self.model,
            session_id="session-a",
            title="A",
            app_settings=self.app_settings_builder.build(),
        )
        _owner_session_core.update_session(
            self.db,
            "chat",
            session_a["session_id"],
            character_file="a.png",
        )
        now = time.time()
        cursor = self.db.execute(
            "INSERT INTO messages(chat_id,session_id,role,content,telegram_message_id,created_at) VALUES(?,?,?,?,?,?)",
            ("chat", "session-a", "user", "original", "77", now),
        )
        self.db.commit()
        user_rowid = int(cursor.lastrowid)

        session_b = _owner_session_core.create_session(
            self.db,
            "chat",
            self.model,
            session_id="session-b",
            title="B",
            app_settings=self.app_settings_builder.build(),
        )
        _owner_session_core.update_session(
            self.db,
            "chat",
            session_b["session_id"],
            character_file="b.png",
        )
        self.assertEqual(
            _owner_metadata.get_meta(self.db, "active_session:chat", ""),
            "session-b",
        )

        captured = {}
        sent = []

        def fake_card_fields(filename, *, app_settings=None):
            captured["character_file"] = filename
            return {"name": "Mira"}

        def fake_regenerate(
            _db,
            _token,
            _api_key,
            session,
            fields,
            _chat_id,
            rowid,
            new_text,
            *,
            app_settings=None,
            **kwargs,
        ):
            captured.update(
                {
                    "session_id": session["session_id"],
                    "fields": fields,
                    "rowid": rowid,
                    "new_text": new_text,
                    "provider_port": kwargs["provider_port"],
                    "memory_service": kwargs["memory_service"],
                    "npc_service": kwargs["npc_service"],
                    "persona_service": kwargs["persona_service"],
                }
            )

        provider = make_test_provider_port()
        memory = make_test_memory_service()
        npc = make_test_npc_service()
        persona = make_test_persona_service()
        with (
            patch.object(
                _owner_edit_messages,
                "card_fields_from_file",
                side_effect=fake_card_fields,
            ),
            patch.object(
                _owner_edit_messages,
                "regenerate_edited_turn",
                side_effect=fake_regenerate,
            ),
            patch.object(
                _owner_edit_messages,
                "send_text",
                side_effect=lambda _token, _chat_id, text: sent.append(text),
            ),
        ):
            _owner_edit_messages.edit_telegram_user_message(
                self.db,
                "token",
                "key",
                "chat",
                77,
                "replacement",
                self.model,
                provider_port=provider,
                memory_service=memory,
                npc_service=npc,
                persona_service=persona,
                app_settings=self.app_settings_builder.build(),
                rag_service=make_test_rag_service(),
            )

        self.assertEqual(captured["session_id"], "session-a")
        self.assertEqual(captured["character_file"], "a.png")
        self.assertEqual(captured["rowid"], user_rowid)
        self.assertEqual(captured["new_text"], "replacement")
        self.assertIs(captured["provider_port"], provider)
        self.assertIs(captured["memory_service"], memory)
        self.assertIs(captured["npc_service"], npc)
        self.assertIs(captured["persona_service"], persona)
        self.assertEqual(sent, [])
        self.assertEqual(
            _owner_metadata.get_meta(self.db, "active_session:chat", ""),
            "session-b",
        )
