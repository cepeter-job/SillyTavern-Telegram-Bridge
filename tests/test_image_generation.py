from application_test_setup import ensure_application_extensions, make_test_provider_port
from settings_test_support import SettingsTestCase

ensure_application_extensions()

import base64
import io
import json
import os
import tempfile
import unittest
import urllib.error
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import bridge.command_panels as _m_command_panels
import bridge.feature_callbacks as _m_feature_callbacks
import bridge.image_generation as _m_image_generation
import bridge.image_panels as _m_image_panels
import bridge.model_selection as _m_model_selection
import bridge.session_naming as _m_session_naming
import bridge.sqlite_store as _m_sqlite_store
import bridge.text_action_input as _m_text_action_input


class _Response:
    def __init__(self, payload):
        self.payload = payload
        self.status = 200

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self, *_args):
        if isinstance(self.payload, bytes):
            return self.payload
        return json.dumps(self.payload).encode()


class ImageGenerationTests(SettingsTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.catalog = Path(self.temp.name) / "providers.yaml"
        self.catalog.write_text(
            (
                "providers:\n  test-image:\n    name: Test Image\n    api_endpoint: "
                "https://images.example/v1\n    api_key_env: TEST_IMAGE_KEY\n    "
                "image_enabled: true\n    image_models: [test-model]\n"
            ),
            encoding="utf-8",
        )
        self.old_catalog = self.app_settings_builder.provider_config_file
        self.old_urlopen = _m_image_generation.strict_urlopen
        self.old_key = os.environ.get("TEST_IMAGE_KEY")
        self.app_settings_builder.provider_config_file = self.catalog
        os.environ["TEST_IMAGE_KEY"] = "test-key"

    def tearDown(self):
        self.app_settings_builder.provider_config_file = self.old_catalog
        _m_image_generation.strict_urlopen = self.old_urlopen
        if self.old_key is None:
            os.environ.pop("TEST_IMAGE_KEY", None)
        else:
            os.environ["TEST_IMAGE_KEY"] = self.old_key
        self.temp.cleanup()

    def test_base64_contract(self):
        captured = []
        raw = b"PNG-DATA"

        def fake_urlopen(request, timeout, *, environ=None):
            captured.append((request, timeout))
            return _Response({"data": [{"b64_json": base64.b64encode(raw).decode(), "revised_prompt": "revised"}]})

        _m_image_generation.strict_urlopen = fake_urlopen
        with patch.dict(os.environ, {"SILLYTAVERN_PROVIDER_ALLOWED_HOSTS": "images.example"}):
            result = _m_image_generation.generate_image(
                "test-image::test-model", "a small moon", "1024x1024", app_settings=self.app_settings_builder.build()
            )
        body = json.loads(captured[0][0].data.decode())
        self.assertEqual(result, (raw, "revised", "test-image::test-model"))
        self.assertEqual(body["model"], "test-model")
        self.assertEqual(body["response_format"], "b64_json")
        self.assertEqual(captured[0][1], 180)

    def test_prompt_and_size_are_validated(self):
        with self.assertRaises(ValueError):
            _m_image_generation.generate_image(
                "test-image::test-model", "", "1024x1024", app_settings=self.app_settings_builder.build()
            )
        with self.assertRaises(ValueError):
            _m_image_generation.generate_image(
                "test-image::test-model", "a prompt", "999x999", app_settings=self.app_settings_builder.build()
            )

    def test_z_image_turbo_rejects_prompt_over_model_limit_before_request(self):
        self.catalog.write_text(
            (
                "providers:\n  test-image:\n    name: Test Image\n    api_endpoint: "
                "https://images.example/v1\n    api_key_env: TEST_IMAGE_KEY\n    "
                "image_enabled: true\n    image_models: [z-image-turbo]\n"
            ),
            encoding="utf-8",
        )
        calls = []

        def fake_urlopen(request, timeout, *, environ=None):
            calls.append((request, timeout))
            return _Response({"data": [{"b64_json": base64.b64encode(b"PNG-DATA").decode()}]})

        _m_image_generation.strict_urlopen = fake_urlopen
        with patch.dict(os.environ, {"SILLYTAVERN_PROVIDER_ALLOWED_HOSTS": "images.example"}):
            with self.assertRaisesRegex(ValueError, "z-image-turbo.*1,200"):
                _m_image_generation.generate_image(
                    "test-image::z-image-turbo",
                    "x" * 1201,
                    "1024x1024",
                    app_settings=self.app_settings_builder.build(),
                )
        self.assertEqual(calls, [])

    def test_unknown_image_model_keeps_generic_4000_prompt_ceiling(self):
        captured = []

        def fake_urlopen(request, timeout, *, environ=None):
            captured.append(json.loads(request.data.decode()))
            return _Response({"data": [{"b64_json": base64.b64encode(b"PNG-DATA").decode()}]})

        _m_image_generation.strict_urlopen = fake_urlopen
        with patch.dict(os.environ, {"SILLYTAVERN_PROVIDER_ALLOWED_HOSTS": "images.example"}):
            result = _m_image_generation.generate_image(
                "test-image::test-model",
                "x" * 4000,
                "1024x1024",
                app_settings=self.app_settings_builder.build(),
            )
            self.assertEqual(result[2], "test-image::test-model")
            self.assertEqual(len(captured[0]["prompt"]), 4000)
            with self.assertRaisesRegex(ValueError, "4,000"):
                _m_image_generation.generate_image(
                    "test-image::test-model",
                    "x" * 4001,
                    "1024x1024",
                    app_settings=self.app_settings_builder.build(),
                )

    def test_provider_prompt_too_long_error_becomes_safe_model_specific_value_error(self):
        def fake_urlopen(request, timeout, *, environ=None):
            raise urllib.error.HTTPError(
                request.full_url,
                400,
                "Bad Request",
                {},
                io.BytesIO(b'{"error":"provider-specific internal prompt text","code":"prompt_too_long"}'),
            )

        _m_image_generation.strict_urlopen = fake_urlopen
        with patch.dict(os.environ, {"SILLYTAVERN_PROVIDER_ALLOWED_HOSTS": "images.example"}):
            with self.assertRaisesRegex(ValueError, "test-model.*too long"):
                _m_image_generation.generate_image(
                    "test-image::test-model",
                    "a prompt",
                    "1024x1024",
                    app_settings=self.app_settings_builder.build(),
                )

    def test_disabled_provider_fails_closed(self):
        self.catalog.write_text("providers: {}\n", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "No image provider"):
            _m_image_generation.generate_image("", "a prompt", app_settings=self.app_settings_builder.build())

    def test_command_is_registered(self):
        source = Path(_m_image_generation.__file__).parent / "bot_commands.py"
        self.assertIn('"command": "imagine"', source.read_text(encoding="utf-8"))


class SceneAwareImageGenerationTests(SettingsTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.old_db = self.app_settings_builder.db_file
        self.old_catalog = self.app_settings_builder.provider_config_file
        self.catalog = Path(self.temp.name) / "providers.yaml"
        self.catalog.write_text(
            "providers:\n"
            "  test-image:\n"
            "    name: Test Image\n"
            "    api_endpoint: https://images.example/v1\n"
            "    image_enabled: true\n"
            "    image_models: [model-a, model-b]\n",
            encoding="utf-8",
        )
        self.app_settings_builder.db_file = Path(self.temp.name) / "bridge.sqlite3"
        self.app_settings_builder.provider_config_file = self.catalog
        self.db = _m_sqlite_store.db_connect(app_settings=self.app_settings_builder.build())
        self.session = _m_session_naming.create_session(
            self.db,
            "chat",
            "story::main",
            session_id="image-scene",
            title="Image Scene",
            app_settings=self.app_settings_builder.build(),
        )
        _m_model_selection.set_task_model(
            self.db,
            "chat",
            self.session["session_id"],
            "utility::image-prompt",
        )

    def tearDown(self):
        self.db.close()
        self.app_settings_builder.db_file = self.old_db
        self.app_settings_builder.provider_config_file = self.old_catalog
        self.temp.cleanup()

    def _add_scene(self):
        self.db.execute(
            "INSERT INTO messages(chat_id,session_id,role,content,created_at) VALUES(?,?,?,?,?)",
            ("chat", self.session["session_id"], "user", "We enter the rain-soaked station.", 1.0),
        )
        cursor = self.db.execute(
            "INSERT INTO messages(chat_id,session_id,role,content,created_at) VALUES(?,?,?,?,?)",
            ("chat", self.session["session_id"], "assistant", "Mira closes her red umbrella beside the platform.", 2.0),
        )
        assistant_rowid = int(cursor.lastrowid)
        self.db.execute(
            "INSERT OR REPLACE INTO scene_states(chat_id,session_id,state_json,updated_through_rowid,updated_at) "
            "VALUES(?,?,?,?,?)",
            (
                "chat",
                self.session["session_id"],
                json.dumps(
                    {
                        "location": "Central station",
                        "weather": "heavy rain",
                        "participants": {"Mira": {"clothing": "blue coat", "holding": "red umbrella"}},
                    }
                ),
                assistant_rowid,
                2.0,
            ),
        )
        self.db.commit()

    def test_current_scene_prompt_uses_committed_state_without_mutating_transcript(self):
        self._add_scene()
        before = self.db.execute(
            "SELECT rowid,role,content FROM messages WHERE chat_id=? AND session_id=? ORDER BY rowid",
            ("chat", self.session["session_id"]),
        ).fetchall()
        seen = {}

        def generate(_key, model, messages, **kwargs):
            seen["model"] = model
            seen["messages"] = messages
            seen["kwargs"] = kwargs
            return "Prompt: cinematic photo, Mira in a blue coat holding a red umbrella at a rainy station"

        provider = make_test_provider_port(generate_backend=generate)
        prompt = _m_image_generation.build_scene_image_prompt(
            self.db,
            "chat",
            self.session,
            {"name": "Mira", "description": "Mira has dark hair and a calm expression."},
            provider_port=provider,
            app_settings=self.app_settings_builder.build(),
        )
        after = self.db.execute(
            "SELECT rowid,role,content FROM messages WHERE chat_id=? AND session_id=? ORDER BY rowid",
            ("chat", self.session["session_id"]),
        ).fetchall()

        self.assertEqual(before, after)
        self.assertEqual(seen["model"], "utility::image-prompt")
        self.assertTrue(seen["kwargs"]["force_non_stream"])
        source = seen["messages"][1]["content"]
        self.assertIn("Central station", source)
        self.assertIn("blue coat", source)
        self.assertIn("Mira closes her red umbrella", source)
        self.assertIn("Mira has dark hair", source)
        self.assertEqual(
            prompt,
            "cinematic photo, Mira in a blue coat holding a red umbrella at a rainy station",
        )

    def test_current_scene_uses_selected_model_prompt_limit(self):
        self.catalog.write_text(
            "providers:\n"
            "  test-image:\n"
            "    name: Test Image\n"
            "    api_endpoint: https://images.example/v1\n"
            "    image_enabled: true\n"
            "    image_models: [z-image-turbo]\n",
            encoding="utf-8",
        )
        self._add_scene()
        provider = make_test_provider_port(generate_backend=lambda *_args, **_kwargs: "x" * 1702)
        settings = self.app_settings_builder.build()
        with patch.object(_m_image_generation, "handle_imagine_prompt") as deliver:
            _m_image_generation.handle_imagine_scene(
                self.db,
                "token",
                "chat",
                self.session,
                {"name": "Mira", "description": "Mira has dark hair."},
                provider_port=provider,
                app_settings=settings,
            )
        deliver.assert_called_once()
        _token, _chat, prompt = deliver.call_args.args[:3]
        self.assertEqual(len(prompt), 1200)
        self.assertEqual(deliver.call_args.kwargs["selection"], "test-image::z-image-turbo")

    def test_current_scene_requires_existing_committed_context(self):
        called = []
        provider = make_test_provider_port(generate_backend=lambda *_args, **_kwargs: called.append(True) or "unused")
        with self.assertRaisesRegex(ValueError, "No current roleplay scene"):
            _m_image_generation.build_scene_image_prompt(
                self.db,
                "chat",
                self.session,
                {"name": "Mira", "description": "Mira has dark hair."},
                provider_port=provider,
                app_settings=self.app_settings_builder.build(),
            )
        self.assertEqual(called, [])

    def test_imagine_panel_exposes_scene_custom_and_options_modes(self):
        text, markup = _m_image_panels.imagine_panel(
            self.db,
            "chat",
            self.session["session_id"],
            app_settings=self.app_settings_builder.build(),
        )
        callbacks = {button["callback_data"] for row in markup["inline_keyboard"] for button in row}
        self.assertIn("Current Scene", text)
        self.assertIn("model-a", text)
        self.assertIn("1024x1024", text)
        self.assertEqual(
            callbacks,
            {"imagine:scene", "imagine:custom", "imagine:options", "imagine:close"},
        )

    def test_image_options_are_session_scoped_and_resettable(self):
        settings = self.app_settings_builder.build()
        self.assertEqual(
            _m_image_generation.session_image_settings(
                self.db,
                "chat",
                self.session["session_id"],
                app_settings=settings,
            ),
            ("test-image::model-a", "1024x1024"),
        )
        _m_image_generation.set_session_image_model(
            self.db,
            "chat",
            self.session["session_id"],
            "test-image::model-b",
            app_settings=settings,
        )
        _m_image_generation.set_session_image_size(
            self.db,
            "chat",
            self.session["session_id"],
            "1024x1536",
        )
        self.assertEqual(
            _m_image_generation.session_image_settings(
                self.db,
                "chat",
                self.session["session_id"],
                app_settings=settings,
            ),
            ("test-image::model-b", "1024x1536"),
        )
        _m_image_generation.reset_session_image_settings(
            self.db,
            "chat",
            self.session["session_id"],
        )
        self.assertEqual(
            _m_image_generation.session_image_settings(
                self.db,
                "chat",
                self.session["session_id"],
                app_settings=settings,
            ),
            ("test-image::model-a", "1024x1024"),
        )

    def test_model_option_callback_uses_opaque_token_and_persists_selection(self):
        settings = self.app_settings_builder.build()
        _text, markup = _m_image_panels.imagine_model_panel(
            self.db,
            "chat",
            self.session["session_id"],
            app_settings=settings,
        )
        model_b = next(button for row in markup["inline_keyboard"] for button in row if "model-b" in button["text"])
        self.assertNotIn("model-b", model_b["callback_data"])
        callback = {"id": "cb", "message": {"message_id": 77}}
        request_context = SimpleNamespace(app_settings=settings)
        with patch.object(_m_feature_callbacks, "send_imagine_options_menu"):
            handled = _m_feature_callbacks.handle_feature_panel_callback(
                self.db,
                "token",
                callback,
                lambda *_args: None,
                model_b["callback_data"],
                "chat",
                callback["message"],
                self.session,
                self.session["session_id"],
                None,
                group_service=None,
                provider_port=make_test_provider_port(),
                request_context=request_context,
            )
        self.assertTrue(handled)
        self.assertEqual(
            _m_image_generation.session_image_settings(
                self.db,
                "chat",
                self.session["session_id"],
                app_settings=settings,
            )[0],
            "test-image::model-b",
        )

    def test_size_option_callback_persists_selection(self):
        settings = self.app_settings_builder.build()
        callback = {"id": "cb", "message": {"message_id": 77}}
        request_context = SimpleNamespace(app_settings=settings)
        with patch.object(_m_feature_callbacks, "send_imagine_options_menu"):
            handled = _m_feature_callbacks.handle_feature_panel_callback(
                self.db,
                "token",
                callback,
                lambda *_args: None,
                "imagine:size:1536x1024",
                "chat",
                callback["message"],
                self.session,
                self.session["session_id"],
                None,
                group_service=None,
                provider_port=make_test_provider_port(),
                request_context=request_context,
            )
        self.assertTrue(handled)
        self.assertEqual(
            _m_image_generation.session_image_settings(
                self.db,
                "chat",
                self.session["session_id"],
                app_settings=settings,
            )[1],
            "1536x1024",
        )

    def test_custom_prompt_uses_session_image_model_and_size(self):
        settings = self.app_settings_builder.build()
        _m_image_generation.set_session_image_model(
            self.db,
            "chat",
            self.session["session_id"],
            "test-image::model-b",
            app_settings=settings,
        )
        _m_image_generation.set_session_image_size(
            self.db,
            "chat",
            self.session["session_id"],
            "1536x1024",
        )
        request_context = SimpleNamespace(app_settings=settings)
        with (
            patch.object(_m_text_action_input, "handle_imagine_prompt") as generate,
            patch.object(_m_text_action_input, "_cancel_pending"),
        ):
            handled = _m_text_action_input._handle_text_action_input(
                self.db,
                "token",
                "",
                "chat",
                self.session,
                {},
                "custom visual prompt",
                {"action": "imagine", "session_id": self.session["session_id"]},
                None,
                provider_port=make_test_provider_port(),
                memory_service=None,
                npc_service=None,
                persona_service=None,
                request_context=request_context,
                rag_service=None,
            )
        self.assertTrue(handled)
        generate.assert_called_once_with(
            "token",
            "chat",
            "custom visual prompt",
            selection="test-image::model-b",
            size="1536x1024",
            app_settings=settings,
        )

    def test_inline_imagine_text_opens_panel_instead_of_generating_directly(self):
        calls = []
        with patch.object(
            _m_command_panels,
            "send_imagine_menu",
            side_effect=lambda *args, **kwargs: calls.append((args, kwargs)),
        ):
            handled = _m_command_panels._handle_generation_panels(
                self.db,
                "token",
                {},
                "chat",
                "/imagine old inline prompt",
                "/imagine old inline prompt",
                self.session,
                self.session["session_id"],
                None,
                delivery_port=None,
                provider_port=None,
                memory_service=None,
                npc_service=None,
                persona_service=None,
                request_context=object(),
                rag_service=None,
            )
        self.assertTrue(handled)
        self.assertEqual(len(calls), 1)

    def test_custom_prompt_button_uses_selected_model_prompt_limit(self):
        self.catalog.write_text(
            "providers:\n"
            "  test-image:\n"
            "    name: Test Image\n"
            "    api_endpoint: https://images.example/v1\n"
            "    image_enabled: true\n"
            "    image_models: [z-image-turbo]\n",
            encoding="utf-8",
        )
        callback = {"id": "cb", "message": {"message_id": 77}}
        request_context = SimpleNamespace(app_settings=self.app_settings_builder.build())
        with patch.object(_m_feature_callbacks, "start_text_action_input") as start_input:
            _m_feature_callbacks.handle_feature_panel_callback(
                self.db,
                "token",
                callback,
                lambda *_args: None,
                "imagine:custom",
                "chat",
                callback["message"],
                self.session,
                self.session["session_id"],
                None,
                group_service=None,
                provider_port=make_test_provider_port(),
                request_context=request_context,
            )
        self.assertEqual(
            start_input.call_args.args[5],
            "Send a custom image prompt (1–1,200 characters).",
        )

    def test_custom_prompt_button_starts_scoped_text_input(self):
        callback = {"id": "cb", "message": {"message_id": 77}}
        request_context = SimpleNamespace(app_settings=self.app_settings_builder.build())
        answers = []
        with patch.object(_m_feature_callbacks, "start_text_action_input") as start_input:
            handled = _m_feature_callbacks.handle_feature_panel_callback(
                self.db,
                "token",
                callback,
                lambda _token, _callback_id, text: answers.append(text),
                "imagine:custom",
                "chat",
                callback["message"],
                self.session,
                self.session["session_id"],
                None,
                group_service=None,
                provider_port=make_test_provider_port(),
                request_context=request_context,
            )
        self.assertTrue(handled)
        self.assertEqual(answers, ["Send prompt"])
        start_input.assert_called_once_with(
            self.db,
            "token",
            "chat",
            self.session["session_id"],
            "imagine",
            "Send a custom image prompt (1–4,000 characters).",
            callback,
        )

    def test_current_scene_button_uses_scene_generator(self):
        callback = {"id": "cb", "message": {"message_id": 77}}
        request_context = SimpleNamespace(app_settings=self.app_settings_builder.build())
        answers = []
        fields = {"name": "Mira", "description": "dark hair"}
        with (
            patch.object(_m_feature_callbacks, "send_typing"),
            patch.object(_m_feature_callbacks, "card_fields_from_file", return_value=fields),
            patch.object(_m_feature_callbacks, "handle_imagine_scene") as generate_scene,
        ):
            handled = _m_feature_callbacks.handle_feature_panel_callback(
                self.db,
                "token",
                callback,
                lambda _token, _callback_id, text: answers.append(text),
                "imagine:scene",
                "chat",
                callback["message"],
                self.session,
                self.session["session_id"],
                None,
                group_service=None,
                provider_port=make_test_provider_port(),
                request_context=request_context,
            )
        self.assertTrue(handled)
        self.assertEqual(answers, ["Generating current scene"])
        generate_scene.assert_called_once()
        args, kwargs = generate_scene.call_args
        self.assertEqual(args[:5], (self.db, "token", "chat", self.session, fields))
        self.assertIs(kwargs["app_settings"], request_context.app_settings)


if __name__ == "__main__":
    unittest.main()
