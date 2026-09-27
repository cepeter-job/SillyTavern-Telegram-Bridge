from application_test_setup import ensure_application_extensions
from settings_test_support import make_test_settings

ensure_application_extensions()

import base64
import contextlib
import io
import json
import time
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import bridge.codex_auth as codex_auth
import bridge.main as main
import bridge.provider_discovery as discovery
from bridge.model_router import ModelRouter


def _jwt(**claims):
    def segment(payload):
        raw = json.dumps(payload, separators=(",", ":")).encode()
        return base64.urlsafe_b64encode(raw).decode().rstrip("=")

    return f"{segment({'alg': 'none'})}.{segment(claims)}.signature"


class CodexProviderSurfaceTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.home = Path(self.temp.name)
        self.auth_file = self.home / "codex_oauth.json"
        self.access = _jwt(exp=time.time() + 3600)
        self.catalog = self.home / "providers.yaml"
        self.catalog.write_text(
            """providers:
  codex:
    name: OpenAI Codex OAuth
    api_endpoint: https://chatgpt.com/backend-api/codex
    transport: openai_codex
    models:
      - gpt-5.4
""",
            encoding="utf-8",
        )
        self.settings = make_test_settings(
            {
                "SILLYTAVERN_PROVIDER_CONFIG": str(self.catalog),
                "SILLYTAVERN_PROVIDER_ALLOWED_HOSTS": "chatgpt.com,auth.openai.com",
                "SILLYTAVERN_CODEX_AUTH_FILE": str(self.auth_file),
            },
            home=self.home,
        )
        self.router = ModelRouter(
            load_catalog=lambda: {
                "codex": {
                    "transport": "openai_codex",
                    "api_endpoint": codex_auth.DEFAULT_CODEX_BASE_URL,
                    "models": ["gpt-5.4"],
                }
            }
        )

    def tearDown(self):
        self.temp.cleanup()

    def test_startup_requires_oauth_login_not_api_key(self):
        with self.assertRaisesRegex(RuntimeError, "--codex-login"):
            main.validate_startup_credential(
                "codex::gpt-5.4",
                self.router,
                app_settings=self.settings,
            )

        codex_auth.save_tokens(self.auth_file, {"access_token": self.access, "refresh_token": "refresh"})
        main.validate_startup_credential(
            "codex::gpt-5.4",
            self.router,
            app_settings=self.settings,
        )

    def test_login_opens_prefilled_browser_without_printing_device_code(self):
        opened = []
        output = io.StringIO()
        old_login = main.device_login
        old_open = getattr(main, "webbrowser", None)

        def fake_login(_path, *, environ, notify):
            del environ
            notify("https://auth.openai.com/codex/device", "ABCD-EFGH")
            return {"access_token": self.access, "refresh_token": "refresh"}

        class Browser:
            @staticmethod
            def open(url, new=0):
                opened.append((url, new))
                return True

        main.device_login = fake_login
        main.webbrowser = Browser
        try:
            with contextlib.redirect_stdout(output), patch.object(main.os, "open", side_effect=OSError):
                exit_code = main._run_codex_auth_action("login", self.settings)
        finally:
            main.device_login = old_login
            if old_open is None:
                delattr(main, "webbrowser")
            else:
                main.webbrowser = old_open

        self.assertEqual(exit_code, 0)
        self.assertEqual(opened, [("https://auth.openai.com/codex/device?user_code=ABCD-EFGH", 2)])
        self.assertNotIn("ABCD-EFGH", output.getvalue())
        self.assertIn("browser", output.getvalue())

    def test_auth_status_output_does_not_expose_tokens(self):
        codex_auth.save_tokens(self.auth_file, {"access_token": self.access, "refresh_token": "secret-refresh"})
        output = io.StringIO()

        with contextlib.redirect_stdout(output):
            exit_code = main._run_codex_auth_action("status", self.settings)

        self.assertEqual(exit_code, 0)
        self.assertIn("ready", output.getvalue())
        self.assertNotIn("secret", output.getvalue())

    def test_provider_health_reports_auth_without_models_request(self):
        codex_auth.save_tokens(self.auth_file, {"access_token": self.access, "refresh_token": "refresh"})
        old_open = discovery.strict_urlopen
        discovery.strict_urlopen = lambda *_args, **_kwargs: self.fail("Codex health must not call /models")
        try:
            results = discovery.provider_health_checks("codex", app_settings=self.settings)
        finally:
            discovery.strict_urlopen = old_open

        self.assertEqual(results, [("codex", "OpenAI Codex OAuth", "authenticated")])

    def test_model_groups_marks_codex_transport_supported(self):
        groups = discovery.get_model_groups(app_settings=self.settings)

        self.assertIn("codex", groups)
        self.assertTrue(groups["codex"][2])
        self.assertEqual(groups["codex"][1], [("gpt-5.4", "codex::gpt-5.4")])

    def test_settings_default_oauth_file_is_under_bridge_home(self):
        settings = make_test_settings({}, home=self.home)
        self.assertEqual(settings.codex_oauth_file, settings.bridge_home / "codex_oauth.json")


if __name__ == "__main__":
    unittest.main()
