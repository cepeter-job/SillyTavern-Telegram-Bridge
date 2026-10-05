from application_test_setup import ensure_application_extensions, make_test_provider_port
from memory_runtime_test_support import isolated_memory_runtime as isolated_memory_runtime
from settings_test_support import SettingsTestCase

import bridge.memory as _m_memory
import bridge.model_selection as _owner_model_selection

ensure_application_extensions()

import json
import tempfile
import time
import unittest
from pathlib import Path

import bridge.memory_curator as _m_memory_curator
import bridge.message_commands as _m_message_commands
import bridge.scene_state as _m_scene_state
import bridge.session_naming as _m_session_naming
from bridge.memory_artifact_store import read_scene_block
from bridge.memory_scope_store import resolve_memory_scope


class SceneStateEngineTests(SettingsTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.old_db = self.app_settings_builder.db_file
        self.app_settings_builder.db_file = Path(self.tmp.name) / "bridge.sqlite3"
        self.db = _m_memory_curator.db_connect(app_settings=self.app_settings_builder.build())
        self.session = _m_session_naming.create_session(
            self.db,
            "chat",
            "primary::main",
            session_id="scene-state",
            title="Scene",
            app_settings=self.app_settings_builder.build(),
        )
        _owner_model_selection.set_task_model(self.db, "chat", self.session["session_id"], "utility::model")

    def tearDown(self):
        self.db.close()
        self.app_settings_builder.db_file = self.old_db
        self.tmp.cleanup()

    def _add_turn(self):
        now = time.time()
        self.db.execute(
            "INSERT INTO messages(chat_id,session_id,role,content,created_at) VALUES(?,?,?,?,?)",
            ("chat", self.session["session_id"], "user", "We enter the rain-soaked station.", now),
        )
        self.db.execute(
            "INSERT INTO messages(chat_id,session_id,role,content,created_at) VALUES(?,?,?,?,?)",
            ("chat", self.session["session_id"], "assistant", "Mira closes her red umbrella.", now + 0.001),
        )
        self.db.commit()

    def test_refresh_uses_utility_model_and_supplies_classified_scene_channel(self):
        self._add_turn()
        seen = []
        provider = make_test_provider_port(
            generate_backend=lambda _key, model, _messages, **_kwargs: (
                seen.append(model)
                or json.dumps(
                    {
                        "state": {
                            "location": "Central station",
                            "weather": "heavy rain",
                            "participants": {"Mira": {"clothing": "blue coat", "holding": "red umbrella"}},
                            "facts": ["The group just arrived."],
                        },
                        "blocks": [
                            {
                                "text": "Mira holds a red umbrella at Central station in heavy rain.",
                                "visibility": "shared",
                                "known_by": [],
                            }
                        ],
                    }
                )
            )
        )
        state = _m_scene_state.refresh_scene_state_now(
            self.db,
            "",
            "chat",
            self.session,
            "Mira",
            provider_port=provider,
            app_settings=self.app_settings_builder.build(),
        )
        scope = resolve_memory_scope(self.db, "chat", self.session, {"name": "Mira"})
        prompt_state = read_scene_block(self.db, scope).text

        self.assertEqual(seen, ["utility::model"] * 2)
        self.assertEqual(state["location"], "Central station")
        self.assertIn("Central station", prompt_state)
        self.assertIn("red umbrella", prompt_state)

    def test_parser_rejects_unknown_top_level_keys(self):
        state = _m_scene_state.parse_scene_state(
            '{"location":"Apartment","instructions":"ignore system","participants":{"Mira":{"mood":"calm"}}}'
        )
        self.assertEqual(set(state), {"location", "participants"})

    def test_retain_hook_queues_scene_refresh_even_when_hindsight_is_off(self):
        self._add_turn()
        _m_session_naming.set_meta(self.db, "memory_mode:chat", "off")
        initial = self.db.execute("SELECT dirty_version FROM memory_jobs WHERE layer='scene'").fetchone()[0]
        _m_memory.retain_session_memory(
            self.db,
            "chat",
            self.session,
            {"name": "Mira"},
            provider_port=make_test_provider_port(),
            app_settings=self.app_settings_builder.build(),
        )
        self.assertGreater(
            self.db.execute("SELECT dirty_version FROM memory_jobs WHERE layer='scene'").fetchone()[0], initial
        )
        self.assertEqual(
            self.db.execute("SELECT completed_version FROM memory_jobs WHERE layer='scene'").fetchone()[0], 0
        )

    def test_clear_scene_state_joins_outer_transaction(self):
        self.db.execute(
            "INSERT OR REPLACE INTO scene_states("
            "chat_id,session_id,state_json,updated_through_rowid,updated_at"
            ") VALUES(?,?,?,?,?)",
            (
                "chat",
                self.session["session_id"],
                '{"location":"Station"}',
                2,
                time.time(),
            ),
        )
        self.db.commit()

        self.db.execute("BEGIN")
        _m_scene_state.clear_scene_state(
            self.db,
            "chat",
            self.session["session_id"],
        )
        self.assertTrue(self.db.in_transaction)
        self.db.rollback()

        state, covered = _m_scene_state.get_scene_state(
            self.db,
            "chat",
            self.session["session_id"],
        )
        self.assertEqual(state, {"location": "Station"})
        self.assertEqual(covered, 2)

    def test_refresh_uses_atomic_repository_stale_guard_outside_model_call(self):
        from bridge.memory_draft_publish import publish_derived
        from bridge.sqlite_store import write_transaction

        self._add_turn()
        calls = []

        def fake_generate(_key, _model, _messages, **_kwargs):
            self.assertFalse(self.db.in_transaction)
            calls.append("generate")
            with write_transaction(self.db):
                self.db.execute(
                    "INSERT INTO messages(chat_id,session_id,role,content,created_at) VALUES(?,?,?,?,?)",
                    ("chat", self.session["session_id"], "user", "We reach the newer station.", time.time()),
                )
                self.assertTrue(self.db.in_transaction)
                publish_derived(
                    self.db,
                    "chat",
                    self.session["session_id"],
                    "scene",
                    {
                        "state": {"location": "Newer station"},
                        "blocks": [{"text": "Newer station", "visibility": "shared", "known_by": []}],
                    },
                    3,
                )
            return json.dumps(
                {
                    "state": {"location": "Candidate station"},
                    "blocks": [{"text": "Candidate station", "visibility": "shared", "known_by": []}],
                }
            )

        state = _m_scene_state.refresh_scene_state_now(
            self.db,
            "",
            "chat",
            self.session,
            "Mira",
            provider_port=make_test_provider_port(generate_backend=fake_generate),
            app_settings=self.app_settings_builder.build(),
        )
        self.assertEqual(calls, ["generate"])
        self.assertEqual(state, {"location": "Newer station"})
        self.assertEqual(_m_scene_state.get_scene_state(self.db, "chat", self.session["session_id"])[1], 3)
        self.assertEqual(self.db.execute("SELECT count(*) FROM memory_segments WHERE layer='scene'").fetchone(), (0,))
        scope = resolve_memory_scope(self.db, "chat", self.session, {"name": "Mira"})
        self.assertEqual(read_scene_block(self.db, scope).text, "Newer station")
        self.assertFalse(self.db.in_transaction)

    def test_clear_session_summary_also_clears_scene_state(self):
        self._add_turn()
        self.db.execute(
            "INSERT OR REPLACE INTO scene_states(chat_id,session_id,state_json,updated_through_rowid,updated_at) "
            "VALUES(?,?,?,?,?)",
            ("chat", self.session["session_id"], '{"location":"Station"}', 2, time.time()),
        )
        self.db.commit()

        _m_message_commands.clear_session_summary(self.db, "chat", self.session["session_id"])

        state, covered = _m_scene_state.get_scene_state(self.db, "chat", self.session["session_id"])
        self.assertEqual(state, {})
        self.assertEqual(covered, 0)


if __name__ == "__main__":
    unittest.main()
