"""Boundary regressions found while reviewing the native Codex provider."""

import json
import os
import stat
import threading
import time

import pytest
from settings_test_support import make_test_settings
from test_codex_auth import _jwt
from test_codex_transport import _StreamingResponse

from bridge import codex_auth as auth
from bridge import codex_transport as transport
from bridge import main


@pytest.fixture
def codex_settings(tmp_path):
    settings = make_test_settings(
        {"SILLYTAVERN_PROVIDER_ALLOWED_HOSTS": "chatgpt.com,auth.openai.com,other.example"}, home=tmp_path
    )
    auth.save_tokens(
        settings.codex_oauth_file,
        {"access_token": _jwt(exp=time.time() + 3600), "refresh_token": "private-refresh"},
    )
    return settings


def generate(settings, events, **kwargs):
    return transport.generate_codex_response(
        "gpt-5.6-terra",
        [{"role": "user", "content": "Hello"}],
        {"max_tokens": 64},
        {},
        "session",
        app_settings=settings,
        open_request=lambda *a, **k: _StreamingResponse(events),
        **kwargs,
    )


def test_codex_catalog_discovery_uses_existing_oauth_directly(codex_settings, monkeypatch):
    import yaml

    from bridge import provider_discovery

    token = _jwt(
        exp=time.time() + 3600,
        **{"https://api.openai.com/auth": {"chatgpt_account_id": "test-account"}},
    )
    auth.save_tokens(codex_settings.codex_oauth_file, {"access_token": token, "refresh_token": "private-refresh"})
    codex_settings.provider_config_file.parent.mkdir(parents=True, exist_ok=True)
    codex_settings.provider_config_file.write_text(
        yaml.safe_dump(
            {
                "providers": {
                    "codex": {
                        "api_endpoint": "https://chatgpt.com/backend-api/codex",
                        "transport": "openai_codex",
                        "discover_models": True,
                        "models": ["pinned-model"],
                    }
                }
            }
        )
    )

    class CatalogResponse:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self, limit=-1):
            body = json.dumps(
                {
                    "models": [
                        {"slug": "visible-model", "supported_in_api": True, "visibility": "list"},
                        {"slug": "hidden-model", "supported_in_api": True, "visibility": "hide"},
                        {"slug": "unsupported-model", "supported_in_api": False, "visibility": "list"},
                    ]
                }
            ).encode()
            return body if limit < 0 else body[:limit]

    requests = []

    def open_request(request, **kwargs):
        requests.append((request, kwargs))
        return CatalogResponse()

    monkeypatch.setattr(provider_discovery, "strict_urlopen", open_request)
    result, refreshed, failed = provider_discovery.refresh_model_catalog(
        force=True, provider_id="codex", app_settings=codex_settings
    )

    assert (refreshed, failed) == (1, 0)
    assert result["providers"]["codex"]["models"] == ["pinned-model", "visible-model"]
    request, kwargs = requests[0]
    assert request.full_url == "https://chatgpt.com/backend-api/codex/models?client_version=1.0.0"
    assert request.get_header("Authorization") == f"Bearer {token}"
    assert request.get_header("Chatgpt-account-id") == "test-account"
    assert kwargs["timeout"] == 30


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"models": {}},
        {"models": [None]},
        {"models": [{"slug": "model", "supported_in_api": True, "visibility": []}]},
    ],
)
def test_codex_catalog_parser_rejects_malformed_catalogs(payload):
    from bridge.provider_discovery import _codex_model_ids

    with pytest.raises(ValueError, match="invalid Codex"):
        _codex_model_ids(payload)


