"""Sanitized provider failures suitable for end-user messages."""

from __future__ import annotations

import socket
import urllib.error


class ProviderUserError(RuntimeError):
    """A provider failure whose message is safe to show to an end user."""


def _safe_model_name(model: str) -> str:
    value = str(model or "").strip()
    if not value or len(value) > 200 or any(ord(character) < 32 or ord(character) == 127 for character in value):
        return "the selected model"
    return value


def provider_http_status(error: BaseException) -> int | None:
    if not isinstance(error, urllib.error.HTTPError):
        return None
    value = error.code
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def actionable_provider_user_error(error: BaseException, model: str) -> ProviderUserError | None:
    """Return a safe actionable message for recognized provider failures."""
    selected = _safe_model_name(model)
    status = provider_http_status(error)
    if status == 429:
        message = f"Model {selected} is rate-limited (HTTP 429). Try again later or choose another model."
    elif status in {401, 403}:
        message = (
            f"Model {selected} was rejected by the provider (HTTP {status}). "
            "Check its credentials or choose another model."
        )
    elif status == 402:
        message = f"The provider requires credits for {selected} (HTTP 402). Add credits or choose another model."
    elif status == 404:
        message = f"Model {selected} is unavailable (HTTP 404). Refresh providers or choose another model."
    elif status == 413:
        message = (
            f"The request for {selected} is too large (HTTP 413). Start a shorter session or choose another model."
        )
    elif status in {408, 504}:
        message = f"Model {selected} timed out (HTTP {status}). Retry or choose another model."
    elif status is not None and 500 <= status <= 599:
        message = f"The provider for {selected} is temporarily unavailable (HTTP {status}). Try again later."
    elif status is not None:
        message = f"The provider rejected {selected} (HTTP {status}). Retry or choose another model."
    elif isinstance(error, (TimeoutError, socket.timeout)):
        message = f"Model {selected} timed out. Retry or choose another model."
    elif isinstance(error, (urllib.error.URLError, ConnectionError)):
        message = f"The provider for {selected} could not be reached. Retry or choose another model."
    else:
        return None
    return ProviderUserError(message)


def provider_user_error(error: BaseException, model: str) -> ProviderUserError:
    """Classify a provider exception without exposing arbitrary upstream details."""
    known = actionable_provider_user_error(error, model)
    if known is not None:
        return known
    return ProviderUserError(f"Model {_safe_model_name(model)} failed. Retry or choose another model.")
