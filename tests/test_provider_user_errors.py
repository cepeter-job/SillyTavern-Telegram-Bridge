from __future__ import annotations

import pytest

from bridge.port_contracts import ProviderRequestError
from bridge.provider_user_errors import ProviderUserError, actionable_provider_user_error, provider_user_error


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (
            ProviderRequestError("rate_limit", "openrouter-free::qwen/qwen3.8-27b:free", status=429),
            "Model openrouter-free::qwen/qwen3.8-27b:free is rate-limited (HTTP 429). "
            "Try again later or choose another model.",
        ),
        (
            ProviderRequestError("authentication", "openrouter-free::qwen/qwen3.8-27b:free", status=401),
            "Model openrouter-free::qwen/qwen3.8-27b:free was rejected by the provider (HTTP 401). "
            "Check its credentials or choose another model.",
        ),
        (
            ProviderRequestError("model_unavailable", "openrouter-free::qwen/qwen3.8-27b:free", status=404),
            "Model openrouter-free::qwen/qwen3.8-27b:free is unavailable (HTTP 404). "
            "Refresh providers or choose another model.",
        ),
        (
            ProviderRequestError("provider_unavailable", "openrouter-free::qwen/qwen3.8-27b:free", status=503),
            "The provider for openrouter-free::qwen/qwen3.8-27b:free is temporarily unavailable (HTTP 503). "
            "Try again later.",
        ),
        (
            ProviderRequestError("timeout", "openrouter-free::qwen/qwen3.8-27b:free"),
            "Model openrouter-free::qwen/qwen3.8-27b:free timed out. Retry or choose another model.",
        ),
    ],
)
def test_provider_user_error_renders_canonical_failures(error, expected):
    result = provider_user_error(error)
    assert isinstance(result, ProviderUserError)
    assert str(result) == expected


def test_unexpected_application_error_is_not_actionable_provider_error():
    error = RuntimeError("api_key=super-secret /private/path")
    assert actionable_provider_user_error(error) is None
