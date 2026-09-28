from __future__ import annotations

import urllib.error
from email.message import Message
from pathlib import Path

import pytest
from settings_test_support import make_test_settings

from bridge import provider_transport
from bridge.model_router import ModelRouter
from bridge.port_contracts import ProviderRequestError
from bridge.provider_port import ProviderPort


def _router() -> ModelRouter:
    return ModelRouter(
        load_catalog=lambda: {
            "provider": {
                "models": ["model"],
                "api_endpoint": "http://127.0.0.1:9999/v1",
                "transport": "chat_completions",
            }
        }
    )


def test_provider_transport_normalizes_http_rate_limit_without_raw_details(monkeypatch):
    error = urllib.error.HTTPError(
        "https://provider.example/private",
        429,
        "secret upstream body",
        Message(),
        None,
    )
    monkeypatch.setattr(provider_transport, "strict_urlopen", lambda *_args, **_kwargs: (_ for _ in ()).throw(error))

    with pytest.raises(ProviderRequestError) as raised:
        provider_transport.generate_provider_text(
            _router(),
            "test-key",
            "provider::model",
            [],
            app_settings=make_test_settings(),
        )

    assert raised.value.kind == "rate_limit"
    assert raised.value.status == 429
    assert raised.value.model == "provider::model"
    assert "secret upstream body" not in str(raised.value)
    assert "provider.example" not in str(raised.value)


def test_provider_transport_normalizes_model_routing_failure():
    with pytest.raises(ProviderRequestError) as raised:
        provider_transport.generate_provider_text(
            _router(),
            "test-key",
            "missing::model",
            [],
            app_settings=make_test_settings(),
        )

    assert raised.value.kind == "model_unavailable"
    assert raised.value.model == "missing::model"
    assert raised.value.status is None


def test_provider_port_preserves_unexpected_runtime_error():
    unexpected = RuntimeError("programming defect")

    def fail(*_args, **_kwargs):
        raise unexpected

    with pytest.raises(RuntimeError) as raised:
        ProviderPort(fail).generate("key", "provider::model", [])

    assert raised.value is unexpected
    assert not isinstance(raised.value, ProviderRequestError)


def test_presentation_error_formatter_has_no_transport_dependencies():
    source = (Path(__file__).parents[1] / "bridge" / "provider_user_errors.py").read_text(encoding="utf-8")
    assert "urllib" not in source
    assert "socket" not in source
