from application_test_setup import (
    ensure_application_extensions,
    make_native_test_sync_service,
    make_test_delivery_port,
    make_test_group_service,
    make_test_memory_service,
    make_test_request_context,
)
from settings_test_support import SettingsTestCase

import bridge.bot_commands as _owner_bot_commands
import bridge.help_details as _owner_help_details
import bridge.memory_curator as _owner_memory_curator
import bridge.provider_discovery as _owner_provider_discovery
import bridge.sync_panels as _owner_sync_panels
import bridge.telegram as _owner_telegram
from bridge import prompt_panels

ensure_application_extensions()

import tempfile
import unittest
from pathlib import Path

import bridge.cards as _m_cards
import bridge.command_routes as _m_command_routes
import bridge.director_goals as _m_director_goals
import bridge.help_details as _m_help_details
import bridge.memory_curator as _m_memory_curator
import bridge.scene_state as _m_scene_state
import bridge.session_naming as _m_session_naming
import bridge.sync_core as _m_sync_core


class HelpDrilldownTests(SettingsTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.app_settings_builder.db_file = Path(self.tmp.name) / "bridge.sqlite3"
        self.db = _m_memory_curator.db_connect(app_settings=self.app_settings_builder.build())

    def tearDown(self):
        self.db.close()
        self.tmp.cleanup()

    def _help_delivery(self, calls):
        return make_test_delivery_port(
            send_panel_request=lambda _token, method, payload, **_kwargs: calls.append((method, payload)) or {},
        )

    def test_category_renders_command_buttons_and_detail(self):
        calls = []
        original_request = _m_cards.send_panel_request
        _m_cards.send_panel_request = lambda _token, method, payload, **_kwargs: calls.append((method, payload)) or {}
        try:
            _m_command_routes.send_help_menu(
                "token",
                "chat",
                "basic",
                delivery_port=self._help_delivery(calls),
                request_context=make_test_request_context(
                    self.db, "panel-session", app_settings=self.app_settings_builder.build()
                ),
            )
            buttons = [button for row in calls[-1][1]["reply_markup"]["inline_keyboard"] for button in row]
            self.assertIn("help:cmd:basic:0", {button["callback_data"] for button in buttons})
            self.assertIn("/start", {button["text"] for button in buttons})
            _m_command_routes.send_help_menu(
                "token",
                "chat",
                "basic",
                77,
                0,
                delivery_port=self._help_delivery(calls),
                request_context=make_test_request_context(
                    self.db, "panel-session", app_settings=self.app_settings_builder.build()
                ),
            )
            detail = calls[-1][1]
            self.assertEqual(calls[-1][0], "editMessageText")
            self.assertIn("Help — /start", detail["text"])
            self.assertIn("opening message", detail["text"])
            detail_callbacks = {
                button["callback_data"] for row in detail["reply_markup"]["inline_keyboard"] for button in row
            }
            self.assertEqual(detail_callbacks, {"help:basic", "help:close"})
        finally:
            _m_cards.send_panel_request = original_request

    def test_help_copy_explains_topics_and_actions(self):
        calls = []
        original_request = _m_cards.send_panel_request
        _m_cards.send_panel_request = lambda _token, method, payload, **_kwargs: calls.append((method, payload)) or {}
        try:
            _m_command_routes.send_help_menu(
                "token",
                "chat",
                delivery_port=self._help_delivery(calls),
                request_context=make_test_request_context(
                    self.db, "panel-session", app_settings=self.app_settings_builder.build()
                ),
            )
            root = calls[-1][1]
            self.assertIn("Choose a topic below", root["text"])
            labels = {button["text"] for row in root["reply_markup"]["inline_keyboard"] for button in row}
            self.assertIn("💬 Start & Sessions", labels)
            _m_command_routes.send_help_menu(
                "token",
                "chat",
                "basic",
                delivery_port=self._help_delivery(calls),
                request_context=make_test_request_context(
                    self.db, "panel-session", app_settings=self.app_settings_builder.build()
                ),
            )
            category = calls[-1][1]
            self.assertIn("Start a conversation", category["text"])
            self.assertIn("Tap a command below", category["text"])
            _m_command_routes.send_help_menu(
                "token",
                "chat",
                "basic",
                77,
                0,
                delivery_port=self._help_delivery(calls),
                request_context=make_test_request_context(
                    self.db, "panel-session", app_settings=self.app_settings_builder.build()
                ),
            )
            self.assertIn("What it does:", calls[-1][1]["text"])
        finally:
            _m_cards.send_panel_request = original_request

    def test_command_detail_callback_rejects_stale_index(self):
        answers = []
        original_answer = _owner_telegram.answer_callback
        _owner_telegram.answer_callback = lambda _token, _callback_id, text: answers.append(text)
        try:
            handled = _owner_help_details.handle_help_callback(
                self.db,
                "token",
                {"id": "cb"},
                _owner_telegram.answer_callback,
                "help:cmd:basic:99",
                "chat",
                {"message_id": 77},
                {},
                "default",
                None,
                delivery_port=make_test_delivery_port(),
                request_context=make_test_request_context(
                    self.db, "panel-session", app_settings=self.app_settings_builder.build()
                ),
            )
        finally:
            _owner_telegram.answer_callback = original_answer
        self.assertTrue(handled)
        self.assertEqual(answers, ["Help choice expired"])

    def test_category_command_list_is_paginated_at_eight(self):
        calls = []
        original_request = _m_cards.send_panel_request
        _m_cards.send_panel_request = lambda _token, method, payload, **_kwargs: calls.append((method, payload)) or {}
        try:
            _m_command_routes.send_help_menu(
                "token",
                "chat",
                "generation",
                delivery_port=self._help_delivery(calls),
                request_context=make_test_request_context(
                    self.db, "panel-session", app_settings=self.app_settings_builder.build()
                ),
            )
        finally:
            _m_cards.send_panel_request = original_request
        buttons = [button for row in calls[0][1]["reply_markup"]["inline_keyboard"] for button in row]
        command_buttons = [button for button in buttons if button["callback_data"].startswith("help:cmd:")]
        self.assertEqual(len(command_buttons), 8)
        self.assertIn(
            "help:cmdpage:generation:1",
            {button["callback_data"] for row in calls[0][1]["reply_markup"]["inline_keyboard"] for button in row},
        )

    def test_standalone_export_import_are_not_public_help_commands(self):
        public_commands = {
            command for entries in _m_help_details.HELP_CATEGORIES.values() for command, _summary in entries
        }
        self.assertNotIn("/export", public_commands)
        self.assertNotIn("/import", public_commands)
        self.assertIn("/sync", public_commands)

    def test_cancel_is_documented_and_registered(self):
        public_commands = {
            command for entries in _m_help_details.HELP_CATEGORIES.values() for command, _summary in entries
        }
        self.assertIn("/cancel", public_commands)
        detail = _m_help_details.command_detail("/cancel", "")
        self.assertIn("pending input", detail.lower())
        self.assertIn("nothing is applied", detail.lower())

        calls = []
        original_request = _owner_bot_commands.telegram_request
        _owner_bot_commands.telegram_request = lambda _token, method, payload: calls.append((method, payload)) or {}
        try:
            _owner_bot_commands.set_bot_commands("token")
        finally:
            _owner_bot_commands.telegram_request = original_request

        commands = {item["command"]: item["description"] for item in calls[-1][1]["commands"]}
        self.assertEqual(
            commands["cancel"],
            "Cancel the current pending input",
        )

    def test_telegram_command_menu_matches_help_top_level_commands(self):
        calls = []
        original_request = _owner_bot_commands.telegram_request
        _owner_bot_commands.telegram_request = lambda _token, method, payload: calls.append((method, payload)) or {}
        try:
            _owner_bot_commands.set_bot_commands("token")
        finally:
            _owner_bot_commands.telegram_request = original_request

        bot_commands = {"/" + item["command"] for item in calls[-1][1]["commands"]}
        help_bases = {
            command.split()[0].split("|", 1)[0]
            for entries in _m_help_details.HELP_CATEGORIES.values()
            for command, _summary in entries
        }
        self.assertEqual(bot_commands, help_bases)

    def test_sync_help_exposes_only_live_api_sync(self):
        summary = dict(_m_help_details.HELP_CATEGORIES["basic"])["/sync"]
        detail = _m_help_details.command_detail("/sync", summary)
        self.assertIn("Live API Sync", summary)
        self.assertIn("Live API Sync controls", summary)
        self.assertNotIn("file sync", summary.lower())
        self.assertNotIn("Phase ", summary)
        self.assertIn("Sync now runs a one-shot API reconciliation", detail)
        self.assertNotIn("file sync", detail.lower())
        self.assertIn("Refresh status only redraws state", detail)
        calls = []
        original_request = _owner_bot_commands.telegram_request
        _owner_bot_commands.telegram_request = lambda _token, method, payload: calls.append((method, payload)) or {}
        try:
            _owner_bot_commands.set_bot_commands("token")
        finally:
            _owner_bot_commands.telegram_request = original_request
        commands = {item["command"]: item["description"] for item in calls[-1][1]["commands"]}
        self.assertEqual(commands["sync"], "Open Live API Sync controls")

    def test_sync_binding_is_stable_and_panel_is_scoped(self):
        session = _m_session_naming.create_session(
            self.db, "chat", "provider/model", session_id="sync-session", app_settings=self.app_settings_builder.build()
        )
        first = _m_sync_core.ensure_sync_binding(self.db, "chat", session["session_id"])
        second = _m_sync_core.ensure_sync_binding(self.db, "chat", session["session_id"])
        self.assertEqual(first["sync_id"], second["sync_id"])
        calls = []
        original_request = _m_cards.send_panel_request
        _m_cards.send_panel_request = lambda _token, method, payload, **_kwargs: calls.append((method, payload)) or {}
        try:
            _owner_sync_panels.send_sync_menu(
                "token",
                "chat",
                self.db,
                session,
                sync_service=make_native_test_sync_service(app_settings=self.app_settings_builder.build()),
                request_context=make_test_request_context(
                    self.db, session["session_id"], app_settings=self.app_settings_builder.build()
                ),
            )
        finally:
            _m_cards.send_panel_request = original_request
        callbacks = {
            button["callback_data"] for row in calls[-1][1]["reply_markup"]["inline_keyboard"] for button in row
        }
        self.assertEqual(callbacks, {"sync:realtime", "sync:now", "sync:status", "sync:close"})

    def test_director_goal_and_scene_commands_are_documented(self):
        commands = {command for entries in _m_help_details.HELP_CATEGORIES.values() for command, _summary in entries}
        self.assertIn("/group goal", commands)
        self.assertNotIn("/group goal <objective>", commands)
        self.assertIn("/scene", commands)
        self.assertNotIn("/scene refresh", commands)
        self.assertIn("panel", _m_help_details.command_detail("/group goal", "").lower())
        self.assertIn("refresh", _m_help_details.command_detail("/scene", "").lower())

    def test_memory_search_stays_direct_while_scene_actions_use_panel(self):
        commands = {command for entries in _m_help_details.HELP_CATEGORIES.values() for command, _summary in entries}
        self.assertIn("/memory search <query>", commands)
        self.assertNotIn("/scene clear", commands)

        memory_detail = _m_help_details.command_detail("/memory search <query>", "")
        self.assertIn("active session", memory_detail.lower())
        self.assertIn("query", memory_detail.lower())

        target = _m_help_details._help_command_target("scene clear")
        self.assertIsNotNone(target)
        category, index = target
        self.assertEqual(_m_help_details.HELP_CATEGORIES[category][index][0], "/scene")

        readme = (Path(__file__).parents[1] / "README.md").read_text(encoding="utf-8")
        self.assertIn("canonical command reference", readme)

    def test_help_details_exactly_cover_public_catalog(self):
        public_commands = {
            command for entries in _m_help_details.HELP_CATEGORIES.values() for command, _summary in entries
        }
        self.assertEqual(set(_m_help_details.COMMAND_DETAILS), public_commands)
        self.assertTrue(all(_m_help_details.COMMAND_DETAILS[command].strip() for command in public_commands))

    def test_only_free_form_or_explicit_actions_remain_direct_help_commands(self):
        public_commands = {
            command for entries in _m_help_details.HELP_CATEGORIES.values() for command, _summary in entries
        }
        expected_direct = {
            "/macro <text>",
            "/edit <text>",
            "/memory search <query>",
            "/remember <fact>",
            "/check <domain> <DC> <action>",
        }
        self.assertTrue(expected_direct <= public_commands)
        for retired in {
            "/language <language>",
            "/prompt text",
            "/memory curated refresh",
            "/databank search <query>",
            "/databank versions <filename>",
            "/databank activate <filename> <version>",
            "/databank reindex [filename]",
            "/databank remove <filename> confirm",
            "/group status",
            "/group add <character>",
            "/group mode <mode>",
            "/group goal <objective>",
            "/group goal clear",
            "/scene refresh",
            "/scene clear",
            "/check mode",
        }:
            self.assertNotIn(retired, public_commands)

    def test_help_lookup_routes_retired_shortcuts_to_panels(self):
        cases = {
            "group add": "/group",
            "group add Karen": "/group",
            "group on": "/group",
            "group off": "/group",
            "databank reindex": "/databank",
            "databank reindex notes.pdf": "/databank",
            "databank activate notes.pdf 2": "/databank",
            "remember this is important": "/remember <fact>",
            "group list": "/group",
            "group goal status": "/group goal",
            "group goal off": "/group goal",
            "scene status": "/scene",
            "scene refresh": "/scene",
            "language status": "/language",
            "voice on": "/voice",
            "databank list": "/databank",
            "prompt text": "/prompt",
            "check mode": "/check",
        }
        for requested, expected_command in cases.items():
            with self.subTest(requested=requested):
                target = _m_help_details._help_command_target(requested)
                self.assertIsNotNone(target)
                category, index = target
                self.assertEqual(_m_help_details.HELP_CATEGORIES[category][index][0], expected_command)

    def test_help_command_fast_path_always_renders_panel(self):
        calls = []
        original_send = _m_help_details.send_help_menu
        _m_help_details.send_help_menu = lambda *args, **kwargs: calls.append((args, kwargs))
        try:
            self.assertEqual(_m_help_details.normalize_help_command("/help@SillyTavernPunzmeBot"), "")
            self.assertEqual(_m_help_details.normalize_help_command("/help scene refresh"), "scene refresh")
            self.assertIsNone(_m_help_details.normalize_help_command("help"))
            self.assertTrue(
                _m_command_routes.send_help_command(
                    "token",
                    "chat",
                    "/help scene refresh",
                    delivery_port=make_test_delivery_port(),
                    request_context=make_test_request_context(
                        self.db, "panel-session", app_settings=self.app_settings_builder.build()
                    ),
                )
            )
            expected_index = [command for command, _summary in _m_help_details.HELP_CATEGORIES["voice_group"]].index(
                "/scene"
            )
            self.assertEqual(calls[-1][0], ("token", "chat", "voice_group", None, expected_index))
            self.assertTrue(
                _m_command_routes.send_help_command(
                    "token",
                    "chat",
                    "/help unknown",
                    delivery_port=make_test_delivery_port(),
                    request_context=make_test_request_context(
                        self.db, "panel-session", app_settings=self.app_settings_builder.build()
                    ),
                )
            )
            self.assertEqual(calls[-1][0], ("token", "chat"))
            self.assertFalse(
                _m_command_routes.send_help_command(
                    "token",
                    "chat",
                    "/helper",
                    delivery_port=make_test_delivery_port(),
                    request_context=make_test_request_context(
                        self.db, "panel-session", app_settings=self.app_settings_builder.build()
                    ),
                )
            )
        finally:
            _m_help_details.send_help_menu = original_send

    def test_help_callbacks_are_marked_for_fast_path(self):
        self.assertTrue(_m_help_details.is_help_callback("help:menu"))
        self.assertTrue(_m_help_details.is_help_callback("help:cmd:voice_group:7"))
        self.assertFalse(_m_help_details.is_help_callback("persona:menu"))

    def test_read_only_and_feature_commands_render_panels(self):
        session = _m_session_naming.create_session(
            self.db,
            "chat",
            self.app_settings_builder.default_model,
            session_id="panel-session",
            app_settings=self.app_settings_builder.build(),
        )
        calls = []
        original_prompt_panel = prompt_panels.send_panel_message
        original_groups = _owner_provider_discovery.get_model_groups
        prompt_panels.send_panel_message = lambda *args, **kwargs: calls.append((args, kwargs))
        _owner_provider_discovery.get_model_groups = lambda *, app_settings=None: {}
        try:
            _m_command_routes.send_prompt_menu(
                "token",
                "chat",
                self.db,
                session,
                {"name": "Test"},
                group_service=make_test_group_service(app_settings=self.app_settings_builder.build()),
                memory_service=make_test_memory_service(),
                request_context=make_test_request_context(
                    self.db, session["session_id"], app_settings=self.app_settings_builder.build()
                ),
            )
            self.assertIn("prompt:budget", str(calls[-1]))
            scene_calls = []
            _m_scene_state.send_scene_menu(
                "token",
                "chat",
                self.db,
                session,
                delivery_port=make_test_delivery_port(
                    send_panel_request=lambda *args, **kwargs: scene_calls.append((args, kwargs)) or {},
                ),
                request_context=make_test_request_context(
                    self.db, session["session_id"], app_settings=self.app_settings_builder.build()
                ),
            )
            self.assertIn("scene:refresh", str(scene_calls[-1]))
            _m_director_goals.set_director_goal(self.db, "chat", session["session_id"], "Reveal the door")
            _m_director_goals.send_director_goal_menu(
                "token",
                "chat",
                self.db,
                session,
                request_context=make_test_request_context(
                    self.db, session["session_id"], app_settings=self.app_settings_builder.build()
                ),
                delivery_port=make_test_delivery_port(
                    send_panel_request=lambda *args, **kwargs: calls.append((args, kwargs)) or {}
                ),
            )
            self.assertIn("goal:set", str(calls[-1]))
            _owner_memory_curator.send_curated_memory_menu(
                "token",
                "chat",
                self.db,
                session,
                request_context=make_test_request_context(
                    self.db, session["session_id"], app_settings=self.app_settings_builder.build()
                ),
                delivery_port=make_test_delivery_port(
                    send_panel_request=lambda *args, **kwargs: calls.append((args, kwargs)) or {}
                ),
            )
            self.assertIn("curated:refresh", str(calls[-1]))
        finally:
            prompt_panels.send_panel_message = original_prompt_panel
            _owner_provider_discovery.get_model_groups = original_groups


if __name__ == "__main__":
    unittest.main()