def test_codex_catalog_discovery_is_disabled_by_default(codex_settings, monkeypatch):
    import yaml

    from bridge import provider_discovery

    codex_settings.provider_config_file.parent.mkdir(parents=True, exist_ok=True)
    codex_settings.provider_config_file.write_text(
        yaml.safe_dump(
            {
                "providers": {
                    "codex": {
                        "api_endpoint": "https://chatgpt.com/backend-api/codex",
                        "transport": "openai_codex",
                        "models": ["pinned-model"],
                    }
                }
            }
        )
    )
    monkeypatch.setattr(
        provider_discovery,
        "strict_urlopen",
        lambda *_args, **_kwargs: pytest.fail("Codex discovery must remain opt-in"),
    )

    result, refreshed, failed = provider_discovery.refresh_model_catalog(
        force=True, provider_id="codex", app_settings=codex_settings
    )

    assert (refreshed, failed) == (0, 0)
    assert result["providers"]["codex"]["models"] == ["pinned-model"]


def test_codex_catalog_discovery_rejects_non_native_endpoint(codex_settings, monkeypatch):
    import yaml

    from bridge import provider_discovery

    codex_settings.provider_config_file.parent.mkdir(parents=True, exist_ok=True)
    codex_settings.provider_config_file.write_text(
        yaml.safe_dump(
            {
                "providers": {
                    "codex": {
                        "api_endpoint": "https://other.example/v1",
                        "transport": "openai_codex",
                        "discover_models": True,
                        "models": ["pinned-model"],
                    }
                }
            }
        )
    )
    monkeypatch.setattr(
        provider_discovery,
        "strict_urlopen",
        lambda *_args, **_kwargs: pytest.fail("Codex OAuth must not be sent to a custom endpoint"),
    )

    result, refreshed, failed = provider_discovery.refresh_model_catalog(
        force=True, provider_id="codex", app_settings=codex_settings
    )

    assert (refreshed, failed) == (0, 1)
    assert result["providers"]["codex"]["models"] == ["pinned-model"]


def test_headless_login_uses_controlling_terminal_not_captured_output(codex_settings, tmp_path, monkeypatch, capsys):
    terminal = tmp_path / "terminal"
    real_open = os.open

    def open_terminal(path, flags, *args, **kwargs):
        if path == "/dev/tty":
            return real_open(terminal, os.O_WRONLY | os.O_CREAT, 0o600)
        return real_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(main.os, "open", open_terminal)
    monkeypatch.setattr(main.webbrowser, "open", lambda *a, **k: False)

    def login(_path, *, environ, notify):
        notify(auth.CODEX_DEVICE_URL, "ABCD-EFGH")
        return {}

    monkeypatch.setattr(main, "device_login", login)
    assert main._run_codex_auth_action("login", codex_settings) == 0
    assert "ABCD-EFGH" in terminal.read_text()
    assert "ABCD-EFGH" not in capsys.readouterr().out


@pytest.mark.parametrize("target", ["state", "lock", "parent"])
def test_oauth_rejects_symlinks_without_touching_target(tmp_path, target):
    directory = tmp_path / "auth"
    directory.mkdir()
    victim = tmp_path / "victim"
    victim.write_text("unchanged")
    victim.chmod(0o644)
    path = directory / "state.json"
    if target == "parent":
        directory.rmdir()
        directory.symlink_to(tmp_path, target_is_directory=True)
    else:
        (path if target == "state" else path.with_suffix(".json.lock")).symlink_to(victim)
    with pytest.raises(auth.CodexAuthError):
        auth.save_tokens(path, {"access_token": "access", "refresh_token": "refresh"})
    assert victim.read_text() == "unchanged"
    assert stat.S_IMODE(victim.stat().st_mode) == 0o644


def test_oauth_bounds_existing_state_before_reading(tmp_path):
    path = tmp_path / "oversized.json"
    path.write_text(json.dumps({"tokens": {"access_token": "x" * (1024 * 1024), "refresh_token": "secret"}}))
    with pytest.raises(auth.CodexAuthError):
        auth.load_tokens(path)


def test_failed_event_does_not_expose_provider_message(codex_settings):
    with pytest.raises(RuntimeError) as error:
        generate(
            codex_settings,
            [{"type": "response.failed", "response": {"error": {"message": "PRIVATE_PROMPT_AND_TOKEN"}}}],
        )
    assert "PRIVATE_PROMPT_AND_TOKEN" not in str(error.value)


