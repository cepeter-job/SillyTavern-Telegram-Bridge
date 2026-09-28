from __future__ import annotations

import socket
import urllib.error
from email.message import Message

import pytest

from bridge.provider_user_errors import ProviderUserError, provider_user_error


class _ApplicationError(RuntimeError):
    code = 429


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (
            urllib.error.HTTPError("https://provider.example", 429, "secret upstream detail", Message(), None),
            "Model openrouter-free::qwen/qwen3.8-27b:free is rate-limited (HTTP 429). "
            "Try again later or choose another model.",
        ),
        (
            urllib.error.HTTPError("https://provider.example", 401, "secret upstream detail", Message(), None),
            "Model openrouter-free::qwen/qwen3.8-27b:free was rejected by the provider (HTTP 401). "
            "Check its credentials or choose another model.",
        ),
        (
            urllib.error.HTTPError("https://provider.example", 404, "secret upstream detail", Message(), None),
            "Model openrouter-free::qwen/qwen3.8-27b:free is unavailable (HTTP 404). "
            "Refresh providers or choose another model.",
        ),
        (
            urllib.error.HTTPError("https://provider.example", 503, "secret upstream detail", Message(), None),
            "The provider for openrouter-free::qwen/qwen3.8-27b:free is temporarily unavailable (HTTP 503). "
            "Try again later.",
        ),
        (
            socket.timeout("private endpoint detail"),
            "Model openrouter-free::qwen/qwen3.8-27b:free timed out. Retry or choose another model.",
        ),
    ],
)
def test_provider_user_error_classifies_failures_without_leaking_details(error, expected):
    result = provider_user_error(error, "openrouter-free::qwen/qwen3.8-27b:free")

    assert isinstance(result, ProviderUserError)
    assert str(result) == expected
    assert "secret upstream detail" not in str(result)
    assert "provider.example" not in str(result)


def test_provider_user_error_uses_bounded_generic_message_for_unknown_exception():
    result = provider_user_error(_ApplicationError("api_key=super-secret /private/path"), "provider::model")

    assert str(result) == "Model provider::model failed. Retry or choose another model."
    assert "super-secret" not in str(result)
    assert "/private/path" not in str(result)
