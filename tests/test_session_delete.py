from application_test_setup import (
    ensure_application_extensions,
    make_test_group_service,
    make_test_memory_service,
    make_test_request_context,
)
from settings_test_support import SettingsTestCase

import bridge.cards as _owner_cards
import bridge.session_core as _owner_session_core

ensure_application_extensions()

import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace

import bridge.cards as _m_cards
import bridge.memory as _m_memory
import bridge.memory_curator as _m_memory_curator
import bridge.message_commands as _m_message_commands
import bridge.session_callbacks as _owner_session_callbacks
import bridge.session_naming as _m_session_naming
from bridge.episodic_memory import store_episodic_memory


class SessionDeletionTests(SettingsTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.app_settings_builder.db_file = Path(self.tmp.name) / "bridge.sqlite3"
        self.db = _m_memory_curator.db_connect(app_settings=self.app_settings_builder.build())
        self.original_purge = _m_memory.purge_hindsight_session
        self.purged = []
        _m_memory.purge_hindsight_session = lambda _db, chat_id, session_id, *, app_settings=None: (
            self.purged.append((chat_id, session_id)) or 0
        )

    def tearDown(self):
        _m_memory.purge_hindsight_session = self.original_purge
        self.db.close()
        self.tmp.cleanup()

    def test_inactive_session_deletes_all_local_data(self):
        active = _owner_session_core.ensure_session(
            self.db, "chat", self.app_settings_builder.default_model, app_settings=self.app_settings_builder.build()
        )
        inactive = _m_session_naming.create_session(
            self.db,
            "chat",
            self.app_settings_builder.default_model,
            session_id="inactive",
            app_settings=self.app_settings_builder.build(),
        )
        self.db.execute(
            "INSERT INTO messages(chat_id,session_id,role,content,created_at) VALUES(?,?,?,?,?)",
            ("chat", inactive["session_id"], "user", "old", time.time()),
        )
        self.db.execute(
            "INSERT INTO group_sessions(chat_id,session_id,updated_at) VALUES(?,?,?)",
            ("chat", inactive["session_id"], time.time()),
        )
        store_episodic_memory(
            self.db,
            "chat",
            inactive["session_id"],
            kind="fact",
            importance=0.9,
            summary="Inactive session secret.",
            source_start_rowid=1,
            source_end_rowid=8,
        )
        self.db.commit()

        deleted, reason = _owner_session_core.delete_session_data(
            self.db,
            "chat",
            inactive["session_id"],
            active["session_id"],
            operation_id=701,
            memory_service=SimpleNamespace(
                queue_cleanup=lambda _db, chat_id, session_id: self.purged.append((chat_id, session_id)) or 0
            ),
        )

        self.assertTrue(deleted, reason)
        self.assertEqual(_m_message_commands.operation_phase(self.db, 701), "applied")
        self.assertIsNone(
            _m_memory_curator.load_session(
                self.db,
                "chat",
                inactive["session_id"],
                self.app_settings_builder.default_model,
                app_settings=self.app_settings_builder.build(),
            )
            if self.db.execute(
                "SELECT 1 FROM sessions WHERE chat_id=? AND session_id=?", ("chat", inactive["session_id"])
            ).fetchone()
            else None
        )
        self.assertIsNotNone(
            _m_memory_curator.load_session(
                self.db,
                "chat",
                active["session_id"],
                self.app_settings_builder.default_model,
                app_settings=self.app_settings_builder.build(),
            )
        )
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM messages WHERE session_id='inactive'").fetchone()[0], 0)
        self.assertEqual(
            self.db.execute("SELECT COUNT(*) FROM group_sessions WHERE session_id='inactive'").fetchone()[0], 0
        )
        self.assertEqual(
            self.db.execute("SELECT COUNT(*) FROM episodic_memories WHERE session_id='inactive'").fetchone()[0], 0
        )
        self.assertEqual(self.purged, [("chat", "inactive")])

    def test_session_delete_commits_and_queues_when_hindsight_is_unavailable(self):
        from bridge.memory_retirement_store import queue_session_memory_cleanup

        active = _owner_session_core.ensure_session(
            self.db, "chat", self.app_settings_builder.default_model, app_settings=self.app_settings_builder.build()
        )
        inactive = _m_session_naming.create_session(
            self.db,
            "chat",
            self.app_settings_builder.default_model,
            session_id="preserved",
            app_settings=self.app_settings_builder.build(),
        )
        self.db.execute(
            "INSERT INTO messages(chat_id,session_id,role,content,created_at) VALUES(?,?,?,?,?)",
            ("chat", inactive["session_id"], "user", "keep", time.time()),
        )
        self.db.commit()
        deleted, reason = _owner_session_core.delete_session_data(
            self.db,
            "chat",
            inactive["session_id"],
            active["session_id"],
            memory_service=make_test_memory_service(queue_session_cleanup=queue_session_memory_cleanup),
        )

        self.assertTrue(deleted, reason)
        self.assertIsNone(
            self.db.execute("SELECT 1 FROM sessions WHERE chat_id='chat' AND session_id='preserved'").fetchone()
        )
        self.assertEqual(self.db.execute("SELECT count(*) FROM messages WHERE session_id='preserved'").fetchone(), (0,))
        self.assertEqual(
            self.db.execute("SELECT count(*) FROM memory_cleanup_discovery WHERE session_id='preserved'").fetchone(),
            (1,),
        )

    def test_active_session_and_busy_session_are_protected(self):
        active = _owner_session_core.ensure_session(
            self.db, "chat", self.app_settings_builder.default_model, app_settings=self.app_settings_builder.build()
        )
        denied, reason = _owner_session_core.delete_session_data(
            self.db, "chat", active["session_id"], active["session_id"], memory_service=make_test_memory_service()
        )
        self.assertFalse(denied)
        self.assertEqual(reason, "active session")
        inactive = _m_session_naming.create_session(
            self.db,
            "chat",
            self.app_settings_builder.default_model,
            session_id="busy",
            app_settings=self.app_settings_builder.build(),
        )
        self.db.execute(
            (
                "INSERT INTO jobs(update_id,chat_id,session_id,telegram_message_id,kind,p"
                "ayload_json,state,created_at,updated_at) VALUES(?,?,?,?,?,?,?, ?,?)"
            ),
            (801, "chat", inactive["session_id"], "1", "generation", "{}", "queued", time.time(), time.time()),
        )
        self.db.commit()
        denied, reason = _owner_session_core.delete_session_data(
            self.db, "chat", inactive["session_id"], active["session_id"], memory_service=make_test_memory_service()
        )
        self.assertFalse(denied)
        self.assertEqual(reason, "session has active jobs")

    def test_successful_delete_sends_fresh_session_menu(self):
        active = _owner_session_core.ensure_session(
            self.db, "chat", self.app_settings_builder.default_model, app_settings=self.app_settings_builder.build()
        )
        inactive = _m_session_naming.create_session(
            self.db,
            "chat",
            self.app_settings_builder.default_model,
            session_id="inactive",
            app_settings=self.app_settings_builder.build(),
        )
        menu_calls = []
        original_resolve = _owner_session_callbacks.resolve_dynamic_callback_token
        original_delete = _owner_session_callbacks.delete_session_data
        original_remove = _owner_session_callbacks.remove_inline_keyboard
        original_menu = _owner_session_callbacks.send_session_menu
        _owner_session_callbacks.resolve_dynamic_callback_token = lambda *_args, **_kwargs: inactive["session_id"]
        _owner_session_callbacks.delete_session_data = lambda *_args, **_kwargs: (True, "")
        _owner_session_callbacks.remove_inline_keyboard = lambda *_args, **_kwargs: None
        _owner_session_callbacks.send_session_menu = lambda *args, **kwargs: menu_calls.append((args, kwargs))
        try:
            handled = _owner_session_callbacks.handle_session_callback(
                self.db,
                "token",
                {"id": "callback", "message": {"message_id": 55}},
                lambda *_args, **_kwargs: None,
                "sessiondeleteconfirm:token",
                "chat",
                {"message_id": 55},
                active,
                active["session_id"],
                None,
                group_service=make_test_group_service(app_settings=self.app_settings_builder.build()),
                memory_service=make_test_memory_service(),
                request_context=make_test_request_context(
                    self.db, active["session_id"], app_settings=self.app_settings_builder.build()
                ),
            )
        finally:
            _owner_session_callbacks.resolve_dynamic_callback_token = original_resolve
            _owner_session_callbacks.delete_session_data = original_delete
            _owner_session_callbacks.remove_inline_keyboard = original_remove
            _owner_session_callbacks.send_session_menu = original_menu

        self.assertTrue(handled)
        self.assertEqual(len(menu_calls), 1)
        self.assertIsNone(menu_calls[0][0][4])

    def test_session_cancel_discards_binding_and_closes_panel_with_routed_chat(self):
        session = _owner_session_core.ensure_session(
            self.db, "chat", self.app_settings_builder.default_model, app_settings=self.app_settings_builder.build()
        )
        closed = []
        original_close = _owner_session_callbacks.close_panel_message
        _owner_session_callbacks.close_panel_message = lambda *args: closed.append(args)
        callback = {"id": "callback", "message": {"message_id": 55}}
        try:
            handled = _owner_session_callbacks.handle_session_callback(
                self.db,
                "token",
                callback,
                lambda *_args, **_kwargs: None,
                "session:cancel",
                "chat",
                {"message_id": 55},
                session,
                session["session_id"],
                None,
                group_service=make_test_group_service(app_settings=self.app_settings_builder.build()),
                memory_service=make_test_memory_service(),
                request_context=make_test_request_context(
                    self.db, session["session_id"], app_settings=self.app_settings_builder.build()
                ),
            )
        finally:
            _owner_session_callbacks.close_panel_message = original_close

        self.assertTrue(handled)
        self.assertEqual(closed, [(self.db, "token", "chat", {"message": {"message_id": 55}})])

    def test_session_panel_has_inline_delete_actions_and_protects_active_selection(self):
        active = _owner_session_core.ensure_session(
            self.db, "chat", self.app_settings_builder.default_model, app_settings=self.app_settings_builder.build()
        )
        inactive = _m_session_naming.create_session(
            self.db,
            "chat",
            self.app_settings_builder.default_model,
            session_id="inactive",
            app_settings=self.app_settings_builder.build(),
        )
        calls = []
        original_request = _m_cards.send_panel_request
        _m_cards.send_panel_request = lambda _token, method, payload, **_kwargs: calls.append((method, payload)) or {}
        try:
            _owner_cards.send_session_menu(
                "token",
                "chat",
                [active, inactive],
                active["session_id"],
                request_context=make_test_request_context(
                    self.db, active["session_id"], app_settings=self.app_settings_builder.build()
                ),
            )
        finally:
            _m_cards.send_panel_request = original_request
        rows = calls[0][1]["reply_markup"]["inline_keyboard"]
        callbacks = [button["callback_data"] for row in rows for button in row]
        self.assertEqual(sum(value.startswith("sessiondelete:") for value in callbacks), 1)
        self.assertIn("session:protected", callbacks)
        self.assertIn("session:" + active["session_id"], callbacks)
        self.assertIn("session:" + inactive["session_id"], callbacks)
        self.assertNotIn("session:delete", callbacks)


def test_session_deletion_cascades_all_narrative_rows_but_not_personal_defaults(tmp_path):
    import sqlite3
    from contextlib import closing

    from narrative_test_support import NARRATIVE_DERIVED_TABLES, seed_narrative_story
    from settings_test_support import SettingsBuilder

    from bridge.schema import initialize_database_schema
    from bridge.session_core import create_session, delete_session_data

    builder = SettingsBuilder()
    builder.db_file = tmp_path / "delete.sqlite3"
    with closing(sqlite3.connect(builder.db_file)) as db:
        db.execute("PRAGMA foreign_keys=ON")
        initialize_database_schema(db)
        create_session(db, "chat", "fixture::model", session_id="story", app_settings=builder.build())
        create_session(db, "chat", "fixture::model", session_id="active", app_settings=builder.build())
        seed_narrative_story(db, "chat", "story")
        result = delete_session_data(
            db, "chat", "story", "active", memory_service=SimpleNamespace(queue_cleanup=lambda *a: None)
        )
        assert result == (True, "deleted")
        for table in (*NARRATIVE_DERIVED_TABLES, "narrative_settings"):
            assert db.execute(f"SELECT COUNT(*) FROM {table} WHERE session_id='story'").fetchone()[0] == 0  # noqa: S608 -- fixed test tables
        assert db.execute("SELECT COUNT(*) FROM narrative_defaults").fetchone()[0] == 1
        assert not db.execute("PRAGMA foreign_key_check").fetchall()


def test_new_session_materializes_default_style_and_never_inherits_live_personal_default(tmp_path):
    import sqlite3
    from contextlib import closing

    from settings_test_support import SettingsBuilder

    from bridge.narrative_settings import (
        preset_narrative_settings,
        save_session_narrative_settings,
        save_user_narrative_default,
    )
    from bridge.schema import initialize_database_schema
    from bridge.session_core import create_session, ensure_session

    builder = SettingsBuilder()
    builder.db_file = tmp_path / "create.sqlite3"
    with closing(sqlite3.connect(builder.db_file)) as db:
        db.execute("PRAGMA foreign_keys=ON")
        initialize_database_schema(db)
        save_user_narrative_default(db, "owner", preset_narrative_settings("observer"))
        first = ensure_session(db, "chat", "fixture::model", app_settings=builder.build())
        second = create_session(db, "chat", "fixture::model", session_id="new-story", app_settings=builder.build())
        for session in (first, second):
            row = db.execute(
                "SELECT json_extract(settings_json,'$.preset') FROM narrative_settings WHERE session_id=?",
                (session["session_id"],),
            ).fetchone()
            assert row == ("player_centric",)
        save_session_narrative_settings(db, "chat", "new-story", preset_narrative_settings("world_driven"))
        create_session(db, "chat", "fixture::model", session_id="new-story", app_settings=builder.build())
        assert db.execute(
            "SELECT json_extract(settings_json,'$.preset') FROM narrative_settings WHERE session_id='new-story'"
        ).fetchone() == ("world_driven",)


if __name__ == "__main__":
    unittest.main()