def test_truncated_stream_is_not_a_successful_reply(codex_settings):
    with pytest.raises(RuntimeError, match="completion"):
        generate(codex_settings, [{"type": "response.output_text.delta", "delta": "Incomplete story"}])


def test_completed_payload_takes_precedence_over_partial_deltas(codex_settings):
    assert (
        generate(
            codex_settings,
            [
                {"type": "response.output_text.delta", "delta": "part"},
                {"type": "response.completed", "response": {"output_text": "Complete story"}},
            ],
        )
        == "Complete story"
    )


def test_stream_callback_is_throttled_for_a_burst(codex_settings, monkeypatch):
    # One frozen clock tick models a burst arriving before the next preview interval.
    monkeypatch.setattr(time, "monotonic", lambda: 100.0)
    events = [{"type": "response.output_text.delta", "delta": "word "} for _ in range(100)]
    events.append({"type": "response.completed", "response": {}})
    previews = []
    assert generate(codex_settings, events, stream_callback=previews.append) == ("word " * 100).strip()
    assert len(previews) <= 2
    assert previews[-1] == ("word " * 100).strip()


def test_cancelled_request_never_reads_credentials_or_calls_network(codex_settings, monkeypatch):
    cancel = threading.Event()
    cancel.set()
    monkeypatch.setattr(transport, "resolve_access_token", lambda *a, **k: pytest.fail("cancelled token read"))
    assert generate(codex_settings, [], cancel_event=cancel) == ""


def test_native_oauth_token_cannot_be_sent_to_another_allowlisted_provider(codex_settings, monkeypatch):
    monkeypatch.setattr(transport, "resolve_access_token", lambda *a, **k: pytest.fail("foreign endpoint token read"))
    with pytest.raises(RuntimeError, match="Codex endpoint"):
        transport.generate_codex_response(
            "model", [], {}, {"api_endpoint": "https://other.example/v1"}, "s", app_settings=codex_settings
        )


def test_native_codex_body_omits_unsupported_max_output_tokens(codex_settings):
    bodies = []

    def opened(request, **kwargs):
        bodies.append(json.loads(request.data))
        return _StreamingResponse([{"type": "response.completed", "response": {"output_text": "ok"}}])

    assert (
        transport.generate_codex_response(
            "gpt-5.6-terra", [], {"max_tokens": 64}, {}, "s", app_settings=codex_settings, open_request=opened
        )
        == "ok"
    )
    assert "max_output_tokens" not in bodies[0]


def test_stream_input_is_bounded(codex_settings):
    with pytest.raises(RuntimeError, match="safety limit"):
        generate(codex_settings, [{"type": "response.output_text.delta", "delta": "x" * (2 * 1024 * 1024)}])


def test_oauth_error_code_is_not_arbitrary_provider_text():
    assert auth._oauth_error_code({"error": "PRIVATE_CREDENTIAL"}, "codex_refresh_failed") == "codex_refresh_failed"


def test_health_uses_same_default_codex_endpoint_as_generation(codex_settings, monkeypatch):
    from bridge import provider_discovery

    monkeypatch.setattr(
        provider_discovery,
        "load_provider_catalog",
        lambda **k: {"codex": {"transport": "openai_codex", "name": "Codex", "models": ["gpt-5.6-terra"]}},
    )
    rows = provider_discovery.provider_health_checks("codex", app_settings=codex_settings)
    assert rows == [("codex", "Codex", "authenticated")]


def test_stream_reader_uses_bounded_line_reads(codex_settings):
    sizes = []

    class Response(_StreamingResponse):
        def readline(self, limit=-1):
            sizes.append(limit)
            return super().readline(limit)

        def __iter__(self):
            pytest.fail("unbounded HTTP line iteration")

    assert (
        transport.generate_codex_response(
            "gpt-5.6-terra",
            [],
            {},
            {},
            "s",
            app_settings=codex_settings,
            open_request=lambda *a, **k: Response([{"type": "response.completed", "response": {"output_text": "ok"}}]),
        )
        == "ok"
    )
    assert sizes and all(0 < size <= 262145 for size in sizes)
