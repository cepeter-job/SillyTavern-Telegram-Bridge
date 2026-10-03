import dataclasses
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

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

    def _write_cache(self, settings, payload):
        settings.model_cache_file.parent.mkdir(parents=True, exist_ok=True)
        settings.model_cache_file.write_text(json.dumps(payload), encoding="utf-8")

    def test_discovered_context_metadata_overrides_global_fallback(self):
        settings = self._catalog_settings(
            "providers:\n"
            "  demo:\n"
            "    models: [model-a]\n"
            "    discover_model_metadata: true\n"
        )
        self._write_cache(
            settings,
            {
                "demo": {
                    "models": [],
                    "model_context_window_tokens": {"model-a": 131072},
                    "metadata_refreshed_at": 1,
                    "metadata_last_attempt_at": 1,
                }
            },
        )

        profile = context.context_profile("demo::model-a", app_settings=settings)

        self.assertEqual(profile.window_tokens, 131072)
        self.assertEqual(profile.source, "discovered-provider-model")

    def test_explicit_provider_context_beats_discovered_metadata(self):
        settings = self._catalog_settings(
            "providers:\n"
            "  demo:\n"
            "    models: [model-a]\n"
            "    discover_model_metadata: true\n"
            "    context_window_tokens: 65536\n"
        )
        self._write_cache(
            settings,
            {
                "demo": {
                    "models": [],
                    "model_context_window_tokens": {"model-a": 131072},
                    "metadata_refreshed_at": 1,
                    "metadata_last_attempt_at": 1,
                }
            },
        )

        profile = context.context_profile("demo::model-a", app_settings=settings)

        self.assertEqual(profile.window_tokens, 65536)
        self.assertEqual(profile.source, "provider")

    def test_explicit_model_context_beats_provider_and_discovered_metadata(self):
        settings = self._catalog_settings(
            "providers:\n"
            "  demo:\n"
            "    models: [model-a]\n"
            "    discover_model_metadata: true\n"
            "    context_window_tokens: 65536\n"
            "    model_context_window_tokens:\n"
            "      model-a: 131072\n"
        )
        self._write_cache(
            settings,
            {
                "demo": {
                    "models": [],
                    "model_context_window_tokens": {"model-a": 262144},
                    "metadata_refreshed_at": 1,
                    "metadata_last_attempt_at": 1,
                }
            },
        )

        profile = context.context_profile("demo::model-a", app_settings=settings)

        self.assertEqual(profile.window_tokens, 131072)
        self.assertEqual(profile.source, "provider-model")

    def test_discovered_metadata_is_ignored_when_flag_off_or_model_unconfigured(self):
        settings = self._catalog_settings(
            "providers:\n"
            "  demo:\n"
            "    models: [model-a]\n"
        )
        self._write_cache(
            settings,
            {
                "demo": {
                    "models": [],
                    "model_context_window_tokens": {"model-a": 131072, "other": 262144},
                    "metadata_refreshed_at": 1,
                    "metadata_last_attempt_at": 1,
                }
            },
        )
        disabled = context.context_profile("demo::model-a", app_settings=settings)

        enabled = self._catalog_settings(
            "providers:\n"
            "  demo:\n"
            "    models: [model-a]\n"
            "    discover_model_metadata: true\n"
        )
        self._write_cache(
            enabled,
            {
                "demo": {
                    "models": [],
                    "model_context_window_tokens": {"other": 262144},
                    "metadata_refreshed_at": 1,
                    "metadata_last_attempt_at": 1,
                }
            },
        )
        unconfigured = context.context_profile("demo::other", app_settings=enabled)

        self.assertEqual(disabled.window_tokens, settings.context_window_tokens)
        self.assertEqual(disabled.source, "global-fallback")
        self.assertEqual(unconfigured.window_tokens, enabled.context_window_tokens)
        self.assertEqual(unconfigured.source, "global-fallback")

    def test_context_diagnostics_accepts_discovered_provider_model_source(self):
        import bridge.context_diagnostics as diagnostics

        settings = self.app_settings_builder.build()
        session = {"session_id": "session", "model_id": "unknown"}
        live = context.ContextProfile(32768, 4096, 656, 28016, 4.0, "global-fallback")
        saved = json.dumps({"source": "discovered-provider-model"})

        with mock.patch.object(diagnostics, "context_profile", return_value=live), mock.patch.object(
            diagnostics, "get_meta", return_value=saved
        ):
            snapshot = diagnostics.context_diagnostics_snapshot(
                mock.Mock(),
                "chat",
                session,
                app_settings=settings,
            )

        self.assertEqual(snapshot["source"], "discovered-provider-model")

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
