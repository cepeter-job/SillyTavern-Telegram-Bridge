from application_test_setup import ensure_application_extensions
from codex_test_support import StreamingResponse as _StreamingResponse
from codex_test_support import jwt_token as _jwt
from settings_test_support import SettingsTestCase, make_test_settings

ensure_application_extensions()

import json
import threading
import time
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

import bridge.codex_auth as codex_auth
import bridge.codex_transport as codex_transport
import bridge.provider_transport as provider_transport
from bridge.model_router import ModelRouter


class CodexTransportTests(SettingsTestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.home = Path(self.temp.name)
        self.auth_file = self.home / "codex_oauth.json"
        self.access = _jwt(
            exp=time.time() + 3600,
            **{"https://api.openai.com/auth": {"chatgpt_account_id": "acct-1"}},
        )
        codex_auth.save_tokens(self.auth_file, {"access_token": self.access, "refresh_token": "refresh"})
        self.settings = make_test_settings(
            {
                "SILLYTAVERN_PROVIDER_ALLOWED_HOSTS": "chatgpt.com,auth.openai.com",
                "SILLYTAVERN_CODEX_AUTH_FILE": str(self.auth_file),
            },
            home=self.home,
        )
        self.spec = {
            "transport": "openai_codex",
            "api_endpoint": codex_auth.DEFAULT_CODEX_BASE_URL,
            "models": ["gpt-5.4"],
        }

    def tearDown(self):
        self.temp.cleanup()

    def test_builds_native_responses_request_and_extracts_streamed_text(self):
        captured = []

        def fake_open(request, timeout, *, environ):
            captured.append((request, timeout, environ))
            return _StreamingResponse(
                [
                    {"type": "response.output_text.delta", "delta": "native"},
                    {"type": "response.output_text.delta", "delta": " codex"},
                    {"type": "response.completed", "response": {"status": "completed"}},
                ]
            )

        result = codex_transport.generate_codex_response(
            "gpt-5.4",
            [
                {"role": "system", "content": "Stay in character."},
                {"role": "user", "content": "Hello"},
                {"role": "assistant", "content": "Hi"},
            ],
            {"max_tokens": 512, "reasoning_budget": 4096},
            self.spec,
            "telegram:1:session",
            app_settings=self.settings,
            open_request=fake_open,
        )

        self.assertEqual(result, "native codex")
        request, timeout, environ = captured[0]
        body = json.loads(request.data.decode())
        self.assertEqual(request.full_url, f"{codex_auth.DEFAULT_CODEX_BASE_URL}/responses")
        self.assertEqual(timeout, 240)
        self.assertEqual(environ["SILLYTAVERN_PROVIDER_ALLOWED_HOSTS"], "chatgpt.com,auth.openai.com")
        self.assertEqual(body["model"], "gpt-5.4")
        self.assertEqual(body["instructions"], "Stay in character.")
        self.assertEqual(body["input"][0]["content"], [{"type": "input_text", "text": "Hello"}])
        self.assertEqual(body["input"][1]["content"], [{"type": "output_text", "text": "Hi"}])
        self.assertEqual(body["reasoning"], {"effort": "medium", "summary": "auto"})
        self.assertNotIn("max_output_tokens", body)
        self.assertIs(body["store"], False)
        self.assertIs(body["stream"], True)
        self.assertEqual(request.headers["Authorization"], f"Bearer {self.access}")
        self.assertEqual(request.headers["Originator"], "codex_cli_rs")
        self.assertRegex(request.headers["User-agent"], r"^codex_cli_rs/0\.0\.0")
        self.assertEqual(request.headers["Chatgpt-account-id"], "acct-1")

    def test_strips_large_context_alias_before_codex_request(self):
        bodies = []

        def fake_open(request, timeout, *, environ):
            del timeout, environ
            bodies.append(json.loads(request.data.decode()))
            return _StreamingResponse(
                [{"type": "response.output_text.delta", "delta": "ok"}, {"type": "response.completed", "response": {}}]
            )

        result = codex_transport.generate_codex_response(
            "gpt-5.6-luna-900k",
            [{"role": "user", "content": "Hello"}],
            {"reasoning_budget": 0},
            self.spec,
            "session",
            app_settings=self.settings,
            open_request=fake_open,
        )

        self.assertEqual(result, "ok")
        self.assertEqual(bodies[0]["model"], "gpt-5.6-luna")

    def test_reasoning_budget_is_optional_and_clamped_per_model(self):
        bodies = []

        def fake_open(request, timeout, *, environ):
            del timeout, environ
            bodies.append(json.loads(request.data.decode()))
            return _StreamingResponse(
                [{"type": "response.output_text.delta", "delta": "ok"}, {"type": "response.completed", "response": {}}]
            )

        for model, budget in (("gpt-5.4", 0), ("gpt-5.4", 16384), ("gpt-5.6-sol", 16384)):
            codex_transport.generate_codex_response(
                model,
                [{"role": "user", "content": "Hello"}],
                {"max_tokens": 64, "reasoning_budget": budget},
                self.spec,
                "session",
                app_settings=self.settings,
                open_request=fake_open,
            )

        self.assertNotIn("reasoning", bodies[0])
        self.assertEqual(bodies[1]["reasoning"]["effort"], "xhigh")
        self.assertEqual(bodies[2]["reasoning"]["effort"], "max")

    def test_dispatches_openai_codex_without_an_api_key(self):
        router = ModelRouter(load_catalog=lambda: {"codex": self.spec})
        old_generate = provider_transport.generate_codex_response
        captured = []

        def fake_generate(*args, **kwargs):
            captured.append((args, kwargs))
            return "ok"

        provider_transport.generate_codex_response = fake_generate
        try:
            result = provider_transport.generate_provider_text(
                router,
                "",
                "codex::gpt-5.4",
                [{"role": "user", "content": "Hello"}],
                settings={"max_tokens": 64},
                app_settings=self.settings,
            )
        finally:
            provider_transport.generate_codex_response = old_generate

        self.assertEqual(result, "ok")
        self.assertEqual(captured[0][0][0], "gpt-5.4")

    def test_stream_callback_receives_visible_text_and_cancel_stops_reading(self):
        streamed = []

        cancel = threading.Event()

        def preview(text):
            streamed.append(text)
            cancel.set()

        result = codex_transport.generate_codex_response(
            "gpt-5.4",
            [{"role": "user", "content": "Hello"}],
            {"max_tokens": 64, "reasoning_budget": 0},
            self.spec,
            "telegram:1:session",
            app_settings=self.settings,
            stream_callback=preview,
            cancel_event=cancel,
            open_request=lambda *_args, **_kwargs: _StreamingResponse(
                [
                    {"type": "response.output_text.delta", "delta": "first"},
                    {"type": "response.output_text.delta", "delta": " second"},
                ]
            ),
        )

        self.assertEqual(result, "first")
        self.assertEqual(streamed[-1], "first")

    def test_one_401_forces_one_serialized_refresh_and_retry(self):
        requests = []
        resolutions = []

        class Unauthorized(RuntimeError):
            code = 401

            def close(self):
                return None

        def resolve(_path, *, rejected_access_token=None, **_kwargs):
            resolutions.append(rejected_access_token)
            return _jwt(exp=time.time() + 3600, account="new-account" if rejected_access_token else "old-account")

        def fake_open(request, timeout, *, environ):
            del timeout, environ
            requests.append(request)
            if len(requests) == 1:
                raise Unauthorized("rejected")
            return _StreamingResponse(
                [
                    {"type": "response.output_text.delta", "delta": "retried"},
                    {"type": "response.completed", "response": {}},
                ]
            )

        original_resolver = codex_transport.resolve_access_token
        codex_transport.resolve_access_token = resolve
        try:
            result = codex_transport.generate_codex_response(
                "gpt-5.4",
                [{"role": "user", "content": "hello"}],
                {"max_tokens": 128},
                self.spec,
                "session",
                app_settings=self.settings,
                open_request=fake_open,
            )
        finally:
            codex_transport.resolve_access_token = original_resolver

        self.assertEqual(result, "retried")
        self.assertEqual(len(requests), 2)
        self.assertEqual(resolutions[0], None)
        self.assertTrue(resolutions[1])
        self.assertNotEqual(requests[0].headers["Authorization"], requests[1].headers["Authorization"])

    def test_completed_response_falls_back_to_nested_output_text(self):
        result = codex_transport.generate_codex_response(
            "gpt-5.4",
            [{"role": "user", "content": "Hello"}],
            {"max_tokens": 64},
            self.spec,
            "session",
            app_settings=self.settings,
            open_request=lambda *_args, **_kwargs: _StreamingResponse(
                [
                    {
                        "type": "response.completed",
                        "response": {
                            "output": [{"content": [{"type": "output_text", "text": {"value": "completed fallback"}}]}]
                        },
                    }
                ]
            ),
        )

        self.assertEqual(result, "completed fallback")

    def test_http_failure_raises_typed_provider_transport_error(self):
        from bridge.provider_errors import ProviderTransportError

        class RateLimited(RuntimeError):
            code = 429

            def __init__(self, message):
                super().__init__(message)
                self.headers = {"Retry-After": "37"}

            def close(self):
                return None

        with self.assertRaises(ProviderTransportError) as raised:
            codex_transport.generate_codex_response(
                "gpt-5.4",
                [{"role": "user", "content": "Hello"}],
                {"max_tokens": 64},
                self.spec,
                "session",
                app_settings=self.settings,
                open_request=lambda *_args, **_kwargs: (_ for _ in ()).throw(RateLimited("secret")),
            )

        self.assertEqual(raised.exception.category, "rate_limit")
        self.assertEqual(raised.exception.status, 429)
        self.assertEqual(getattr(raised.exception, "retry_after", None), 37)
        self.assertNotIn("secret", str(raised.exception))

    def test_failed_response_raises_bounded_provider_error(self):
        with self.assertRaisesRegex(RuntimeError, "OpenAI Codex request failed; provider response was unsuccessful"):
            codex_transport.generate_codex_response(
                "gpt-5.4",
                [{"role": "user", "content": "Hello"}],
                {"max_tokens": 64, "reasoning_budget": 0},
                self.spec,
                "telegram:1:session",
                app_settings=self.settings,
                open_request=lambda *_args, **_kwargs: _StreamingResponse(
                    [{"type": "response.failed", "response": {"error": {"message": "denied"}}}]
                ),
            )


if __name__ == "__main__":
    unittest.main()
