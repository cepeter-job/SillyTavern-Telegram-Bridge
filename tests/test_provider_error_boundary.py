from __future__ import annotations

import socket
import urllib.error
from email.message import Message

import pytest
from application_test_setup import make_test_provider_port


def http_error(status: int) -> urllib.error.HTTPError:
    return urllib.error.HTTPError(
        "https://provider.example/private",
        status,
        "secret upstream detail",
        Message(),
        None,
    )


@pytest.mark.parametrize(
    ("status", "category", "text"),
    [
        (
            429,
            "rate_limit",
            "Model provider::model is rate-limited (HTTP 429). Try again later or choose another model.",
        ),
        (
            401,
            "authentication",
            (
                "Model provider::model was rejected by the provider (HTTP 401). "
                "Check its credentials or choose another model."
            ),
        ),
        (
            402,
            "credits",
            "The provider requires credits for provider::model (HTTP 402). Add credits or choose another model.",
        ),
        (
            404,
            "model_unavailable",
            "Model provider::model is unavailable (HTTP 404). Refresh providers or choose another model.",
        ),
        (
            413,
            "request_too_large",
            "The request for provider::model is too large (HTTP 413). Start a shorter session or choose another model.",
        ),
        (
            503,
            "provider_unavailable",
            "The provider for provider::model is temporarily unavailable (HTTP 503). Try again later.",
        ),
    ],
)
def test_provider_port_normalizes_http_failures_without_transport_details(status, category, text):
    from bridge.provider_errors import ProviderRequestError

    port = make_test_provider_port(generate_backend=lambda *_a, **_k: (_ for _ in ()).throw(http_error(status)))
    with pytest.raises(ProviderRequestError) as raised:
        port.generate("", "provider::model", [])

    error = raised.value
    assert error.category == category
    assert error.status == status
    assert str(error) == text
    assert "secret upstream detail" not in str(error)
    assert "provider.example" not in str(error)
    assert not hasattr(error, "cause")


@pytest.mark.parametrize(
    "source",
    [
        TimeoutError("private timeout detail"),
        socket.timeout("private timeout detail"),
        urllib.error.URLError("private endpoint detail"),
        ConnectionError("private endpoint detail"),
    ],
)
def test_provider_port_normalizes_network_failures(source):
    from bridge.provider_errors import ProviderRequestError

    port = make_test_provider_port(generate_backend=lambda *_a, **_k: (_ for _ in ()).throw(source))
    with pytest.raises(ProviderRequestError) as raised:
        port.generate("", "provider::model", [])

    assert raised.value.category in {"timeout", "network"}
    assert "private" not in str(raised.value)


def test_provider_port_does_not_relabel_unexpected_runtime_error():
    from bridge.provider_errors import ProviderRequestError

    port = make_test_provider_port(
        generate_backend=lambda *_a, **_k: (_ for _ in ()).throw(RuntimeError("application invariant failed"))
    )
    with pytest.raises(RuntimeError) as raised:
        port.generate("", "provider::model", [])

    assert not isinstance(raised.value, ProviderRequestError)
    assert str(raised.value) == "application invariant failed"


def test_character_optimizer_propagates_only_typed_provider_failure(tmp_path):
    from settings_test_support import make_test_settings

    from bridge.character_quality import optimize_character
    from bridge.provider_errors import ProviderRequestError
    from bridge.session_core import create_session
    from bridge.sqlite_store import db_connect

    settings = make_test_settings(home=tmp_path)
    db = db_connect(tmp_path / "errors.sqlite3", app_settings=settings)
    try:
        session = create_session(db, "chat", "provider::model", session_id="s", app_settings=settings)
        port = make_test_provider_port(generate_backend=lambda *_a, **_k: (_ for _ in ()).throw(http_error(429)))
        with pytest.raises(ProviderRequestError):
            optimize_character(
                db,
                "chat",
                session,
                {"name": "Alice"},
                provider_port=port,
                app_settings=settings,
            )
    finally:
        db.close()
