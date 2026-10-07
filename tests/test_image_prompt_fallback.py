"""Regression coverage for current-scene image prompt fallback and stage-specific errors."""

import json
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from application_test_setup import ensure_application_extensions, make_test_provider_port
from settings_test_support import SettingsTestCase
from test_provider_resilience import setup as provider_setup

ensure_application_extensions()

import bridge.image_callbacks as image_callbacks
import bridge.image_generation as image_generation
import bridge.image_prompt_provider as image_prompt_provider
import bridge.model_selection as model_selection
import bridge.session_naming as session_naming
import bridge.sqlite_store as sqlite_store
from bridge.provider_errors import ProviderRequestError


class _RecordingProvider:
    def __init__(self, delegate):
        self.delegate = delegate
        self.purposes = []

    def for_usage(self, chat_id, session_id, purpose):
        self.purposes.append((chat_id, session_id, purpose))
        return self.delegate.for_usage(chat_id, session_id, purpose)


class ImagePromptFallbackRegressionTests(SettingsTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.catalog = root / "providers.yaml"
        self.catalog.write_text(
            "providers:\n"
            "  utility:\n"
            "    name: Utility\n"
            "    api_endpoint: https://utility.example/v1\n"
            "    models: [image-prompt]\n",
            encoding="utf-8",
        )
        self.app_settings_builder.db_file = root / "bridge.sqlite3"
        self.app_settings_builder.provider_config_file = self.catalog
        self.app_settings_builder.character_dir = root
        self.app_settings_builder.default_character_file = "Mira.png"
        (root / "Mira.png").write_bytes(b"png")
        settings = self.app_settings_builder.build()
        self.db = sqlite_store.db_connect(app_settings=settings)
        self.session = session_naming.create_session(
            self.db,
            "chat",
            "story::main",
            session_id="image-scene",
            title="Image Scene",
            app_settings=settings,
        )
        model_selection.set_task_model(
            self.db,
            "chat",
            self.session["session_id"],
            "utility::image-prompt",
        )
        self.db.execute(
            "INSERT INTO messages(chat_id,session_id,role,content,created_at) VALUES(?,?,?,?,?)",
            ("chat", self.session["session_id"], "assistant", "Mira waits at the rainy station.", 1.0),
        )
        rowid = self.db.execute("SELECT MAX(rowid) FROM messages").fetchone()[0]
        self.db.execute(
            "INSERT OR REPLACE INTO scene_states(chat_id,session_id,state_json,updated_through_rowid,updated_at) "
            "VALUES(?,?,?,?,?)",
            ("chat", self.session["session_id"], json.dumps({"location": "Central station"}), rowid, 1.0),
        )
        self.db.commit()

    def tearDown(self):
        self.db.close()
        self.temp.cleanup()

    def test_scene_prompt_is_routed_as_image_prompt_utility_work(self):
        delegate = make_test_provider_port(generate_backend=lambda *_a, **_k: "Mira at Central station")
        provider = _RecordingProvider(delegate)

        image_generation.build_scene_image_prompt(
            self.db,
            "chat",
            self.session,
            {"name": "Mira", "description": "Dark hair."},
            provider_port=provider,
            app_settings=self.app_settings_builder.build(),
        )

        self.assertEqual(provider.purposes, [("chat", self.session["session_id"], "image_prompt")])

    def test_image_prompt_purpose_inherits_utility_fallbacks(self):
        _clock, _health, policy = provider_setup(utility_fallbacks=["beta::other"])

        self.assertEqual(policy.candidates("one", "image_prompt"), ("alpha::one", "beta::other"))

    def test_current_scene_prompt_failure_reports_utility_stage_not_image_model(self):
        error = ProviderRequestError("openrouter-free::apodex/apodex-1.1-mini:free", "rate_limit", 429, retry_after=37)
        wrapped = image_prompt_provider.ImagePromptPreparationError(error)
        messages = []
        callback = {"id": "cb", "message": {"message_id": 77}}
        request_context = SimpleNamespace(app_settings=self.app_settings_builder.build())

        with (
            patch.object(image_callbacks, "send_typing"),
            patch.object(image_callbacks, "handle_imagine_scene", side_effect=wrapped),
            patch.object(image_callbacks, "send_text", side_effect=lambda _t, _c, text: messages.append(text)),
        ):
            image_callbacks._imagine_scene(
                self.db,
                "token",
                callback,
                lambda *_args: None,
                "chat",
                self.session,
                make_test_provider_port(),
                request_context,
            )

        self.assertEqual(
            messages,
            [
                "Image prompt preparation failed.\n\n"
                "Utility model: openrouter-free::apodex/apodex-1.1-mini:free\n"
                "Reason: Rate limited by provider (HTTP 429)\n"
                "Retry: in 37 seconds\n"
                "Image model: not called\n"
                "Next: Try again later or choose another Utility model in /provider."
            ],
        )
