from application_test_setup import ensure_application_extensions, make_test_delivery_port, make_test_provider_port
from settings_test_support import SettingsTestCase

ensure_application_extensions()

import base64
import io
import json
import os
import struct
import tempfile
import unittest
import urllib.error
import zlib
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import bridge.command_panels as _m_command_panels
import bridge.feature_callbacks as _m_feature_callbacks
import bridge.image_callbacks as _m_image_callbacks
import bridge.image_generation as _m_image_generation
import bridge.image_panels as _m_image_panels
import bridge.model_selection as _m_model_selection
import bridge.session_naming as _m_session_naming
import bridge.sqlite_store as _m_sqlite_store
import bridge.text_action_input as _m_text_action_input
from bridge.image_reference import ImageReference
from bridge.image_routing import ImageRoute
from bridge.provider_errors import ProviderRequestError


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


def _png_chunk(kind: bytes, data: bytes) -> bytes:
    crc = zlib.crc32(kind)
    crc = zlib.crc32(data, crc) & 0xFFFFFFFF
    return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", crc)


def _character_png(
    name: str = "Mira", description: str = "Mira has dark hair.", *, valid_metadata: bool = True
) -> bytes:
    signature = b"\x89PNG\r\n\x1a\n"
    ihdr = _png_chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0))
    if valid_metadata:
        payload = base64.b64encode(json.dumps({"name": name, "description": description}).encode())
    else:
        payload = b"not-valid-base64-or-json"
    metadata = _png_chunk(b"tEXt", b"chara\x00" + payload)
    idat = _png_chunk(b"IDAT", zlib.compress(b"\x00\x00\x00\x00"))
    return signature + ihdr + metadata + idat + _png_chunk(b"IEND", b"")


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
        body = io.BytesIO(b'{"error":"provider-specific internal prompt text","code":"prompt_too_long"}')
        errors = []

        def fake_urlopen(request, timeout, *, environ=None):
            error = urllib.error.HTTPError(request.full_url, 400, "Bad Request", {}, body)
            errors.append(error)
            raise error

        _m_image_generation.strict_urlopen = fake_urlopen
        with patch.dict(os.environ, {"SILLYTAVERN_PROVIDER_ALLOWED_HOSTS": "images.example"}):
            with self.assertRaisesRegex(ValueError, "test-model.*too long"):
                _m_image_generation.generate_image(
                    "test-image::test-model",
                    "a prompt",
                    "1024x1024",
                    app_settings=self.app_settings_builder.build(),
                )
        self.assertTrue(body.closed)
        self.assertIs(errors[0].fp, body)

    def test_generate_image_http_429_becomes_sanitized_provider_request_error(self):
        def fake_urlopen(request, timeout, *, environ=None):
            raise urllib.error.HTTPError(
                request.full_url,
                429,
                "provider-secret-reason",
                {"Retry-After": "37", "X-Secret": "do-not-leak"},
                io.BytesIO(b'{"error":"provider internal secret"}'),
            )

        _m_image_generation.strict_urlopen = fake_urlopen
        with patch.dict(os.environ, {"SILLYTAVERN_PROVIDER_ALLOWED_HOSTS": "images.example"}):
            with self.assertRaises(ProviderRequestError) as raised:
                _m_image_generation.generate_image(
                    "test-image::test-model",
                    "a prompt",
                    "1024x1024",
                    app_settings=self.app_settings_builder.build(),
                )

        error = raised.exception
        self.assertEqual(error.model, "test-image::test-model")
        self.assertEqual(error.category, "rate_limit")
        self.assertEqual(error.status, 429)
        self.assertEqual(error.retry_after, 37.0)
        self.assertNotIn("provider internal secret", str(error))
        self.assertNotIn("provider-secret-reason", str(error))

    def test_edit_image_prompt_too_long_closes_http_response(self):
        body = io.BytesIO(b'{"code":"prompt_too_long"}')
        errors = []

        def fake_urlopen(request, timeout, *, environ=None):
            error = urllib.error.HTTPError(request.full_url, 422, "private reason", {}, body)
            errors.append(error)
            raise error

        _m_image_generation.strict_urlopen = fake_urlopen
        with patch.dict(os.environ, {"SILLYTAVERN_PROVIDER_ALLOWED_HOSTS": "images.example"}):
            with self.assertRaisesRegex(ValueError, "step-image-edit-2.*too long"):
                _m_image_generation.edit_image(
                    self._step_route(),
                    "a prompt",
                    ImageReference(b"PNG", "image/png", "Mira.png"),
                    "1024x1024",
                    app_settings=self.app_settings_builder.build(),
                )
        self.assertTrue(body.closed)
        self.assertIs(errors[0].fp, body)

    def test_edit_image_timeout_becomes_sanitized_provider_request_error(self):
        _m_image_generation.strict_urlopen = lambda *args, **kwargs: (_ for _ in ()).throw(TimeoutError("secret"))
        with patch.dict(os.environ, {"SILLYTAVERN_PROVIDER_ALLOWED_HOSTS": "images.example"}):
            with self.assertRaises(ProviderRequestError) as raised:
                _m_image_generation.edit_image(
                    self._step_route(),
                    "portrait",
                    ImageReference(b"PNG", "image/png", "Mira.png"),
                    "1024x1024",
                    app_settings=self.app_settings_builder.build(),
                )

        error = raised.exception
        self.assertEqual(error.model, "test-image::step-image-edit-2")
        self.assertEqual(error.category, "timeout")
        self.assertIsNone(error.status)
        self.assertNotIn("secret", str(error))

    def test_image_provider_error_message_is_detailed_and_safe(self):
        error = ProviderRequestError(
            "nano-gpt::qwen-image-2.0",
            "rate_limit",
            429,
            retry_after=37,
        )
        self.assertEqual(
            _m_image_generation.image_provider_error_message(error),
            "Image generation failed.\n\n"
            "Model: nano-gpt::qwen-image-2.0\n"
            "Reason: Rate limited by provider (HTTP 429)\n"
            "Retry: in 37 seconds\n"
            "Next: Try again later or choose another image model in /imagine.",
        )

    def test_disabled_provider_fails_closed(self):
        self.catalog.write_text("providers: {}\n", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "No image provider"):
            _m_image_generation.generate_image("", "a prompt", app_settings=self.app_settings_builder.build())

    def test_imagine_delivery_sends_image_only_and_removes_progress_message(self):
        events = []

        def fake_send_text(_token, _chat_id, text):
            events.append(("progress", text))
            return [321]

        def fake_photo(_token, _chat_id, _raw, caption):
            events.append(("photo", caption))

        def fake_telegram_request(_token, method, payload):
            events.append(("delete", method, payload))
            return {}

        with (
            patch.object(_m_image_generation, "send_text", side_effect=fake_send_text),
            patch.object(
                _m_image_generation,
                "generate_image",
                return_value=(b"PNG-DATA", "revised provider prompt", "test-image::test-model"),
            ),
            patch.object(_m_image_generation, "_multipart_photo", side_effect=fake_photo),
            patch.object(_m_image_generation, "telegram_request", side_effect=fake_telegram_request, create=True),
        ):
            _m_image_generation._deliver_resolved_image(
                "token",
                "chat",
                "a small moon",
                route=ImageRoute(
                    selection="test-image::test-model",
                    provider_id="test-image",
                    model="test-model",
                    transport="text",
                    edit_route="",
                    spec={},
                ),
                reference=None,
                size="1024x1024",
                app_settings=self.app_settings_builder.build(),
            )

        self.assertEqual(
            events,
            [
                ("progress", "🎨 Generating image…"),
                ("photo", ""),
                ("delete", "deleteMessage", {"chat_id": "chat", "message_id": 321}),
            ],
        )

    def _step_route(self, **spec_overrides):
        spec = {
            "api_endpoint": "https://images.example/v1",
            "api_key_env": "TEST_IMAGE_KEY",
            "image_enabled": True,
            "image_models": ["step-image-edit-2"],
            **spec_overrides,
        }
        return ImageRoute(
            selection="test-image::step-image-edit-2",
            provider_id="test-image",
            model="step-image-edit-2",
            transport="reference",
            edit_route="openai",
            spec=spec,
        )

    def test_edit_image_sends_openai_multipart_contract_and_uses_default_edit_endpoint(self):
        captured = []

        def fake_urlopen(request, timeout, *, environ=None):
            captured.append((request, timeout))
            return _Response(
                {"data": [{"b64_json": base64.b64encode(b"EDITED").decode(), "revised_prompt": "revised"}]}
            )

        reference = ImageReference(b"PNG-REFERENCE", "image/png", "Mira.png")
        _m_image_generation.strict_urlopen = fake_urlopen
        with patch.dict(os.environ, {"SILLYTAVERN_PROVIDER_ALLOWED_HOSTS": "images.example"}):
            result = _m_image_generation.edit_image(
                self._step_route(),
                "same character in a garden",
                reference,
                "1024x1024",
                app_settings=self.app_settings_builder.build(),
            )

        request, timeout = captured[0]
        body = request.data
        self.assertEqual(request.full_url, "https://images.example/v1/images/edits")
        self.assertEqual(request.get_header("Authorization"), "Bearer test-key")
        self.assertIn("multipart/form-data; boundary=", request.get_header("Content-type"))
        self.assertIn(b'name="model"\r\n\r\nstep-image-edit-2', body)
        self.assertIn(b'name="prompt"\r\n\r\nsame character in a garden', body)
        self.assertIn(b'name="n"\r\n\r\n1', body)
        self.assertIn(b'name="image"; filename="Mira.png"', body)
        self.assertIn(b"Content-Type: image/png", body)
        self.assertIn(b"PNG-REFERENCE", body)
        self.assertIn(b'name="size"\r\n\r\n1024x1024', body)
        self.assertEqual(timeout, 180)
        self.assertEqual(result, (b"EDITED", "revised", "test-image::step-image-edit-2"))

    def test_edit_image_uses_explicit_edit_endpoint_without_rewriting_generation_endpoint(self):
        captured = []

        def fake_urlopen(request, timeout, *, environ=None):
            captured.append(request.full_url)
            return _Response({"data": [{"b64_json": base64.b64encode(b"EDITED").decode()}]})

        _m_image_generation.strict_urlopen = fake_urlopen
        route = self._step_route(
            image_endpoint="https://images.example/custom-generation",
            image_edit_endpoint="https://edits.example/custom-edit",
        )
        with patch.dict(
            os.environ,
            {"SILLYTAVERN_PROVIDER_ALLOWED_HOSTS": "images.example,edits.example"},
        ):
            _m_image_generation.edit_image(
                route,
                "portrait",
                ImageReference(b"PNG", "image/png", "Mira.png"),
                "1024x1024",
                app_settings=self.app_settings_builder.build(),
            )
        self.assertEqual(captured, ["https://edits.example/custom-edit"])

    def test_step_image_edit_2_rejects_prompt_over_512_before_request(self):
        calls = []
        _m_image_generation.strict_urlopen = lambda *args, **kwargs: calls.append(args)
        with self.assertRaisesRegex(ValueError, "step-image-edit-2.*512"):
            _m_image_generation.edit_image(
                self._step_route(),
                "x" * 513,
                ImageReference(b"PNG", "image/png", "Mira.png"),
                "1024x1024",
                app_settings=self.app_settings_builder.build(),
            )
        self.assertEqual(calls, [])

    def test_step_edit_unsupported_bridge_landscape_omits_size(self):
        captured = []

        def fake_urlopen(request, timeout, *, environ=None):
            captured.append(request.data)
            return _Response({"data": [{"b64_json": base64.b64encode(b"EDITED").decode()}]})

        _m_image_generation.strict_urlopen = fake_urlopen
        with patch.dict(os.environ, {"SILLYTAVERN_PROVIDER_ALLOWED_HOSTS": "images.example"}):
            _m_image_generation.edit_image(
                self._step_route(),
                "portrait",
                ImageReference(b"PNG", "image/png", "Mira.png"),
                "1536x1024",
                app_settings=self.app_settings_builder.build(),
            )
        self.assertNotIn(b'name="size"', captured[0])

    def test_imagine_provider_failure_removes_progress_message(self):
        events = []

        with (
            patch.object(_m_image_generation, "send_text", return_value=[321]),
            patch.object(_m_image_generation, "generate_image", side_effect=RuntimeError("provider failed")),
            patch.object(
                _m_image_generation,
                "telegram_request",
                side_effect=lambda _token, method, payload: events.append((method, payload)) or {},
            ),
            self.assertRaisesRegex(RuntimeError, "provider failed"),
        ):
            _m_image_generation._deliver_resolved_image(
                "token",
                "chat",
                "a small moon",
                route=ImageRoute(
                    selection="test-image::test-model",
                    provider_id="test-image",
                    model="test-model",
                    transport="text",
                    edit_route="",
                    spec={},
                ),
                reference=None,
                size="1024x1024",
                app_settings=self.app_settings_builder.build(),
            )

        self.assertEqual(events, [("deleteMessage", {"chat_id": "chat", "message_id": 321})])

    def test_imagine_delivery_failure_removes_progress_message(self):
        events = []

        with (
            patch.object(_m_image_generation, "send_text", return_value=[322]),
            patch.object(
                _m_image_generation,
                "generate_image",
                return_value=(b"PNG-DATA", "", "test-image::test-model"),
            ),
            patch.object(_m_image_generation, "_multipart_photo", side_effect=RuntimeError("delivery failed")),
            patch.object(
                _m_image_generation,
                "telegram_request",
                side_effect=lambda _token, method, payload: events.append((method, payload)) or {},
            ),
            self.assertRaisesRegex(RuntimeError, "delivery failed"),
        ):
            _m_image_generation._deliver_resolved_image(
                "token",
                "chat",
                "a small moon",
                route=ImageRoute(
                    selection="test-image::test-model",
                    provider_id="test-image",
                    model="test-model",
                    transport="text",
                    edit_route="",
                    spec={},
                ),
                reference=None,
                size="1024x1024",
                app_settings=self.app_settings_builder.build(),
            )

        self.assertEqual(events, [("deleteMessage", {"chat_id": "chat", "message_id": 322})])

    def test_progress_delete_failure_does_not_mask_successful_image_delivery(self):
        delivered = []

        with (
            patch.object(_m_image_generation, "send_text", return_value=[323]),
            patch.object(
                _m_image_generation,
                "generate_image",
                return_value=(b"PNG-DATA", "", "test-image::test-model"),
            ),
            patch.object(
                _m_image_generation,
                "_multipart_photo",
                side_effect=lambda _token, _chat, _raw, caption: delivered.append(caption),
            ),
            patch.object(_m_image_generation, "telegram_request", side_effect=RuntimeError("delete failed")),
        ):
            _m_image_generation._deliver_resolved_image(
                "token",
                "chat",
                "a small moon",
                route=ImageRoute(
                    selection="test-image::test-model",
                    provider_id="test-image",
                    model="test-model",
                    transport="text",
                    edit_route="",
                    spec={},
                ),
                reference=None,
                size="1024x1024",
                app_settings=self.app_settings_builder.build(),
            )

        self.assertEqual(delivered, [""])

    def test_command_is_registered(self):
        source = Path(_m_image_generation.__file__).parent / "bot_commands.py"
        self.assertIn('"command": "imagine"', source.read_text(encoding="utf-8"))


