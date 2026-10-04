from application_test_setup import (
    ensure_application_extensions,
    make_test_conversation_service,
    make_test_memory_service,
    make_test_npc_service,
)
from settings_test_support import SettingsTestCase

import bridge.callbacks as _owner_callbacks
import bridge.conversation_callbacks as _owner_conversation_callbacks
import bridge.session_core as _owner_session_core

ensure_application_extensions()

# pyright: reportAttributeAccessIssue=false

import json
import tempfile
import time
import unittest
from pathlib import Path

import bridge.memory_curator as _m_memory_curator
import bridge.message_commands as _m_message_commands
import bridge.session_naming as _m_session_naming
from bridge.episodic_memory import store_episodic_memory


class ResetBehaviorTests(SettingsTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.old_db = self.app_settings_builder.db_file
        self.old_reset = _m_message_commands.reset_session
        self.old_send = _owner_conversation_callbacks.send_text
        self.old_remove = _owner_callbacks.remove_inline_keyboard
        self.app_settings_builder.db_file = Path(self.tmp.name) / "bridge.sqlite3"
        self.db = _m_memory_curator.db_connect(app_settings=self.app_settings_builder.build())
        self.session = _owner_session_core.ensure_session(
            self.db, "chat", self.app_settings_builder.default_model, app_settings=self.app_settings_builder.build()
        )

    def tearDown(self):
        _owner_conversation_callbacks.reset_session = self.old_reset
        _owner_conversation_callbacks.send_text = self.old_send
        _owner_conversation_callbacks.remove_inline_keyboard = self.old_remove
        self.db.close()
        self.app_settings_builder.db_file = self.old_db
        self.tmp.cleanup()

    def test_reset_command_preempts_pending_input(self):
        _m_session_naming.set_meta(
            self.db,
            "text_action_input:chat",
            json.dumps(
                {
                    "session_id": self.session["session_id"],
                    "action": "edit",
                    "expires_at": time.time() + 600,
                }
            ),
        )
        opened = []
        original_request = _m_message_commands.send_panel_request
        _m_message_commands.send_panel_request = lambda _token, method, payload, **_kwargs: (
            opened.append((method, payload)) or {}
        )
        try:
            make_test_conversation_service(app_settings=self.app_settings_builder.build()).process_message(
                self.db,
                "token",
                "key",
                self.app_settings_builder.default_model,
                {},
                "chat",
                "/reset",
            )
        finally:
            _m_message_commands.send_panel_request = original_request
        self.assertEqual(opened[0][0], "sendMessage")
        self.assertEqual(opened[0][1]["reply_markup"]["inline_keyboard"][0][0]["callback_data"], "reset:confirm")
        self.assertIn('"action": "edit"', _m_session_naming.get_meta(self.db, "text_action_input:chat", ""))

    def test_confirmed_reset_sends_visible_completion_message(self):
        sent = []
        removed = []

        def original_answer(*_args, **_kwargs):
            return None

        _owner_conversation_callbacks.reset_session = lambda *_args, **_kwargs: None
        _owner_conversation_callbacks.send_text = lambda _token, _chat, text: sent.append(text) or []
        _owner_conversation_callbacks.remove_inline_keyboard = lambda _db, _token, callback: removed.append(callback)
        callback = {"id": "callback-1", "message": {"message_id": 10, "chat": {"id": "chat"}}}
        handled = _owner_conversation_callbacks.handle_reset_callback(
            self.db,
            "token",
            callback,
            original_answer,
            "reset:confirm",
            "chat",
            callback["message"],
            self.session,
            self.session["session_id"],
            None,
            memory_service=make_test_memory_service(),
            npc_service=make_test_npc_service(),
        )
        self.assertTrue(handled)
        self.assertEqual(sent, ["Reset complete. The active session was cleared."])
        self.assertEqual(removed, [callback])

    def test_reset_clears_episodic_memories(self):
        store_episodic_memory(
            self.db,
            "chat",
            self.session["session_id"],
            kind="fact",
            importance=0.9,
            summary="Old red key fact.",
            source_start_rowid=1,
            source_end_rowid=8,
        )
        self.db.commit()

        _m_message_commands.reset_session(
            self.db,
            "token",
            "chat",
            self.session,
            memory_service=make_test_memory_service(),
            npc_service=make_test_npc_service(),
        )

        self.assertEqual(
            self.db.execute(
                "SELECT COUNT(*) FROM episodic_memories WHERE chat_id=? AND session_id=?",
                ("chat", self.session["session_id"]),
            ).fetchone()[0],
            0,
        )

    def test_reset_clears_curated_memory_and_deletes_outgoing_telegram_messages(self):
        curator_key = _m_memory_curator.memory_curator_key("chat", self.session["session_id"])
        _m_session_naming.set_meta(
            self.db,
            curator_key,
            json.dumps({"items": [{"key": "stable", "text": "durable fact"}], "through_rowid": 2}),
        )
        deleted = []
        original_delete = _m_message_commands.delete_outgoing_messages
        _m_message_commands.delete_outgoing_messages = lambda *args, **kwargs: deleted.append((args, kwargs))
        try:
            _m_message_commands.reset_session(
                self.db,
                "token",
                "chat",
                self.session,
                memory_service=make_test_memory_service(),
                npc_service=make_test_npc_service(),
            )
        finally:
            _m_message_commands.delete_outgoing_messages = original_delete

        self.assertEqual(_m_session_naming.get_meta(self.db, curator_key, ""), "")
        self.assertEqual(len(deleted), 1)
        self.assertEqual(deleted[0][0][2:4], ("chat", self.session["session_id"]))


class NarrativeResetTests(SettingsTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.app_settings_builder.db_file = Path(self.tmp.name) / "narrative-reset.sqlite3"
        self.db = _m_memory_curator.db_connect(app_settings=self.app_settings_builder.build())
        self.addCleanup(self.db.close)
        self.session = _owner_session_core.create_session(
            self.db, "chat", "fixture::model", session_id="story", app_settings=self.app_settings_builder.build()
        )
        from narrative_test_support import seed_narrative_story

        seed_narrative_story(self.db, "chat", "story")

    def _reset(self, purge=None):
        from types import SimpleNamespace
        from unittest.mock import patch

        with (
            patch.object(_m_message_commands, "delete_outgoing_messages"),
            patch.object(_m_message_commands, "delete_incoming_messages"),
            patch.object(_m_message_commands, "telegram_request", return_value={}),
        ):
            _m_message_commands.reset_session(
                self.db,
                "token",
                "chat",
                self.session,
                memory_service=SimpleNamespace(purge_session=purge or (lambda *a: 0)),
                npc_service=make_test_npc_service(),
            )

    def test_reset_clears_every_derived_narrative_table_and_keeps_preferences(self):
        from narrative_test_support import NARRATIVE_DERIVED_TABLES

        from bridge.narrative_settings import load_session_narrative_settings, load_user_narrative_default

        before = self.db.execute("SELECT * FROM narrative_settings").fetchall()

        def purge(db, chat_id, session_id):
            self.assertFalse(db.in_transaction)
            self.assertEqual((chat_id, session_id), ("chat", "story"))
            self.assertGreater(db.execute("SELECT COUNT(*) FROM messages").fetchone()[0], 0)
            return 0

        self._reset(purge)
        for table in NARRATIVE_DERIVED_TABLES:
            with self.subTest(table=table):
                self.assertEqual(self.db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0], 0)  # noqa: S608 -- fixed test tables
        self.assertEqual(self.db.execute("SELECT * FROM narrative_settings").fetchall(), before)
        self.assertEqual(load_session_narrative_settings(self.db, "chat", "story").preset, "observer")
        self.assertEqual(load_user_narrative_default(self.db, "owner").preset, "world_driven")
        self.assertIsNotNone(self.db.execute("SELECT 1 FROM sessions WHERE session_id='story'").fetchone())

    def test_failed_external_cleanup_preserves_narrative_and_transcript(self):
        before = "\n".join(self.db.iterdump())

        def fail(*args):
            raise RuntimeError("memory unavailable")

        with self.assertRaisesRegex(RuntimeError, "memory unavailable"):
            self._reset(fail)
        self.assertEqual("\n".join(self.db.iterdump()), before)

    def test_local_reset_failure_rolls_back_transcript_summary_and_narrative(self):
        from unittest.mock import patch

        before_messages = self.db.execute("SELECT * FROM messages").fetchall()
        before_summary = self.db.execute("SELECT * FROM session_summaries").fetchall()
        before_state = self.db.execute("SELECT * FROM narrative_state").fetchall()
        with patch.object(
            _m_message_commands, "reset_conversation", side_effect=RuntimeError("injected local failure")
        ):
            with self.assertRaisesRegex(RuntimeError, "injected local failure"):
                self._reset()
        self.assertFalse(self.db.in_transaction)
        self.assertEqual(self.db.execute("SELECT * FROM messages").fetchall(), before_messages)
        self.assertEqual(self.db.execute("SELECT * FROM session_summaries").fetchall(), before_summary)
        self.assertEqual(self.db.execute("SELECT * FROM narrative_state").fetchall(), before_state)

    def test_reset_rejects_existing_transaction_before_external_cleanup(self):
        self.db.execute("BEGIN")
        calls = []
        try:
            with self.assertRaisesRegex(RuntimeError, "transaction"):
                self._reset(lambda *a: calls.append(True))
            self.assertTrue(self.db.in_transaction)
            self.assertEqual(calls, [])
        finally:
            self.db.rollback()

    def test_summary_clear_joins_existing_caller_transaction(self):
        from bridge.memory import clear_session_summary

        before = self.db.execute("SELECT * FROM session_summaries").fetchall()
        self.db.execute("BEGIN")
        clear_session_summary(self.db, "chat", "story")
        self.assertTrue(self.db.in_transaction)
        self.db.rollback()
        self.assertEqual(self.db.execute("SELECT * FROM session_summaries").fetchall(), before)

    def test_reset_confirmation_explains_narrative_cleanup_and_retained_style(self):
        from bridge.reset_panel import RESET_CONFIRMATION_TEXT

        self.assertIn("narrative scenes, threads, arcs", RESET_CONFIRMATION_TEXT)
        self.assertIn("Keep Narrative Style", RESET_CONFIRMATION_TEXT)


if __name__ == "__main__":
    unittest.main()
