import base64
import io
import json
import stat
import threading
import time
import unittest
import urllib.error
from pathlib import Path
from tempfile import TemporaryDirectory

import bridge.codex_auth as codex_auth


class _Response:
    def __init__(self, payload, *, status=200):
        self.payload = payload
        self.status = status

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self, _limit=-1):
        return json.dumps(self.payload).encode()


def _jwt(**claims):
    def segment(payload):
        raw = json.dumps(payload, separators=(",", ":")).encode()
        return base64.urlsafe_b64encode(raw).decode().rstrip("=")

    return f"{segment({'alg': 'none'})}.{segment(claims)}.signature"


class CodexAuthTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.auth_file = Path(self.temp.name) / "private" / "codex_oauth.json"

    def tearDown(self):
        self.temp.cleanup()

    def test_save_is_atomic_private_and_status_never_exposes_tokens(self):
        codex_auth.save_tokens(
            self.auth_file,
            {"access_token": "secret-access", "refresh_token": "secret-refresh"},
        )

        self.assertEqual(stat.S_IMODE(self.auth_file.parent.stat().st_mode), 0o700)
        self.assertEqual(stat.S_IMODE(self.auth_file.stat().st_mode), 0o600)
        self.assertEqual(codex_auth.load_tokens(self.auth_file)["access_token"], "secret-access")
        status = codex_auth.auth_status(self.auth_file)
        self.assertTrue(status["authenticated"])
        self.assertNotIn("secret", json.dumps(status))

    def test_resolve_uses_unexpired_access_token_without_network(self):
        access = _jwt(exp=time.time() + 3600)
        codex_auth.save_tokens(self.auth_file, {"access_token": access, "refresh_token": "refresh"})

        resolved = codex_auth.resolve_access_token(
            self.auth_file,
            environ={},
            open_request=lambda *_args, **_kwargs: self.fail("unexpected refresh"),
        )

        self.assertEqual(resolved, access)

    def test_resolve_refreshes_opaque_access_token_without_expiry_claim(self):
        new_access = _jwt(exp=time.time() + 3600)
        codex_auth.save_tokens(
            self.auth_file,
            {"access_token": "opaque-access", "refresh_token": "old-refresh"},
        )
        calls = []

        def fake_open(_request, timeout, *, environ):
            self.assertEqual(timeout, 15)
            calls.append(environ)
            return _Response({"access_token": new_access, "refresh_token": "new-refresh"})

        resolved = codex_auth.resolve_access_token(
            self.auth_file,
            environ={"SILLYTAVERN_PROVIDER_ALLOWED_HOSTS": "auth.openai.com"},
            open_request=fake_open,
        )

        self.assertEqual(resolved, new_access)
        self.assertEqual(len(calls), 1)

    def test_resolve_refreshes_expired_token_and_persists_rotated_pair(self):
        old_access = _jwt(exp=time.time() - 60)
        new_access = _jwt(exp=time.time() + 3600)
        codex_auth.save_tokens(self.auth_file, {"access_token": old_access, "refresh_token": "old-refresh"})
        captured = []

        def fake_open(request, timeout, *, environ):
            captured.append((request, timeout, environ))
            return _Response({"access_token": new_access, "refresh_token": "new-refresh"})

        resolved = codex_auth.resolve_access_token(
            self.auth_file,
            environ={"SILLYTAVERN_PROVIDER_ALLOWED_HOSTS": "auth.openai.com"},
            open_request=fake_open,
        )

        self.assertEqual(resolved, new_access)
        stored = codex_auth.load_tokens(self.auth_file)
        self.assertEqual(stored["refresh_token"], "new-refresh")
        request, timeout, environ = captured[0]
        self.assertEqual(request.full_url, codex_auth.CODEX_OAUTH_TOKEN_URL)
        self.assertEqual(timeout, 15)
        self.assertEqual(environ["SILLYTAVERN_PROVIDER_ALLOWED_HOSTS"], "auth.openai.com")
        self.assertIn(b"grant_type=refresh_token", request.data)
        self.assertIn(b"refresh_token=old-refresh", request.data)

    def test_device_login_polls_and_saves_independent_token_family(self):
        access = _jwt(exp=time.time() + 3600)
        responses = iter(
            [
                _Response({"user_code": "ABCD-EFGH", "device_auth_id": "device-1", "interval": 1, "expires_in": 1800}),
                _Response({}, status=429),
                _Response({}, status=403),
                _Response({"authorization_code": "auth-code", "code_verifier": "verifier"}),
                _Response({"access_token": access, "refresh_token": "refresh-token"}),
            ]
        )
        seen = []

        def fake_open(request, timeout, *, environ):
            seen.append(request)
            return next(responses)

        notices = []
        result = codex_auth.device_login(
            self.auth_file,
            environ={"SILLYTAVERN_PROVIDER_ALLOWED_HOSTS": "auth.openai.com"},
            open_request=fake_open,
            sleep=lambda _seconds: None,
            notify=lambda url, code: notices.append((url, code)),
        )

        self.assertEqual(result["access_token"], access)
        self.assertEqual(notices, [("https://auth.openai.com/codex/device", "ABCD-EFGH")])
        self.assertEqual(codex_auth.load_tokens(self.auth_file)["refresh_token"], "refresh-token")
        self.assertEqual(
            [request.full_url for request in seen],
            [
                "https://auth.openai.com/api/accounts/deviceauth/usercode",
                "https://auth.openai.com/api/accounts/deviceauth/token",
                "https://auth.openai.com/api/accounts/deviceauth/token",
                "https://auth.openai.com/api/accounts/deviceauth/token",
                codex_auth.CODEX_OAUTH_TOKEN_URL,
            ],
        )

    def test_http_refresh_error_preserves_relogin_classification(self):
        expired = _jwt(exp=time.time() - 60)
        codex_auth.save_tokens(self.auth_file, {"access_token": expired, "refresh_token": "old-refresh"})

        def rejected(request, timeout, *, environ):
            del timeout, environ
            body = io.BytesIO(json.dumps({"error": {"code": "invalid_request"}}).encode())
            raise urllib.error.HTTPError(request.full_url, 401, "Unauthorized", {}, body)

        with self.assertRaises(codex_auth.CodexAuthError) as caught:
            codex_auth.resolve_access_token(
                self.auth_file,
                environ={"SILLYTAVERN_PROVIDER_ALLOWED_HOSTS": "auth.openai.com"},
                open_request=rejected,
            )

        self.assertEqual(caught.exception.code, "invalid_request")
        self.assertTrue(caught.exception.relogin_required)
        self.assertEqual(codex_auth.load_tokens(self.auth_file)["refresh_token"], "old-refresh")

    def test_concurrent_rejection_rotates_single_use_refresh_once(self):
        old_access = _jwt(exp=time.time() + 3600)
        new_access = _jwt(exp=time.time() + 3600)
        codex_auth.save_tokens(self.auth_file, {"access_token": old_access, "refresh_token": "old-refresh"})
        network_calls = []
        results = []

        def fake_open(_request, timeout, *, environ):
            self.assertEqual(timeout, 15)
            network_calls.append(environ)
            time.sleep(0.02)
            return _Response({"access_token": new_access, "refresh_token": "new-refresh"})

        def resolve():
            results.append(
                codex_auth.resolve_access_token(
                    self.auth_file,
                    environ={"SILLYTAVERN_PROVIDER_ALLOWED_HOSTS": "auth.openai.com"},
                    open_request=fake_open,
                    rejected_access_token=old_access,
                )
            )

        threads = [threading.Thread(target=resolve), threading.Thread(target=resolve)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        self.assertEqual(results, [new_access, new_access])
        self.assertEqual(len(network_calls), 1)

    def test_codex_headers_derive_account_and_residency_claims(self):
        access = _jwt(
            exp=time.time() + 3600,
            **{
                "https://api.openai.com/auth": {
                    "chatgpt_account_id": "account-123",
                    "chatgpt_data_residency": "eu",
                }
            },
        )

        headers = codex_auth.codex_headers(access, client_version="0.2.0")

        self.assertEqual(headers["Authorization"], f"Bearer {access}")
        self.assertEqual(headers["ChatGPT-Account-ID"], "account-123")
        self.assertEqual(headers["x-openai-internal-codex-residency"], "eu")
        self.assertEqual(headers["originator"], "codex_cli_rs")
        self.assertEqual(headers["User-Agent"], "codex_cli_rs/0.0.0 (SillyTavernTelegramBridge/0.2.0)")

    def test_logout_removes_tokens_without_error_when_repeated(self):
        codex_auth.save_tokens(self.auth_file, {"access_token": "access", "refresh_token": "refresh"})

        codex_auth.logout(self.auth_file)
        codex_auth.logout(self.auth_file)

        self.assertFalse(self.auth_file.exists())
        self.assertFalse(codex_auth.auth_status(self.auth_file)["authenticated"])


if __name__ == "__main__":
    unittest.main()
