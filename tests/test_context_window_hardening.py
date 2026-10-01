import dataclasses
import tempfile
import unittest
from pathlib import Path

from application_test_setup import make_test_persona_service
from settings_test_support import SettingsTestCase

import bridge.context_compaction as context
import bridge.generation as generation


class ContextWindowHardeningTests(SettingsTestCase):
    def _catalog_settings(self, body: str):
        root = Path(tempfile.mkdtemp(prefix="context-hardening-"))
        path = root / "providers.yaml"
        path.write_text(body, encoding="utf-8")
        return dataclasses.replace(self.app_settings_builder.build(), provider_config_file=path)

    def test_provider_and_model_context_metadata_override_global_fallback(self):
        settings = self._catalog_settings(
            "providers:\n"
            "  demo:\n"
            "    models: [model-a]\n"
            "    context_window_tokens: 65536\n"
            "    model_context_window_tokens:\n"
            "      model-a: 131072\n"
            "    token_estimate_chars_per_token: 3.6\n"
            "    model_token_estimate_chars_per_token:\n"
            "      model-a: 3.0\n"
        )
        profile = context.context_profile("demo::model-a", app_settings=settings)
        self.assertEqual(profile.window_tokens, 131072)
        self.assertEqual(profile.chars_per_token, 3.0)
        self.assertEqual(profile.source, "provider-model")

    def test_context_budget_reserves_conservative_safety_margin(self):
        settings = self.app_settings_builder.build()
        profile = context.context_profile("unknown", app_settings=settings)
        self.assertGreaterEqual(profile.safety_margin_tokens, 512)
        self.assertEqual(
            profile.input_budget_tokens,
            profile.window_tokens - profile.output_reserve_tokens - profile.safety_margin_tokens,
        )
        self.assertEqual(
            context.context_input_budget_tokens("unknown", app_settings=settings), profile.input_budget_tokens
        )

    def test_small_window_clamps_output_reserve_without_consuming_safety_margin(self):
        settings = dataclasses.replace(
            self.app_settings_builder.build(),
            context_window_tokens=4096,
            context_output_reserve_tokens=4096,
        )
        profile = context.context_profile("unknown", app_settings=settings)
        self.assertGreaterEqual(profile.input_budget_tokens, 2048)
        self.assertGreaterEqual(profile.output_reserve_tokens, 512)
        self.assertLessEqual(
            profile.input_budget_tokens + profile.output_reserve_tokens + profile.safety_margin_tokens,
            profile.window_tokens,
        )

    def test_token_estimation_accepts_model_specific_chars_per_token(self):
        messages = [{"role": "user", "content": "x" * 4000}]
        default = context.estimate_message_tokens(messages)
        conservative = context.estimate_message_tokens(messages, chars_per_token=2.0)
        self.assertGreater(conservative, default)

    def test_fixed_prompt_overflow_raises_preflight_error_and_reports_stats(self):
        settings = dataclasses.replace(
            self.app_settings_builder.build(),
            context_window_tokens=4096,
            context_output_reserve_tokens=512,
        )
        session = {
            "model_id": "unknown-model",
            "persona_id": "",
            "system_prompt": "",
            "author_note": "",
            "world_file": "",
            "response_language": "auto",
        }
        fields = {
            "name": "Character",
            "description": "x" * 30000,
            "personality": "",
            "scenario": "",
            "first_mes": "",
            "mes_example": "",
            "system_prompt": "",
            "post_history_instructions": "",
        }
        stats = {}
        with self.assertRaises(context.ContextWindowBudgetError) as raised:
            generation.build_chat_messages(
                session,
                fields,
                "CURRENT",
                [],
                persona_service=make_test_persona_service(),
                app_settings=settings,
                context_stats=stats,
            )
        self.assertTrue(stats["over_budget"])
        self.assertIn("cannot fit", str(raised.exception).lower())
        self.assertEqual(raised.exception.stats["final_tokens"], stats["final_tokens"])


if __name__ == "__main__":
    unittest.main()
