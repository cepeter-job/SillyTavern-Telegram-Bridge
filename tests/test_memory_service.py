from application_test_setup import (
    ensure_application_extensions,
    make_test_application_services,
    make_test_conversation_service,
    make_test_group_service,
    make_test_input_flow_service,
    make_test_npc_service,
    make_test_persona_service,
    make_test_provider_port,
    make_test_rag_service,
    make_test_request_context,
)
from settings_test_support import SettingsTestCase

import bridge.edit_messages as _owner_edit_messages
import bridge.image_messages as _owner_image_messages
import bridge.message_commands as _owner_message_commands
import bridge.native_imports as _owner_native_imports
import bridge.sqlite_store as _sqlite_store
from bridge.rag_service import RagService

ensure_application_extensions()

import sqlite3
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import bridge.command_routes as _m_command_routes
import bridge.memory as _m_memory
import bridge.memory_backend as _m_memory_backend
import bridge.memory_curator as _m_memory_curator
import bridge.message_commands as _m_message_commands
import bridge.native_imports as _m_telegram
import bridge.session_naming as _m_session_naming
from bridge.memory_service import MemoryPromptContext, MemoryService


class MemoryServiceTests(SettingsTestCase):
    def setUp(self):
        from bridge.memory_contracts import MemoryReadScope

        self.db = sqlite3.connect(":memory:")
        self.session = {"session_id": "session-1", "character_file": "mira.png"}
        self.fields = {"name": "Mira"}
        self.scope = MemoryReadScope("chat", "session-1", 1.0, 19, 0, ("mira",), historical=True)
        self.calls = []

    def tearDown(self):
        self.db.close()

    def _service(self, **overrides):
        from bridge.memory_contracts import MemoryBlock

        def reader(channel):
            def read(db, scope, *args):
                self.assertIs(scope, self.scope)
                self.calls.append(channel)
                return MemoryBlock(channel + " material", channel=channel)

            return read

        values = dict(
            resolve_scope=lambda *args, **kwargs: self.scope,
            scoped_recall=reader("recall"),
            scoped_episodes=reader("episodic"),
            scoped_summary=reader("summary"),
            scoped_scene=reader("scene"),
            validate_blocks=lambda db, scope, blocks: self.calls.append("validate") or blocks,
            summary_state=lambda *args: ("stored summary", 23),
            retain_session=lambda *args: None,
            purge_session_memory=lambda *args: 0,
        )
        values.update(overrides)
        return MemoryService(**values)

    def test_normal_prompt_context_combines_independent_scoped_readers(self):
        result = self._service().prompt_context(self.db, "chat", self.session, self.fields, "Where are we?")
        self.assertEqual(
            (result.recall, result.episodic, result.summary, result.scene),
            (
                "recall material",
                "episodic material",
                "summary material",
                "scene material",
            ),
        )
        self.assertIs(result.scope, self.scope)
        self.assertEqual(self.calls, ["summary", "scene", "episodic", "recall", "validate"])

    def test_edit_adapter_resolves_pre_user_boundary_before_every_read(self):
        resolved = []
        service = self._service(resolve_scope=lambda *args, **kwargs: resolved.append(kwargs) or self.scope)
        result = service.prompt_context(self.db, "chat", self.session, self.fields, "edited", edited_user_rowid=20)
        self.assertEqual(resolved[0]["through_rowid"], 19)
        self.assertIs(resolved[0]["historical"], True)
        self.assertIs(result.scope, self.scope)
        self.assertEqual(self.calls[-1], "validate")

    def test_unresolved_scope_never_reads_memory(self):
        result = self._service(resolve_scope=lambda *args, **kwargs: None).prompt_context(
            self.db,
            "chat",
            self.session,
            self.fields,
            "query",
        )
        self.assertEqual(result, MemoryPromptContext())
        self.assertEqual(self.calls, [])

    def test_final_validation_controls_all_rendered_channels(self):
        from bridge.memory_contracts import MemoryBlock

        result = self._service(
            validate_blocks=lambda db, scope, blocks: tuple(MemoryBlock(channel=block.channel) for block in blocks)
        ).prompt_context(self.db, "chat", self.session, self.fields, "query")
        self.assertEqual((result.recall, result.episodic, result.summary, result.scene), ("", "", "", ""))

    def test_retain_delegates_to_injected_provider(self):
        calls = []
        self._service(retain_session=lambda *args: calls.append(args)).retain(
            self.db,
            "chat",
            self.session,
            self.fields,
        )
        self.assertEqual(calls, [(self.db, "chat", self.session, self.fields)])

    def test_purge_session_returns_injected_provider_result(self):
        calls = []
        result = self._service(purge_session_memory=lambda *args: calls.append(args) or 7).purge_session(
            self.db,
            "chat",
            "session-1",
        )
        self.assertEqual(result, 7)
        self.assertEqual(calls, [(self.db, "chat", "session-1")])

    def test_summary_status_delegates_to_summary_state_provider(self):
        self.assertEqual(self._service().summary_status(self.db, "chat", "session-1"), ("stored summary", 23))


