from __future__ import annotations

import tempfile
import time
import unittest
from pathlib import Path

from settings_test_support import SettingsTestCase

import bridge.command_routes as _owner_command_routes
import bridge.failed_turns as _owner_failed_turns
import bridge.sqlite_store as _sqlite_store
from bridge.provider_errors import ProviderRequestError


class RetryModelTurnOnlyTests(SettingsTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = _sqlite_store.db_connect(
            Path(self.tmp.name) / "retry.sqlite3", app_settings=self.app_settings_builder.build()
        )

    def tearDown(self):
        self.db.close()
        self.tmp.cleanup()

    def test_command_failures_do_not_enter_model_retry_queue(self):
        for index, text in enumerate(
            (
                "/regen",
                "/status",
                "/retry@BridgeBot",
                "@BridgeBot /continue",
                "start",
            ),
            start=1,
        ):
            _owner_failed_turns.record_failed_turn(
                self.db,
                "chat",
                index,
                text,
                "provider::model",
                "command failed",
                "session",
            )

        count = self.db.execute(
            "SELECT COUNT(*) FROM failed_turns WHERE chat_id=?",
            ("chat",),
        ).fetchone()[0]
        self.assertEqual(count, 0)
        self.assertIsNone(_owner_failed_turns.latest_failed_turn(self.db, "chat"))

    def test_start_with_text_remains_retryable_model_turn(self):
        _owner_failed_turns.record_failed_turn(
            self.db,
            "chat",
            51,
            "start hello",
            "provider::model",
            "model failed",
            "session-b",
        )

        failed = _owner_failed_turns.latest_failed_turn(self.db, "chat")

        self.assertIsNotNone(failed)
        self.assertEqual(str(failed[0]), "51")
        self.assertEqual(failed[1], "start hello")

    def test_latest_failed_turn_skips_legacy_command_rows(self):
        _owner_failed_turns.record_failed_turn(
            self.db,
            "chat",
            41,
            "normal user prompt",
            "provider::model",
            "model failed",
            "session-a",
        )
        now = time.time()
        self.db.execute(
            "INSERT INTO failed_turns("
            "chat_id,telegram_message_id,text,model,session_id,attempts,"
            "last_error,created_at,updated_at"
            ") VALUES(?,?,?,?,?,?,?,?,?)",
            (
                "chat",
                "42",
                "/regen",
                "provider::model",
                "session-a",
                1,
                "legacy command failure",
                now,
                now + 10,
            ),
        )
        self.db.commit()

        failed = _owner_failed_turns.latest_failed_turn(self.db, "chat")

        self.assertIsNotNone(failed)
        self.assertEqual(str(failed[0]), "41")
        self.assertEqual(failed[1], "normal user prompt")

    def test_failed_turn_records_resolved_session_model(self):
        now = time.time()
        self.db.execute(
            "INSERT INTO sessions("
            "chat_id,session_id,title,character_file,model_id,persona_id,"
            "world_file,author_note,system_prompt,response_language,"
            "created_at,updated_at"
            ") VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                "chat",
                "session-model",
                "Model session",
                "character.png",
                "cline-pass::cline-pass/glm-5.2",
                "",
                "",
                "",
                "",
                "auto",
                now,
                now,
            ),
        )
        self.db.commit()

        _owner_failed_turns.record_failed_turn(
            self.db,
            "chat",
            61,
            "normal prompt",
            "provider-one::provider-one/model-a",
            "provider failed",
            "session-model",
        )

        failed = _owner_failed_turns.latest_failed_turn(self.db, "chat")
        self.assertIsNotNone(failed)
        self.assertEqual(
            failed[2],
            "cline-pass::cline-pass/glm-5.2",
        )

    def test_failed_turn_upsert_refreshes_resolved_session_model(self):
        now = time.time()
        self.db.execute(
            "INSERT INTO sessions("
            "chat_id,session_id,title,character_file,model_id,persona_id,"
            "world_file,author_note,system_prompt,response_language,"
            "created_at,updated_at"
            ") VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                "chat",
                "session-refresh",
                "Refresh session",
                "character.png",
                "provider-one::provider-one/model-a",
                "",
                "",
                "",
                "",
                "auto",
                now,
                now,
            ),
        )
        self.db.commit()
        _owner_failed_turns.record_failed_turn(
            self.db,
            "chat",
            62,
            "normal prompt",
            "provider-one::provider-one/model-a",
            "first failure",
            "session-refresh",
        )

        self.db.execute(
            "UPDATE sessions SET model_id=? WHERE chat_id=? AND session_id=?",
            (
                "cline-pass::cline-pass/glm-5.2",
                "chat",
                "session-refresh",
            ),
        )
        self.db.commit()
        _owner_failed_turns.record_failed_turn(
            self.db,
            "chat",
            62,
            "normal prompt",
            "provider-one::provider-one/model-a",
            "retry failure",
            "session-refresh",
        )

        failed = _owner_failed_turns.latest_failed_turn(self.db, "chat")
        self.assertIsNotNone(failed)
        self.assertEqual(
            failed[2],
            "cline-pass::cline-pass/glm-5.2",
        )
        self.assertEqual(failed[3], 2)

    def test_retry_failure_report_includes_safe_provider_details(self):
        failed = (42, "private prompt", "stored::model", 1, "old failure", "session")
        report = _owner_command_routes._retry_failure_report(
            failed,
            ProviderRequestError("cline-paid::z-ai/glm-5.2", "rate_limit", 429),
        )

        self.assertIn("Retry attempt 2 failed.", report)
        self.assertIn("Turn: Telegram message 42", report)
        self.assertIn("Model: cline-paid::z-ai/glm-5.2", report)
        self.assertIn("rate-limited (HTTP 429)", report)
        self.assertIn("remains queued for /retry", report)
        self.assertIn("/providers", report)
        self.assertNotIn("private prompt", report)
        self.assertNotIn("old failure", report)

    def test_retry_failure_report_redacts_unknown_exception_details(self):
        failed = (43, "private prompt", "stored::model", 2, "old failure", "session")
        report = _owner_command_routes._retry_failure_report(
            failed,
            RuntimeError("https://private.invalid?api_key=SECRET"),
        )

        self.assertIn("Retry attempt 3 failed.", report)
        self.assertIn("Model: stored::model", report)
        self.assertIn("character backend failed before producing a usable response", report)
        self.assertNotIn("private.invalid", report)
        self.assertNotIn("SECRET", report)

    def test_retry_failure_report_explains_malformed_light_novel_response(self):
        failed = (44, "prompt", "stored::model", 1, "old failure", "session")
        report = _owner_command_routes._retry_failure_report(
            failed,
            ValueError("Story response has no usable narrative"),
        )

        self.assertIn("empty or malformed Light Novel response", report)

    def test_retry_failure_report_survives_malformed_legacy_identifiers(self):
        failed = ("not-an-id", "prompt", "bad model name", "not-a-count", "old failure", "session")
        report = _owner_command_routes._retry_failure_report(failed, RuntimeError("SECRET"))

        self.assertIn("Retry attempt 1 failed.", report)
        self.assertIn("Turn: Telegram message unavailable", report)
        self.assertIn("Model: the selected model", report)
        self.assertNotIn("SECRET", report)


if __name__ == "__main__":
    unittest.main()