class SceneAwareImageGenerationTests(SettingsTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.old_db = self.app_settings_builder.db_file
        self.old_catalog = self.app_settings_builder.provider_config_file
        self.old_character_dir = self.app_settings_builder.character_dir
        self.old_default_character_file = self.app_settings_builder.default_character_file
        self.character_dir = Path(self.temp.name) / "characters"
        self.character_dir.mkdir()
        self.app_settings_builder.character_dir = self.character_dir
        self.app_settings_builder.default_character_file = "Mira.png"
        (self.character_dir / "Mira.png").write_bytes(_character_png())
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
        self.app_settings_builder.character_dir = self.old_character_dir
        self.app_settings_builder.default_character_file = self.old_default_character_file
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

    def _configure_auto_catalog(self):
        self.catalog.write_text(
            "image_auto:\n"
            "  text_model: test-image::chroma\n"
            "  reference_model: test-image::step-image-edit-2\n"
            "providers:\n"
            "  test-image:\n"
            "    name: Test Image\n"
            "    api_endpoint: https://images.example/v1\n"
            "    image_enabled: true\n"
            "    image_models: [chroma, step-image-edit-2]\n"
            "    image_model_capabilities:\n"
            "      chroma:\n"
            "        mode: text\n"
            "      step-image-edit-2:\n"
            "        mode: reference\n"
            "        edit_route: openai\n",
            encoding="utf-8",
        )

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
        self.assertEqual(prompt, "cinematic photo, Mira in a blue coat holding a red umbrella at a rainy station")

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
        with (
            patch.object(_m_image_generation, "send_text", return_value=[]),
            patch.object(
                _m_image_generation,
                "generate_image",
                return_value=(b"TEXT", "", "test-image::z-image-turbo"),
            ) as generate,
            patch.object(_m_image_generation, "_multipart_photo"),
        ):
            _m_image_generation.handle_imagine_scene(
                self.db,
                "token",
                "chat",
                self.session,
                provider_port=provider,
                app_settings=settings,
            )
        generate.assert_called_once()
        selection, prompt, _size = generate.call_args.args[:3]
        self.assertEqual(len(prompt), 1200)
        self.assertEqual(selection, "test-image::z-image-turbo")

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

    def test_auto_scene_with_valid_character_png_uses_reference_edit_model(self):
        self._configure_auto_catalog()
        self._add_scene()
        provider = make_test_provider_port(generate_backend=lambda *_args, **_kwargs: "Mira at the rainy station")
        with (
            patch.object(_m_image_generation, "send_text", return_value=[]),
            patch.object(
                _m_image_generation,
                "edit_image",
                return_value=(b"EDITED", "", "test-image::step-image-edit-2"),
            ) as edit,
            patch.object(_m_image_generation, "generate_image") as text_generate,
            patch.object(_m_image_generation, "_multipart_photo"),
        ):
            _m_image_generation.handle_imagine_scene(
                self.db,
                "token",
                "chat",
                self.session,
                provider_port=provider,
                app_settings=self.app_settings_builder.build(),
            )

        edit.assert_called_once()
        route, prompt, reference, size = edit.call_args.args[:4]
        self.assertEqual(route.selection, "test-image::step-image-edit-2")
        self.assertEqual(route.transport, "reference")
        self.assertEqual(reference.data, (self.character_dir / "Mira.png").read_bytes())
        self.assertIn("identity reference", prompt)
        self.assertIn("Mira at the rainy station", prompt)
        self.assertLessEqual(len(prompt), 512)
        self.assertEqual(size, "1024x1024")
        text_generate.assert_not_called()

    def test_auto_scene_without_character_png_uses_configured_text_model(self):
        self._configure_auto_catalog()
        self._add_scene()
        (self.character_dir / "Mira.png").unlink()
        provider = make_test_provider_port(generate_backend=lambda *_args, **_kwargs: "rainy station")
        with (
            patch.object(_m_image_generation, "send_text", return_value=[]),
            patch.object(_m_image_generation, "edit_image") as edit,
            patch.object(
                _m_image_generation,
                "generate_image",
                return_value=(b"TEXT", "", "test-image::chroma"),
            ) as text_generate,
            patch.object(_m_image_generation, "_multipart_photo"),
        ):
            _m_image_generation.handle_imagine_scene(
                self.db,
                "token",
                "chat",
                self.session,
                provider_port=provider,
                app_settings=self.app_settings_builder.build(),
            )

        edit.assert_not_called()
        text_generate.assert_called_once()
        self.assertEqual(text_generate.call_args.args[0], "test-image::chroma")

    def test_auto_scene_with_corrupt_png_uses_text_model(self):
        self._configure_auto_catalog()
        self._add_scene()
        (self.character_dir / "Mira.png").write_bytes(b"broken png")
        provider = make_test_provider_port(generate_backend=lambda *_args, **_kwargs: "rainy station")
        with (
            patch.object(_m_image_generation, "send_text", return_value=[]),
            patch.object(_m_image_generation, "edit_image") as edit,
            patch.object(
                _m_image_generation,
                "generate_image",
                return_value=(b"TEXT", "", "test-image::chroma"),
            ) as text_generate,
            patch.object(_m_image_generation, "_multipart_photo"),
        ):
            _m_image_generation.handle_imagine_scene(
                self.db,
                "token",
                "chat",
                self.session,
                provider_port=provider,
                app_settings=self.app_settings_builder.build(),
            )

        edit.assert_not_called()
        text_generate.assert_called_once()
        self.assertEqual(text_generate.call_args.args[0], "test-image::chroma")

    def test_manual_text_scene_ignores_available_reference(self):
        self._configure_auto_catalog()
        self._add_scene()
        settings = self.app_settings_builder.build()
        _m_image_generation.set_session_image_model(
            self.db,
            "chat",
            self.session["session_id"],
            "test-image::chroma",
            app_settings=settings,
        )
        provider = make_test_provider_port(generate_backend=lambda *_args, **_kwargs: "rainy station")
        with (
            patch.object(_m_image_generation, "send_text", return_value=[]),
            patch.object(_m_image_generation, "edit_image") as edit,
            patch.object(
                _m_image_generation,
                "generate_image",
                return_value=(b"TEXT", "", "test-image::chroma"),
            ) as text_generate,
            patch.object(_m_image_generation, "_multipart_photo"),
        ):
            _m_image_generation.handle_imagine_scene(
                self.db,
                "token",
                "chat",
                self.session,
                provider_port=provider,
                app_settings=settings,
            )

        edit.assert_not_called()
        text_generate.assert_called_once()
        self.assertEqual(text_generate.call_args.args[0], "test-image::chroma")

    def test_manual_reference_scene_without_reference_fails_before_network(self):
        self._configure_auto_catalog()
        self._add_scene()
        settings = self.app_settings_builder.build()
        _m_image_generation.set_session_image_model(
            self.db,
            "chat",
            self.session["session_id"],
            "test-image::step-image-edit-2",
            app_settings=settings,
        )
        (self.character_dir / "Mira.png").unlink()
        provider = make_test_provider_port(generate_backend=lambda *_args, **_kwargs: "unused")
        with (
            patch.object(_m_image_generation, "edit_image") as edit,
            patch.object(_m_image_generation, "generate_image") as text_generate,
            self.assertRaisesRegex(ValueError, r"requires.*reference"),
        ):
            _m_image_generation.handle_imagine_scene(
                self.db,
                "token",
                "chat",
                self.session,
                provider_port=provider,
                app_settings=settings,
            )
        edit.assert_not_called()
        text_generate.assert_not_called()

    def test_valid_png_with_invalid_card_metadata_still_uses_reference(self):
        self._configure_auto_catalog()
        self._add_scene()
        (self.character_dir / "Mira.png").write_bytes(_character_png(valid_metadata=False))
        provider = make_test_provider_port(generate_backend=lambda *_args, **_kwargs: "rainy station")
        with (
            patch.object(_m_image_generation, "send_text", return_value=[]),
            patch.object(
                _m_image_generation,
                "edit_image",
                return_value=(b"EDITED", "", "test-image::step-image-edit-2"),
            ) as edit,
            patch.object(_m_image_generation, "_multipart_photo"),
        ):
            _m_image_generation.handle_imagine_scene(
                self.db,
                "token",
                "chat",
                self.session,
                provider_port=provider,
                app_settings=self.app_settings_builder.build(),
            )
        edit.assert_called_once()

    def test_character_switch_changes_reference_on_next_imagine(self):
        self._configure_auto_catalog()
        self._add_scene()
        nora = _character_png("Nora", "Nora has silver hair.")
        (self.character_dir / "Nora.png").write_bytes(nora)
        provider = make_test_provider_port(generate_backend=lambda *_args, **_kwargs: "station portrait")
        with (
            patch.object(_m_image_generation, "send_text", return_value=[]),
            patch.object(
                _m_image_generation,
                "edit_image",
                return_value=(b"EDITED", "", "test-image::step-image-edit-2"),
            ) as edit,
            patch.object(_m_image_generation, "_multipart_photo"),
        ):
            _m_image_generation.handle_imagine_scene(
                self.db,
                "token",
                "chat",
                self.session,
                provider_port=provider,
                app_settings=self.app_settings_builder.build(),
            )
            switched = dict(self.session)
            switched["character_file"] = "Nora.png"
            _m_image_generation.handle_imagine_scene(
                self.db,
                "token",
                "chat",
                switched,
                provider_port=provider,
                app_settings=self.app_settings_builder.build(),
            )

        self.assertEqual(edit.call_count, 2)
        self.assertEqual(edit.call_args_list[0].args[2].data, (self.character_dir / "Mira.png").read_bytes())
        self.assertEqual(edit.call_args_list[1].args[2].data, nora)

    def test_reference_provider_failure_does_not_call_auto_text_fallback(self):
        self._configure_auto_catalog()
        self._add_scene()
        provider = make_test_provider_port(generate_backend=lambda *_args, **_kwargs: "rainy station")
        with (
            patch.object(_m_image_generation, "send_text", return_value=[]),
            patch.object(_m_image_generation, "edit_image", side_effect=RuntimeError("upstream failed")) as edit,
            patch.object(_m_image_generation, "generate_image") as text_generate,
            self.assertRaisesRegex(RuntimeError, "upstream failed"),
        ):
            _m_image_generation.handle_imagine_scene(
                self.db,
                "token",
                "chat",
                self.session,
                provider_port=provider,
                app_settings=self.app_settings_builder.build(),
            )
        edit.assert_called_once()
        text_generate.assert_not_called()

    def test_custom_prompt_auto_reference_uses_edit_route(self):
        self._configure_auto_catalog()
        with (
            patch.object(_m_image_generation, "send_text", return_value=[]),
            patch.object(
                _m_image_generation,
                "edit_image",
                return_value=(b"EDITED", "", "test-image::step-image-edit-2"),
            ) as edit,
            patch.object(_m_image_generation, "generate_image") as text_generate,
            patch.object(_m_image_generation, "_multipart_photo"),
        ):
            _m_image_generation.handle_imagine_custom_prompt(
                self.db,
                "token",
                "chat",
                self.session,
                "sitting in a garden",
                app_settings=self.app_settings_builder.build(),
            )

        edit.assert_called_once()
        self.assertIn("identity reference", edit.call_args.args[1])
        self.assertIn("sitting in a garden", edit.call_args.args[1])
        text_generate.assert_not_called()

    def test_custom_prompt_over_reference_budget_fails_before_provider_call(self):
        self._configure_auto_catalog()
        with (
            patch.object(_m_image_generation, "edit_image") as edit,
            patch.object(_m_image_generation, "generate_image") as text_generate,
            self.assertRaisesRegex(ValueError, "prompt.*512"),
        ):
            _m_image_generation.handle_imagine_custom_prompt(
                self.db,
                "token",
                "chat",
                self.session,
                "x" * 512,
                app_settings=self.app_settings_builder.build(),
            )
        edit.assert_not_called()
        text_generate.assert_not_called()

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
        self.assertEqual(callbacks, {f"imagine:{action}" for action in ("scene", "custom", "options", "close")})

    def test_auto_model_panel_shows_auto_selected(self):
        self._configure_auto_catalog()
        _text, markup = _m_image_panels.imagine_model_panel(
            self.db,
            "chat",
            self.session["session_id"],
            app_settings=self.app_settings_builder.build(),
        )
        labels = [button["text"] for row in markup["inline_keyboard"] for button in row]
        self.assertIn("✅ Auto", labels)

    def test_auto_panel_shows_resolved_reference_model(self):
        self._configure_auto_catalog()
        text, _markup = _m_image_panels.imagine_panel(
            self.db,
            "chat",
            self.session["session_id"],
            character_file="Mira.png",
            app_settings=self.app_settings_builder.build(),
        )
        self.assertIn("Auto → step-image-edit-2", text)
        self.assertIn("character reference", text)

    def test_manual_text_panel_remains_visibly_manual(self):
        self._configure_auto_catalog()
        settings = self.app_settings_builder.build()
        _m_image_generation.set_session_image_model(
            self.db,
            "chat",
            self.session["session_id"],
            "test-image::chroma",
            app_settings=settings,
        )
        text, _markup = _m_image_panels.imagine_panel(
            self.db,
            "chat",
            self.session["session_id"],
            character_file="Mira.png",
            app_settings=settings,
        )
        self.assertIn("Model: chroma", text)
        self.assertNotIn("Auto →", text)

    def test_reference_route_with_unsupported_size_shows_provider_controlled_sizing(self):
        self._configure_auto_catalog()
        settings = self.app_settings_builder.build()
        _m_image_generation.set_session_image_size(
            self.db,
            "chat",
            self.session["session_id"],
            "1536x1024",
        )
        text, _markup = _m_image_panels.imagine_options_panel(
            self.db,
            "chat",
            self.session["session_id"],
            character_file="Mira.png",
            app_settings=settings,
        )
        self.assertIn("Size: Auto (reference model)", text)
        self.assertNotIn("Size: 1536x1024", text)

    def test_send_imagine_options_menu_uses_active_character_for_auto_resolution(self):
        self._configure_auto_catalog()
        settings = self.app_settings_builder.build()
        _m_image_generation.set_session_image_size(
            self.db,
            "chat",
            self.session["session_id"],
            "1536x1024",
        )
        request_context = SimpleNamespace(app_settings=settings)
        with patch.object(_m_image_panels, "_send") as send:
            _m_image_panels.send_imagine_options_menu(
                "token",
                "chat",
                self.db,
                self.session,
                request_context=request_context,
            )
        text = send.call_args.args[2]
        self.assertIn("Auto → step-image-edit-2", text)
        self.assertIn("character reference", text)
        self.assertIn("Size: Auto (reference model)", text)

    def test_missing_active_character_does_not_substitute_default_card_metadata(self):
        (self.character_dir / "Default.png").write_bytes(_character_png("Default", "WRONG DEFAULT DESCRIPTION"))
        self.app_settings_builder.default_character_file = "Default.png"
        missing = dict(self.session)
        missing["character_file"] = "Missing.png"

        fields = _m_image_generation._visual_card_fields(
            missing,
            app_settings=self.app_settings_builder.build(),
        )

        self.assertEqual(fields["name"], "Missing")
        self.assertEqual(fields["description"], "")

    def test_model_panel_without_image_models_keeps_setup_guidance(self):
        self.catalog.write_text("providers: {}\n", encoding="utf-8")
        _text, markup = _m_image_panels.imagine_model_panel(
            self.db,
            "chat",
            self.session["session_id"],
            app_settings=self.app_settings_builder.build(),
        )
        labels = [button["text"] for row in markup["inline_keyboard"] for button in row]
        self.assertIn("No image models configured", labels)

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
        with patch.object(_m_image_callbacks, "send_imagine_options_menu"):
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
                delivery_port=make_test_delivery_port(),
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
        with patch.object(_m_image_callbacks, "send_imagine_options_menu"):
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
                delivery_port=make_test_delivery_port(),
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
            patch.object(_m_text_action_input, "handle_imagine_custom_prompt") as generate,
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
            self.db,
            "token",
            "chat",
            self.session,
            "custom visual prompt",
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
        with patch.object(_m_image_callbacks, "start_text_action_input") as start_input:
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
                delivery_port=make_test_delivery_port(),
            )
        self.assertEqual(
            start_input.call_args.args[5],
            "Send a custom image prompt (1–1,119 characters). Style: Realism.",
        )

    def test_custom_prompt_button_auto_reference_uses_reduced_input_limit(self):
        self._configure_auto_catalog()
        callback = {"id": "cb", "message": {"message_id": 77}}
        request_context = SimpleNamespace(app_settings=self.app_settings_builder.build())
        with patch.object(_m_image_callbacks, "start_text_action_input") as start_input:
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
                delivery_port=make_test_delivery_port(),
            )
        prompt = start_input.call_args.args[5]
        limit = int(prompt.split("1–", 1)[1].split(" ", 1)[0].replace(",", ""))
        self.assertGreater(limit, 0)
        self.assertLess(limit, 512)

    def test_custom_prompt_button_starts_scoped_text_input(self):
        callback = {"id": "cb", "message": {"message_id": 77}}
        request_context = SimpleNamespace(app_settings=self.app_settings_builder.build())
        answers = []
        with patch.object(_m_image_callbacks, "start_text_action_input") as start_input:
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
                delivery_port=make_test_delivery_port(),
            )
        self.assertTrue(handled)
        self.assertEqual(answers, ["Send prompt"])
        start_input.assert_called_once_with(
            self.db,
            "token",
            "chat",
            self.session["session_id"],
            "imagine",
            "Send a custom image prompt (1–3,919 characters). Style: Realism.",
            callback,
        )

    def test_current_scene_provider_error_is_reported_to_user(self):
        callback = {"id": "cb", "message": {"message_id": 77}}
        request_context = SimpleNamespace(app_settings=self.app_settings_builder.build())
        messages = []
        error = ProviderRequestError("nano-gpt::qwen-image-2.0", "rate_limit", 429, retry_after=37)
        with (
            patch.object(_m_image_callbacks, "send_typing"),
            patch.object(_m_image_callbacks, "handle_imagine_scene", side_effect=error),
            patch.object(_m_image_callbacks, "send_text", side_effect=lambda _t, _c, text: messages.append(text)),
        ):
            handled = _m_feature_callbacks.handle_feature_panel_callback(
                self.db,
                "token",
                callback,
                lambda *_args: None,
                "imagine:scene",
                "chat",
                callback["message"],
                self.session,
                self.session["session_id"],
                None,
                group_service=None,
                provider_port=make_test_provider_port(),
                request_context=request_context,
                delivery_port=make_test_delivery_port(),
            )

        self.assertTrue(handled)
        self.assertEqual(
            messages,
            [
                "Image generation failed.\n\n"
                "Model: nano-gpt::qwen-image-2.0\n"
                "Reason: Rate limited by provider (HTTP 429)\n"
                "Retry: in 37 seconds\n"
                "Next: Try again later or choose another image model in /imagine."
            ],
        )

    def test_custom_prompt_provider_error_is_reported_to_user(self):
        settings = self.app_settings_builder.build()
        request_context = SimpleNamespace(app_settings=settings)
        state = {"action": "imagine", "session_id": self.session["session_id"]}
        messages = []
        error = ProviderRequestError("nano-gpt::qwen-image-2.0", "provider_unavailable", 503)
        with (
            patch.object(_m_text_action_input, "handle_imagine_custom_prompt", side_effect=error),
            patch.object(
                _m_text_action_input,
                "send_pending_input_message",
                side_effect=lambda _db, _t, _c, _k, _s, text: messages.append(text),
            ),
            patch.object(_m_text_action_input, "_cancel_pending") as cancel,
        ):
            handled = _m_text_action_input._handle_text_action_input(
                self.db,
                "token",
                "",
                "chat",
                self.session,
                {},
                "custom visual prompt",
                state,
                None,
                provider_port=make_test_provider_port(),
                memory_service=None,
                npc_service=None,
                persona_service=None,
                request_context=request_context,
                rag_service=None,
            )

        self.assertTrue(handled)
        self.assertEqual(
            messages,
            [
                "Image generation failed.\n\n"
                "Model: nano-gpt::qwen-image-2.0\n"
                "Reason: Provider temporarily unavailable (HTTP 503)\n"
                "Next: Try again later or choose another image model in /imagine."
            ],
        )
        cancel.assert_not_called()

    def test_current_scene_button_uses_scene_generator(self):
        callback = {"id": "cb", "message": {"message_id": 77}}
        request_context = SimpleNamespace(app_settings=self.app_settings_builder.build())
        answers = []
        with (
            patch.object(_m_image_callbacks, "send_typing"),
            patch.object(_m_image_callbacks, "handle_imagine_scene") as generate_scene,
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
                delivery_port=make_test_delivery_port(),
            )
        self.assertTrue(handled)
        self.assertEqual(answers, ["Generating current scene"])
        generate_scene.assert_called_once()
        args, kwargs = generate_scene.call_args
        self.assertEqual(args[:4], (self.db, "token", "chat", self.session))
        self.assertIs(kwargs["app_settings"], request_context.app_settings)


if __name__ == "__main__":
    unittest.main()