class MemoryServiceMessageIntegrationTests(SettingsTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.old_db = self.app_settings_builder.db_file
        self.app_settings_builder.db_file = Path(self.tmp.name) / "bridge.sqlite3"
        self.db = _m_memory_curator.db_connect(app_settings=self.app_settings_builder.build())
        self.session = _m_session_naming.create_session(
            self.db,
            "chat",
            "provider::model",
            session_id="memory-message",
            app_settings=self.app_settings_builder.build(),
        )
        from bridge.conversation_lifecycle import mark_started

        mark_started(self.db, "chat", self.session["session_id"], 0)
        self.fields = {"name": "Mira"}

    def tearDown(self):
        self.db.close()
        self.app_settings_builder.db_file = self.old_db
        self.tmp.cleanup()

    def test_generate_and_store_reply_uses_injected_memory_service(self):
        calls = []
        captured = {}

        class FakeMemory:
            def prompt_context(
                self,
                db,
                chat_id,
                session,
                fields,
                query,
                **kwargs,
            ):
                calls.append(
                    (
                        "context",
                        db,
                        chat_id,
                        session["session_id"],
                        fields["name"],
                        query,
                        kwargs,
                    )
                )
                return MemoryPromptContext(
                    recall="service recall",
                    summary="service summary",
                )

            def retain(self, db, chat_id, session, fields):
                calls.append(
                    (
                        "retain",
                        db,
                        chat_id,
                        session["session_id"],
                        fields["name"],
                    )
                )

        def build_messages(
            session,
            fields,
            text,
            history_rows,
            *,
            app_settings=None,
            **kwargs,
        ):
            captured.update(kwargs)
            return [{"role": "user", "content": text}]

        def legacy_called(*_args, app_settings=None, **_kwargs):
            raise AssertionError("legacy memory global must not run")

        with (
            patch.object(
                _m_memory_backend,
                "recall_memory_context",
                side_effect=legacy_called,
            ),
            patch.object(
                _m_memory,
                "retain_session_memory",
                side_effect=legacy_called,
            ),
            patch.object(
                RagService,
                "bundle",
                return_value={},
            ),
            patch.object(
                RagService,
                "context_for_prompt",
                return_value="",
            ),
            patch.object(
                RagService,
                "citation_footer",
                return_value="",
            ),
            patch.object(
                _m_message_commands,
                "build_chat_messages",
                side_effect=build_messages,
            ),
            patch.object(
                _m_message_commands,
                "send_typing",
            ),
            patch.object(
                _m_message_commands,
                "normalize_response_language",
                return_value="en",
            ),
            patch.object(
                _m_message_commands,
                "get_generation_settings",
                return_value={},
            ),
            patch.object(
                _m_message_commands,
                "render_response_language",
                side_effect=lambda _key, _model, reply, *_args, **_kwargs: reply,
            ),
            patch.object(
                _m_message_commands,
                "save_response_variant",
                return_value=1,
            ),
            patch.object(
                _m_message_commands,
                "queue_user_quote_tts",
            ),
            patch.object(
                _m_message_commands,
                "send_reply",
            ),
        ):
            _m_message_commands.generate_and_store_reply(
                self.db,
                "token",
                "key",
                self.fields,
                "chat",
                "hello",
                self.session,
                self.session["session_id"],
                "provider::model",
                None,
                "",
                None,
                None,
                provider_port=make_test_provider_port(generate_backend=lambda *_args, **_kwargs: "reply"),
                group_service=make_test_group_service(app_settings=self.app_settings_builder.build()),
                memory_service=FakeMemory(),
                npc_service=make_test_npc_service(),
                persona_service=make_test_persona_service(),
                app_settings=self.app_settings_builder.build(),
                rag_service=make_test_rag_service(),
            )

        self.assertEqual(captured["memory_context"], "service recall")
        self.assertEqual(captured["session_summary"], "service summary")
        self.assertEqual(calls[0][0], "context")
        self.assertEqual(calls[0][2:6], ("chat", "memory-message", "Mira", "hello"))
        self.assertEqual(calls[0][6], {})
        self.assertEqual(calls[1][0], "retain")
        self.assertEqual(calls[1][2:], ("chat", "memory-message", "Mira"))

    def test_edited_turn_recovery_uses_injected_memory_service(self):
        now = time.time()
        user_cursor = self.db.execute(
            "INSERT INTO messages(chat_id,session_id,role,content,created_at) VALUES(?,?,?,?,?)",
            ("chat", self.session["session_id"], "user", "old text", now),
        )
        user_rowid = int(user_cursor.lastrowid)
        self.db.execute(
            "INSERT INTO messages(chat_id,session_id,role,content,created_at) VALUES(?,?,?,?,?)",
            ("chat", self.session["session_id"], "assistant", "old reply", now + 0.001),
        )
        self.db.commit()
        calls = []
        captured = {}

        class FakeMemory:
            def prompt_context(
                self,
                db,
                chat_id,
                session,
                fields,
                query,
                **kwargs,
            ):
                calls.append(("context", kwargs))
                return MemoryPromptContext(
                    recall="recovery recall",
                    summary="recovery summary",
                )

            def retain(self, db, chat_id, session, fields):
                calls.append(("retain", chat_id, session["session_id"]))

        def legacy_called(*_args, app_settings=None, **_kwargs):
            raise AssertionError("legacy memory global must not run")

        def build_messages(
            session,
            fields,
            text,
            history_rows,
            *,
            app_settings=None,
            **kwargs,
        ):
            captured.update(kwargs)
            return [{"role": "user", "content": text}]

        with (
            patch.object(
                _m_memory_backend,
                "recall_memory_context",
                side_effect=legacy_called,
            ),
            patch.object(
                _m_memory,
                "get_session_summary",
                side_effect=legacy_called,
            ),
            patch.object(
                _m_memory,
                "retain_session_memory",
                side_effect=legacy_called,
            ),
            patch.object(
                RagService,
                "bundle",
                return_value={},
            ),
            patch.object(
                RagService,
                "context_for_prompt",
                return_value="",
            ),
            patch.object(
                _owner_edit_messages,
                "build_chat_messages",
                side_effect=build_messages,
            ),
            patch.object(
                _owner_edit_messages,
                "send_typing",
            ),
            patch.object(
                RagService,
                "citation_footer",
                return_value="",
            ),
            patch.object(
                _owner_edit_messages,
                "render_session_response",
                side_effect=lambda _api_key, _session, reply, _chat_id, _settings, **_kwargs: reply,
            ),
            patch.object(
                _owner_edit_messages,
                "save_response_variant",
            ),
            patch.object(
                _owner_edit_messages,
                "send_reply",
            ),
        ):
            _owner_edit_messages.regenerate_edited_turn(
                self.db,
                "token",
                "key",
                self.session,
                self.fields,
                "chat",
                user_rowid,
                "new text",
                provider_port=make_test_provider_port(generate_backend=lambda *_args, **_kwargs: "new reply"),
                memory_service=FakeMemory(),
                npc_service=make_test_npc_service(),
                persona_service=make_test_persona_service(),
                app_settings=self.app_settings_builder.build(),
                rag_service=make_test_rag_service(),
            )

        self.assertEqual(
            calls[0],
            ("context", {"edited_user_rowid": user_rowid}),
        )
        self.assertEqual(captured["memory_context"], "recovery recall")
        self.assertEqual(captured["session_summary"], "recovery summary")
        self.assertEqual(
            calls[1],
            ("retain", "chat", self.session["session_id"]),
        )

    def test_regen_command_propagates_injected_memory_service(self):
        memory = object()
        captured = {}

        def fake_regen(*args, app_settings=None, **kwargs):
            captured.update(kwargs)

        with (
            patch.object(
                _m_command_routes,
                "_dispatch_extension_command_routes",
                return_value=False,
            ),
            patch.object(
                _m_command_routes,
                "regenerate_last",
                side_effect=fake_regen,
            ),
        ):
            handled = _m_command_routes.handle_command_route(
                self.db,
                "token",
                "key",
                "provider::model",
                self.fields,
                "chat",
                "/regen",
                "/regen",
                self.session,
                self.session["session_id"],
                "provider::model",
                "",
                "User",
                request_context=make_test_request_context(
                    self.db, self.session["session_id"], app_settings=self.app_settings_builder.build()
                ),
                conversation_service=make_test_application_services(
                    memory=memory, app_settings=self.app_settings_builder.build()
                ).conversation,
                delivery_port=make_test_application_services(
                    memory=memory, app_settings=self.app_settings_builder.build()
                ).delivery,
                group_service=make_test_application_services(
                    memory=memory, app_settings=self.app_settings_builder.build()
                ).group,
                memory_service=make_test_application_services(
                    memory=memory, app_settings=self.app_settings_builder.build()
                ).memory,
                npc_service=make_test_application_services(
                    memory=memory, app_settings=self.app_settings_builder.build()
                ).npc,
                persona_service=make_test_application_services(
                    memory=memory, app_settings=self.app_settings_builder.build()
                ).persona,
                provider_port=make_test_application_services(
                    memory=memory, app_settings=self.app_settings_builder.build()
                ).provider,
                sync_service=make_test_application_services(
                    memory=memory, app_settings=self.app_settings_builder.build()
                ).sync,
                rag_service=make_test_rag_service(),
            )

        self.assertTrue(handled)
        self.assertIs(captured["memory_service"], memory)

    def test_pending_input_receives_injected_memory_service(self):
        memory = object()
        captured = {}

        def fake_pending(*args, **kwargs):
            captured.update(kwargs)
            return True

        make_test_conversation_service(
            app_settings=self.app_settings_builder.build(),
            memory=memory,
            input_flow=make_test_input_flow_service(
                handle_pending_backend=fake_pending, app_settings=self.app_settings_builder.build()
            ),
        ).process_message(
            self.db,
            "token",
            "key",
            "provider::model",
            self.fields,
            "chat",
            "replacement text",
        )

        self.assertIs(captured["memory_service"], memory)

    def test_image_message_uses_injected_memory_service(self):
        calls = []
        captured = {}

        class FakeMemory:
            def prompt_context(
                self,
                db,
                chat_id,
                session,
                fields,
                query,
                **kwargs,
            ):
                calls.append(("context", query))
                return MemoryPromptContext(
                    recall="image recall",
                    summary="image summary",
                )

            def retain(self, db, chat_id, session, fields):
                calls.append(("retain", session["session_id"]))

        def legacy_called(*_args, app_settings=None, **_kwargs):
            raise AssertionError("legacy memory global must not run")

        def build_messages(
            session,
            fields,
            text,
            history_rows,
            *,
            app_settings=None,
            **kwargs,
        ):
            captured.update(kwargs)
            return [{"role": "user", "content": text}]

        group_service = SimpleNamespace(
            current_speaker=lambda *_args, **_kwargs: None,
            advance_turn=lambda *_args, **_kwargs: None,
        )

        with (
            patch.object(
                _m_memory_backend,
                "recall_memory_context",
                side_effect=legacy_called,
            ),
            patch.object(
                _m_memory,
                "retain_session_memory",
                side_effect=legacy_called,
            ),
            patch.object(
                RagService,
                "bundle",
                return_value={},
            ),
            patch.object(
                RagService,
                "context_for_prompt",
                return_value="",
            ),
            patch.object(
                _owner_image_messages,
                "build_chat_messages",
                side_effect=build_messages,
            ),
            patch.object(
                _owner_image_messages,
                "send_typing",
            ),
            patch.object(
                _owner_image_messages,
                "get_generation_settings",
                return_value={},
            ),
            patch.object(
                RagService,
                "citation_footer",
                return_value="",
            ),
            patch.object(
                _owner_image_messages,
                "render_session_response",
                side_effect=lambda _key, _session, reply, *_args, **_kwargs: reply,
            ),
            patch.object(
                _owner_image_messages,
                "save_response_variant",
                return_value=1,
            ),
            patch.object(
                _owner_image_messages,
                "send_reply",
            ),
        ):
            _owner_image_messages.process_image_message(
                self.db,
                "token",
                "key",
                self.session,
                self.fields,
                "chat",
                "describe this",
                b"image-bytes",
                provider_port=make_test_provider_port(generate_backend=lambda *_args, **_kwargs: "image reply"),
                group_service=group_service,
                memory_service=FakeMemory(),
                npc_service=make_test_npc_service(),
                persona_service=make_test_persona_service(),
                group_director_service=make_test_application_services(
                    app_settings=self.app_settings_builder.build()
                ).group_director,
                app_settings=self.app_settings_builder.build(),
                rag_service=make_test_rag_service(),
            )

        self.assertEqual(captured["memory_context"], "image recall")
        self.assertEqual(captured["session_summary"], "image summary")
        self.assertEqual(
            calls,
            [
                ("context", "describe this"),
                ("retain", self.session["session_id"]),
            ],
        )

    def test_png_document_adapter_propagates_memory_service(self):
        memory = object()
        captured = {}
        document = {
            "file_name": "photo.png",
            "file_id": "file-id",
            "file_size": 5,
            "caption": "caption",
        }

        with (
            patch.object(
                _m_telegram,
                "_consume_world_upload",
                return_value=False,
            ),
            patch.object(
                _m_telegram,
                "download_telegram_file",
                return_value=b"not-a-card",
            ),
            patch.object(
                _m_telegram,
                "parse_png_chara_bytes",
                side_effect=ValueError("not card"),
            ),
            patch.object(
                _m_telegram,
                "load_session",
                return_value=self.session,
            ),
            patch.object(
                _m_telegram,
                "card_fields_from_file",
                return_value=self.fields,
            ),
        ):
            _owner_native_imports.import_telegram_document(
                self.db,
                "token",
                "chat",
                document,
                "provider::model",
                api_key="key",
                process_image=lambda *_args, **kwargs: captured.update(kwargs),
                memory_service=memory,
                persona_service=make_test_persona_service(),
                group_director_service=make_test_application_services(
                    app_settings=self.app_settings_builder.build()
                ).group_director,
                app_settings=self.app_settings_builder.build(),
                rag_service=make_test_rag_service(),
                provider_port=make_test_provider_port(),
                request_context=make_test_request_context(
                    self.db, self.session["session_id"], app_settings=self.app_settings_builder.build()
                ),
            )

        self.assertIs(captured["memory_service"], memory)


class MemoryServiceExplicitInjectionBoundaryTests(SettingsTestCase):
    def test_memory_application_paths_do_not_resolve_compatibility_service(self):
        root = Path(__file__).parents[1] / "bridge"
        for filename in (
            "message_commands.py",
            "telegram.py",
            "callbacks.py",
            "response_delivery.py",
            "speech.py",
            "voice_jobs.py",
            "bot_commands.py",
            "databank_panels.py",
            "document_jobs.py",
            "enum_callbacks.py",
            "memory_panels.py",
            "preset_panels.py",
            "settings_panels.py",
            "system_prompt_panels.py",
            "voice_panels.py",
            "input_flows.py",
            "pending_input.py",
            "persona_callbacks.py",
            "persona_input.py",
            "persona_panels.py",
            "settings_input.py",
            "text_action_input.py",
            "continuation.py",
            "generation.py",
            "generation_recovery.py",
            "regeneration.py",
            "response_variants.py",
            "swipe_panels.py",
            "edit_messages.py",
            "image_messages.py",
            "macro_commands.py",
            "note_panels.py",
            "preset_actions.py",
            "prompt_diagnostics.py",
        ):
            source = (root / filename).read_text(encoding="utf-8")
            self.assertNotIn("resolve_memory_service", source, filename)
            self.assertNotIn("compatibility_memory_service", source, filename)


class MemoryServiceBoundaryTests(SettingsTestCase):
    def test_reviewed_application_paths_do_not_call_memory_backend_functions_directly(self):
        root = Path(__file__).parents[1] / "bridge"
        reviewed = (
            "edit_messages.py",
            "image_messages.py",
            "macro_commands.py",
            "note_panels.py",
            "preset_actions.py",
            "prompt_diagnostics.py",
            "continuation.py",
            "generation.py",
            "generation_recovery.py",
            "regeneration.py",
            "response_variants.py",
            "swipe_panels.py",
            "message_commands.py",
        )
        forbidden_calls = (
            "recall_memory_context(",
            "session_summary_for_prompt(",
            "retain_session_memory(",
            "purge_hindsight_session(",
            "get_session_summary(",
        )

        for filename in reviewed:
            source = (root / filename).read_text(encoding="utf-8")
            for forbidden in forbidden_calls:
                with self.subTest(filename=filename, forbidden=forbidden):
                    self.assertNotIn(forbidden, source)

    def test_reset_session_uses_memory_service_boundary(self):
        tmp = tempfile.TemporaryDirectory()
        old_db = self.app_settings_builder.db_file
        try:
            self.app_settings_builder.db_file = Path(tmp.name) / "reset.sqlite3"
            db = _m_memory_curator.db_connect(app_settings=self.app_settings_builder.build())
            session = _m_session_naming.create_session(
                db, "chat", "provider::model", session_id="reset-memory", app_settings=self.app_settings_builder.build()
            )
            db.execute(
                "INSERT INTO messages(chat_id,session_id,role,content,created_at) VALUES(?,?,?,?,?)",
                ("chat", "reset-memory", "user", "hello", time.time()),
            )
            db.commit()
            calls = []

            class FakeMemory:
                def purge_session(self, current_db, chat_id, session_id):
                    calls.append((current_db, chat_id, session_id))
                    return 1

            with (
                patch.object(
                    _m_memory,
                    "purge_hindsight_session",
                    side_effect=AssertionError("raw purge must not run"),
                ),
                patch.object(
                    _sqlite_store,
                    "optimize_database",
                ),
            ):
                _owner_message_commands.reset_session(
                    db,
                    "token",
                    "chat",
                    session,
                    memory_service=FakeMemory(),
                    npc_service=make_test_npc_service(),
                )

            self.assertEqual(calls, [(db, "chat", "reset-memory")])
            self.assertEqual(
                db.execute(
                    "SELECT COUNT(*) FROM messages WHERE chat_id=? AND session_id=?",
                    ("chat", "reset-memory"),
                ).fetchone()[0],
                0,
            )
            db.close()
        finally:
            self.app_settings_builder.db_file = old_db
            tmp.cleanup()


if __name__ == "__main__":
    unittest.main()
