"""Canonical sanitized provider-request failures at the provider boundary."""

from __future__ import annotations

import socket
import urllib.error

_KINDS = {
    "rate_limit",
    "authentication",
    "credits_required",
    "model_unavailable",
    "request_too_large",
    "timeout",
    "provider_unavailable",
    "connection",
    "provider_rejected",
}


def _safe_model_name(model: str) -> str:
    value = str(model or "").strip()
    if (
        not value
        or len(value) > 200
        or any(character.isspace() or ord(character) < 32 or ord(character) == 127 for character in value)
    ):
        return "the selected model"
    return value


class ProviderRequestError(RuntimeError):
    """A bounded provider failure containing no upstream body, URL, credential, or path."""

    def __init__(
        self,
        kind: str,
        model: str,
        *,
        status: int | None = None,
        message: str = "provider request failed",
    ) -> None:
        if kind not in _KINDS:
            raise ValueError("invalid provider failure kind")
        super().__init__(message)
        self.kind = kind
        self.model = _safe_model_name(model)
        self.status = status if isinstance(status, int) and 100 <= status <= 599 else None


def provider_error_for_http_status(
    model: str,
    status: int,
    *,
    message: str = "provider request failed",
) -> ProviderRequestError:
    if status == 429:
        kind = "rate_limit"
    elif status in {401, 403}:
        kind = "authentication"
    elif status == 402:
        kind = "credits_required"
    elif status == 404:
        kind = "model_unavailable"
    elif status == 413:
        kind = "request_too_large"
    elif status in {408, 504}:
        kind = "timeout"
    elif 500 <= status <= 599:
        kind = "provider_unavailable"
    else:
        kind = "provider_rejected"
    return ProviderRequestError(kind, model, status=status, message=message)


def normalize_provider_transport_error(error: BaseException, model: str) -> ProviderRequestError | None:
    """Normalize known transport failures; leave unexpected application/runtime defects untouched."""
    if isinstance(error, ProviderRequestError):
        return error
    if isinstance(error, urllib.error.HTTPError):
        return provider_error_for_http_status(model, int(error.code))
    if isinstance(error, (TimeoutError, socket.timeout)):
        return ProviderRequestError("timeout", model)
    if isinstance(error, (urllib.error.URLError, ConnectionError)):
        return ProviderRequestError("connection", model)
    return None
