from application_test_setup import (
    ensure_application_extensions,
    make_test_application_services,
    make_test_request_context,
)
from settings_test_support import SettingsTestCase

ensure_application_extensions()

import json
import tempfile
import time
from pathlib import Path

import bridge.callback_dispatch as _m_callback_dispatch
import bridge.cards as _m_cards
import bridge.character_callbacks as _owner_character_callbacks
import bridge.memory_curator as _m_memory_curator
import bridge.panel_bindings as _owner_panel_bindings
import bridge.session_core as _owner_session_core
import bridge.session_naming as _m_session_naming
import bridge.telegram as _m_telegram


class CharacterUploadLifecycleTests(SettingsTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.app_settings_builder.db_file = Path(self.tmp.name) / "bridge.sqlite3"
        self.db = _m_memory_curator.db_connect(app_settings=self.app_settings_builder.build())

    def tearDown(self):
        self.db.close()
        self.tmp.cleanup()

    def test_failed_upload_guidance_does_not_enable_upload(self):
        session = _owner_session_core.ensure_session(
            self.db, "chat", self.app_settings_builder.default_model, app_settings=self.app_settings_builder.build()
        )
        context = make_test_request_context(
            self.db, session["session_id"], "user-1", app_settings=self.app_settings_builder.build()
        )
        original_request = _owner_character_callbacks.send_panel_request
        _owner_character_callbacks.send_panel_request = lambda *_args, **_kwargs: (_ for _ in ()).throw(
            RuntimeError("guidance edit failed")
        )
        try:
            with self.assertRaisesRegex(RuntimeError, "guidance edit failed"):
                _owner_character_callbacks._handle_upload_menu(
                    self.db,
                    "token",
                    {"id": "callback-105"},
                    lambda *_args: None,
                    "chat",
                    {"message_id": 105},
                    request_context=context,
                )
        finally:
            _owner_character_callbacks.send_panel_request = original_request
        self.assertEqual(_m_session_naming.get_meta(self.db, "character_upload:chat:user-1", ""), "")

    def test_navigating_away_from_character_upload_releases_upload_mode(self):
        from bridge.conversation_lifecycle import has_pending_character_upload

        session = _owner_session_core.ensure_session(
            self.db, "chat", self.app_settings_builder.default_model, app_settings=self.app_settings_builder.build()
        )
        context = make_test_request_context(
            self.db, session["session_id"], "user-1", app_settings=self.app_settings_builder.build()
        )
        other_context = make_test_request_context(
            self.db, session["session_id"], "user-2", app_settings=self.app_settings_builder.build()
        )
        sent_ids = iter((105, 106, 107))
        original_request = _m_telegram.telegram_request
        original_answer = _m_callback_dispatch.answer_callback

        def fake_request(_token, method, payload):
            if method == "sendMessage":
                return {"message_id": next(sent_ids)}
            return {"message_id": payload.get("message_id")}

        _m_telegram.telegram_request = fake_request
        _m_callback_dispatch.answer_callback = lambda *_args, **_kwargs: None
        try:
            _m_cards.send_character_menu("token", "chat", session["character_file"], request_context=context)
            callback = {
                "id": "callback-105",
                "from": {"id": "user-1"},
                "data": "character:upload",
                "message": {"message_id": 105, "chat": {"id": "chat"}},
            }
            _m_callback_dispatch.process_callback(
                self.db,
                "token",
                callback,
                services=make_test_application_services(app_settings=self.app_settings_builder.build()),
            )
            self.assertTrue(has_pending_character_upload(self.db, "chat", session["session_id"], "user-1"))
            _m_cards.send_character_menu("token", "chat", session["character_file"], request_context=other_context)
            self.assertTrue(has_pending_character_upload(self.db, "chat", session["session_id"], "user-1"))
            _m_cards.send_character_menu("token", "chat", session["character_file"], request_context=context)
        finally:
            _m_telegram.telegram_request = original_request
            _m_callback_dispatch.answer_callback = original_answer
        self.assertFalse(has_pending_character_upload(self.db, "chat", session["session_id"], "user-1"))

    def test_other_session_navigation_releases_same_actors_upload(self):
        from bridge.conversation_lifecycle import has_pending_character_upload

        session = _owner_session_core.ensure_session(
            self.db, "chat", self.app_settings_builder.default_model, app_settings=self.app_settings_builder.build()
        )
        _m_session_naming.set_meta(
            self.db,
            "character_upload:chat:user-1",
            json.dumps(
                {
                    "session_id": session["session_id"],
                    "actor_id": "user-1",
                    "panel_message_id": "105",
                    "expires_at": time.time() + 600,
                }
            ),
        )
        _owner_panel_bindings.bind_management_panel(self.db, "chat", "user-1", 105)
        other_session_context = make_test_request_context(
            self.db, "another-session", "user-1", app_settings=self.app_settings_builder.build()
        )
        original_request = _m_telegram.telegram_request
        _m_telegram.telegram_request = lambda *_args, **_kwargs: {}
        try:
            _m_telegram.close_active_management_panel("token", "chat", request_context=other_session_context)
        finally:
            _m_telegram.telegram_request = original_request
        self.assertFalse(has_pending_character_upload(self.db, "chat", session["session_id"], "user-1"))

    def test_duplicate_upload_confirmation_handoff_stops_on_later_navigation(self):
        from character_test_support import card_png as _card_png

        from bridge import native_imports
        from bridge.conversation_lifecycle import has_pending_character_upload

        self.app_settings_builder.character_dir = Path(self.tmp.name) / "characters"
        self.app_settings_builder.character_backup_dir = Path(self.tmp.name) / "backups"
        settings = self.app_settings_builder.build()
        settings.character_dir.mkdir(parents=True, exist_ok=True)
        settings.character_backup_dir.mkdir(parents=True, exist_ok=True)
        session = _owner_session_core.ensure_session(self.db, "chat", settings.default_model, app_settings=settings)
        context = make_test_request_context(self.db, session["session_id"], "user-1", app_settings=settings)
        sent_ids = iter((205, 206, 207))
        original_request = _m_telegram.telegram_request
        original_answer = _m_callback_dispatch.answer_callback
        original_send_text = native_imports.send_text

        def fake_request(_token, method, payload):
            if method == "sendMessage":
                return {"message_id": next(sent_ids)}
            return {"message_id": payload.get("message_id")}

        _m_telegram.telegram_request = fake_request
        _m_callback_dispatch.answer_callback = lambda *_args, **_kwargs: None
        native_imports.send_text = lambda *_args, **_kwargs: [1]
        try:
            _m_cards.send_character_menu("token", "chat", session["character_file"], request_context=context)
            _m_callback_dispatch.process_callback(
                self.db,
                "token",
                {
                    "id": "callback-205",
                    "from": {"id": "user-1"},
                    "data": "character:upload",
                    "message": {"message_id": 205, "chat": {"id": "chat"}},
                },
                services=make_test_application_services(app_settings=settings),
            )
            raw = _card_png("Alice", "original")
            native_imports.import_character_card(
                self.db, "token", "chat", "Alice.png", raw, app_settings=settings, request_context=context
            )
            self.assertTrue(has_pending_character_upload(self.db, "chat", session["session_id"], "user-1"))
            native_imports.import_character_card(
                self.db,
                "token",
                "chat",
                "Alice.png",
                _card_png("Alice", "updated"),
                app_settings=settings,
                request_context=context,
            )
            self.assertTrue(has_pending_character_upload(self.db, "chat", session["session_id"], "user-1"))
            self.assertEqual(_owner_panel_bindings.active_management_panel(self.db, "chat", "user-1"), 206)
            _m_cards.send_character_menu("token", "chat", session["character_file"], request_context=context)
        finally:
            _m_telegram.telegram_request = original_request
            _m_callback_dispatch.answer_callback = original_answer
            native_imports.send_text = original_send_text
        self.assertFalse(has_pending_character_upload(self.db, "chat", session["session_id"], "user-1"))
