from application_test_setup import ensure_application_extensions, make_test_persona_service
from settings_test_support import SettingsTestCase

ensure_application_extensions()

import unittest
from unittest import mock

import bridge.generation as generation
from bridge.codex_models import codex_wire_model
from bridge.context_compaction import (
    compact_chat_messages,
    context_input_budget_tokens,
    context_profile,
    context_window_tokens,
)


class CodexContextVariantTests(SettingsTestCase):
    def setUp(self):
        self.settings = self.app_settings_builder.build()

    def test_verified_variants_use_900k_window(self):
        for family in ("sol", "terra", "luna"):
            model = f"openai-codex::gpt-5.6-{family}-900k"
            profile = context_profile(model=model, app_settings=self.settings)
            self.assertEqual(profile.window_tokens, 900_000)
            self.assertEqual(profile.input_budget_tokens, 887_712)
            self.assertEqual(profile.safety_margin_tokens, 8_192)
            self.assertEqual(codex_wire_model(model.rsplit("::", 1)[1]), f"gpt-5.6-{family}")

    def test_other_models_keep_configured_window(self):
        profile = context_profile(app_settings=self.settings)
        expected = profile.input_budget_tokens
        self.assertEqual(context_window_tokens(app_settings=self.settings), self.settings.context_window_tokens)
        self.assertEqual(context_input_budget_tokens(app_settings=self.settings), expected)
        models = (
            "openai-codex::gpt-5.6-sol",
            "openai-codex::gpt-5.5-900k",
            "other::gpt-5.6-sol-900k",
            "gpt-5.6-sol-900k",
        )
        for model in models:
            self.assertEqual(context_input_budget_tokens(model=model, app_settings=self.settings), expected)
        self.assertEqual(codex_wire_model("gpt-5.5-900k"), "gpt-5.5-900k")

    def test_large_variant_preserves_prompt_that_default_budget_compacts(self):
        messages = [{"role": "system", "content": "fixed rules"}]
        for index in range(40):
            role = "user" if index % 2 == 0 else "assistant"
            messages.append({"role": role, "content": f"turn-{index} " + ("context " * 1200)})
        messages.append({"role": "user", "content": "CURRENT TURN"})

        default_messages, default_stats = compact_chat_messages(
            messages,
            budget_tokens=context_input_budget_tokens(model="provider::model", app_settings=self.settings),
            app_settings=self.settings,
        )
        large_messages, large_stats = compact_chat_messages(
            messages,
            budget_tokens=context_input_budget_tokens(
                model="openai-codex::gpt-5.6-sol-900k",
                app_settings=self.settings,
            ),
            app_settings=self.settings,
        )

        self.assertGreater(default_stats["dropped_history"], 0)
        self.assertLess(len(default_messages), len(messages))
        self.assertEqual(large_messages, messages)
        self.assertEqual(large_stats["dropped_history"], 0)

    def test_chat_builder_passes_selected_model_budget_to_compactor(self):
        captured = {}

        def compact(messages, budget_tokens=None, *, chars_per_token=4.0, app_settings):
            del app_settings
            captured["budget"] = budget_tokens
            captured["chars_per_token"] = chars_per_token
            return messages, {
                "budget_tokens": budget_tokens,
                "original_tokens": 1,
                "final_tokens": 1,
                "dropped_history": 0,
                "rag_trimmed": False,
                "memory_trimmed": False,
                "summary_trimmed": False,
                "over_budget": False,
            }

        session = {
            "model_id": "openai-codex::gpt-5.6-luna-900k",
            "persona_id": "",
            "system_prompt": "",
            "author_note": "",
            "world_file": "",
            "response_language": "auto",
        }
        fields = {
            "name": "Character",
            "description": "",
            "personality": "",
            "scenario": "",
            "first_mes": "",
            "mes_example": "",
            "system_prompt": "",
            "post_history_instructions": "",
        }
        with mock.patch.object(generation, "compact_chat_messages", side_effect=compact):
            generation.build_chat_messages(
                session,
                fields,
                "Hello",
                [],
                persona_service=make_test_persona_service(),
                app_settings=self.settings,
            )
        self.assertEqual(captured["budget"], 887_712)
        self.assertEqual(captured["chars_per_token"], 4.0)

        session.pop("model_id")
        with mock.patch.object(generation, "compact_chat_messages", side_effect=compact):
            generation.build_chat_messages(
                session,
                fields,
                "Hello",
                [],
                persona_service=make_test_persona_service(),
                app_settings=self.settings,
            )
        self.assertEqual(
            captured["budget"],
            context_profile(app_settings=self.settings).input_budget_tokens,
        )


if __name__ == "__main__":
    unittest.main()
