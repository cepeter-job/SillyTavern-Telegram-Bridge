from application_test_setup import ensure_application_extensions, make_test_provider_port
from memory_runtime_test_support import isolated_memory_runtime as isolated_memory_runtime
from settings_test_support import SettingsTestCase

import bridge.memory as _owner_memory
import bridge.model_selection as _owner_model_selection

ensure_application_extensions()

import tempfile
import time
import unittest
from pathlib import Path

import bridge.memory_curator as _m_memory_curator
import bridge.session_naming as _m_session_naming


class TaskModelRoutingTests(SettingsTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.old_db = self.app_settings_builder.db_file
        self.app_settings_builder.db_file = Path(self.tmp.name) / "bridge.sqlite3"
        self.db = _m_memory_curator.db_connect(app_settings=self.app_settings_builder.build())
        self.session = _m_session_naming.create_session(
            self.db,
            "chat",
            "primary::main-model",
            session_id="task-routing",
            title="Task routing",
            app_settings=self.app_settings_builder.build(),
        )

    def tearDown(self):
        self.db.close()
        self.app_settings_builder.db_file = self.old_db
        self.tmp.cleanup()

    def test_utility_route_defaults_to_main_model(self):
        self.assertEqual(
            _m_memory_curator.task_model_for_session(
                self.db, "chat", self.session, "summary", app_settings=self.app_settings_builder.build()
            ),
            "primary::main-model",
        )

    def test_summary_uses_session_utility_model(self):
        _owner_model_selection.set_task_model(
            self.db,
            "chat",
            self.session["session_id"],
            "cheap::summary-model",
        )
        now = time.time()
        self.db.execute(
            "INSERT INTO messages(chat_id,session_id,role,content,created_at) VALUES(?,?,?,?,?)",
            ("chat", self.session["session_id"], "user", "Remember the blue key.", now),
        )
        self.db.execute(
            "INSERT INTO messages(chat_id,session_id,role,content,created_at) VALUES(?,?,?,?,?)",
            ("chat", self.session["session_id"], "assistant", "The blue key is in the drawer.", now + 0.001),
        )
        self.db.commit()

        seen_models = []
        provider = make_test_provider_port(
            generate_backend=lambda _key, model, _messages, **_kwargs: (
                seen_models.append(model)
                or '{"blocks":[{"text":"Blue key in drawer.","visibility":"shared","known_by":[]}]}'
            )
        )
        summary = _owner_memory.generate_session_summary(
            self.db,
            "chat",
            self.session,
            force=True,
            provider_port=provider,
            app_settings=self.app_settings_builder.build(),
        )

        self.assertEqual(summary, "Blue key in drawer.")
        self.assertEqual(seen_models, ["cheap::summary-model"] * 2)

    def test_stale_utility_model_falls_back_to_routable_main(self):
        catalog = Path(self.tmp.name) / "providers.yaml"
        catalog.write_text(
            "providers:\n  primary:\n    models: [main-model]\n  cheap:\n    models: [summary-model]\n",
            encoding="utf-8",
        )
        self.app_settings_builder.provider_config_file = catalog
        settings = self.app_settings_builder.build()
        for stale in ("retired::old-model", "cheap::removed-model"):
            with self.subTest(stale=stale):
                _owner_model_selection.set_task_model(self.db, "chat", self.session["session_id"], stale, "utility")
                self.assertEqual(
                    _owner_model_selection.task_model_for_session(
                        self.db, "chat", self.session, "summary", app_settings=settings
                    ),
                    "primary::main-model",
                )

    def test_summary_prompt_states_exact_audience_contract(self):
        now = time.time()
        self.db.execute(
            "INSERT INTO messages(chat_id,session_id,role,content,created_at) VALUES(?,?,?,?,?)",
            ("chat", self.session["session_id"], "user", "A public fact.", now),
        )
        self.db.commit()
        prompts = []

        def generate(_key, _model, messages, **_kwargs):
            prompts.append(messages[0]["content"])
            return '{"blocks":[{"text":"Public fact.","visibility":"shared","known_by":[]}]}'

        _owner_memory.generate_session_summary(
            self.db,
            "chat",
            self.session,
            force=True,
            provider_port=make_test_provider_port(generate_backend=generate),
            app_settings=self.app_settings_builder.build(),
        )
        self.assertTrue(prompts)
        self.assertTrue(all('visibility="shared" requires known_by=[]' in prompt for prompt in prompts))

    def test_main_clears_utility_override(self):
        _owner_model_selection.set_task_model(self.db, "chat", self.session["session_id"], "cheap::summary-model")
        _owner_model_selection.set_task_model(self.db, "chat", self.session["session_id"], "main")
        self.assertEqual(
            _m_memory_curator.task_model_for_session(
                self.db, "chat", self.session, "summary", app_settings=self.app_settings_builder.build()
            ),
            "primary::main-model",
        )


if __name__ == "__main__":
    unittest.main()
